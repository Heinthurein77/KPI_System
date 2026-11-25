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

# Refresh-token rotation: frequent by design (every access-token lifetime),
# but a stolen refresh token must not be hammerable without bound.
REFRESH_RATE_LIMIT = "30/minute"

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
#
# S-04 (security review 2026-11-25): the lockout escalates — 1 minute,
# then 5 minutes, then 30 minutes (capped) for repeated lockouts on the
# same account — instead of a flat 60 s window an attacker can wait out
# forever at 10 guesses/minute. A successful login resets the escalation.
_LOCKOUT_DURATIONS_SECONDS = (60, 300, 1800)
_FAILED_LOGIN_WINDOW_SECONDS = 60
_FAILED_LOGIN_LIMIT = 10
# M-14 (security review 2026-11-25): the failed-login store used to grow
# without bound — _prune_failed_logins only ever pruned the identifier
# being looked up, so entries for other identifiers accumulated forever.
# The store is now capped: once it passes _FAILED_LOGIN_MAX_ENTRIES, a
# full prune drops every expired entry (and expired lockout) across all
# identifiers; if it is still over the cap, the oldest identifiers are
# evicted. There is no background timer in this process model (gunicorn
# workers), so the cap is enforced on write — the store can never exceed
# the cap by more than one record per call.
_FAILED_LOGIN_MAX_ENTRIES = 10_000
_failed_logins: dict[str, list[float]] = {}
# identifier -> (locked_until_monotonic, consecutive_lock_count)
_lockouts: dict[str, tuple[float, int]] = {}


def _prune_all_failed_logins(now: float) -> None:
    """Drop every expired entry across all identifiers (M-14)."""
    for identifier in list(_failed_logins):
        attempts = [t for t in _failed_logins[identifier] if now - t < _FAILED_LOGIN_WINDOW_SECONDS]
        if attempts:
            _failed_logins[identifier] = attempts
        else:
            _failed_logins.pop(identifier, None)
    for identifier in list(_lockouts):
        locked_until, _count = _lockouts[identifier]
        if now >= locked_until:
            _lockouts.pop(identifier, None)


def _prune_failed_logins(identifier: str, now: float) -> list[float]:
    attempts = [t for t in _failed_logins.get(identifier, []) if now - t < _FAILED_LOGIN_WINDOW_SECONDS]
    if attempts:
        _failed_logins[identifier] = attempts
    else:
        _failed_logins.pop(identifier, None)
    return attempts


def is_login_locked(identifier: str) -> bool:
    """True when this account is inside an (escalating) lockout, or has hit
    the failed-login limit in the current window. NOTE: an expired lockout
    entry is deliberately NOT removed here — its consecutive-lock count is
    what makes the next lockout escalate (S-04), and dropping it on read
    would silently reset every account to a 1-minute lock. The count is
    reset by a successful login (clear_failed_logins) or by store
    eviction when the M-14 cap is hit."""
    now = time.monotonic()
    locked_until, _count = _lockouts.get(identifier, (0.0, 0))
    if now < locked_until:
        return True
    return len(_prune_failed_logins(identifier, now)) >= _FAILED_LOGIN_LIMIT


def record_failed_login(identifier: str) -> None:
    now = time.monotonic()
    _failed_logins.setdefault(identifier, []).append(now)
    attempts = _prune_failed_logins(identifier, now)
    if len(attempts) >= _FAILED_LOGIN_LIMIT:
        # Escalating lockout (S-04): each successive lockout on this
        # account lasts longer, up to 30 minutes.
        _locked_until, lock_count = _lockouts.get(identifier, (0.0, 0))
        duration = _LOCKOUT_DURATIONS_SECONDS[min(lock_count, len(_LOCKOUT_DURATIONS_SECONDS) - 1)]
        _lockouts[identifier] = (now + duration, lock_count + 1)
        # The failures that caused the lock are already counted/audited;
        # clear the window so the next lockout needs a fresh 10 failures
        # after this lock expires instead of re-triggering immediately.
        _failed_logins.pop(identifier, None)
    if len(_failed_logins) + len(_lockouts) > _FAILED_LOGIN_MAX_ENTRIES:
        # M-14: cap the store — prune everything expired first.
        _prune_all_failed_logins(now)
        while len(_failed_logins) + len(_lockouts) > _FAILED_LOGIN_MAX_ENTRIES:
            if _failed_logins:
                _failed_logins.pop(next(iter(_failed_logins)))
            elif _lockouts:
                _lockouts.pop(next(iter(_lockouts)))
            else:
                break


def clear_failed_logins(identifier: str) -> None:
    _failed_logins.pop(identifier, None)
    # A successful login resets the escalation (S-04): the next lockout
    # after a genuine login starts again at 1 minute.
    _lockouts.pop(identifier, None)


def reset_failed_login_attempts() -> None:
    """Test helper — conftest resets this between tests."""
    _failed_logins.clear()
    _lockouts.clear()
