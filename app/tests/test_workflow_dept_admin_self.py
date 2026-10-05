"""HTTP-level integration test for a Dept Admin's own self-assessment via
GET /api/my-kpi, and the self-review-avoidance rule in
kpi_service.submit_for_dept_approval: a Dept Admin's own submission must skip
straight to pending_final_approval, never pending_dept_approval (they'd
otherwise be reviewing themselves)."""

from app.models.user import UserRole
from app.routers.dashboard import current_month_period
from app.tests.factories import auth_headers, make_department, make_tenant, make_template, make_user


def test_dept_admin_self_assessment_skips_department_review(db, client):
    tenant = make_tenant(db)
    dept = make_department(db, tenant)
    dept_admin = make_user(db, tenant, UserRole.DEPT_ADMIN, name="Dept Admin", department=dept)

    year, period = current_month_period()
    # Custom KPI assigned to this Dept Admin specifically, locked to the current period.
    make_template(
        db,
        tenant,
        metric_name="Team Output",
        target=50.0,
        weight=1.0,
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

    # POST /api/kpi/employee/submit -> must go straight to pending_final_approval,
    # never pending_dept_approval, since a Dept Admin can't review their own KPI.
    resp = client.post(
        "/api/kpi/employee/submit",
        json={"year": year, "period": period, "scores": {}},
        headers=auth_headers(dept_admin, tenant),
    )
    assert resp.status_code == 200
    submitted = resp.json()
    assert submitted[0]["status"] == "pending_final_approval"

    # A subsequent GET /api/my-kpi reflects the read-only branch for the now-submitted KPI.
    resp = client.get("/api/my-kpi", headers=auth_headers(dept_admin, tenant))
    assert resp.status_code == 200
    assert resp.json()["submissions"][0]["status"] == "pending_final_approval"
