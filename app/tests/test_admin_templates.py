"""HTTP-level coverage for the KPI template endpoints in app/routers/admin.py:
GET/POST /api/admin/templates, POST /api/admin/templates/custom,
DELETE /api/admin/templates/{id}.
"""

from app.models.department import Department
from app.models.kpi_template import KPITemplate
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


def test_delete_template_dept_admin_cannot_delete_own_assigned_template(db, client):
    """A Dept Admin may not delete a KPI metric assigned to them personally,
    even when it sits in their own department and has no history."""
    tenant = make_tenant(db)
    dept = make_department(db, tenant)
    dept_admin = make_user(db, tenant, UserRole.DEPT_ADMIN, department=dept)
    template = make_template(db, tenant, department=dept, employee=dept_admin)

    resp = client.delete(
        f"/api/admin/templates/{template.id}", headers=auth_headers(dept_admin, tenant)
    )
    assert resp.status_code == 403
    assert "assigned to you" in resp.json()["detail"]

    # The template still exists.
    assert db.get(KPITemplate, template.id) is not None


def test_delete_template_tenant_admin_can_delete_personal_template(db, client):
    """Tenant Admin keeps full delete power, including personally-assigned templates."""
    tenant = make_tenant(db)
    dept = make_department(db, tenant)
    admin = make_user(db, tenant, UserRole.TENANT_ADMIN)
    dept_admin = make_user(db, tenant, UserRole.DEPT_ADMIN, department=dept)
    template = make_template(db, tenant, department=dept, employee=dept_admin)

    resp = client.delete(
        f"/api/admin/templates/{template.id}", headers=auth_headers(admin, tenant)
    )
    assert resp.status_code == 200


# ---------- Weight/target snapshots (M2) ----------


def test_weight_edit_does_not_rewrite_existing_scores(client, db):
    """Editing a template's weight must not change already-created
    submissions' combined scores (snapshot, not live template)."""
    from app.routers.dashboard import combined_final_score

    tenant = make_tenant(db)
    dept = make_department(db, tenant)
    admin = make_user(db, tenant, UserRole.TENANT_ADMIN, name="Boss")
    emp = make_user(db, tenant, UserRole.EMPLOYEE, name="Emp", department=dept)
    tpl = make_template(db, tenant, target=100, weight=50, department=dept, employee=emp, is_recurring=True)
    sub = make_submission(db, tenant, emp, tpl, status=KPIStatus.APPROVED, final_score=80)
    # Factory bypasses the service layer: set the snapshot like creation does.
    sub.weight_snapshot = tpl.weight
    sub.target_snapshot = tpl.target
    db.commit()

    before = combined_final_score([sub])["attainment"]

    resp = client.patch(
        f"/api/admin/templates/{tpl.id}/weight",
        json={"weight": 100},
        headers=auth_headers(admin, tenant),
    )
    assert resp.status_code == 200
    db.refresh(sub)
    db.refresh(tpl)
    assert tpl.weight == 100

    after = combined_final_score([sub])["attainment"]
    assert after == before
    assert sub.weight_snapshot == 50  # frozen at creation


def test_new_submissions_get_current_weight(client, db):
    """Submissions created after a weight edit pick up the new weight."""
    from app.services import kpi_service

    tenant = make_tenant(db)
    dept = make_department(db, tenant)
    admin = make_user(db, tenant, UserRole.TENANT_ADMIN, name="Boss")
    emp = make_user(db, tenant, UserRole.EMPLOYEE, name="Emp", department=dept)
    tpl = make_template(db, tenant, target=100, weight=50, department=dept, employee=emp,
                         is_recurring=True, locked_year=2026, locked_period="January")

    resp = client.patch(
        f"/api/admin/templates/{tpl.id}/weight",
        json={"weight": 70},
        headers=auth_headers(admin, tenant),
    )
    assert resp.status_code == 200
    db.refresh(tpl)  # the PATCH ran in another session; drop the stale identity-map copy

    subs = kpi_service.ensure_period_submissions(db, emp, 2026, "November")
    assert len(subs) == 1
    assert subs[0].weight_snapshot == 70
    assert subs[0].target_snapshot == 100


# ---------- Custom template period/year validation (audit fix 3) ----------


def _custom_payload(employee, **overrides):
    payload = {
        "employee_id": employee.id,
        "metric_name": "Special Project",
        "target": 50.0,
        "weight": 1.0,
        "year": 2026,
        "period": "March",
    }
    payload.update(overrides)
    return payload


def _dept_admin_with_employee(db):
    tenant = make_tenant(db)
    dept = make_department(db, tenant)
    dept_admin = make_user(db, tenant, UserRole.DEPT_ADMIN, department=dept)
    employee = make_user(db, tenant, UserRole.EMPLOYEE, department=dept)
    return tenant, dept_admin, employee


def test_create_custom_template_rejects_invalid_period(db, client):
    tenant, dept_admin, employee = _dept_admin_with_employee(db)
    resp = client.post(
        "/api/admin/templates/custom",
        json=_custom_payload(employee, period="NotAMonth"),
        headers=auth_headers(dept_admin, tenant),
    )
    assert resp.status_code == 422
    # nothing persisted
    assert db.query(KPITemplate).count() == 0


def test_create_custom_template_rejects_invalid_year(db, client):
    tenant, dept_admin, employee = _dept_admin_with_employee(db)
    resp = client.post(
        "/api/admin/templates/custom",
        json=_custom_payload(employee, year=1999),
        headers=auth_headers(dept_admin, tenant),
    )
    assert resp.status_code == 422
    assert db.query(KPITemplate).count() == 0


# ---------- Atomic rebalance + create (audit fix 2) ----------


def _rebalance_setup(db):
    tenant = make_tenant(db)
    dept = make_department(db, tenant)
    dept_admin = make_user(db, tenant, UserRole.DEPT_ADMIN, department=dept)
    employee = make_user(db, tenant, UserRole.EMPLOYEE, department=dept)
    existing = make_template(
        db, tenant, metric_name="Existing KPI", weight=30.0,
        department=dept, employee=employee, is_recurring=True,
    )
    return tenant, dept_admin, employee, existing


def test_custom_with_rebalance_success_applies_both(db, client):
    tenant, dept_admin, employee, existing = _rebalance_setup(db)
    resp = client.post(
        "/api/admin/templates/custom-with-rebalance",
        json={
            **_custom_payload(employee, metric_name="New KPI", weight=20.0),
            "is_recurring": True,
            "weight_adjustments": [{"template_id": existing.id, "weight": 40.0}],
        },
        headers=auth_headers(dept_admin, tenant),
    )
    assert resp.status_code == 200
    body = resp.json()
    assert body["metric_name"] == "New KPI"
    assert body["is_custom"] is True
    db.refresh(existing)
    assert existing.weight == 40.0


def test_custom_with_rebalance_rolls_back_weights_on_failed_create(db, client):
    tenant, dept_admin, employee, existing = _rebalance_setup(db)
    # A template with the same name+period already exists → the create fails.
    dept = db.query(Department).filter_by(tenant_id=tenant.id).first()
    make_template(
        db, tenant, metric_name="New KPI", department=dept, employee=employee,
        locked_year=2026, locked_period="March", is_recurring=True,
    )
    resp = client.post(
        "/api/admin/templates/custom-with-rebalance",
        json={
            **_custom_payload(employee, metric_name="New KPI", weight=20.0),
            "is_recurring": True,
            "weight_adjustments": [{"template_id": existing.id, "weight": 40.0}],
        },
        headers=auth_headers(dept_admin, tenant),
    )
    assert resp.status_code == 400
    # The weight change must NOT have been persisted.
    db.refresh(existing)
    assert existing.weight == 30.0


def test_custom_with_rebalance_rejects_invalid_period(db, client):
    tenant, dept_admin, employee, existing = _rebalance_setup(db)
    resp = client.post(
        "/api/admin/templates/custom-with-rebalance",
        json={
            **_custom_payload(employee, period="Someday"),
            "weight_adjustments": [{"template_id": existing.id, "weight": 40.0}],
        },
        headers=auth_headers(dept_admin, tenant),
    )
    assert resp.status_code == 422
    db.refresh(existing)
    assert existing.weight == 30.0
