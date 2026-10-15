"""HTTP-level integration test for a Dept Admin's own self-assessment via
GET /api/my-kpi, and the no-self-approval rule for it:
a Dept Admin's own submission goes straight to pending_final_approval on
submit (it skips department review — nobody may approve their own KPI),
it never appears in the Dept Admin's own review queue, and only the
Tenant Admin gives final approval.
"""

from app.models.user import UserRole
from app.routers.dashboard import current_month_period
from app.tests.factories import auth_headers, make_department, make_tenant, make_template, make_user


def test_dept_admin_self_assessment_skips_dept_review(db, client):
    tenant = make_tenant(db)
    dept = make_department(db, tenant)
    dept_admin = make_user(db, tenant, UserRole.DEPT_ADMIN, name="Dept Admin", department=dept)
    tenant_admin = make_user(db, tenant, UserRole.TENANT_ADMIN, name="Tenant Admin")

    year, period = current_month_period()
    # Custom KPI assigned to this Dept Admin specifically, locked to the current period.
    make_template(
        db,
        tenant,
        metric_name="Team Output",
        target=50.0,
        weight=100.0,
        department=dept,
        employee=dept_admin,
        locked_year=year,
        locked_period=period,
    )

    # GET /api/my-kpi materializes the draft for the Dept Admin's own custom KPI.
    resp = client.get("/api/my-kpi", headers=auth_headers(dept_admin, tenant))
    assert resp.status_code == 200
    body = resp.json()
    assert body["is_current_period"] is True
    assert len(body["submissions"]) == 1
    submission = body["submissions"][0]
    assert submission["status"] == "draft"
    assert submission["kpi_template"]["metric_name"] == "Team Output"
    submission_id = submission["id"]

    # POST /api/kpi/employee/save with a self_score, via the shared self-assess endpoint.
    resp = client.post(
        "/api/kpi/employee/save",
        json={"year": year, "period": period, "scores": {str(submission_id): 45.0}},
        headers=auth_headers(dept_admin, tenant),
    )
    assert resp.status_code == 200
    assert resp.json()[0]["self_score"] == 45.0

    # Submit -> straight to pending_final_approval (department review is skipped).
    resp = client.post(
        "/api/kpi/employee/submit",
        json={"year": year, "period": period, "scores": {}},
        headers=auth_headers(dept_admin, tenant),
    )
    assert resp.status_code == 200
    submitted = resp.json()
    assert submitted[0]["status"] == "pending_final_approval"

    # The Dept Admin's own submission never appears in their review queue.
    resp = client.get(
        "/api/dashboard",
        params={"year": year, "period": period},
        headers=auth_headers(dept_admin, tenant),
    )
    assert resp.status_code == 200
    ids = {s["id"] for s in resp.json()["submissions"]}
    assert submission_id not in ids

    # The Dept Admin cannot approve their own KPI — 403, no state change.
    resp = client.post(
        f"/api/kpi/{submission_id}/dept-approve",
        json={"year": year, "period": period, "dept_score": 45.0, "remarks": None},
        headers=auth_headers(dept_admin, tenant),
    )
    assert resp.status_code == 403
    assert resp.json()["detail"] == "You cannot review your own KPI submission."

    # The Tenant Admin gives final approval.
    resp = client.post(
        f"/api/kpi/{submission_id}/final-approve",
        json={"year": year, "period": period, "final_score": 45.0, "remarks": None},
        headers=auth_headers(tenant_admin, tenant),
    )
    assert resp.status_code == 200
    assert resp.json()["status"] == "approved"
