"""Shared rate limiter (slowapi).

Applied to brute-force-sensitive endpoints (login). Limits are per client IP.
When the app runs behind a reverse proxy, set the proxy to forward the real
client IP via X-Forwarded-For and configure trusted proxies so get_remote_address
sees it.
"""

from slowapi import Limiter
from slowapi.util import get_remote_address

limiter = Limiter(key_func=get_remote_address)

# Brute-force protection for login endpoints: 10 attempts per minute per IP.
# Legitimate users retrying a typo'd password stay well under this.
LOGIN_RATE_LIMIT = "10/minute"
