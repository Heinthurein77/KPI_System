"""HTTP coverage for the required 100% KPI-weight save and submit guard."""

import pytest

from app.models.kpi_submission import KPISubmission, KPIStatus
from app.models.user import UserRole
from app.services.kpi_service import WEIGHT_TOTAL_MESSAGE
from app.tests.factories import auth_headers, make_department, make_tenant, make_template, make_user


@pytest.mark.parametrize("path", ["/api/kpi/employee/save", "/api/kpi/employee/submit"])
def test_employee_kpi_actions_require_total_weight_of_100(db, client, path):
    tenant = make_tenant(db)
    department = make_department(db, tenant)
    employee = make_user(db, tenant, UserRole.EMPLOYEE, department=department)
    make_template(db, tenant, target=100.0, weight=99.9, department=department)

    dashboard = client.get("/api/dashboard", headers=auth_headers(employee, tenant)).json()
    submission_id = dashboard["submissions"][0]["id"]

    response = client.post(
        path,
        json={
            "year": dashboard["active_year"],
            "period": dashboard["active_period"],
            "scores": {str(submission_id): 80.0},
        },
        headers=auth_headers(employee, tenant),
    )

    assert response.status_code == 400
    assert response.json()["detail"] == WEIGHT_TOTAL_MESSAGE
    db.expire_all()
    blocked_submission = db.get(KPISubmission, submission_id)
    assert blocked_submission.self_score is None
    assert blocked_submission.status == KPIStatus.DRAFT


def test_employee_kpi_save_accepts_exact_100_total_weight(db, client):
    tenant = make_tenant(db)
    department = make_department(db, tenant)
    employee = make_user(db, tenant, UserRole.EMPLOYEE, department=department)
    make_template(db, tenant, metric_name="Quality", target=100.0, weight=33.33, department=department)
    make_template(db, tenant, metric_name="Delivery", target=100.0, weight=33.33, department=department)
    make_template(db, tenant, metric_name="Safety", target=100.0, weight=33.34, department=department)

    dashboard = client.get("/api/dashboard", headers=auth_headers(employee, tenant)).json()
    response = client.post(
        "/api/kpi/employee/save",
        json={
            "year": dashboard["active_year"],
            "period": dashboard["active_period"],
            "scores": {str(dashboard["submissions"][0]["id"]): 80.0},
        },
        headers=auth_headers(employee, tenant),
    )

    assert response.status_code == 200
