"""HTTP-level coverage for the user endpoints in app/routers/admin.py:
GET /api/admin/users, GET/PUT/DELETE /api/admin/users/{id},
POST /api/admin/users, POST /api/admin/users/{id}/toggle-active.
"""

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
