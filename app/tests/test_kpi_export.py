"""HTTP coverage for the read-only employee KPI Excel export."""

from io import BytesIO

from openpyxl import load_workbook

from app.models.user import UserRole
from app.tests.factories import auth_headers, make_department, make_submission, make_template, make_tenant, make_user


def test_employee_kpi_export_contains_selected_month_scores(db, client):
    tenant = make_tenant(db)
    admin = make_user(db, tenant, UserRole.TENANT_ADMIN)
    department = make_department(db, tenant)
    employee = make_user(db, tenant, UserRole.EMPLOYEE, department=department, name="Aye Aye")
    template = make_template(db, tenant, metric_name="Sales", target=100.0, weight=100.0, department=department)
    make_submission(
        db, tenant, employee, template, year=2026, period="January",
        self_score=80.0, dept_score=85.0, final_score=90.0,
    )

    response = client.get(
        "/api/admin/kpi-export",
        params={"employee_id": employee.id, "month": "January", "year": 2026},
        headers=auth_headers(admin, tenant),
    )

    assert response.status_code == 200
    assert response.headers["content-type"].startswith(
        "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
    )
    assert "employee-kpi" in response.headers["content-disposition"]

    workbook = load_workbook(BytesIO(response.content), data_only=True)
    sheet = workbook["KPI Report"]
    assert sheet["B3"].value == "Aye Aye"
    assert sheet["E3"].value == "January"
    assert sheet["E4"].value == 2026
    assert sheet["H3"].value == 90
    assert sheet["A8"].value == "Sales"
    assert sheet["B8"].value == 100
    assert sheet["G8"].value == 90


def test_employee_kpi_export_is_department_scoped(db, client):
    tenant = make_tenant(db)
    own_department = make_department(db, tenant, name="Own")
    other_department = make_department(db, tenant, name="Other")
    dept_admin = make_user(db, tenant, UserRole.DEPT_ADMIN, department=own_department)
    employee = make_user(db, tenant, UserRole.EMPLOYEE, department=other_department)

    response = client.get(
        "/api/admin/kpi-export",
        params={"employee_id": employee.id, "month": "January", "year": 2026},
        headers=auth_headers(dept_admin, tenant),
    )

    assert response.status_code == 403
