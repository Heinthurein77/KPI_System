"""First-login forced password change (users.must_change_password).

Admin-created, admin-reset and bulk-imported users must change their
password on first login; /api/auth/change-password clears the flag.
"""

from app.core.rate_limit import _client_ip_key
from app.models.user import UserRole
from app.services import bulk_import_service
from app.tests.factories import (
    DEFAULT_PASSWORD,
    auth_headers,
    make_department,
    make_tenant,
    make_user,
)


def _tenant_admin(db):
    tenant = make_tenant(db)
    dept = make_department(db, tenant)
    admin = make_user(db, tenant, UserRole.TENANT_ADMIN, name="Tenant Admin", department=dept)
    return tenant, dept, admin, auth_headers(admin, tenant)


def test_create_user_sets_must_change_password(client, db):
    _, dept, _, headers = _tenant_admin(db)
    resp = client.post(
        "/api/admin/users",
        json={
            "name": "New Hire",
            "email": "newhire@example.com",
            "password": "InitialPass1!",
            "role": "employee",
            "department_id": dept.id,
        },
        headers=headers,
    )
    assert resp.status_code == 200
    assert resp.json()["must_change_password"] is True


def test_reset_password_sets_must_change_password(client, db):
    tenant, dept, _, headers = _tenant_admin(db)
    user = make_user(db, tenant, UserRole.EMPLOYEE, name="Reset Me", department=dept)
    resp = client.post(
        f"/api/admin/users/{user.id}/reset-password",
        json={"password": "ResetPass1!"},
        headers=headers,
    )
    assert resp.status_code == 200
    db.refresh(user)
    assert user.must_change_password is True


def test_change_password_clears_flag(client, db):
    tenant = make_tenant(db)
    user = make_user(db, tenant, UserRole.EMPLOYEE)
    user.must_change_password = True
    db.commit()

    resp = client.post(
        "/api/auth/change-password",
        json={"current_password": DEFAULT_PASSWORD, "new_password": "BrandNewPass1!"},
        headers=auth_headers(user, tenant),
    )
    assert resp.status_code == 204
    db.refresh(user)
    assert user.must_change_password is False
    # The token_version bump kills the session — the dead cookie is cleared so
    # the SPA doesn't keep sending it; the user logs in again.
    assert "access_token=" in resp.headers.get("set-cookie", "")
    assert "Max-Age=0" in resp.headers["set-cookie"]


def test_login_response_includes_must_change_password(client, db):
    tenant = make_tenant(db)
    user = make_user(db, tenant, UserRole.EMPLOYEE)
    user.must_change_password = True
    db.commit()

    resp = client.post(
        "/api/auth/login",
        json={"email": user.email, "password": DEFAULT_PASSWORD},
        headers={"X-Tenant-Slug": tenant.slug},
    )
    assert resp.status_code == 200
    assert resp.json()["user"]["must_change_password"] is True


def test_bulk_import_sets_must_change_password(db):
    tenant = make_tenant(db)
    dept = make_department(db, tenant)
    admin = make_user(db, tenant, UserRole.DEPT_ADMIN, name="Dept Boss", department=dept)

    from io import BytesIO

    from openpyxl import Workbook

    wb = Workbook()
    ws = wb.active
    ws.append(["name", "email", "position", "metric", "target", "weight"])
    ws.append(["Aung Aung", "aung@example.com", "Sales Executive", "Sales", 100, 100])
    buf = BytesIO()
    wb.save(buf)
    parsed = bulk_import_service.parse_import_file(buf.getvalue(), db, tenant.id)
    assert parsed.ok
    bulk_import_service.execute_import(db, admin, parsed)

    from sqlalchemy import select

    from app.models.user import User

    imported = db.scalar(select(User).where(User.email == "aung@example.com"))
    assert imported is not None
    assert imported.must_change_password is True


class _FakeRequest:
    def __init__(self, headers=None, remote="10.0.0.5"):
        self.headers = headers or {}

        class _Client:
            host = remote

        self.client = _Client()


def test_rate_limit_key_uses_x_forwarded_for():
    req = _FakeRequest(headers={"x-forwarded-for": "203.0.113.7, 10.0.0.1"})
    assert _client_ip_key(req) == "203.0.113.7"


def test_rate_limit_key_falls_back_to_remote_addr():
    req = _FakeRequest(remote="10.0.0.9")
    assert _client_ip_key(req) == "10.0.0.9"


def test_platform_tenant_creation_sets_must_change_password(client, db):
    from app.models.user import User
    from sqlalchemy import select

    root = make_user(db, None, UserRole.SUPER_ADMIN, email="root@platform.example.com")
    resp = client.post(
        "/api/platform/tenants",
        json={
            "name": "Acme Corp",
            "slug": "acme",
            "admin_name": "Acme Admin",
            "admin_email": "admin@acme.example.com",
            "admin_password": "AcmePass1!",
        },
        headers=auth_headers(root),
    )
    assert resp.status_code == 200, resp.text
    tenant_admin = db.scalar(select(User).where(User.email == "admin@acme.example.com"))
    assert tenant_admin is not None
    assert tenant_admin.must_change_password is True
