"""Direct service-level tests for the KPI Excel exporters.

HTTP coverage lives in test_kpi_export.py; these tests call the builders
directly so formula-guard wiring can be proven with hostile values the
HTTP layer could never produce (e.g. a status string starting with "=").
"""

from io import BytesIO

from openpyxl import load_workbook

from app.models.user import UserRole
from app.services.kpi_export_service import (
    build_employee_annual_kpi_report,
    build_employee_kpi_report,
)
from app.tests.factories import (
    make_department,
    make_submission,
    make_template,
    make_tenant,
    make_user,
)


def _setup(db):
    tenant = make_tenant(db)
    department = make_department(db, tenant)
    employee = make_user(db, tenant, UserRole.EMPLOYEE, department=department, name="Aye Aye")
    template = make_template(db, tenant, metric_name="Sales", target=100.0, weight=100.0, department=department)
    return tenant, department, employee, template


def test_monthly_report_sanitizes_formula_like_metric_name(db):
    _tenant, _dept, employee, template = _setup(db)
    template.metric_name = "=2+2"
    db.commit()
    make_submission(db, _tenant, employee, template, year=2026, period="January",
                    self_score=80.0, dept_score=85.0, final_score=90.0)

    from sqlalchemy import select

    from app.models.kpi_submission import KPISubmission

    submissions = db.scalars(select(KPISubmission)).all()
    output = build_employee_kpi_report(
        employee, submissions, 2026, "January", {"attainment": 90, "status": "Performance"},
    )

    sheet = load_workbook(BytesIO(output.getvalue()), data_only=True)["KPI Report"]
    assert sheet["A8"].value == "'=2+2"


def test_annual_summary_sanitizes_formula_like_status(db):
    _tenant, _dept, employee, template = _setup(db)
    make_submission(db, _tenant, employee, template, year=2026, period="January",
                    self_score=80.0, dept_score=85.0, final_score=90.0)

    from sqlalchemy import select

    from app.models.kpi_submission import KPISubmission

    submissions = db.scalars(select(KPISubmission)).all()
    monthly_scores = {
        month: None for month in
        ["January", "February", "March", "April", "May", "June",
         "July", "August", "September", "October", "November", "December"]
    }
    # A hostile status string in the annual summary table: the one write
    # in the export layer that used to bypass _safe() (T-m1).
    monthly_scores["January"] = {"attainment": 90, "scored_count": 1, "status": "=2+2"}

    output = build_employee_annual_kpi_report(
        employee, submissions, 2026, list(monthly_scores), monthly_scores,
    )

    workbook = load_workbook(BytesIO(output.getvalue()), data_only=True)
    summary = workbook["Annual Summary"]
    assert summary["A8"].value == "January"
    assert summary["E8"].value == "'=2+2"
    # The January detail sheet keeps working alongside the summary.
    assert workbook["January"]["A8"].value == "Sales"


def test_annual_report_with_no_submissions_still_builds(db):
    _tenant, _dept, employee, _template = _setup(db)
    months = ["January", "February", "March", "April", "May", "June",
              "July", "August", "September", "October", "November", "December"]
    monthly_scores = {month: None for month in months}

    output = build_employee_annual_kpi_report(employee, [], 2026, months, monthly_scores)

    workbook = load_workbook(BytesIO(output.getvalue()), data_only=True)
    summary = workbook["Annual Summary"]
    assert summary["B3"].value == "Aye Aye"
    assert summary["H3"].value == "N/A"
    assert summary["A8"].value == "January"
    assert workbook["January"]["A8"].value == "No KPI records for this month."
