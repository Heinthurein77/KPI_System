"""Extra endpoint coverage for the thinnest areas (T-m2): the dashboard
endpoint (beyond the role-boundary and same-name tests in
test_dashboard_endpoint.py) and the analytics endpoints (beyond the
happy-path aggregates in test_analytics.py).
"""

import pytest

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


# ---------------------------------------------------------------------------
# Dashboard
# ---------------------------------------------------------------------------


def test_dashboard_employee_sees_own_submissions_and_combined_score(db, client):
    tenant = make_tenant(db)
    dept = make_department(db, tenant)
    employee = make_user(db, tenant, UserRole.EMPLOYEE, department=dept)
    template = make_template(db, tenant, metric_name="Sales", target=100.0, weight=100.0, department=dept)
    make_submission(
        db, tenant, employee, template, year=2026, period="March",
        status=KPIStatus.APPROVED, self_score=80.0, dept_score=85.0, final_score=90.0,
        department=dept,
    )

    resp = client.get(
        "/api/dashboard", params={"year": 2026, "period": "March"},
        headers=auth_headers(employee, tenant),
    )

    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["active_year"] == 2026
    assert body["active_period"] == "March"
    assert len(body["submissions"]) == 1
    assert body["submissions"][0]["kpi_template"]["metric_name"] == "Sales"
    assert body["combined_score"]["attainment"] == pytest.approx(90.0)
    assert body["combined_score"]["scored_count"] == 1


def test_dashboard_rejects_invalid_period_and_status_filter(db, client):
    tenant = make_tenant(db)
    admin = make_user(db, tenant, UserRole.TENANT_ADMIN)

    resp = client.get(
        "/api/dashboard", params={"period": "Smarch"},
        headers=auth_headers(admin, tenant),
    )
    assert resp.status_code == 422

    resp = client.get(
        "/api/dashboard", params={"status_filter": "bogus"},
        headers=auth_headers(admin, tenant),
    )
    assert resp.status_code == 422


def test_dashboard_tenant_admin_department_filter(db, client):
    tenant = make_tenant(db)
    sales = make_department(db, tenant, name="Sales")
    ops = make_department(db, tenant, name="Operations")
    admin = make_user(db, tenant, UserRole.TENANT_ADMIN)
    emp_sales = make_user(db, tenant, UserRole.EMPLOYEE, name="Sam Sales", department=sales)
    emp_ops = make_user(db, tenant, UserRole.EMPLOYEE, name="Ollie Ops", department=ops)
    template = make_template(db, tenant, metric_name="Sales", target=100.0, weight=100.0)
    make_submission(db, tenant, emp_sales, template, year=2026, period="March",
                    status=KPIStatus.APPROVED, final_score=90.0, department=sales)
    make_submission(db, tenant, emp_ops, template, year=2026, period="March",
                    status=KPIStatus.APPROVED, final_score=70.0, department=ops)

    resp = client.get(
        "/api/dashboard",
        params={"year": 2026, "period": "March", "department_id": sales.id},
        headers=auth_headers(admin, tenant),
    )

    assert resp.status_code == 200, resp.text
    names = {s["employee"]["name"] for s in resp.json()["submissions"]}
    assert names == {"Sam Sales"}


def test_dashboard_status_filter_narrows_to_approved(db, client):
    tenant = make_tenant(db)
    dept = make_department(db, tenant)
    admin = make_user(db, tenant, UserRole.TENANT_ADMIN)
    employee = make_user(db, tenant, UserRole.EMPLOYEE, department=dept)
    template = make_template(db, tenant, metric_name="Sales", target=100.0, weight=100.0, department=dept)
    make_submission(db, tenant, employee, template, year=2026, period="March",
                    status=KPIStatus.DRAFT, self_score=50.0, department=dept)
    make_submission(db, tenant, employee, template, year=2026, period="April",
                    status=KPIStatus.APPROVED, final_score=90.0, department=dept)

    resp = client.get(
        "/api/dashboard",
        params={"year": 2026, "period": "March", "status_filter": "approved"},
        headers=auth_headers(admin, tenant),
    )
    assert resp.status_code == 200, resp.text
    assert resp.json()["submissions"] == []

    resp = client.get(
        "/api/dashboard",
        params={"year": 2026, "period": "April", "status_filter": "approved"},
        headers=auth_headers(admin, tenant),
    )
    assert resp.status_code == 200, resp.text
    assert len(resp.json()["submissions"]) == 1


def test_my_kpi_forbidden_for_employee(db, client):
    tenant = make_tenant(db)
    employee = make_user(db, tenant, UserRole.EMPLOYEE)

    resp = client.get("/api/my-kpi", headers=auth_headers(employee, tenant))

    assert resp.status_code == 403


# ---------------------------------------------------------------------------
# Analytics
# ---------------------------------------------------------------------------


def test_analytics_overview_empty_period(db, client):
    tenant = make_tenant(db)
    dept = make_department(db, tenant)
    admin = make_user(db, tenant, UserRole.TENANT_ADMIN)
    make_user(db, tenant, UserRole.EMPLOYEE, name="Solo", department=dept)

    resp = client.get(
        "/api/analytics/overview", params={"year": 2026, "period": "March"},
        headers=auth_headers(admin, tenant),
    )

    assert resp.status_code == 200, resp.text
    data = resp.json()
    assert data["employee_count"] == 1
    assert data["submitted_count"] == 0
    assert data["approved_count"] == 0
    assert data["pending_count"] == 1
    assert data["submission_rate"] == 0.0
    assert data["avg_score"] is None
    assert data["buckets"] == {"Need to improve": 0, "Normal": 0, "Performance": 0}
    assert data["by_department"][0]["avg_score"] is None


def test_analytics_overview_counts_only_approved_in_averages(db, client):
    tenant = make_tenant(db)
    dept = make_department(db, tenant)
    admin = make_user(db, tenant, UserRole.TENANT_ADMIN)
    approved_emp = make_user(db, tenant, UserRole.EMPLOYEE, name="Approved Ana", department=dept)
    draft_emp = make_user(db, tenant, UserRole.EMPLOYEE, name="Draft Dan", department=dept)
    template = make_template(db, tenant, metric_name="Sales", target=100.0, weight=100.0, department=dept)
    make_submission(db, tenant, approved_emp, template, year=2026, period="March",
                    status=KPIStatus.APPROVED, final_score=80.0, department=dept)
    make_submission(db, tenant, draft_emp, template, year=2026, period="March",
                    status=KPIStatus.DRAFT, self_score=100.0, department=dept)

    resp = client.get(
        "/api/analytics/overview", params={"year": 2026, "period": "March"},
        headers=auth_headers(admin, tenant),
    )

    assert resp.status_code == 200, resp.text
    data = resp.json()
    assert data["submitted_count"] == 1
    assert data["approved_count"] == 1
    assert data["avg_score"] == pytest.approx(80.0)
    assert data["buckets"] == {"Need to improve": 0, "Normal": 0, "Performance": 1}


def test_analytics_overview_forbidden_for_dept_admin(db, client):
    tenant = make_tenant(db)
    dept_admin = make_user(db, tenant, UserRole.DEPT_ADMIN)

    resp = client.get("/api/analytics/overview", headers=auth_headers(dept_admin, tenant))

    assert resp.status_code == 403


def test_analytics_trend_clamps_periods(db, client):
    tenant = make_tenant(db)
    admin = make_user(db, tenant, UserRole.TENANT_ADMIN)

    resp = client.get(
        "/api/analytics/trend", params={"periods": 100},
        headers=auth_headers(admin, tenant),
    )
    assert resp.status_code == 200, resp.text
    assert len(resp.json()) == 24

    resp = client.get(
        "/api/analytics/trend", params={"periods": 0},
        headers=auth_headers(admin, tenant),
    )
    assert resp.status_code == 200, resp.text
    assert len(resp.json()) == 1
