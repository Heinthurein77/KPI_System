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
