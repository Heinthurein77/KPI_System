"""Shared rate limiter (slowapi).

Applied to brute-force-sensitive and heavy endpoints. Limits are per
client IP by default — but only via the trusted-proxy rule in
app/core/proxy.py: X-Forwarded-For is honoured only when the socket peer
is a trusted proxy (Caddy on the private Docker network / loopback).
A direct client forging the header gets keyed on its real socket IP,
so the login limit can no longer be dodged by rotating fake IPs.

Per-worker note (accepted): slowapi's in-memory store lives per gunicorn
worker, so with 2 workers an IP limit of N/minute is effectively ~2N.
The per-account login limit below is also per-worker, so the same 2x
applies there; it still bounds a rotating-IP attack to ~20 failed
attempts/minute per account instead of unlimited.

Hot endpoints that many users hit from one office NAT (check-in/out,
reports, exports) are keyed per authenticated user, not per IP — an
office full of employees shares one public IP, so a per-IP limit there
would lock the whole office out at 09:00.
"""

import time

from slowapi import Limiter
from slowapi.util import get_remote_address

from app.core.proxy import trusted_client_ip


def _client_ip_key(request):
    return trusted_client_ip(request)


def _authenticated_user_key(request):
    """Per-user limit key: the session token's user id when present,
    otherwise the (trusted) client IP. Decodes the JWT without a DB hit."""
    try:
        from app.core.security import SESSION_COOKIE_NAME, decode_session_token

        token = request.cookies.get(SESSION_COOKIE_NAME)
        claims = decode_session_token(token) if token else None
        if claims is not None:
            return f"user:{claims.user_id}"
    except Exception:
        pass
    return _client_ip_key(request)


limiter = Limiter(key_func=_client_ip_key)

# Brute-force protection for login endpoints: 10 attempts per minute per IP.
# Legitimate users retrying a typo'd password stay well under this.
LOGIN_RATE_LIMIT = "10/minute"

# Password-change / reset endpoints: 5 attempts per minute (wrong-current-
# password probing must not be unlimited, even with a stolen session).
PASSWORD_RATE_LIMIT = "5/minute"

# CPU/memory-heavy endpoints: parsing a workbook and fanning out emails are
# expensive, so cap how often each client can trigger them.
UPLOAD_RATE_LIMIT = "5/minute"
REMINDER_RATE_LIMIT = "5/minute"

# Hot endpoints (per authenticated user — see module docstring).
ATTENDANCE_RATE_LIMIT = "10/minute"   # check-in / check-out
REPORT_RATE_LIMIT = "20/minute"       # attendance / KPI report reads
EXPORT_RATE_LIMIT = "10/minute"       # Excel / PDF exports
BULK_APPROVE_RATE_LIMIT = "10/minute"
RECURRING_RATE_LIMIT = "5/minute"     # recurring KPI run
ADMIN_WRITE_RATE_LIMIT = "10/minute"  # user / template creation


# ------------------------------------------------------- per-account login limit
# The IP limit alone does not stop a distributed attack (many IPs, one
# account). Failed logins are also counted per account identifier, in
# memory, and reset on a successful login. This is deliberately separate
# from slowapi: its key function cannot read the request body (the email).
_FAILED_LOGIN_WINDOW_SECONDS = 60
_FAILED_LOGIN_LIMIT = 10
_failed_logins: dict[str, list[float]] = {}


def _prune_failed_logins(identifier: str, now: float) -> list[float]:
    attempts = [t for t in _failed_logins.get(identifier, []) if now - t < _FAILED_LOGIN_WINDOW_SECONDS]
    if attempts:
        _failed_logins[identifier] = attempts
    else:
        _failed_logins.pop(identifier, None)
    return attempts


def is_login_locked(identifier: str) -> bool:
    """True when this account has hit the failed-login limit in the window."""
    return len(_prune_failed_logins(identifier, time.monotonic())) >= _FAILED_LOGIN_LIMIT


def record_failed_login(identifier: str) -> None:
    _failed_logins.setdefault(identifier, []).append(time.monotonic())


def clear_failed_logins(identifier: str) -> None:
    _failed_logins.pop(identifier, None)


def reset_failed_login_attempts() -> None:
    """Test helper — conftest resets this between tests."""
    _failed_logins.clear()
