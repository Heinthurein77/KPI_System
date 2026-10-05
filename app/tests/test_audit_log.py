"""Coverage for the audit trail: app/services/audit_service.py's hooks into
kpi_service.py / admin.py, and GET /api/admin/audit-log in app/routers/admin.py.
"""

from app.models.kpi_submission import KPIStatus
from app.models.user import UserRole
from app.tests.factories import (
    auth_headers,
    make_department,
    make_submission,
    make_template,
    make_tenant,
    make_user,
)


def test_dept_approve_records_audit_entry(db, client):
    tenant = make_tenant(db)
    dept = make_department(db, tenant)
    tenant_admin = make_user(db, tenant, UserRole.TENANT_ADMIN)
    dept_admin = make_user(db, tenant, UserRole.DEPT_ADMIN, department=dept)
    employee = make_user(db, tenant, UserRole.EMPLOYEE, department=dept)
    template = make_template(db, tenant, metric_name="Revenue", department=dept)
    submission = make_submission(
        db, tenant, employee, template, status=KPIStatus.PENDING_DEPT_APPROVAL, self_score=80.0, dept_score=80.0
    )

    resp = client.post(
        f"/api/kpi/{submission.id}/dept-approve",
        json={"year": submission.year, "period": submission.month_or_quarter, "dept_score": 90.0},
        headers=auth_headers(dept_admin, tenant),
    )
    assert resp.status_code == 200

    # The audit log itself is a Tenant Admin oversight tool (see admin.py) —
    # even the dept_admin whose own action this is can't read it back.
    log_resp = client.get("/api/admin/audit-log", headers=auth_headers(tenant_admin, tenant))
    assert log_resp.status_code == 200
    entries = log_resp.json()
    assert len(entries) == 1
    entry = entries[0]
    assert entry["action"] == "kpi_dept_approved"
    assert entry["actor_name"] == dept_admin.name
    assert entry["actor_role"] == "dept_admin"
    assert "Revenue" in entry["entity_label"]
    assert employee.name in entry["entity_label"]
    assert "80" in entry["summary"] and "90" in entry["summary"]

def test_override_and_reject_record_distinct_actions(db, client):
    tenant = make_tenant(db)
    dept = make_department(db, tenant)
    admin = make_user(db, tenant, UserRole.TENANT_ADMIN)
    employee = make_user(db, tenant, UserRole.EMPLOYEE, department=dept)
    template = make_template(db, tenant, department=dept)
    submission = make_submission(
        db, tenant, employee, template, status=KPIStatus.PENDING_FINAL_APPROVAL, dept_score=70.0
    )

    resp = client.post(
        f"/api/kpi/{submission.id}/override",
        json={"year": submission.year, "period": submission.month_or_quarter, "final_score": 95.0},
        headers=auth_headers(admin, tenant),
    )
    assert resp.status_code == 200

    other_submission = make_submission(
        db, tenant, employee, make_template(db, tenant, metric_name="Bugs", department=dept),
        status=KPIStatus.PENDING_DEPT_APPROVAL,
    )
    reject_resp = client.post(
        f"/api/kpi/{other_submission.id}/reject",
        json={"year": other_submission.year, "period": other_submission.month_or_quarter, "remarks": "redo"},
        headers=auth_headers(admin, tenant),
    )
    assert reject_resp.status_code == 200

    log_resp = client.get("/api/admin/audit-log", headers=auth_headers(admin, tenant))
    actions = {e["action"] for e in log_resp.json()}
    assert "kpi_overridden" in actions
    assert "kpi_rejected" in actions


def test_template_create_and_delete_record_audit_entries(db, client):
    tenant = make_tenant(db)
    admin = make_user(db, tenant, UserRole.TENANT_ADMIN)

    create_resp = client.post(
        "/api/admin/templates",
        json={"metric_name": "Revenue", "target": 100.0, "weight": 1.0, "department_id": None},
        headers=auth_headers(admin, tenant),
    )
    template_id = create_resp.json()["id"]

    delete_resp = client.delete(f"/api/admin/templates/{template_id}", headers=auth_headers(admin, tenant))
    assert delete_resp.status_code == 200

    log_resp = client.get("/api/admin/audit-log", headers=auth_headers(admin, tenant))
    actions = [e["action"] for e in log_resp.json()]
    assert actions.count("kpi_template_created") == 1
    assert actions.count("kpi_template_deleted") == 1


def test_audit_log_forbidden_for_dept_admin(db, client):
    """Unlike this file's other admin.py endpoints, the audit log is a Tenant
    Admin oversight tool, not a line-manager one — a Dept Admin is forbidden
    even from the entries their own actions generated (see test above)."""
    tenant = make_tenant(db)
    dept = make_department(db, tenant)
    dept_admin = make_user(db, tenant, UserRole.DEPT_ADMIN, department=dept)

    resp = client.get("/api/admin/audit-log", headers=auth_headers(dept_admin, tenant))
    assert resp.status_code == 403


def test_audit_log_requires_auth(client):
    resp = client.get("/api/admin/audit-log")
    assert resp.status_code == 401


def test_audit_log_forbidden_for_employee(db, client):
    tenant = make_tenant(db)
    employee = make_user(db, tenant, UserRole.EMPLOYEE)

    resp = client.get("/api/admin/audit-log", headers=auth_headers(employee, tenant))
    assert resp.status_code == 403


def test_audit_log_cross_tenant_isolation(db, client):
    tenant_a = make_tenant(db, name="Acme", slug="acme")
    tenant_b = make_tenant(db, name="Globex", slug="globex")
    admin_a = make_user(db, tenant_a, UserRole.TENANT_ADMIN)
    admin_b = make_user(db, tenant_b, UserRole.TENANT_ADMIN)

    client.post(
        "/api/admin/templates",
        json={"metric_name": "Acme Only", "target": 1.0, "weight": 1.0, "department_id": None},
        headers=auth_headers(admin_a, tenant_a),
    )

    resp_b = client.get("/api/admin/audit-log", headers=auth_headers(admin_b, tenant_b))
    assert resp_b.json() == []
