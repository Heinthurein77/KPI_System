"""M6: admin custom-KPI creation window.

User rule: during days 1–7 of a month, tenant admins AND dept admins may
add the PREVIOUS month's KPI, but not the current month's (its set
materialises on day 8) and not months already past their grace window.
Dept admins stay scoped to their own department's members (existing
endpoint behavior — no role extension was needed). Employees' day-8
rollover is untouched by this gate.
"""

from datetime import date
from unittest.mock import patch

import pytest
from fastapi import HTTPException

from app.models.user import UserRole
from app.services import kpi_service
from app.tests.factories import auth_headers, make_department, make_tenant, make_user

# Day 3 of November 2026: grace week — October is the previous (grace) month.
DAY_3 = date(2026, 11, 3)


def _people(db):
    tenant = make_tenant(db)
    dept = make_department(db, tenant)
    tenant_admin = make_user(db, tenant, UserRole.TENANT_ADMIN, department=dept)
    dept_admin = make_user(db, tenant, UserRole.DEPT_ADMIN, department=dept)
    employee = make_user(db, tenant, UserRole.EMPLOYEE, department=dept)
    return tenant, dept, tenant_admin, dept_admin, employee


def test_previous_month_creation_on_day_3_succeeds_for_tenant_admin(db):
    _, _, tenant_admin, _, employee = _people(db)
    sub = kpi_service.create_custom_employee_kpi(
        db, tenant_admin, employee, "Grace Month KPI", 100.0, 10.0,
        2026, "October", today=DAY_3,
    )
    assert sub.month_or_quarter == "October"
    assert sub.year == 2026


def test_previous_month_creation_on_day_3_succeeds_for_dept_admin(db):
    _, _, _, dept_admin, employee = _people(db)
    sub = kpi_service.create_custom_employee_kpi(
        db, dept_admin, employee, "Grace Month KPI", 100.0, 10.0,
        2026, "October", today=DAY_3,
    )
    assert sub.month_or_quarter == "October"


def test_current_month_creation_on_day_3_rejected(db):
    _, _, tenant_admin, _, employee = _people(db)
    with pytest.raises(HTTPException) as exc_info:
        kpi_service.create_custom_employee_kpi(
            db, tenant_admin, employee, "Too Early KPI", 100.0, 10.0,
            2026, "November", today=DAY_3,
        )
    assert exc_info.value.status_code == 400


def test_past_grace_month_creation_rejected(db):
    _, _, tenant_admin, _, employee = _people(db)
    # September 2026 grace ended Oct 7 — long past on Nov 3.
    with pytest.raises(HTTPException) as exc_info:
        kpi_service.create_custom_employee_kpi(
            db, tenant_admin, employee, "Stale KPI", 100.0, 10.0,
            2026, "September", today=DAY_3,
        )
    assert exc_info.value.status_code == 400


def test_future_month_creation_still_allowed(db):
    _, _, tenant_admin, _, employee = _people(db)
    sub = kpi_service.create_custom_employee_kpi(
        db, tenant_admin, employee, "Pre-created KPI", 100.0, 10.0,
        2026, "December", today=DAY_3,
    )
    assert sub.month_or_quarter == "December"


def test_current_month_creation_on_day_8_allowed(db):
    _, _, tenant_admin, _, employee = _people(db)
    sub = kpi_service.create_custom_employee_kpi(
        db, tenant_admin, employee, "Day 8 KPI", 100.0, 10.0,
        2026, "November", today=date(2026, 11, 8),
    )
    assert sub.month_or_quarter == "November"


def test_endpoint_dept_admin_own_dept_previous_month_on_day_3(db, client):
    tenant, dept, _, dept_admin, employee = _people(db)
    with patch.object(kpi_service, "app_today", return_value=DAY_3):
        resp = client.post(
            "/api/admin/templates/custom",
            json={
                "employee_id": employee.id,
                "metric_name": "Grace Month KPI",
                "target": 50.0,
                "weight": 1.0,
                "year": 2026,
                "period": "October",
            },
            headers=auth_headers(dept_admin, tenant),
        )
    assert resp.status_code == 200, resp.text
    assert resp.json()["metric_name"] == "Grace Month KPI"


def test_endpoint_dept_admin_current_month_on_day_3_rejected(db, client):
    tenant, dept, _, dept_admin, employee = _people(db)
    with patch.object(kpi_service, "app_today", return_value=DAY_3):
        resp = client.post(
            "/api/admin/templates/custom",
            json={
                "employee_id": employee.id,
                "metric_name": "Too Early KPI",
                "target": 50.0,
                "weight": 1.0,
                "year": 2026,
                "period": "November",
            },
            headers=auth_headers(dept_admin, tenant),
        )
    assert resp.status_code == 400, resp.text


def test_endpoint_dept_admin_other_department_rejected(db, client):
    # Scoping runs before the gate, so no date faking is needed here:
    # the 403 must surface even for a period the gate would allow.
    tenant = make_tenant(db)
    dept = make_department(db, tenant, name="Mine")
    other_dept = make_department(db, tenant, name="Theirs")
    dept_admin = make_user(db, tenant, UserRole.DEPT_ADMIN, department=dept)
    other_employee = make_user(db, tenant, UserRole.EMPLOYEE, department=other_dept)
    with patch.object(kpi_service, "app_today", return_value=DAY_3):
        resp = client.post(
            "/api/admin/templates/custom",
            json={
                "employee_id": other_employee.id,
                "metric_name": "Sneaky",
                "target": 10.0,
                "weight": 1.0,
                "year": 2026,
                "period": "October",
            },
            headers=auth_headers(dept_admin, tenant),
        )
    assert resp.status_code == 403
