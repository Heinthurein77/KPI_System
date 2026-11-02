"""HTTP coverage for the KPI-weight save and submit guard.

Production rule (see validate_period_total_weight): a period's weights
must not EXCEED 100 — under-allocation is intentional and valid. So the
blocking case is a total over 100, not a total under it.
"""

import pytest

from app.models.kpi_submission import KPISubmission, KPIStatus
from app.models.user import UserRole
from app.services.kpi_service import WEIGHT_TOTAL_MESSAGE
from app.tests.factories import auth_headers, make_department, make_tenant, make_template, make_user, open_fillable_period


@pytest.mark.parametrize("path", ["/api/kpi/employee/save", "/api/kpi/employee/submit"])
def test_employee_kpi_actions_block_weight_total_over_100(db, client, path):
    tenant = make_tenant(db)
    department = make_department(db, tenant)
    employee = make_user(db, tenant, UserRole.EMPLOYEE, department=department)
    make_template(db, tenant, target=100.0, weight=100.1, department=department)

    oy, op = open_fillable_period()
    dashboard = client.get("/api/dashboard", params={"year": oy, "period": op}, headers=auth_headers(employee, tenant)).json()
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

    oy, op = open_fillable_period()
    dashboard = client.get("/api/dashboard", params={"year": oy, "period": op}, headers=auth_headers(employee, tenant)).json()
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
