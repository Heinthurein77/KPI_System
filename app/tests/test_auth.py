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
    assert body["access_token"]


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
