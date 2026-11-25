"""S-04 + M-14 (security review 2026-11-25): escalating lockout, password
complexity (letter + digit + common-password blocklist), new≠current, and
the bounded failed-login store.
"""

import pytest

from app.core import rate_limit
from app.core.security import validate_password_complexity
from app.models.user import UserRole
from app.tests.factories import DEFAULT_PASSWORD, auth_headers, make_tenant, make_user


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
