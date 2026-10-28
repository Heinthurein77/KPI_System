"""Tests for the recurring rollover day (submission_grace + gate).

Rule under test: a month's recurring KPIs only start on day
SUBMISSION_GRACE_DAYS + 1 (the 8th with the default 7-day grace), every
month, automatically. Days 1..grace_days belong to closing the previous
month: the current month's recurring templates are not applicable yet,
and the admin rollover endpoint refuses the current month. Past months
(including the grace month) and future months are unaffected.
"""

import datetime
from datetime import date
from unittest.mock import patch

import pytest
from fastapi import HTTPException

from app.models.user import UserRole
from app.routers.dashboard import _recurring_rollover_on
from app.services import kpi_service, submission_grace
from app.tests.factories import (
    auth_headers,
    make_department,
    make_template,
    make_tenant,
    make_user,
)

GRACE = 7  # default SUBMISSION_GRACE_DAYS


def _setup_recurring(db):
    tenant = make_tenant(db)
    dept = make_department(db, tenant)
    emp = make_user(db, tenant, UserRole.EMPLOYEE, name="Recurring Guy", department=dept)
    recurring = make_template(
        db, tenant, employee=emp, is_recurring=True,
        locked_year=2026, locked_period="September",
    )
    dept_tpl = make_template(db, tenant, department=dept)
    return tenant, dept, emp, recurring, dept_tpl


# ---------------------------------------------------------------------------
# Rollover-day helpers
# ---------------------------------------------------------------------------


def test_rollover_open_and_date():
    assert submission_grace.recurring_rollover_open(date(2026, 11, 7), GRACE) is False
    assert submission_grace.recurring_rollover_open(date(2026, 11, 8), GRACE) is True
    assert submission_grace.recurring_rollover_date(date(2026, 11, 3), GRACE) == date(2026, 11, 8)
    assert submission_grace.recurring_rollover_date(date(2026, 11, 8), GRACE) is None


def test_assert_rollover_open():
    with pytest.raises(HTTPException) as exc:
        submission_grace.assert_recurring_rollover_open(2026, "November", date(2026, 11, 3), GRACE)
    assert exc.value.status_code == 400
    assert "2026-11-08" in exc.value.detail
    # From the 8th onward it passes.
    submission_grace.assert_recurring_rollover_open(2026, "November", date(2026, 11, 8), GRACE)
    # Past months are never gated, even during the first week.
    submission_grace.assert_recurring_rollover_open(2026, "October", date(2026, 11, 3), GRACE)
    # Future months are never gated either.
    submission_grace.assert_recurring_rollover_open(2026, "December", date(2026, 11, 3), GRACE)


# ---------------------------------------------------------------------------
# Lazy path: applicable_templates gate
# ---------------------------------------------------------------------------


def test_recurring_template_gated_until_8th(db):
    _, _, emp, recurring, dept_tpl = _setup_recurring(db)
    # Nov 3: current month's recurring template is NOT applicable yet,
    # but the department template still is.
    templates = kpi_service.applicable_templates(db, emp, 2026, "November", today=date(2026, 11, 3))
    assert recurring not in templates
    assert dept_tpl in templates
    # Nov 8: the recurring template becomes applicable.
    templates = kpi_service.applicable_templates(db, emp, 2026, "November", today=date(2026, 11, 8))
    assert recurring in templates


def test_recurring_template_still_applicable_for_grace_month(db):
    _, _, emp, recurring, _ = _setup_recurring(db)
    # Nov 3: October is the grace month — its recurring template is unaffected.
    templates = kpi_service.applicable_templates(db, emp, 2026, "October", today=date(2026, 11, 3))
    assert recurring in templates


# ---------------------------------------------------------------------------
# Dashboard rollover flag
# ---------------------------------------------------------------------------


def test_recurring_rollover_on_flag(db):
    _, _, emp, _, _ = _setup_recurring(db)
    assert _recurring_rollover_on(db, emp, 2026, "November", date(2026, 11, 3)) == "2026-11-08"
    assert _recurring_rollover_on(db, emp, 2026, "November", date(2026, 11, 8)) is None
    assert _recurring_rollover_on(db, emp, 2026, "October", date(2026, 11, 3)) is None
    # An employee with no recurring templates gets no flag.
    tenant2 = make_tenant(db, name="Other", slug="other")
    dept2 = make_department(db, tenant2)
    plain = make_user(db, tenant2, UserRole.EMPLOYEE, name="Plain Guy", department=dept2)
    assert _recurring_rollover_on(db, plain, 2026, "November", date(2026, 11, 3)) is None


# ---------------------------------------------------------------------------
# Admin endpoint guard
# ---------------------------------------------------------------------------


def test_recurring_run_endpoint_refuses_current_month_before_8th(db, client):
    tenant = make_tenant(db)
    admin = make_user(db, tenant, UserRole.TENANT_ADMIN, name="Boss")
    with patch("app.routers.admin.date") as mock_date:
        mock_date.today.return_value = datetime.date(2026, 11, 3)
        r = client.post(
            "/api/admin/recurring/run",
            json={"year": 2026, "period": "November"},
            headers=auth_headers(admin, tenant),
        )
    assert r.status_code == 400
    assert "2026-11-08" in r.json()["detail"]


def test_recurring_run_endpoint_allows_from_8th(db, client):
    tenant = make_tenant(db)
    admin = make_user(db, tenant, UserRole.TENANT_ADMIN, name="Boss")
    with patch("app.routers.admin.date") as mock_date:
        mock_date.today.return_value = datetime.date(2026, 11, 8)
        r = client.post(
            "/api/admin/recurring/run",
            json={"year": 2026, "period": "November"},
            headers=auth_headers(admin, tenant),
        )
    assert r.status_code == 200
