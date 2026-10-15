"""Platform Super Admin login (POST /api/platform/auth/login). This endpoint
is deliberately separate from the tenant login and only ever matches rows
where tenant_id IS NULL — a normal tenant-scoped user's credentials, even if
otherwise correct, must never succeed against it."""

from app.models.user import UserRole
from app.tests.factories import DEFAULT_PASSWORD, make_tenant, make_user


def test_platform_login_success(db, client):
    admin = make_user(db, None, UserRole.SUPER_ADMIN, email="root@platform.example.com")

    resp = client.post(
        "/api/platform/auth/login",
        json={"email": admin.email, "password": DEFAULT_PASSWORD},
    )

    assert resp.status_code == 200
    body = resp.json()
    assert body["user"]["role"] == "super_admin"
    assert body["user"]["tenant_id"] is None
    assert body["access_token"]


def test_platform_login_wrong_password(db, client):
    admin = make_user(db, None, UserRole.SUPER_ADMIN, email="root2@platform.example.com")

    resp = client.post(
        "/api/platform/auth/login",
        json={"email": admin.email, "password": "wrong-password"},
    )

    assert resp.status_code == 401


def test_platform_login_rejects_tenant_scoped_user(db, client):
    """A tenant-scoped user's email/password — even though correct for the
    tenant login flow — must not authenticate through the platform endpoint,
    since it only queries User rows where tenant_id IS NULL."""
    tenant = make_tenant(db)
    tenant_user = make_user(db, tenant, UserRole.TENANT_ADMIN, email="admin@acme.example.com")

    resp = client.post(
        "/api/platform/auth/login",
        json={"email": tenant_user.email, "password": DEFAULT_PASSWORD},
    )

    assert resp.status_code == 401


def test_platform_me_returns_super_admin_without_tenant_slug(db, client):
    """Regression: platform admins were logged out on every page refresh
    because the tenant-scoped /api/auth/me always 401s for tenant-less
    accounts. /api/platform/auth/me is the exempt equivalent."""
    admin = make_user(db, None, UserRole.SUPER_ADMIN, email="root@platform.example.com")
    # TestClient persists cookies across requests like a browser.
    r = client.post("/api/platform/auth/login", json={"email": admin.email, "password": DEFAULT_PASSWORD})
    assert r.status_code == 200
    # No X-Tenant-Slug header: exactly what a platform session sends.
    r = client.get("/api/platform/auth/me")
    assert r.status_code == 200
    assert r.json()["role"] == "super_admin"
    assert r.json()["tenant_id"] is None


def test_platform_me_rejects_tenant_user(db, client):
    tenant = make_tenant(db)
    user = make_user(db, tenant, UserRole.TENANT_ADMIN, name="Boss")
    r = client.post(
        "/api/auth/login",
        json={"email": user.email, "password": DEFAULT_PASSWORD},
        headers={"X-Tenant-Slug": tenant.slug},
    )
    assert r.status_code == 200
    r = client.get("/api/platform/auth/me", headers={"X-Tenant-Slug": tenant.slug})
    assert r.status_code in (401, 403)


def test_platform_me_unauthenticated_401(db, client):
    assert client.get("/api/platform/auth/me").status_code == 401


def test_platform_change_password_ok(db, client):
    admin = make_user(db, None, UserRole.SUPER_ADMIN, email="root-pw@platform.example.com")
    r = client.post("/api/platform/auth/login", json={"email": admin.email, "password": DEFAULT_PASSWORD})
    assert r.status_code == 200
    r = client.post(
        "/api/platform/auth/change-password",
        json={"current_password": DEFAULT_PASSWORD, "new_password": "BrandNewPass123"},
    )
    assert r.status_code == 204
    # Old password no longer works; token version was bumped.
    r = client.post("/api/platform/auth/login", json={"email": admin.email, "password": DEFAULT_PASSWORD})
    assert r.status_code == 401
    r = client.post("/api/platform/auth/login", json={"email": admin.email, "password": "BrandNewPass123"})
    assert r.status_code == 200


def test_platform_change_password_wrong_current(db, client):
    admin = make_user(db, None, UserRole.SUPER_ADMIN, email="root-pw2@platform.example.com")
    client.post("/api/platform/auth/login", json={"email": admin.email, "password": DEFAULT_PASSWORD})
    r = client.post(
        "/api/platform/auth/change-password",
        json={"current_password": "wrong", "new_password": "BrandNewPass123"},
    )
    assert r.status_code == 400


def test_platform_change_password_rejects_tenant_user(db, client):
    tenant = make_tenant(db)
    user = make_user(db, tenant, UserRole.TENANT_ADMIN, name="Boss")
    client.post(
        "/api/auth/login",
        json={"email": user.email, "password": DEFAULT_PASSWORD},
        headers={"X-Tenant-Slug": tenant.slug},
    )
    r = client.post(
        "/api/platform/auth/change-password",
        json={"current_password": DEFAULT_PASSWORD, "new_password": "BrandNewPass123"},
        headers={"X-Tenant-Slug": tenant.slug},
    )
    assert r.status_code in (401, 403)
