"""HTTP-level coverage for the department endpoints in app/routers/admin.py:
GET/POST /api/admin/departments, DELETE /api/admin/departments/{id}.

Departments are Tenant-Admin-only (require_tenant_admin) -- there is no edit
endpoint, only create/list/delete.
"""

from app.models.user import UserRole
from app.tests.factories import auth_headers, make_department, make_tenant, make_template, make_user


def _tenant_admin(db, tenant):
    return make_user(db, tenant, UserRole.TENANT_ADMIN)


# ---------- Happy path CRUD ----------


def test_create_department_success(db, client):
    tenant = make_tenant(db)
    admin = _tenant_admin(db, tenant)

    resp = client.post(
        "/api/admin/departments", json={"name": "Sales"}, headers=auth_headers(admin, tenant)
    )
    assert resp.status_code == 200
    body = resp.json()
    assert body["name"] == "Sales"
    assert isinstance(body["id"], int)


def test_list_departments_includes_employee_count(db, client):
    tenant = make_tenant(db)
    admin = _tenant_admin(db, tenant)
    dept = make_department(db, tenant, name="Engineering")
    make_user(db, tenant, UserRole.EMPLOYEE, department=dept)
    make_department(db, tenant, name="Empty Dept")

    resp = client.get("/api/admin/departments", headers=auth_headers(admin, tenant))
    assert resp.status_code == 200
    by_name = {d["name"]: d for d in resp.json()}
    assert by_name["Engineering"]["employee_count"] == 1
    assert by_name["Empty Dept"]["employee_count"] == 0


def test_delete_department_success_when_empty(db, client):
    tenant = make_tenant(db)
    admin = _tenant_admin(db, tenant)
    dept = make_department(db, tenant)

    resp = client.delete(f"/api/admin/departments/{dept.id}", headers=auth_headers(admin, tenant))
    assert resp.status_code == 200
    assert resp.json() == {"status": "ok"}

    listing = client.get("/api/admin/departments", headers=auth_headers(admin, tenant))
    assert dept.id not in [d["id"] for d in listing.json()]


# ---------- Delete-with-dependents guard ----------


def test_delete_department_blocked_when_has_users(db, client):
    tenant = make_tenant(db)
    admin = _tenant_admin(db, tenant)
    dept = make_department(db, tenant)
    make_user(db, tenant, UserRole.EMPLOYEE, department=dept)

    resp = client.delete(f"/api/admin/departments/{dept.id}", headers=auth_headers(admin, tenant))
    assert resp.status_code == 400
    assert "still has users" in resp.json()["detail"]


def test_delete_department_blocked_when_has_templates(db, client):
    tenant = make_tenant(db)
    admin = _tenant_admin(db, tenant)
    dept = make_department(db, tenant)
    make_template(db, tenant, department=dept)

    resp = client.delete(f"/api/admin/departments/{dept.id}", headers=auth_headers(admin, tenant))
    assert resp.status_code == 400
    assert "still has users" in resp.json()["detail"]


def test_delete_department_succeeds_after_dependents_removed(db, client):
    tenant = make_tenant(db)
    admin = _tenant_admin(db, tenant)
    dept = make_department(db, tenant)
    other_dept = make_department(db, tenant, name="Other")
    user = make_user(db, tenant, UserRole.EMPLOYEE, department=dept)
    template = make_template(db, tenant, department=dept)

    # First attempt still blocked.
    resp = client.delete(f"/api/admin/departments/{dept.id}", headers=auth_headers(admin, tenant))
    assert resp.status_code == 400

    # Reassign the user and delete the template via the real API, then retry.
    edit_resp = client.put(
        f"/api/admin/users/{user.id}",
        json={
            "name": user.name,
            "email": user.email,
            "role": "employee",
            "department_id": other_dept.id,
        },
        headers=auth_headers(admin, tenant),
    )
    assert edit_resp.status_code == 200

    del_template_resp = client.delete(
        f"/api/admin/templates/{template.id}", headers=auth_headers(admin, tenant)
    )
    assert del_template_resp.status_code == 200

    resp = client.delete(f"/api/admin/departments/{dept.id}", headers=auth_headers(admin, tenant))
    assert resp.status_code == 200
    assert resp.json() == {"status": "ok"}


# ---------- Cross-tenant isolation ----------


def test_delete_department_cross_tenant_returns_404(db, client):
    tenant_a = make_tenant(db, name="Acme", slug="acme")
    tenant_b = make_tenant(db, name="Globex", slug="globex")
    admin_a = _tenant_admin(db, tenant_a)
    dept_b = make_department(db, tenant_b, name="Globex Dept")

    resp = client.delete(f"/api/admin/departments/{dept_b.id}", headers=auth_headers(admin_a, tenant_a))
    assert resp.status_code == 404

    # The department must still exist under tenant B.
    listing = client.get(
        "/api/admin/departments", headers=auth_headers(_tenant_admin(db, tenant_b), tenant_b)
    )
    assert dept_b.id in [d["id"] for d in listing.json()]


# ---------- Unauthenticated / wrong-role rejection ----------


def test_list_departments_requires_auth(client):
    resp = client.get("/api/admin/departments")
    assert resp.status_code == 401


def test_create_department_requires_auth(client):
    resp = client.post("/api/admin/departments", json={"name": "Sales"})
    assert resp.status_code == 401


def test_create_department_forbidden_for_employee(db, client):
    tenant = make_tenant(db)
    employee = make_user(db, tenant, UserRole.EMPLOYEE)

    resp = client.post(
        "/api/admin/departments", json={"name": "Sales"}, headers=auth_headers(employee, tenant)
    )
    assert resp.status_code == 403


def test_create_department_forbidden_for_dept_admin(db, client):
    """Departments are Tenant-Admin-only; a Dept Admin (allowed by require_dept_admin
    elsewhere) must still be rejected here since this route uses require_tenant_admin."""
    tenant = make_tenant(db)
    dept = make_department(db, tenant)
    dept_admin = make_user(db, tenant, UserRole.DEPT_ADMIN, department=dept)

    resp = client.post(
        "/api/admin/departments", json={"name": "Sales"}, headers=auth_headers(dept_admin, tenant)
    )
    assert resp.status_code == 403


def test_delete_department_forbidden_for_dept_admin(db, client):
    tenant = make_tenant(db)
    dept = make_department(db, tenant)
    dept_admin = make_user(db, tenant, UserRole.DEPT_ADMIN, department=dept)
    other_dept = make_department(db, tenant, name="Other")

    resp = client.delete(
        f"/api/admin/departments/{other_dept.id}", headers=auth_headers(dept_admin, tenant)
    )
    assert resp.status_code == 403
