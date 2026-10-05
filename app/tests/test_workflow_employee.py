"""HTTP-level integration tests for the full Employee KPI approval workflow,
driving app/routers/dashboard.py and app/routers/kpi.py together the way a
real client would: GET /api/dashboard (materializes drafts) -> POST
/api/kpi/employee/save -> POST /api/kpi/employee/submit -> POST
/api/kpi/{id}/dept-approve -> POST /api/kpi/{id}/final-approve, plus the
reject branch off dept review.
"""

from app.models.user import UserRole
from app.tests.factories import (
    auth_headers,
    make_department,
    make_tenant,
    make_template,
    make_user,
)


def _setup_tenant(db):
    tenant = make_tenant(db)
    dept = make_department(db, tenant)
    employee = make_user(db, tenant, UserRole.EMPLOYEE, name="Employee One", department=dept)
    dept_admin = make_user(db, tenant, UserRole.DEPT_ADMIN, name="Dept Admin", department=dept)
    tenant_admin = make_user(db, tenant, UserRole.TENANT_ADMIN, name="Tenant Admin")
    # Company-wide template (no department, no employee) applies to every Employee.
    template = make_template(db, tenant, metric_name="Revenue", target=100.0, weight=1.0)
    return tenant, dept, employee, dept_admin, tenant_admin, template


def test_full_happy_path_employee_to_approved(db, client):
    tenant, dept, employee, dept_admin, tenant_admin, template = _setup_tenant(db)

    # Step 1: GET /api/dashboard as the Employee materializes a draft submission
    # for the current period (real client would land here with no query params).
    resp = client.get("/api/dashboard", headers=auth_headers(employee, tenant))
    assert resp.status_code == 200
    body = resp.json()
    assert body["is_current_period"] is True
    assert len(body["submissions"]) == 1
    submission = body["submissions"][0]
    assert submission["status"] == "draft"
    assert submission["self_score"] is None
    submission_id = submission["id"]
    year, period = body["active_year"], body["active_period"]

    # Step 2: POST /api/kpi/employee/save with a self_score.
    resp = client.post(
        "/api/kpi/employee/save",
        json={"year": year, "period": period, "scores": {str(submission_id): 95.0}},
        headers=auth_headers(employee, tenant),
    )
    assert resp.status_code == 200
    saved = resp.json()
    assert saved[0]["self_score"] == 95.0
    assert saved[0]["status"] == "draft"

    # Step 3: POST /api/kpi/employee/submit -> pending_dept_approval.
    resp = client.post(
        "/api/kpi/employee/submit",
        json={"year": year, "period": period, "scores": {}},
        headers=auth_headers(employee, tenant),
    )
    assert resp.status_code == 200
    submitted = resp.json()
    assert submitted[0]["status"] == "pending_dept_approval"

    # Step 4: Dept Admin approves -> pending_final_approval.
    resp = client.post(
        f"/api/kpi/{submission_id}/dept-approve",
        json={"year": year, "period": period, "dept_score": 90.0, "remarks": "Solid month"},
        headers=auth_headers(dept_admin, tenant),
    )
    assert resp.status_code == 200
    dept_approved = resp.json()
    assert dept_approved["status"] == "pending_final_approval"
    assert dept_approved["dept_score"] == 90.0

    # Step 5: Tenant Admin gives final approval -> approved.
    resp = client.post(
        f"/api/kpi/{submission_id}/final-approve",
        json={"year": year, "period": period, "final_score": 92.0, "remarks": "Approved"},
        headers=auth_headers(tenant_admin, tenant),
    )
    assert resp.status_code == 200
    final = resp.json()
    assert final["status"] == "approved"
    assert final["final_score"] == 92.0

    # Step 6: Tenant Admin's dashboard now shows this employee's combined score.
    resp = client.get(
        "/api/dashboard",
        params={"year": year, "period": period},
        headers=auth_headers(tenant_admin, tenant),
    )
    assert resp.status_code == 200
    dash = resp.json()
    combined = dash["employee_combined"]
    entry = combined[str(employee.id)]
    assert entry["name"] == "Employee One"
    # target=100, final_score=92 -> attainment 92%.
    assert entry["attainment"] == 92.0
    assert entry["status"] == "warning"


def test_reject_path_then_reject_and_approve_are_refused(db, client):
    tenant, dept, employee, dept_admin, tenant_admin, template = _setup_tenant(db)

    resp = client.get("/api/dashboard", headers=auth_headers(employee, tenant))
    body = resp.json()
    submission_id = body["submissions"][0]["id"]
    year, period = body["active_year"], body["active_period"]

    client.post(
        "/api/kpi/employee/save",
        json={"year": year, "period": period, "scores": {str(submission_id): 70.0}},
        headers=auth_headers(employee, tenant),
    )
    resp = client.post(
        "/api/kpi/employee/submit",
        json={"year": year, "period": period, "scores": {}},
        headers=auth_headers(employee, tenant),
    )
    assert resp.json()[0]["status"] == "pending_dept_approval"

    # Dept Admin rejects instead of approving.
    resp = client.post(
        f"/api/kpi/{submission_id}/reject",
        json={"year": year, "period": period, "remarks": "Needs more detail"},
        headers=auth_headers(dept_admin, tenant),
    )
    assert resp.status_code == 200
    rejected = resp.json()
    assert rejected["status"] == "rejected"
    assert rejected["remarks"] == "Needs more detail"

    # Re-rejecting a rejected submission is refused.
    resp = client.post(
        f"/api/kpi/{submission_id}/reject",
        json={"year": year, "period": period, "remarks": "again"},
        headers=auth_headers(dept_admin, tenant),
    )
    assert resp.status_code == 400

    # Approving a rejected submission is also refused (not awaiting dept review).
    resp = client.post(
        f"/api/kpi/{submission_id}/dept-approve",
        json={"year": year, "period": period},
        headers=auth_headers(dept_admin, tenant),
    )
    assert resp.status_code == 400
