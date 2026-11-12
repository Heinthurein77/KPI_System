"""Tenant login (POST /api/auth/login) and the JWT/tenant cross-check inside
get_current_user (app/core/deps.py) that stops a valid token for one tenant
from being replayed against another tenant's context."""

from app.core.security import verify_password
from app.models.tenant import TenantStatus
from app.models.user import UserRole
from app.tests.factories import DEFAULT_PASSWORD, auth_headers, make_tenant, make_user

def test_tenant_login_success(db, client):
    tenant = make_tenant(db)
    user = make_user(db, tenant, UserRole.EMPLOYEE)

    resp = client.post(
        "/api/auth/login",
        json={"email": user.email, "password": DEFAULT_PASSWORD},
        headers={"X-Tenant-Slug": tenant.slug},
    )

    assert resp.status_code == 200
    body = resp.json()
    assert body["user"]["role"] == "employee"
    assert body["user"]["tenant_id"] == tenant.id
    assert "access_token" not in body  # cookie-only sessions: no token in the body


def test_tenant_login_wrong_password(db, client):
    tenant = make_tenant(db)
    user = make_user(db, tenant, UserRole.EMPLOYEE)

    resp = client.post(
        "/api/auth/login",
        json={"email": user.email, "password": "wrong-password"},
        headers={"X-Tenant-Slug": tenant.slug},
    )

    assert resp.status_code == 401


def test_tenant_login_no_tenant_resolved(db, client):
    """No X-Tenant-Slug header and no subdomain configured (TENANT_BASE_DOMAIN
    unset in tests) means no tenant is resolved at all."""
    tenant = make_tenant(db)
    user = make_user(db, tenant, UserRole.EMPLOYEE)

    resp = client.post(
        "/api/auth/login",
        json={"email": user.email, "password": DEFAULT_PASSWORD},
    )

    assert resp.status_code == 400
    assert resp.json()["detail"] == "No organization could be resolved for this request."


def test_tenant_login_unknown_slug(db, client):
    """An unknown slug leaves request.state.tenant as None (the middleware
    itself never errors) so it surfaces via the same 400 as no-tenant-at-all."""
    tenant = make_tenant(db)
    user = make_user(db, tenant, UserRole.EMPLOYEE)

    resp = client.post(
        "/api/auth/login",
        json={"email": user.email, "password": DEFAULT_PASSWORD},
        headers={"X-Tenant-Slug": "does-not-exist"},
    )

    assert resp.status_code == 400
    assert resp.json()["detail"] == "No organization could be resolved for this request."


def test_tenant_login_suspended_tenant(db, client):
    tenant = make_tenant(db, status=TenantStatus.SUSPENDED)
    user = make_user(db, tenant, UserRole.EMPLOYEE)

    resp = client.post(
        "/api/auth/login",
        json={"email": user.email, "password": DEFAULT_PASSWORD},
        headers={"X-Tenant-Slug": tenant.slug},
    )

    assert resp.status_code == 403
    assert resp.json()["detail"] == "This organization's account is suspended."


def test_tenant_login_inactive_user(db, client):
    tenant = make_tenant(db)
    user = make_user(db, tenant, UserRole.EMPLOYEE, is_active=False)

    resp = client.post(
        "/api/auth/login",
        json={"email": user.email, "password": DEFAULT_PASSWORD},
        headers={"X-Tenant-Slug": tenant.slug},
    )

    assert resp.status_code == 401


def test_token_rejected_when_spoofed_to_different_tenant(db, client):
    """A valid JWT for tenant A's user, replayed with X-Tenant-Slug pointing at
    tenant B, must not authenticate as a member of tenant B (or at all) — the
    header only selects which context to check the token against."""
    tenant_a = make_tenant(db, name="Acme", slug="acme")
    tenant_b = make_tenant(db, name="Globex", slug="globex")
    user_a = make_user(db, tenant_a, UserRole.EMPLOYEE)

    headers = auth_headers(user_a, tenant_a)
    headers["X-Tenant-Slug"] = tenant_b.slug

    resp = client.get("/api/auth/me", headers=headers)
    assert resp.status_code == 401


def test_change_password_success(db, client):
    tenant = make_tenant(db)
    user = make_user(db, tenant, UserRole.EMPLOYEE)
    headers = auth_headers(user, tenant)

    resp = client.post(
        "/api/auth/change-password",
        json={"current_password": DEFAULT_PASSWORD, "new_password": "NewPassword456!"},
        headers=headers,
    )
    assert resp.status_code == 204

    db.refresh(user)
    assert verify_password("NewPassword456!", user.password_hash)

    # The old password no longer works; the new one logs in.
    resp = client.post(
        "/api/auth/login",
        json={"email": user.email, "password": DEFAULT_PASSWORD},
        headers={"X-Tenant-Slug": tenant.slug},
    )
    assert resp.status_code == 401

    resp = client.post(
        "/api/auth/login",
        json={"email": user.email, "password": "NewPassword456!"},
        headers={"X-Tenant-Slug": tenant.slug},
    )
    assert resp.status_code == 200


def test_change_password_wrong_current_password(db, client):
    tenant = make_tenant(db)
    user = make_user(db, tenant, UserRole.EMPLOYEE)
    headers = auth_headers(user, tenant)

    resp = client.post(
        "/api/auth/change-password",
        json={"current_password": "wrong-password", "new_password": "NewPassword456!"},
        headers=headers,
    )
    assert resp.status_code == 400

    db.refresh(user)
    assert verify_password(DEFAULT_PASSWORD, user.password_hash)


def test_change_password_too_short(db, client):
    tenant = make_tenant(db)
    user = make_user(db, tenant, UserRole.EMPLOYEE)
    headers = auth_headers(user, tenant)

    resp = client.post(
        "/api/auth/change-password",
        json={"current_password": DEFAULT_PASSWORD, "new_password": "short"},
        headers=headers,
    )
    assert resp.status_code == 400

    db.refresh(user)
    assert verify_password(DEFAULT_PASSWORD, user.password_hash)


def test_change_password_requires_auth(client):
    resp = client.post(
        "/api/auth/change-password",
        json={"current_password": DEFAULT_PASSWORD, "new_password": "NewPassword456!"},
    )
    assert resp.status_code == 401


def test_tenant_token_without_tenant_header_rejected(db, client):
    """A tenant-scoped user's token sent with no X-Tenant-Slug at all resolves
    no tenant, landing in get_current_user's platform-context branch — which
    only admits tenant_id IS NULL accounts, so this must also fail."""
    tenant = make_tenant(db)
    user = make_user(db, tenant, UserRole.EMPLOYEE)

    headers = auth_headers(user, tenant)
    del headers["X-Tenant-Slug"]

    resp = client.get("/api/auth/me", headers=headers)
    assert resp.status_code == 401


def test_login_rate_limit_returns_429_after_10_attempts(db, client):
    tenant = make_tenant(db)
    make_user(db, tenant, UserRole.EMPLOYEE, email="limited@example.com")

    headers = {"X-Tenant-Slug": tenant.slug}
    codes = []
    for _ in range(11):
        resp = client.post(
            "/api/auth/login",
            json={"email": "limited@example.com", "password": "WrongPassword1!"},
            headers=headers,
        )
        codes.append(resp.status_code)

    assert codes[:10] == [401] * 10
    assert codes[10] == 429
    assert "Too many attempts" in resp.json()["detail"]


def test_logout_revokes_token_server_side(db, client):
    """A replayed token must stop working after logout, not just lose its cookie."""
    from app.tests.factories import DEFAULT_PASSWORD

    tenant = make_tenant(db)
    user = make_user(db, tenant, UserRole.EMPLOYEE)
    r = client.post(
        "/api/auth/login",
        json={"email": user.email, "password": DEFAULT_PASSWORD},
        headers={"X-Tenant-Slug": tenant.slug},
    )
    assert r.status_code == 200
    assert "access_token" not in r.json()  # cookie-only sessions
    first_token = client.cookies.get("access_token")
    assert first_token
    # A second session for the same user, created before the logout.
    r = client.post(
        "/api/auth/login",
        json={"email": user.email, "password": DEFAULT_PASSWORD},
        headers={"X-Tenant-Slug": tenant.slug},
    )
    assert r.status_code == 200
    second_token = client.cookies.get("access_token")
    assert second_token and second_token != first_token

    # Log out the FIRST session explicitly (the jar currently holds the second).
    client.cookies.clear()
    r = client.post(
        "/api/auth/logout",
        headers={"X-Tenant-Slug": tenant.slug, "Cookie": f"access_token={first_token}"},
    )
    assert r.status_code == 204
    # Replay the logged-out token: must be 401 (revoked server-side).
    replay = client.get(
        "/api/auth/me",
        headers={"X-Tenant-Slug": tenant.slug, "Cookie": f"access_token={first_token}"},
    )
    assert replay.status_code == 401
    # The second session still works.
    alive = client.get(
        "/api/auth/me",
        headers={"X-Tenant-Slug": tenant.slug, "Cookie": f"access_token={second_token}"},
    )
    assert alive.status_code == 200


# ---------- Login audit trail (M7, PM review 2026-11-12) ----------


def _audit_entries(client, admin, tenant):
    resp = client.get("/api/admin/audit-log", headers=auth_headers(admin, tenant))
    assert resp.status_code == 200
    return resp.json()


def test_login_success_records_audit_entry(db, client):
    tenant = make_tenant(db)
    admin = make_user(db, tenant, UserRole.TENANT_ADMIN)
    user = make_user(db, tenant, UserRole.EMPLOYEE)

    resp = client.post(
        "/api/auth/login",
        json={"email": user.email, "password": DEFAULT_PASSWORD},
        headers={"X-Tenant-Slug": tenant.slug},
    )
    assert resp.status_code == 200

    # The login set the employee's session cookie in the client jar; clear
    # it so the audit-log read below authenticates as the tenant admin via
    # the Authorization header, not as the employee via the cookie.
    client.cookies.clear()

    entries = [e for e in _audit_entries(client, admin, tenant) if e["action"] == "login_success"]
    assert len(entries) == 1
    entry = entries[0]
    assert entry["entity_type"] == "user"
    assert entry["entity_id"] == user.id
    assert entry["entity_label"] == user.name
    assert entry["actor_name"] == user.name
    assert entry["actor_role"] == "employee"


def test_login_failure_records_audit_entry(db, client):
    tenant = make_tenant(db)
    admin = make_user(db, tenant, UserRole.TENANT_ADMIN)
    user = make_user(db, tenant, UserRole.EMPLOYEE)

    resp = client.post(
        "/api/auth/login",
        json={"email": user.email, "password": "wrong-password"},
        headers={"X-Tenant-Slug": tenant.slug},
    )
    assert resp.status_code == 401

    entries = [e for e in _audit_entries(client, admin, tenant) if e["action"] == "login_failed"]
    assert len(entries) == 1
    entry = entries[0]
    assert entry["entity_type"] == "user"
    assert entry["entity_id"] == user.id
    assert entry["actor_name"] == user.name


def test_login_failure_for_unknown_email_records_no_audit_entry(db, client):
    """An unknown email has no user row to be the audit actor, so there is
    nothing audit_service.log can record — the per-IP rate limit is the
    only control there, and the attempt must not create a row."""
    tenant = make_tenant(db)
    admin = make_user(db, tenant, UserRole.TENANT_ADMIN)

    resp = client.post(
        "/api/auth/login",
        json={"email": "nobody@example.com", "password": "whatever-password"},
        headers={"X-Tenant-Slug": tenant.slug},
    )
    assert resp.status_code == 401

    assert _audit_entries(client, admin, tenant) == []


def test_failed_login_storm_is_bounded_by_lockout(db, client):
    """A failed-login storm must not fill the audit table: the per-account
    lockout caps logged failures at 10/minute per account, and the 11th
    (locked-out, 429) attempt writes no row."""
    tenant = make_tenant(db)
    admin = make_user(db, tenant, UserRole.TENANT_ADMIN)
    make_user(db, tenant, UserRole.EMPLOYEE, email="limited@example.com")

    headers = {"X-Tenant-Slug": tenant.slug}
    codes = []
    for _ in range(11):
        resp = client.post(
            "/api/auth/login",
            json={"email": "limited@example.com", "password": "WrongPassword1!"},
            headers=headers,
        )
        codes.append(resp.status_code)

    assert codes[:10] == [401] * 10
    assert codes[10] == 429

    entries = [e for e in _audit_entries(client, admin, tenant) if e["action"] == "login_failed"]
    assert len(entries) == 10
