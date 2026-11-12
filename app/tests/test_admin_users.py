"""HTTP-level coverage for the user endpoints in app/routers/admin.py:
GET /api/admin/users, GET/PUT/DELETE /api/admin/users/{id},
POST /api/admin/users, POST /api/admin/users/{id}/toggle-active.
"""

from contextlib import contextmanager

from app.models.user import UserRole
from app.tests.factories import auth_headers, make_department, make_tenant, make_user
def _user_payload(**overrides):
    payload = {
        "name": "New Hire",
        "email": "new.hire@example.com",
        "password": "Password123!",
        "role": "employee",
        "department_id": None,
    }
    payload.update(overrides)
    return payload


# ---------- Happy path CRUD ----------


def test_create_user_success_as_tenant_admin(db, client):
    tenant = make_tenant(db)
    admin = make_user(db, tenant, UserRole.TENANT_ADMIN)
    dept = make_department(db, tenant)

    resp = client.post(
        "/api/admin/users",
        json=_user_payload(email="alice@example.com", department_id=dept.id),
        headers=auth_headers(admin, tenant),
    )
    assert resp.status_code == 200
    body = resp.json()
    assert body["email"] == "alice@example.com"
    assert body["role"] == "employee"
    assert body["department_id"] == dept.id

def test_list_users_as_tenant_admin_sees_everyone(db, client):
    tenant = make_tenant(db)
    admin = make_user(db, tenant, UserRole.TENANT_ADMIN)
    dept = make_department(db, tenant)
    make_user(db, tenant, UserRole.EMPLOYEE, department=dept, email="a@example.com")

    resp = client.get("/api/admin/users", headers=auth_headers(admin, tenant))
    assert resp.status_code == 200
    body = resp.json()
    emails = {u["email"] for u in body["users"]}
    assert "a@example.com" in emails
    assert admin.email in emails
    assert set(body["roles"]) == {"tenant_admin", "dept_admin", "employee"}


def test_get_user_success(db, client):
    tenant = make_tenant(db)
    admin = make_user(db, tenant, UserRole.TENANT_ADMIN)
    target = make_user(db, tenant, UserRole.EMPLOYEE)

    resp = client.get(f"/api/admin/users/{target.id}", headers=auth_headers(admin, tenant))
    assert resp.status_code == 200
    assert resp.json()["id"] == target.id


def test_edit_user_success(db, client):
    tenant = make_tenant(db)
    admin = make_user(db, tenant, UserRole.TENANT_ADMIN)
    dept = make_department(db, tenant)
    target = make_user(db, tenant, UserRole.EMPLOYEE, email="old@example.com")

    resp = client.put(
        f"/api/admin/users/{target.id}",
        json={
            "name": "Renamed",
            "email": "renamed@example.com",
            "role": "dept_admin",
            "department_id": dept.id,
        },
        headers=auth_headers(admin, tenant),
    )
    assert resp.status_code == 200
    body = resp.json()
    assert body["name"] == "Renamed"
    assert body["email"] == "renamed@example.com"
    assert body["role"] == "dept_admin"
    assert body["department_id"] == dept.id


def test_toggle_user_active_success_as_tenant_admin(db, client):
    tenant = make_tenant(db)
    admin = make_user(db, tenant, UserRole.TENANT_ADMIN)
    target = make_user(db, tenant, UserRole.EMPLOYEE, is_active=True)

    resp = client.post(
        f"/api/admin/users/{target.id}/toggle-active", headers=auth_headers(admin, tenant)
    )
    assert resp.status_code == 200
    assert resp.json()["is_active"] is False

    resp2 = client.post(
        f"/api/admin/users/{target.id}/toggle-active", headers=auth_headers(admin, tenant)
    )
    assert resp2.status_code == 200
    assert resp2.json()["is_active"] is True


def test_toggle_user_active_self_is_blocked(db, client):
    tenant = make_tenant(db)
    admin = make_user(db, tenant, UserRole.TENANT_ADMIN, is_active=True)

    resp = client.post(
        f"/api/admin/users/{admin.id}/toggle-active", headers=auth_headers(admin, tenant)
    )
    assert resp.status_code == 400
    assert "own account" in resp.json()["detail"]

    db.refresh(admin)
    assert admin.is_active is True


def test_reset_password_success_as_tenant_admin(db, client):
    from app.core.security import verify_password

    tenant = make_tenant(db)
    admin = make_user(db, tenant, UserRole.TENANT_ADMIN)
    target = make_user(db, tenant, UserRole.EMPLOYEE)
    old_hash = target.password_hash

    resp = client.post(
        f"/api/admin/users/{target.id}/reset-password",
        json={"password": "NewPassword123"},
        headers=auth_headers(admin, tenant),
    )
    assert resp.status_code == 200
    assert resp.json()["id"] == target.id

    db.refresh(target)
    assert target.password_hash != old_hash
    assert verify_password("NewPassword123", target.password_hash)


def test_reset_password_rejects_short_password(db, client):
    tenant = make_tenant(db)
    admin = make_user(db, tenant, UserRole.TENANT_ADMIN)
    target = make_user(db, tenant, UserRole.EMPLOYEE)

    resp = client.post(
        f"/api/admin/users/{target.id}/reset-password",
        json={"password": "short"},
        headers=auth_headers(admin, tenant),
    )
    assert resp.status_code == 422


def test_reset_password_dept_admin_own_department_employee(db, client):
    from app.core.security import verify_password

    tenant = make_tenant(db)
    dept = make_department(db, tenant)
    dept_admin = make_user(db, tenant, UserRole.DEPT_ADMIN, department=dept)
    target = make_user(db, tenant, UserRole.EMPLOYEE, department=dept)
    old_hash = target.password_hash

    resp = client.post(
        f"/api/admin/users/{target.id}/reset-password",
        json={"password": "NewPassword123"},
        headers=auth_headers(dept_admin, tenant),
    )
    assert resp.status_code == 200

    db.refresh(target)
    assert target.password_hash != old_hash
    assert verify_password("NewPassword123", target.password_hash)
    assert target.must_change_password is True


def test_reset_password_dept_admin_other_department_forbidden(db, client):
    tenant = make_tenant(db)
    dept = make_department(db, tenant)
    other_dept = make_department(db, tenant, name="Other")
    dept_admin = make_user(db, tenant, UserRole.DEPT_ADMIN, department=dept)
    target = make_user(db, tenant, UserRole.EMPLOYEE, department=other_dept)

    resp = client.post(
        f"/api/admin/users/{target.id}/reset-password",
        json={"password": "NewPassword123"},
        headers=auth_headers(dept_admin, tenant),
    )
    assert resp.status_code == 403


def test_reset_password_dept_admin_cannot_reset_dept_admin(db, client):
    tenant = make_tenant(db)
    dept = make_department(db, tenant)
    dept_admin = make_user(db, tenant, UserRole.DEPT_ADMIN, department=dept)
    other_da = make_user(db, tenant, UserRole.DEPT_ADMIN, name="Other DA", department=dept)

    resp = client.post(
        f"/api/admin/users/{other_da.id}/reset-password",
        json={"password": "NewPassword123"},
        headers=auth_headers(dept_admin, tenant),
    )
    assert resp.status_code == 403


def test_reset_password_tenant_admin_can_reset_dept_admin(db, client):
    from app.core.security import verify_password

    tenant = make_tenant(db)
    dept = make_department(db, tenant)
    admin = make_user(db, tenant, UserRole.TENANT_ADMIN)
    dept_admin = make_user(db, tenant, UserRole.DEPT_ADMIN, department=dept)

    resp = client.post(
        f"/api/admin/users/{dept_admin.id}/reset-password",
        json={"password": "NewPassword123"},
        headers=auth_headers(admin, tenant),
    )
    assert resp.status_code == 200

    db.refresh(dept_admin)
    assert verify_password("NewPassword123", dept_admin.password_hash)


def test_delete_user_success(db, client):
    tenant = make_tenant(db)
    admin = make_user(db, tenant, UserRole.TENANT_ADMIN)
    target = make_user(db, tenant, UserRole.EMPLOYEE)

    resp = client.delete(f"/api/admin/users/{target.id}", headers=auth_headers(admin, tenant))
    assert resp.status_code == 200
    assert resp.json() == {"status": "ok"}

    get_resp = client.get(f"/api/admin/users/{target.id}", headers=auth_headers(admin, tenant))
    assert get_resp.status_code == 404


def test_delete_user_cannot_delete_self(db, client):
    tenant = make_tenant(db)
    admin = make_user(db, tenant, UserRole.TENANT_ADMIN)

    resp = client.delete(f"/api/admin/users/{admin.id}", headers=auth_headers(admin, tenant))
    assert resp.status_code == 400
    assert "own account" in resp.json()["detail"]


def test_edit_user_cannot_demote_self(db, client):
    tenant = make_tenant(db)
    admin = make_user(db, tenant, UserRole.TENANT_ADMIN)

    resp = client.put(
        f"/api/admin/users/{admin.id}",
        json={"name": admin.name, "email": admin.email, "role": "employee", "department_id": None},
        headers=auth_headers(admin, tenant),
    )
    assert resp.status_code == 400
    assert "demote your own account" in resp.json()["detail"]


# ---------- Role assignment guard (no minting SUPER_ADMIN via tenant admin routes) ----------


def test_create_user_cannot_assign_super_admin_role(db, client):
    tenant = make_tenant(db)
    admin = make_user(db, tenant, UserRole.TENANT_ADMIN)

    resp = client.post(
        "/api/admin/users",
        json=_user_payload(email="wannabe@example.com", role="super_admin"),
        headers=auth_headers(admin, tenant),
    )
    assert resp.status_code == 400
    assert "Super Admin" in resp.json()["detail"]


def test_edit_user_cannot_assign_super_admin_role(db, client):
    tenant = make_tenant(db)
    admin = make_user(db, tenant, UserRole.TENANT_ADMIN)
    target = make_user(db, tenant, UserRole.EMPLOYEE)

    resp = client.put(
        f"/api/admin/users/{target.id}",
        json={"name": target.name, "email": target.email, "role": "super_admin", "department_id": None},
        headers=auth_headers(admin, tenant),
    )
    assert resp.status_code == 400
    assert "Super Admin" in resp.json()["detail"]


def test_create_user_dept_admin_cannot_create_non_employee_role(db, client):
    tenant = make_tenant(db)
    dept = make_department(db, tenant)
    dept_admin = make_user(db, tenant, UserRole.DEPT_ADMIN, department=dept)

    resp = client.post(
        "/api/admin/users",
        json=_user_payload(email="promoted@example.com", role="dept_admin"),
        headers=auth_headers(dept_admin, tenant),
    )
    assert resp.status_code == 403


def test_create_user_dept_admin_forced_into_own_department(db, client):
    """Dept Admin creating an Employee always lands in their own department,
    regardless of any department_id they pass in the payload."""
    tenant = make_tenant(db)
    dept = make_department(db, tenant, name="Own Dept")
    other_dept = make_department(db, tenant, name="Other Dept")
    dept_admin = make_user(db, tenant, UserRole.DEPT_ADMIN, department=dept)

    resp = client.post(
        "/api/admin/users",
        json=_user_payload(email="hire@example.com", department_id=other_dept.id),
        headers=auth_headers(dept_admin, tenant),
    )
    assert resp.status_code == 200
    assert resp.json()["department_id"] == dept.id


# ---------- Dept Admin department-scoping guard ----------


def test_toggle_user_active_dept_admin_own_department_succeeds(db, client):
    tenant = make_tenant(db)
    dept = make_department(db, tenant)
    dept_admin = make_user(db, tenant, UserRole.DEPT_ADMIN, department=dept)
    target = make_user(db, tenant, UserRole.EMPLOYEE, department=dept)

    resp = client.post(
        f"/api/admin/users/{target.id}/toggle-active", headers=auth_headers(dept_admin, tenant)
    )
    assert resp.status_code == 200


def test_toggle_user_active_dept_admin_other_department_forbidden(db, client):
    tenant = make_tenant(db)
    dept = make_department(db, tenant, name="Mine")
    other_dept = make_department(db, tenant, name="Theirs")
    dept_admin = make_user(db, tenant, UserRole.DEPT_ADMIN, department=dept)
    target = make_user(db, tenant, UserRole.EMPLOYEE, department=other_dept)

    resp = client.post(
        f"/api/admin/users/{target.id}/toggle-active", headers=auth_headers(dept_admin, tenant)
    )
    assert resp.status_code == 403
    assert "outside your department" in resp.json()["detail"]


def test_list_users_dept_admin_scoped_to_own_department(db, client):
    tenant = make_tenant(db)
    dept = make_department(db, tenant, name="Mine")
    other_dept = make_department(db, tenant, name="Theirs")
    dept_admin = make_user(db, tenant, UserRole.DEPT_ADMIN, department=dept)
    own_employee = make_user(db, tenant, UserRole.EMPLOYEE, department=dept, email="own@example.com")
    other_employee = make_user(
        db, tenant, UserRole.EMPLOYEE, department=other_dept, email="other@example.com"
    )

    resp = client.get("/api/admin/users", headers=auth_headers(dept_admin, tenant))
    assert resp.status_code == 200
    body = resp.json()
    emails = {u["email"] for u in body["users"]}
    assert own_employee.email in emails
    assert other_employee.email not in emails
    assert body["roles"] == ["employee"]


# ---------- Cross-tenant isolation ----------


def test_get_user_cross_tenant_returns_404(db, client):
    tenant_a = make_tenant(db, name="Acme", slug="acme")
    tenant_b = make_tenant(db, name="Globex", slug="globex")
    admin_a = make_user(db, tenant_a, UserRole.TENANT_ADMIN)
    user_b = make_user(db, tenant_b, UserRole.EMPLOYEE)

    resp = client.get(f"/api/admin/users/{user_b.id}", headers=auth_headers(admin_a, tenant_a))
    assert resp.status_code == 404


def test_edit_user_cross_tenant_returns_404(db, client):
    tenant_a = make_tenant(db, name="Acme", slug="acme")
    tenant_b = make_tenant(db, name="Globex", slug="globex")
    admin_a = make_user(db, tenant_a, UserRole.TENANT_ADMIN)
    user_b = make_user(db, tenant_b, UserRole.EMPLOYEE)

    resp = client.put(
        f"/api/admin/users/{user_b.id}",
        json={"name": "x", "email": "x@example.com", "role": "employee", "department_id": None},
        headers=auth_headers(admin_a, tenant_a),
    )
    assert resp.status_code == 404


def test_delete_user_cross_tenant_returns_404(db, client):
    tenant_a = make_tenant(db, name="Acme", slug="acme")
    tenant_b = make_tenant(db, name="Globex", slug="globex")
    admin_a = make_user(db, tenant_a, UserRole.TENANT_ADMIN)
    user_b = make_user(db, tenant_b, UserRole.EMPLOYEE)

    resp = client.delete(f"/api/admin/users/{user_b.id}", headers=auth_headers(admin_a, tenant_a))
    assert resp.status_code == 404


def test_toggle_user_active_cross_tenant_returns_404(db, client):
    tenant_a = make_tenant(db, name="Acme", slug="acme")
    tenant_b = make_tenant(db, name="Globex", slug="globex")
    admin_a = make_user(db, tenant_a, UserRole.TENANT_ADMIN)
    user_b = make_user(db, tenant_b, UserRole.EMPLOYEE)

    resp = client.post(
        f"/api/admin/users/{user_b.id}/toggle-active", headers=auth_headers(admin_a, tenant_a)
    )
    assert resp.status_code == 404


def test_create_user_cross_tenant_department_id_returns_404(db, client):
    tenant_a = make_tenant(db, name="Acme", slug="acme")
    tenant_b = make_tenant(db, name="Globex", slug="globex")
    admin_a = make_user(db, tenant_a, UserRole.TENANT_ADMIN)
    dept_b = make_department(db, tenant_b)

    resp = client.post(
        "/api/admin/users",
        json=_user_payload(email="cross@example.com", department_id=dept_b.id),
        headers=auth_headers(admin_a, tenant_a),
    )
    assert resp.status_code == 404


def test_edit_user_cross_tenant_department_id_returns_404(db, client):
    tenant_a = make_tenant(db, name="Acme", slug="acme")
    tenant_b = make_tenant(db, name="Globex", slug="globex")
    admin_a = make_user(db, tenant_a, UserRole.TENANT_ADMIN)
    target = make_user(db, tenant_a, UserRole.EMPLOYEE)
    dept_b = make_department(db, tenant_b)

    resp = client.put(
        f"/api/admin/users/{target.id}",
        json={
            "name": target.name,
            "email": target.email,
            "role": "employee",
            "department_id": dept_b.id,
        },
        headers=auth_headers(admin_a, tenant_a),
    )
    assert resp.status_code == 404


# ---------- Unauthenticated / wrong-role rejection ----------


def test_list_users_requires_auth(client):
    resp = client.get("/api/admin/users")
    assert resp.status_code == 401


def test_get_user_requires_auth(db, client):
    tenant = make_tenant(db)
    target = make_user(db, tenant, UserRole.EMPLOYEE)
    resp = client.get(f"/api/admin/users/{target.id}")
    assert resp.status_code == 401


def test_create_user_forbidden_for_employee(db, client):
    tenant = make_tenant(db)
    employee = make_user(db, tenant, UserRole.EMPLOYEE)

    resp = client.post(
        "/api/admin/users", json=_user_payload(), headers=auth_headers(employee, tenant)
    )
    assert resp.status_code == 403


def test_delete_user_forbidden_for_employee(db, client):
    tenant = make_tenant(db)
    employee = make_user(db, tenant, UserRole.EMPLOYEE)
    other = make_user(db, tenant, UserRole.EMPLOYEE, email="other@example.com")

    resp = client.delete(f"/api/admin/users/{other.id}", headers=auth_headers(employee, tenant))
    assert resp.status_code == 403


def test_delete_user_forbidden_for_dept_admin(db, client):
    """Delete is Tenant-Admin-only; a Dept Admin (who is allowed elsewhere via
    require_dept_admin) must still be rejected here."""
    tenant = make_tenant(db)
    dept = make_department(db, tenant)
    dept_admin = make_user(db, tenant, UserRole.DEPT_ADMIN, department=dept)
    target = make_user(db, tenant, UserRole.EMPLOYEE, department=dept)

    resp = client.delete(f"/api/admin/users/{target.id}", headers=auth_headers(dept_admin, tenant))
    assert resp.status_code == 403


def test_create_user_duplicate_email_returns_400(db, client):
    tenant = make_tenant(db)
    admin = make_user(db, tenant, UserRole.TENANT_ADMIN)
    make_user(db, tenant, UserRole.EMPLOYEE, email="dup@example.com")

    resp = client.post(
        "/api/admin/users",
        json={
            "name": "Duplicate",
            "email": "dup@example.com",
            "password": "Password123!",
            "role": "employee",
            "department_id": None,
        },
        headers=auth_headers(admin, tenant),
    )
    assert resp.status_code == 400
    assert "already exists" in resp.json()["detail"]


def test_create_user_duplicate_email_case_insensitive(db, client):
    tenant = make_tenant(db)
    admin = make_user(db, tenant, UserRole.TENANT_ADMIN)
    make_user(db, tenant, UserRole.EMPLOYEE, email="dup@example.com")

    resp = client.post(
        "/api/admin/users",
        json={
            "name": "Duplicate",
            "email": "DUP@EXAMPLE.COM",
            "password": "Password123!",
            "role": "employee",
            "department_id": None,
        },
        headers=auth_headers(admin, tenant),
    )
    assert resp.status_code == 400


def test_create_dept_admin_without_department_returns_400(db, client):
    tenant = make_tenant(db)
    admin = make_user(db, tenant, UserRole.TENANT_ADMIN)

    resp = client.post(
        "/api/admin/users",
        json={
            "name": "No Dept Admin",
            "email": "nodept@example.com",
            "password": "Password123!",
            "role": "dept_admin",
            "department_id": None,
        },
        headers=auth_headers(admin, tenant),
    )
    assert resp.status_code == 400
    assert "department" in resp.json()["detail"].lower()


def test_create_dept_admin_with_department_succeeds(db, client):
    tenant = make_tenant(db)
    admin = make_user(db, tenant, UserRole.TENANT_ADMIN)
    dept = make_department(db, tenant)

    resp = client.post(
        "/api/admin/users",
        json={
            "name": "Dept Admin",
            "email": "deptadmin@example.com",
            "password": "Password123!",
            "role": "dept_admin",
            "department_id": dept.id,
        },
        headers=auth_headers(admin, tenant),
    )
    assert resp.status_code == 200
    assert resp.json()["department_id"] == dept.id


@contextmanager
def _smtp_on():
    """Make the real ``settings.smtp_configured`` property True by
    patching the three plain fields it reads (it is a @property, so
    patch.object on the property itself fails)."""
    from unittest.mock import patch

    from app.core.config import settings

    with patch.object(settings, "SMTP_HOST", "smtp.example.com"), \
         patch.object(settings, "SMTP_USERNAME", "sender@example.com"), \
         patch.object(settings, "SMTP_PASSWORD", "app-password"):
        yield


def test_create_user_sends_welcome_email(db, client):
    from unittest.mock import patch

    from app.core.config import settings

    tenant = make_tenant(db)
    admin = make_user(db, tenant, UserRole.TENANT_ADMIN)
    dept = make_department(db, tenant)

    # The welcome email goes out in a background thread (see the timing
    # test below); the create still queues exactly one send.
    with _smtp_on(), patch("app.routers.admin.email_service.send_welcome_email",
               return_value=None) as mock_welcome:
        resp = client.post(
            "/api/admin/users",
            json=_user_payload(email="welcome@example.com", department_id=dept.id, password="InitPass123!"),
            headers=auth_headers(admin, tenant),
        )
    assert resp.status_code == 200
    # The welcome email carries the admin-chosen password and the login
    # link (user-requested); the API response itself never does.
    mock_welcome.assert_called_once_with(
        "welcome@example.com", "New Hire", "InitPass123!", org_name="Acme"
    )
    assert "InitPass123!" not in resp.text


def _slow_fake_smtp(sent, delay_s=3.0, fail=False):
    """Fake smtplib.SMTP: sleeps like a slow mail server, then records
    the message (or raises, when ``fail``)."""
    import time

    class FakeSMTP:
        def __init__(self, *args, **kwargs):
            if fail:
                raise OSError("network down")

        def starttls(self):
            pass

        def login(self, *args):
            pass

        def send_message(self, msg):
            time.sleep(delay_s)
            sent.append(msg)

        def quit(self):
            pass

    return FakeSMTP


def _configured_email_settings():
    from unittest.mock import MagicMock

    s = MagicMock()
    s.smtp_configured = True
    s.SMTP_HOST = "smtp.example.com"
    s.SMTP_PORT = 587
    s.SMTP_USERNAME = "sender@example.com"
    s.SMTP_PASSWORD = "app-password"
    s.SMTP_USE_TLS = True
    s.smtp_from = "sender@example.com"
    s.APP_BASE_URL = "https://kpi.example.com"
    return s


def test_create_user_returns_before_slow_welcome_email(db, client):
    """Regression: the create-user response waited on the SMTP
    round-trip — a 3s mail server made the API take ~3.7s. The
    welcome email now goes out in a background thread: the response
    returns promptly and the email still arrives."""
    import time
    from unittest.mock import patch

    from app.core.config import settings
    from app.services import email_service

    tenant = make_tenant(db)
    admin = make_user(db, tenant, UserRole.TENANT_ADMIN)
    dept = make_department(db, tenant)
    sent = []

    with _smtp_on(), \
         patch.object(email_service, "settings", _configured_email_settings()), \
         patch("smtplib.SMTP", _slow_fake_smtp(sent, delay_s=3.0)):
        started = time.monotonic()
        resp = client.post(
            "/api/admin/users",
            json=_user_payload(email="slow@example.com", department_id=dept.id),
            headers=auth_headers(admin, tenant),
        )
        elapsed = time.monotonic() - started
        # Poll INSIDE the patch context: the background thread must
        # still see the fake SMTP when it sends (the patch is gone
        # once this block exits).
        deadline = time.monotonic() + 8
        while not sent and time.monotonic() < deadline:
            time.sleep(0.1)
    assert resp.status_code == 200
    assert elapsed < 1.5, f"create-user took {elapsed:.2f}s with a 3s mail server"
    # The email still goes out — just after the response.
    assert len(sent) == 1
    assert sent[0]["To"] == "slow@example.com"


def test_create_user_succeeds_when_mail_server_down(db, client):
    """A dead mail server must never fail (or delay) a user creation:
    the user is created, the response is 200, and the failure is only
    logged by the background sender."""
    import time
    from unittest.mock import patch

    from app.core.config import settings
    from app.models.user import User
    from app.services import email_service

    tenant = make_tenant(db)
    admin = make_user(db, tenant, UserRole.TENANT_ADMIN)
    dept = make_department(db, tenant)

    with _smtp_on(), \
         patch.object(email_service, "settings", _configured_email_settings()), \
         patch("smtplib.SMTP", _slow_fake_smtp([], delay_s=0, fail=True)):
        started = time.monotonic()
        resp = client.post(
            "/api/admin/users",
            json=_user_payload(email="down@example.com", department_id=dept.id),
            headers=auth_headers(admin, tenant),
        )
        elapsed = time.monotonic() - started
        # Give the background thread a moment to hit the failure path
        # while the fake SMTP is still patched in (it must not raise
        # out of the request).
        time.sleep(0.5)
    assert resp.status_code == 200
    assert elapsed < 1.5, f"create-user took {elapsed:.2f}s with the mail server down"
    assert db.query(User).filter_by(email="down@example.com").one() is not None
