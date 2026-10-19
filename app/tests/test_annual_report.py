"""Tests for the annual KPI appraisal PDF report."""

from app.models.kpi_submission import KPIStatus
from app.models.user import UserRole
from app.services import annual_report as annual_report_service
from app.tests.factories import (
    auth_headers,
    make_department,
    make_submission,
    make_template,
    make_tenant,
    make_user,
)


def _setup(db):
    tenant = make_tenant(db)
    dept = make_department(db, tenant)
    other_dept = make_department(db, tenant, name="Other")
    tadmin = make_user(db, tenant, UserRole.TENANT_ADMIN, name="TA")
    dadmin = make_user(db, tenant, UserRole.DEPT_ADMIN, name="DA", department=dept)
    emp = make_user(db, tenant, UserRole.EMPLOYEE, name="Emp", department=dept)
    emp2 = make_user(db, tenant, UserRole.EMPLOYEE, name="Emp2", department=other_dept)
    tpl = make_template(db, tenant, department=dept, metric_name="Sales", target=100.0)
    # Jan: 80/100 -> 80%; Feb: 90/100 -> 90% (both approved)
    s1 = make_submission(db, tenant, emp, tpl, year=2026, period="January",
                         status=KPIStatus.APPROVED, self_score=80, dept_score=80,
                         final_score=80)
    s1.remarks = "Good start"
    db.commit()
    make_submission(db, tenant, emp, tpl, year=2026, period="February",
                    status=KPIStatus.APPROVED, self_score=90, dept_score=90,
                    final_score=90)
    return locals()


def test_tenant_admin_downloads_pdf(client, db):
    w = _setup(db)
    r = client.get(f"/api/reports/annual/{w['emp'].id}",
                   params={"year": 2026},
                   headers=auth_headers(w["tadmin"], w["tenant"]))
    assert r.status_code == 200, r.text
    assert r.headers["content-type"] == "application/pdf"
    assert r.content[:5] == b"%PDF-"
    assert "attachment" in r.headers["content-disposition"]


def test_report_data_correctness(db):
    w = _setup(db)
    report = annual_report_service.build_annual_report(
        db, w["tenant"].id, w["emp"].id, 2026)
    assert report.employee_name == "Emp"
    assert report.yearly_avg_attainment == 85.0
    assert report.months_with_data == 2
    assert report.best_month == "February (90.0%)"
    jan = report.months[0]
    assert jan.period == "January" and jan.avg_attainment == 80.0
    assert len(report.metrics) == 1
    assert report.metrics[0].metric_name == "Sales"
    assert report.metrics[0].avg_attainment == 85.0
    assert report.remarks == ["Good start"]


def test_dept_admin_own_dept_ok_other_dept_forbidden(client, db):
    w = _setup(db)
    h = auth_headers(w["dadmin"], w["tenant"])
    r = client.get(f"/api/reports/annual/{w['emp'].id}", params={"year": 2026}, headers=h)
    assert r.status_code == 200, r.text
    r = client.get(f"/api/reports/annual/{w['emp2'].id}", params={"year": 2026}, headers=h)
    assert r.status_code == 403, r.text


def test_employee_forbidden_and_unknown_employee_404(client, db):
    w = _setup(db)
    h = auth_headers(w["emp"], w["tenant"])
    r = client.get(f"/api/reports/annual/{w['emp'].id}", params={"year": 2026}, headers=h)
    assert r.status_code == 403, r.text
    r = client.get("/api/reports/annual/99999", params={"year": 2026},
                   headers=auth_headers(w["tadmin"], w["tenant"]))
    assert r.status_code == 404, r.text


def test_year_validated(client, db):
    w = _setup(db)
    r = client.get(f"/api/reports/annual/{w['emp'].id}", params={"year": 1999},
                   headers=auth_headers(w["tadmin"], w["tenant"]))
    assert r.status_code == 422, r.text


def test_unauthenticated_blocked(client, db):
    w = _setup(db)
    r = client.get(f"/api/reports/annual/{w['emp'].id}", params={"year": 2026})
    assert r.status_code == 401, r.text


def test_empty_year_produces_valid_pdf(db):
    w = _setup(db)
    report = annual_report_service.build_annual_report(
        db, w["tenant"].id, w["emp"].id, 2025)
    assert report.yearly_avg_attainment is None
    assert report.months_with_data == 0
    pdf = annual_report_service.render_pdf(report)
    assert pdf[:5] == b"%PDF-"
