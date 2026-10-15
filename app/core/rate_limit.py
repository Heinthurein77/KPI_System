"""Shared rate limiter (slowapi).

Applied to brute-force-sensitive endpoints (login). Limits are per client IP.
The app runs behind Caddy, which forwards the real client IP via
X-Forwarded-For, so the key function trusts that header (falling back to the
direct remote address). Without this, every request would share the proxy's
IP and the limit would become global instead of per client.
"""

from slowapi import Limiter
from slowapi.util import get_remote_address


def _client_ip_key(request):
    xff = request.headers.get("x-forwarded-for")
    if xff:
        # Rightmost entry is the one appended by OUR reverse proxy (Caddy) —
        # the only entry an attacker cannot spoof. The leftmost entry is
        # client-supplied and must never be trusted (it would let an attacker
        # rotate fake IPs to dodge the rate limit). This assumes a single
        # trusted proxy hop directly in front of the app.
        return xff.split(",")[-1].strip()
    return get_remote_address(request)


limiter = Limiter(key_func=_client_ip_key)

# Brute-force protection for login endpoints: 10 attempts per minute per IP.
# Legitimate users retrying a typo'd password stay well under this.
LOGIN_RATE_LIMIT = "10/minute"

# CPU/memory-heavy endpoints: parsing a workbook and fanning out emails are
# expensive, so cap how often each client can trigger them.
UPLOAD_RATE_LIMIT = "5/minute"
REMINDER_RATE_LIMIT = "5/minute"
