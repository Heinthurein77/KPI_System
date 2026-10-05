"""HTTP-level coverage for app/routers/admin.py's per-employee KPI views --
GET /api/admin/users/{id}/kpi-trend and GET /api/admin/users/{id}/submissions --
plus a cross-cutting sweep of unauthenticated/wrong-role rejection across the
admin router's endpoints (departments, users, templates).

These two GET routes share the exact same tenant-then-department scoping
shape as the rest of admin.py: get_tenant_scoped_or_404 first (404, not 403,
on a cross-tenant id), then a Dept-Admin-only department/role check (403).
"""

from app.models.user import UserRole
from app.tests.factories import (
    auth_headers,
    make_department,
    make_submission,
    make_template,
    make_tenant,
    make_user,
)


# ---------- get_user_kpi_trend ----------


def test_kpi_trend_tenant_admin_success(db, client):
    tenant = make_tenant(db)
    admin = make_user(db, tenant, UserRole.TENANT_ADMIN)
    dept = make_department(db, tenant)
    employee = make_user(db, tenant, UserRole.EMPLOYEE, department=dept)
    template = make_template(db, tenant, department=dept)
    make_submission(db, tenant, employee, template, final_score=90.0)

    resp = client.get(f"/api/admin/users/{employee.id}/kpi-trend", headers=auth_headers(admin, tenant))
    assert resp.status_code == 200
    body = resp.json()
    assert body["employee"]["id"] == employee.id
    assert len(body["points"]) == 12


def test_kpi_trend_dept_admin_own_department_success(db, client):
    tenant = make_tenant(db)
    dept = make_department(db, tenant)
    dept_admin = make_user(db, tenant, UserRole.DEPT_ADMIN, department=dept)
    employee = make_user(db, tenant, UserRole.EMPLOYEE, department=dept)

    resp = client.get(
        f"/api/admin/users/{employee.id}/kpi-trend", headers=auth_headers(dept_admin, tenant)
    )
    assert resp.status_code == 200


def test_kpi_trend_dept_admin_other_department_forbidden(db, client):
    tenant = make_tenant(db)
    dept = make_department(db, tenant, name="Mine")
    other_dept = make_department(db, tenant, name="Theirs")
    dept_admin = make_user(db, tenant, UserRole.DEPT_ADMIN, department=dept)
    other_employee = make_user(db, tenant, UserRole.EMPLOYEE, department=other_dept)

    resp = client.get(
        f"/api/admin/users/{other_employee.id}/kpi-trend", headers=auth_headers(dept_admin, tenant)
    )
    assert resp.status_code == 403
    assert "outside your department" in resp.json()["detail"]


def test_kpi_trend_cross_tenant_returns_404(db, client):
    tenant_a = make_tenant(db, name="Acme", slug="acme")
    tenant_b = make_tenant(db, name="Globex", slug="globex")
    admin_a = make_user(db, tenant_a, UserRole.TENANT_ADMIN)
    employee_b = make_user(db, tenant_b, UserRole.EMPLOYEE)

    resp = client.get(
        f"/api/admin/users/{employee_b.id}/kpi-trend", headers=auth_headers(admin_a, tenant_a)
    )
    assert resp.status_code == 404


# ---------- get_user_submissions_for_period ----------


def test_user_submissions_tenant_admin_success(db, client):
    tenant = make_tenant(db)
    admin = make_user(db, tenant, UserRole.TENANT_ADMIN)
    dept = make_department(db, tenant)
    employee = make_user(db, tenant, UserRole.EMPLOYEE, department=dept)
    template = make_template(db, tenant, department=dept)
    make_submission(db, tenant, employee, template, year=2026, period="January")

    resp = client.get(
        f"/api/admin/users/{employee.id}/submissions",
        params={"year": 2026, "period": "January"},
        headers=auth_headers(admin, tenant),
    )
    assert resp.status_code == 200
    body = resp.json()
    assert body["employee"]["id"] == employee.id
    assert len(body["submissions"]) == 1


def test_user_submissions_dept_admin_own_department_success(db, client):
    tenant = make_tenant(db)
    dept = make_department(db, tenant)
    dept_admin = make_user(db, tenant, UserRole.DEPT_ADMIN, department=dept)
    employee = make_user(db, tenant, UserRole.EMPLOYEE, department=dept)

    resp = client.get(
        f"/api/admin/users/{employee.id}/submissions",
        params={"year": 2026, "period": "January"},
        headers=auth_headers(dept_admin, tenant),
    )
    assert resp.status_code == 200


def test_user_submissions_dept_admin_other_department_forbidden(db, client):
    tenant = make_tenant(db)
    dept = make_department(db, tenant, name="Mine")
    other_dept = make_department(db, tenant, name="Theirs")
    dept_admin = make_user(db, tenant, UserRole.DEPT_ADMIN, department=dept)
    other_employee = make_user(db, tenant, UserRole.EMPLOYEE, department=other_dept)

    resp = client.get(
        f"/api/admin/users/{other_employee.id}/submissions",
        params={"year": 2026, "period": "January"},
        headers=auth_headers(dept_admin, tenant),
    )
    assert resp.status_code == 403
    assert "outside your department" in resp.json()["detail"]


def test_user_submissions_cross_tenant_returns_404(db, client):
    tenant_a = make_tenant(db, name="Acme", slug="acme")
    tenant_b = make_tenant(db, name="Globex", slug="globex")
    admin_a = make_user(db, tenant_a, UserRole.TENANT_ADMIN)
    employee_b = make_user(db, tenant_b, UserRole.EMPLOYEE)

    resp = client.get(
        f"/api/admin/users/{employee_b.id}/submissions",
        params={"year": 2026, "period": "January"},
        headers=auth_headers(admin_a, tenant_a),
    )
    assert resp.status_code == 404


# ---------- Unauthenticated / wrong-role rejection sweep ----------


def test_kpi_trend_requires_auth(db, client):
    tenant = make_tenant(db)
    employee = make_user(db, tenant, UserRole.EMPLOYEE)
    resp = client.get(f"/api/admin/users/{employee.id}/kpi-trend")
    assert resp.status_code == 401


def test_user_submissions_requires_auth(db, client):
    tenant = make_tenant(db)
    employee = make_user(db, tenant, UserRole.EMPLOYEE)
    resp = client.get(
        f"/api/admin/users/{employee.id}/submissions", params={"year": 2026, "period": "January"}
    )
    assert resp.status_code == 401


def test_kpi_trend_forbidden_for_employee(db, client):
    tenant = make_tenant(db)
    dept = make_department(db, tenant)
    employee = make_user(db, tenant, UserRole.EMPLOYEE, department=dept)
    colleague = make_user(db, tenant, UserRole.EMPLOYEE, department=dept, email="colleague@example.com")

    resp = client.get(
        f"/api/admin/users/{colleague.id}/kpi-trend", headers=auth_headers(employee, tenant)
    )
    assert resp.status_code == 403


def test_user_submissions_forbidden_for_employee(db, client):
    tenant = make_tenant(db)
    dept = make_department(db, tenant)
    employee = make_user(db, tenant, UserRole.EMPLOYEE, department=dept)
    colleague = make_user(db, tenant, UserRole.EMPLOYEE, department=dept, email="colleague2@example.com")

    resp = client.get(
        f"/api/admin/users/{colleague.id}/submissions",
        params={"year": 2026, "period": "January"},
        headers=auth_headers(employee, tenant),
    )
    assert resp.status_code == 403
