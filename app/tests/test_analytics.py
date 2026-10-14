"""Tests for the analytics endpoints (overview + trend). Read-only aggregations."""

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


@pytest.fixture
def setup_data(db):
    tenant = make_tenant(db)
    dept_eng = make_department(db, tenant, name="Engineering")
    dept_sales = make_department(db, tenant, name="Sales")
    admin = make_user(db, tenant, UserRole.TENANT_ADMIN, name="Boss")
    emp1 = make_user(db, tenant, UserRole.EMPLOYEE, name="Eng One", department=dept_eng)
    emp2 = make_user(db, tenant, UserRole.EMPLOYEE, name="Eng Two", department=dept_eng)
    emp3 = make_user(db, tenant, UserRole.EMPLOYEE, name="Sales One", department=dept_sales)
    tpl = make_template(db, tenant)

    # October 2026: emp1 submitted (final 90), emp2 draft, emp3 submitted (final 60)
    make_submission(db, tenant, emp1, tpl, year=2026, period="October",
                    status=KPIStatus.APPROVED, final_score=90, department=dept_eng)
    make_submission(db, tenant, emp2, tpl, year=2026, period="October",
                    status=KPIStatus.DRAFT, self_score=70, department=dept_eng)
    make_submission(db, tenant, emp3, tpl, year=2026, period="October",
                    status=KPIStatus.APPROVED, final_score=60, department=dept_sales)
    # September 2026: emp1 submitted (final 80)
    make_submission(db, tenant, emp1, tpl, year=2026, period="September",
                    status=KPIStatus.APPROVED, final_score=80, department=dept_eng)
    db.commit()
    return tenant, admin


def test_overview_aggregates(client, setup_data):
    tenant, admin = setup_data
    r = client.get(
        "/api/analytics/overview",
        params={"year": 2026, "period": "October"},
        headers=auth_headers(admin, tenant),
    )
    assert r.status_code == 200, r.text
    data = r.json()
    assert data["year"] == 2026 and data["period"] == "October"
    assert data["employee_count"] == 3
    assert data["submitted_count"] == 2
    assert data["pending_count"] == 1
    assert data["submission_rate"] == pytest.approx(66.7, abs=0.1)
    assert data["avg_score"] == pytest.approx(75.0)
    # Buckets: 90 -> Performance, 60 -> Normal
    assert data["buckets"] == {"Need to improve": 0, "Normal": 1, "Performance": 1}

    by_dept = {d["department_name"]: d for d in data["by_department"]}
    assert by_dept["Engineering"]["employee_count"] == 2
    assert by_dept["Engineering"]["submitted_count"] == 1
    assert by_dept["Engineering"]["avg_score"] == pytest.approx(90.0)
    assert by_dept["Sales"]["submitted_count"] == 1
    assert by_dept["Sales"]["avg_score"] == pytest.approx(60.0)


def test_overview_rejects_bad_period(client, setup_data):
    tenant, admin = setup_data
    r = client.get(
        "/api/analytics/overview",
        params={"period": "Smarch"},
        headers=auth_headers(admin, tenant),
    )
    assert r.status_code == 422


def test_overview_forbidden_for_employee(client, db):
    tenant = make_tenant(db)
    emp = make_user(db, tenant, UserRole.EMPLOYEE, name="Emp")
    r = client.get("/api/analytics/overview", headers=auth_headers(emp, tenant))
    assert r.status_code == 403


def test_trend_returns_monthly_points(client, setup_data):
    tenant, admin = setup_data
    r = client.get(
        "/api/analytics/trend",
        params={"periods": 2},
        headers=auth_headers(admin, tenant),
    )
    assert r.status_code == 200, r.text
    data = r.json()
    assert len(data) == 2
    by_key = {(p["year"], p["period"]): p for p in data}
    assert by_key[(2026, "October")]["avg_score"] == pytest.approx(75.0)
    assert by_key[(2026, "October")]["submitted_count"] == 2
    assert by_key[(2026, "September")]["avg_score"] == pytest.approx(80.0)


def test_analytics_unauthenticated_401(client):
    assert client.get("/api/analytics/overview").status_code == 401
    assert client.get("/api/analytics/trend").status_code == 401


def test_analytics_dept_admin_forbidden(client, db):
    tenant = make_tenant(db)
    dept = make_department(db, tenant)
    da = make_user(db, tenant, UserRole.DEPT_ADMIN, name="DA", department=dept)
    r = client.get("/api/analytics/overview", headers=auth_headers(da, tenant))
    assert r.status_code == 403
