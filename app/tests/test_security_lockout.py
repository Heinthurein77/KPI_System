"""S-04 + M-14 (security review 2026-11-25): escalating lockout, password
complexity (letter + digit + common-password blocklist), new≠current, and
the bounded failed-login store.
"""

import pytest
import pyotp
from fastapi.testclient import TestClient

from app.core import rate_limit
from app.core.security import validate_password_complexity
from app.main import app
from app.models.user import UserRole
from app.tests.factories import DEFAULT_PASSWORD, auth_headers, make_tenant, make_user

# Bug 4/4: the two 429s must be distinguishable — the per-account lockout
# names the account as locked; the per-IP slowapi limit names the network.
LOCKOUT_429_DETAIL = (
    "Too many failed attempts. This account is temporarily locked — "
    "please wait and try again later."
)
IP_LIMIT_429_DETAIL = (
    "Too many requests from this network (IP rate limit). "
    "Please wait a minute and try again."
)


class _FakeClock:
    def __init__(self):
        self.now = 1000.0

    def monotonic(self):
        return self.now


@pytest.fixture()
def fake_clock(monkeypatch):
    clock = _FakeClock()
    monkeypatch.setattr(rate_limit.time, "monotonic", clock.monotonic)
    return clock


def test_lockout_escalates_1m_then_5m_then_30m(fake_clock):
    ident = "tenant:1:target@example.com"
    assert rate_limit.is_login_locked(ident) is False

    # First lockout: 10 failures → locked for 60 s.
    for _ in range(10):
        rate_limit.record_failed_login(ident)
    assert rate_limit.is_login_locked(ident) is True
    fake_clock.now += 59
    assert rate_limit.is_login_locked(ident) is True
    fake_clock.now += 2  # 61 s in — first lock expired.
    assert rate_limit.is_login_locked(ident) is False

    # Second lockout: 10 more failures → locked for 300 s.
    for _ in range(10):
        rate_limit.record_failed_login(ident)
    assert rate_limit.is_login_locked(ident) is True
    fake_clock.now += 61
    assert rate_limit.is_login_locked(ident) is True
    fake_clock.now += 240  # 301 s in — second lock expired.
    assert rate_limit.is_login_locked(ident) is False

    # Third lockout: capped at 1800 s.
    for _ in range(10):
        rate_limit.record_failed_login(ident)
    assert rate_limit.is_login_locked(ident) is True
    fake_clock.now += 301
    assert rate_limit.is_login_locked(ident) is True
    fake_clock.now += 1500  # 1801 s in — third lock expired.
    assert rate_limit.is_login_locked(ident) is False


def test_successful_login_resets_lockout_escalation(fake_clock):
    ident = "tenant:1:reset@example.com"
    for _ in range(10):
        rate_limit.record_failed_login(ident)
    assert rate_limit.is_login_locked(ident) is True
    fake_clock.now += 61
    assert rate_limit.is_login_locked(ident) is False

    # A successful login clears the escalation: the next lockout is 60 s
    # again, not 300 s.
    rate_limit.clear_failed_logins(ident)
    for _ in range(10):
        rate_limit.record_failed_login(ident)
    assert rate_limit.is_login_locked(ident) is True
    fake_clock.now += 61
    assert rate_limit.is_login_locked(ident) is False


def test_failed_login_store_is_capped(fake_clock, monkeypatch):
    monkeypatch.setattr(rate_limit, "_FAILED_LOGIN_MAX_ENTRIES", 5)
    for i in range(20):
        rate_limit.record_failed_login(f"tenant:1:user{i}@example.com")
    assert len(rate_limit._failed_logins) + len(rate_limit._lockouts) <= 5

    # Expired entries are pruned in full when the cap is hit again.
    fake_clock.now += 3600
    rate_limit.record_failed_login("tenant:1:fresh@example.com")
    assert len(rate_limit._failed_logins) == 1


def test_password_complexity_rules():
    validate_password_complexity("NewPassword456!")
    validate_password_complexity("a1b2c3d4")
    with pytest.raises(ValueError, match="at least 8 characters"):
        validate_password_complexity("Ab1!")
    with pytest.raises(ValueError, match="at least one letter"):
        validate_password_complexity("12345678")
    with pytest.raises(ValueError, match="at least one digit"):
        validate_password_complexity("NoDigitsHere!")
    with pytest.raises(ValueError, match="too common"):
        validate_password_complexity("password123")
    with pytest.raises(ValueError, match="too common"):
        validate_password_complexity("Password123")


def test_change_password_rejects_weak_and_same_passwords(db, client):
    tenant = make_tenant(db)
    user = make_user(db, tenant, UserRole.EMPLOYEE)
    headers = auth_headers(user, tenant)

    for bad in ("short", "allletters!", "password123"):
        resp = client.post(
            "/api/auth/change-password",
            json={"current_password": DEFAULT_PASSWORD, "new_password": bad},
            headers=headers,
        )
        assert resp.status_code == 400, bad

    # new == current is rejected even though it passes complexity.
    resp = client.post(
        "/api/auth/change-password",
        json={"current_password": DEFAULT_PASSWORD, "new_password": DEFAULT_PASSWORD},
        headers=headers,
    )
    assert resp.status_code == 400
    assert "differ" in resp.json()["detail"]

    # 5 requests total here (3 weak + same + this one) — exactly the
    # per-user 5/minute budget, so the good change still goes through.
    resp = client.post(
        "/api/auth/change-password",
        json={"current_password": DEFAULT_PASSWORD, "new_password": "BrandNewPass123"},
        headers=headers,
    )
    assert resp.status_code == 204


def test_platform_change_password_rejects_weak_and_same(db, client):
    admin = make_user(db, None, UserRole.SUPER_ADMIN, email="root-complex@platform.example.com")
    headers = auth_headers(admin)

    resp = client.post(
        "/api/platform/auth/change-password",
        json={"current_password": DEFAULT_PASSWORD, "new_password": "password123"},
        headers=headers,
    )
    assert resp.status_code == 400
    resp = client.post(
        "/api/platform/auth/change-password",
        json={"current_password": DEFAULT_PASSWORD, "new_password": DEFAULT_PASSWORD},
        headers=headers,
    )
    assert resp.status_code == 400


def test_escalating_lockout_via_login_endpoint(db, client):
    """End-to-end: 10 bad logins lock the account (429 on the 11th), and
    the lock is per-account — another account still logs in."""
    tenant = make_tenant(db)
    locked = make_user(db, tenant, UserRole.EMPLOYEE, email="locked@example.com")
    other = make_user(db, tenant, UserRole.EMPLOYEE, email="other@example.com")
    headers = {"X-Tenant-Slug": tenant.slug}

    codes = []
    for _ in range(11):
        resp = client.post(
            "/api/auth/login",
            json={"email": locked.email, "password": "WrongPassword1!"},
            headers=headers,
        )
        codes.append(resp.status_code)
    assert codes[:10] == [401] * 10
    assert codes[10] == 429

    # The lock is per-account, not per-IP: another account still logs in
    # (from a different client IP, so the shared 10/minute IP budget the
    # 11 locked attempts just consumed does not mask the result).
    resp = client.post(
        "/api/auth/login",
        json={"email": other.email, "password": DEFAULT_PASSWORD},
        headers={**headers, "X-Forwarded-For": "10.0.0.99"},
    )
    assert resp.status_code == 200


def test_locked_account_returns_exact_lockout_message(db, client):
    """(a) A locked account gets the lockout wording, naming the account
    as locked. The request comes from a fresh IP so the per-IP limiter
    has budget left and the per-account lockout is the one answering —
    from the same IP the slowapi limit fires first instead."""
    tenant = make_tenant(db)
    user = make_user(db, tenant, UserRole.EMPLOYEE, email="locked-msg@example.com")
    headers = {"X-Tenant-Slug": tenant.slug}
    for _ in range(10):
        resp = client.post(
            "/api/auth/login",
            json={"email": user.email, "password": "WrongPassword1!"},
            headers=headers,
        )
        assert resp.status_code == 401, resp.text

    other_ip = TestClient(app, client=("192.0.2.55", 50000))
    resp = other_ip.post(
        "/api/auth/login",
        json={"email": user.email, "password": DEFAULT_PASSWORD},
        headers=headers,
    )
    assert resp.status_code == 429, resp.text
    assert resp.json()["detail"] == LOCKOUT_429_DETAIL


def test_ip_rate_limit_returns_exact_ip_message(db, client):
    """(b) 11 login attempts from one IP trip the per-IP slowapi limiter,
    which fires before the view — so this 429 carries the network/IP
    wording, not the account-lockout wording."""
    tenant = make_tenant(db)
    make_user(db, tenant, UserRole.EMPLOYEE, email="ip-limited@example.com")
    headers = {"X-Tenant-Slug": tenant.slug}
    codes = []
    for _ in range(11):
        resp = client.post(
            "/api/auth/login",
            json={"email": "ip-limited@example.com", "password": "WrongPassword1!"},
            headers=headers,
        )
        codes.append(resp.status_code)
    assert codes[:10] == [401] * 10
    assert codes[10] == 429
    assert resp.json()["detail"] == IP_LIMIT_429_DETAIL


def test_lockout_and_ip_limit_messages_differ(db, client):
    """(c) Both 429s, side by side, from one locked account: the same-IP
    attempt is answered by the IP limiter, the fresh-IP attempt by the
    per-account lockout — and the two messages must not be the same, so
    a blocked user can tell which block they are under."""
    tenant = make_tenant(db)
    user = make_user(db, tenant, UserRole.EMPLOYEE, email="distinct@example.com")
    headers = {"X-Tenant-Slug": tenant.slug}
    for _ in range(10):
        resp = client.post(
            "/api/auth/login",
            json={"email": user.email, "password": "WrongPassword1!"},
            headers=headers,
        )
        assert resp.status_code == 401, resp.text

    ip_resp = client.post(
        "/api/auth/login",
        json={"email": user.email, "password": "WrongPassword1!"},
        headers=headers,
    )
    assert ip_resp.status_code == 429
    assert ip_resp.json()["detail"] == IP_LIMIT_429_DETAIL

    other_ip = TestClient(app, client=("192.0.2.56", 50000))
    lock_resp = other_ip.post(
        "/api/auth/login",
        json={"email": user.email, "password": DEFAULT_PASSWORD},
        headers=headers,
    )
    assert lock_resp.status_code == 429
    assert lock_resp.json()["detail"] == LOCKOUT_429_DETAIL

    assert ip_resp.json()["detail"] != lock_resp.json()["detail"]
    assert "account" in lock_resp.json()["detail"].lower()
    assert "locked" in lock_resp.json()["detail"].lower()
    assert "IP rate limit" in ip_resp.json()["detail"]


def test_totp_step_lockout_returns_exact_lockout_message(db, client):
    """(d) The TOTP second step (auth.py:243) answers a locked account with
    the same lockout wording as the password step. Needs an enrolled
    account: enrol via the API, take the password step to get the pending
    cookie, lock the account with 10 failures from a fresh IP (so the
    per-IP limiter stays out of the way), then present the pending cookie
    + a correct code from another fresh IP."""
    tenant = make_tenant(db)
    admin = make_user(db, tenant, UserRole.TENANT_ADMIN, email="totp-locked@example.com")
    headers = {"X-Tenant-Slug": tenant.slug}
    # Enrol TOTP (same calls as test_security_totp._enable_totp).
    resp = client.post("/api/auth/totp/enroll", headers=auth_headers(admin, tenant))
    assert resp.status_code == 200, resp.text
    secret = resp.json()["secret"]
    resp = client.post(
        "/api/auth/totp/verify-enrolment",
        json={"code": pyotp.TOTP(secret).now()},
        headers=auth_headers(admin, tenant),
    )
    assert resp.status_code == 200, resp.text

    # Password step: pending cookie, no session yet.
    resp = client.post(
        "/api/auth/login",
        json={"email": admin.email, "password": DEFAULT_PASSWORD},
        headers=headers,
    )
    assert resp.status_code == 200
    assert resp.json()["totp_required"] is True
    pending = client.cookies["totp_pending"]

    # Lock the account: 10 failures from a fresh IP.
    fail_ip = TestClient(app, client=("192.0.2.60", 50000))
    for _ in range(10):
        resp = fail_ip.post(
            "/api/auth/login",
            json={"email": admin.email, "password": "WrongPassword1!"},
            headers=headers,
        )
        assert resp.status_code == 401, resp.text

    # TOTP step from another fresh IP with the pending cookie + right code.
    totp_ip = TestClient(app, client=("192.0.2.61", 50000))
    resp = totp_ip.post(
        "/api/auth/login/totp",
        json={"code": pyotp.TOTP(secret).now()},
        cookies={"totp_pending": pending},
    )
    assert resp.status_code == 429, resp.text
    assert resp.json()["detail"] == LOCKOUT_429_DETAIL
