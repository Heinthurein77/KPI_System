"""HTTP-level coverage for the KPI template endpoints in app/routers/admin.py:
GET/POST /api/admin/templates, POST /api/admin/templates/custom,
DELETE /api/admin/templates/{id}.
"""

from app.models.user import UserRole
from app.models.kpi_submission import KPIStatus
from app.tests.factories import (
    auth_headers,
    make_department,
    make_submission,
    make_template,
    make_tenant,
    make_user,
)


# ---------- Happy path CRUD ----------


def test_create_template_success_company_wide(db, client):
    tenant = make_tenant(db)
    admin = make_user(db, tenant, UserRole.TENANT_ADMIN)

    resp = client.post(
        "/api/admin/templates",
        json={"metric_name": "Revenue", "target": 100.0, "weight": 1.0, "department_id": None},
        headers=auth_headers(admin, tenant),
    )
    assert resp.status_code == 200
    body = resp.json()
    assert body["metric_name"] == "Revenue"
    assert body["department_id"] is None
    assert body["is_custom"] is False


def test_create_template_with_department_success(db, client):
    tenant = make_tenant(db)
    admin = make_user(db, tenant, UserRole.TENANT_ADMIN)
    dept = make_department(db, tenant)

    resp = client.post(
        "/api/admin/templates",
        json={"metric_name": "Bugs Fixed", "target": 10.0, "weight": 0.5, "department_id": dept.id},
        headers=auth_headers(admin, tenant),
    )
    assert resp.status_code == 200
    assert resp.json()["department_id"] == dept.id


def test_list_templates_success(db, client):
    tenant = make_tenant(db)
    admin = make_user(db, tenant, UserRole.TENANT_ADMIN)
    make_template(db, tenant, metric_name="Revenue")

    resp = client.get("/api/admin/templates", headers=auth_headers(admin, tenant))
    assert resp.status_code == 200
    names = {t["metric_name"] for t in resp.json()["kpi_templates"]}
    assert "Revenue" in names


def test_delete_template_success(db, client):
    tenant = make_tenant(db)
    admin = make_user(db, tenant, UserRole.TENANT_ADMIN)
    template = make_template(db, tenant)

    resp = client.delete(f"/api/admin/templates/{template.id}", headers=auth_headers(admin, tenant))
    assert resp.status_code == 200
    assert resp.json() == {"status": "ok"}


def test_create_custom_template_success_dept_admin_own_employee(db, client):
    tenant = make_tenant(db)
    dept = make_department(db, tenant)
    dept_admin = make_user(db, tenant, UserRole.DEPT_ADMIN, department=dept)
    employee = make_user(db, tenant, UserRole.EMPLOYEE, department=dept)

    resp = client.post(
        "/api/admin/templates/custom",
        json={
            "employee_id": employee.id,
            "metric_name": "Special Project",
            "target": 50.0,
            "weight": 1.0,
            "year": 2026,
            "period": "March",
        },
        headers=auth_headers(dept_admin, tenant),
    )
    assert resp.status_code == 200
    body = resp.json()
    assert body["metric_name"] == "Special Project"
    assert body["employee_id"] == employee.id
    assert body["is_custom"] is True
    assert body["is_recurring"] is True


# ---------- Delete-with-dependents-like guard (Dept Admin path only) ----------


def test_delete_template_tenant_admin_unconditional_even_with_submissions(db, client):
    """Tenant Admin's delete is unconditional -- no history protection, unlike
    the Dept Admin path below."""
    tenant = make_tenant(db)
    admin = make_user(db, tenant, UserRole.TENANT_ADMIN)
    dept = make_department(db, tenant)
    employee = make_user(db, tenant, UserRole.EMPLOYEE, department=dept)
    template = make_template(db, tenant, department=dept)
    make_submission(db, tenant, employee, template, status=KPIStatus.APPROVED)

    resp = client.delete(f"/api/admin/templates/{template.id}", headers=auth_headers(admin, tenant))
    assert resp.status_code == 200


def test_delete_template_dept_admin_blocked_by_non_draft_submissions(db, client):
    tenant = make_tenant(db)
    dept = make_department(db, tenant)
    dept_admin = make_user(db, tenant, UserRole.DEPT_ADMIN, department=dept)
    employee = make_user(db, tenant, UserRole.EMPLOYEE, department=dept)
    template = make_template(db, tenant, department=dept)
    make_submission(db, tenant, employee, template, status=KPIStatus.PENDING_DEPT_APPROVAL)

    resp = client.delete(
        f"/api/admin/templates/{template.id}", headers=auth_headers(dept_admin, tenant)
    )
    assert resp.status_code == 400
    assert "can't be deleted" in resp.json()["detail"]


def test_delete_template_dept_admin_succeeds_with_only_draft_submissions(db, client):
    tenant = make_tenant(db)
    dept = make_department(db, tenant)
    dept_admin = make_user(db, tenant, UserRole.DEPT_ADMIN, department=dept)
    employee = make_user(db, tenant, UserRole.EMPLOYEE, department=dept)
    template = make_template(db, tenant, department=dept)
    make_submission(db, tenant, employee, template, status=KPIStatus.DRAFT)

    resp = client.delete(
        f"/api/admin/templates/{template.id}", headers=auth_headers(dept_admin, tenant)
    )
    assert resp.status_code == 200


# ---------- Dept Admin department-scoping guard ----------


def test_create_template_dept_admin_forced_into_own_department(db, client):
    tenant = make_tenant(db)
    dept = make_department(db, tenant, name="Mine")
    other_dept = make_department(db, tenant, name="Theirs")
    dept_admin = make_user(db, tenant, UserRole.DEPT_ADMIN, department=dept)

    resp = client.post(
        "/api/admin/templates",
        json={"metric_name": "Whatever", "target": 1.0, "weight": 1.0, "department_id": other_dept.id},
        headers=auth_headers(dept_admin, tenant),
    )
    assert resp.status_code == 200
    assert resp.json()["department_id"] == dept.id


def test_list_templates_dept_admin_scoped(db, client):
    tenant = make_tenant(db)
    dept = make_department(db, tenant, name="Mine")
    other_dept = make_department(db, tenant, name="Theirs")
    dept_admin = make_user(db, tenant, UserRole.DEPT_ADMIN, department=dept)
    make_template(db, tenant, metric_name="Mine Metric", department=dept)
    make_template(db, tenant, metric_name="Other Metric", department=other_dept)
    make_template(db, tenant, metric_name="Company Wide", department=None)

    resp = client.get("/api/admin/templates", headers=auth_headers(dept_admin, tenant))
    assert resp.status_code == 200
    names = {t["metric_name"] for t in resp.json()["kpi_templates"]}
    assert "Mine Metric" in names
    assert "Company Wide" in names
    assert "Other Metric" not in names


def test_create_custom_template_dept_admin_other_department_forbidden(db, client):
    tenant = make_tenant(db)
    dept = make_department(db, tenant, name="Mine")
    other_dept = make_department(db, tenant, name="Theirs")
    dept_admin = make_user(db, tenant, UserRole.DEPT_ADMIN, department=dept)
    other_employee = make_user(db, tenant, UserRole.EMPLOYEE, department=other_dept)

    resp = client.post(
        "/api/admin/templates/custom",
        json={
            "employee_id": other_employee.id,
            "metric_name": "Sneaky",
            "target": 10.0,
            "weight": 1.0,
            "year": 2026,
            "period": "March",
        },
        headers=auth_headers(dept_admin, tenant),
    )
    assert resp.status_code == 403


def test_delete_template_dept_admin_other_department_forbidden(db, client):
    tenant = make_tenant(db)
    dept = make_department(db, tenant, name="Mine")
    other_dept = make_department(db, tenant, name="Theirs")
    dept_admin = make_user(db, tenant, UserRole.DEPT_ADMIN, department=dept)
    other_template = make_template(db, tenant, department=other_dept)

    resp = client.delete(
        f"/api/admin/templates/{other_template.id}", headers=auth_headers(dept_admin, tenant)
    )
    assert resp.status_code == 403
    assert "outside your department" in resp.json()["detail"]


def test_create_custom_template_tenant_admin_rejects_tenant_admin_target(db, client):
    tenant = make_tenant(db)
    admin = make_user(db, tenant, UserRole.TENANT_ADMIN)
    other_admin = make_user(db, tenant, UserRole.TENANT_ADMIN, email="other-admin@example.com")

    resp = client.post(
        "/api/admin/templates/custom",
        json={
            "employee_id": other_admin.id,
            "metric_name": "Not allowed",
            "target": 10.0,
            "weight": 1.0,
            "year": 2026,
            "period": "March",
        },
        headers=auth_headers(admin, tenant),
    )
    assert resp.status_code == 400


# ---------- Cross-tenant isolation ----------


def test_create_template_cross_tenant_department_id_returns_404(db, client):
    tenant_a = make_tenant(db, name="Acme", slug="acme")
    tenant_b = make_tenant(db, name="Globex", slug="globex")
    admin_a = make_user(db, tenant_a, UserRole.TENANT_ADMIN)
    dept_b = make_department(db, tenant_b)

    resp = client.post(
        "/api/admin/templates",
        json={"metric_name": "Cross", "target": 1.0, "weight": 1.0, "department_id": dept_b.id},
        headers=auth_headers(admin_a, tenant_a),
    )
    assert resp.status_code == 404


def test_create_custom_template_cross_tenant_employee_returns_404(db, client):
    tenant_a = make_tenant(db, name="Acme", slug="acme")
    tenant_b = make_tenant(db, name="Globex", slug="globex")
    admin_a = make_user(db, tenant_a, UserRole.TENANT_ADMIN)
    employee_b = make_user(db, tenant_b, UserRole.EMPLOYEE)

    resp = client.post(
        "/api/admin/templates/custom",
        json={
            "employee_id": employee_b.id,
            "metric_name": "Cross",
            "target": 1.0,
            "weight": 1.0,
            "year": 2026,
            "period": "March",
        },
        headers=auth_headers(admin_a, tenant_a),
    )
    assert resp.status_code == 404


def test_delete_template_cross_tenant_returns_404(db, client):
    tenant_a = make_tenant(db, name="Acme", slug="acme")
    tenant_b = make_tenant(db, name="Globex", slug="globex")
    admin_a = make_user(db, tenant_a, UserRole.TENANT_ADMIN)
    template_b = make_template(db, tenant_b)

    resp = client.delete(
        f"/api/admin/templates/{template_b.id}", headers=auth_headers(admin_a, tenant_a)
    )
    assert resp.status_code == 404


# ---------- Unauthenticated / wrong-role rejection ----------


def test_list_templates_requires_auth(client):
    resp = client.get("/api/admin/templates")
    assert resp.status_code == 401


def test_create_template_forbidden_for_employee(db, client):
    tenant = make_tenant(db)
    employee = make_user(db, tenant, UserRole.EMPLOYEE)

    resp = client.post(
        "/api/admin/templates",
        json={"metric_name": "x", "target": 1.0, "weight": 1.0, "department_id": None},
        headers=auth_headers(employee, tenant),
    )
    assert resp.status_code == 403


def test_delete_template_forbidden_for_employee(db, client):
    tenant = make_tenant(db)
    employee = make_user(db, tenant, UserRole.EMPLOYEE)
    template = make_template(db, tenant)

    resp = client.delete(
        f"/api/admin/templates/{template.id}", headers=auth_headers(employee, tenant)
    )
    assert resp.status_code == 403
