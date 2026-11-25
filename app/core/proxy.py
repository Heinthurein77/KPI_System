"""Trusted-proxy handling for X-Forwarded-For / X-Forwarded-Proto.

The app runs behind Caddy (same Docker network, or localhost in dev),
which overwrites X-Forwarded-For with the real client IP. The app must
only trust those headers when the socket peer actually IS the proxy —
otherwise any direct client can forge X-Forwarded-For and defeat the
Attendance WiFi gate and the per-IP login rate limit.

Trusted peers come from ``TRUSTED_PROXY_CIDRS`` (app/core/config.py):
loopback plus the Docker bridge space by default. That is deliberately
narrower than the old rule (any RFC-1918 peer): 10/8 and 192.168/16
peers are no longer trusted, and on the server the list should be
pinned to the Caddy container / compose network (see the setting's
comment). Public IPs — including the documentation ranges used in
tests (203.0.113.0/24, 198.51.100.0/24, which ``ipaddress``
misleadingly reports as private) — are never trusted. The literal peer
``"testclient"`` is Starlette TestClient's synthetic peer (it has no
socket); it is trusted so the existing test suite, which simulates
office WiFi by setting the header, keeps working. It can never occur
on a real socket connection.
"""

from __future__ import annotations

import ipaddress
from functools import lru_cache


@lru_cache(maxsize=8)
def _trusted_networks(cidrs: str) -> tuple:
    networks = []
    for entry in cidrs.split(","):
        entry = entry.strip()
        if not entry:
            continue
        try:
            networks.append(ipaddress.ip_network(entry, strict=False))
        except ValueError:
            continue
    return tuple(networks)


def _configured_networks() -> tuple:
    # Read through get_settings() at call time (not import time) so a
    # TRUSTED_PROXY_CIDRS change via the environment is honoured
    # without an import cycle: config does not import this module.
    from app.core.config import get_settings

    return _trusted_networks(get_settings().TRUSTED_PROXY_CIDRS)


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
    return any(addr in net for net in _configured_networks())


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
