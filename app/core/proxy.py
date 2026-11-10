"""Trusted-proxy handling for X-Forwarded-For / X-Forwarded-Proto.

The app runs behind Caddy (same Docker network, or localhost in dev),
which overwrites X-Forwarded-For with the real client IP. The app must
only trust those headers when the socket peer actually IS the proxy —
otherwise any direct client can forge X-Forwarded-For and defeat the
Attendance WiFi gate and the per-IP login rate limit.

Trusted peers are loopback and RFC-1918 private networks (the Docker
bridge network Caddy sits on is 172.16/12) plus ULA IPv6. Public IPs —
including the documentation ranges used in tests (203.0.113.0/24,
198.51.100.0/24, which ``ipaddress`` misleadingly reports as private) —
are never trusted. The literal peer ``"testclient"`` is Starlette
TestClient's synthetic peer (it has no socket); it is trusted so the
existing test suite, which simulates office WiFi by setting the header,
keeps working. It can never occur on a real socket connection.
"""

from __future__ import annotations

import ipaddress

_TRUSTED_NETWORKS = (
    ipaddress.ip_network("127.0.0.0/8"),   # loopback
    ipaddress.ip_network("::1/128"),       # loopback v6
    ipaddress.ip_network("10.0.0.0/8"),    # RFC 1918
    ipaddress.ip_network("172.16.0.0/12"),  # RFC 1918 (Docker bridge)
    ipaddress.ip_network("192.168.0.0/16"),  # RFC 1918
    ipaddress.ip_network("fd00::/8"),      # IPv6 ULA
)


def is_trusted_proxy_peer(peer: str | None) -> bool:
    """True when the socket peer is a proxy we trust to set forwarding headers."""
    if peer is None:
        return False
    if peer == "testclient":
        # Starlette TestClient's synthetic peer — tests only, never a real socket.
        return True
    try:
        addr = ipaddress.ip_address(peer)
    except ValueError:
        return False
    return any(addr in net for net in _TRUSTED_NETWORKS)


def _socket_peer(request) -> str:
    return request.client.host if request.client else ""


def trusted_client_ip(request) -> str:
    """Real client IP: the X-Forwarded-For value only when the socket peer
    is a trusted proxy, otherwise the socket peer itself. Caddy overwrites
    the header with the single real client IP, so the rightmost entry is
    the one the proxy appended."""
    peer = _socket_peer(request)
    if is_trusted_proxy_peer(peer):
        forwarded = request.headers.get("x-forwarded-for")
        if forwarded:
            entries = [part.strip() for part in forwarded.split(",") if part.strip()]
            if entries:
                return entries[-1]
    return peer


def is_secure_request(request) -> bool:
    """True when the request arrived over HTTPS. X-Forwarded-Proto is only
    trusted from a trusted proxy peer; otherwise the request's own scheme
    decides (a direct HTTP client cannot claim to be HTTPS)."""
    peer = _socket_peer(request)
    if is_trusted_proxy_peer(peer):
        proto = request.headers.get("x-forwarded-proto", request.url.scheme)
    else:
        proto = request.url.scheme
    return proto.split(",")[0].strip().lower() == "https"
