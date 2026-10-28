"""Tests for the monthly rollover day (submission_grace + gate).

Rule under test: a month's whole KPI set — recurring, department and
company alike — only starts on day SUBMISSION_GRACE_DAYS + 1 (the 8th
with the default 7-day grace), every month, automatically. Days
1..grace_days belong to closing the previous month: no template for the
current month is applicable yet, and the admin rollover endpoint refuses
the current month. Past months (including the grace month) and future
months are unaffected.
"""

import datetime
from datetime import date
from unittest.mock import patch

import pytest
from fastapi import HTTPException

from app.models.user import UserRole
from app.routers.dashboard import _rollover_on
from app.services import kpi_service, submission_grace
from app.tests.factories import (
    auth_headers,
    make_department,
    make_submission,
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


def test_all_templates_gated_until_8th(db):
    _, _, emp, recurring, dept_tpl = _setup_recurring(db)
    # Nov 3: NOTHING for the current month is applicable yet — recurring,
    # department and company templates alike all wait for rollover day.
    templates = kpi_service.applicable_templates(db, emp, 2026, "November", today=date(2026, 11, 3))
    assert templates == []
    # Nov 8: the whole set becomes applicable.
    templates = kpi_service.applicable_templates(db, emp, 2026, "November", today=date(2026, 11, 8))
    assert recurring in templates
    assert dept_tpl in templates


def test_templates_still_applicable_for_grace_month(db):
    _, _, emp, recurring, dept_tpl = _setup_recurring(db)
    # Nov 3: October is the grace month — its templates are unaffected.
    templates = kpi_service.applicable_templates(db, emp, 2026, "October", today=date(2026, 11, 3))
    assert recurring in templates
    assert dept_tpl in templates


def test_ensure_in_rollover_window_returns_empty_without_error(db):
    tenant, _, emp, _, _ = _setup_recurring(db)
    # Nov 3: no drafts materialise for November, and it is NOT the
    # "no metrics configured" error — the month simply has not started.
    assert kpi_service.ensure_period_submissions(db, emp, 2026, "November", today=date(2026, 11, 3)) == []
    # Nov 8: drafts materialise as usual.
    subs = kpi_service.ensure_period_submissions(db, emp, 2026, "November", today=date(2026, 11, 8))
    assert len(subs) == 2


def test_ensure_in_rollover_window_keeps_existing_submissions(db):
    tenant, _, emp, recurring, _ = _setup_recurring(db)
    existing = make_submission(db, tenant, emp, recurring, period="November", self_score=80.0)
    # A draft pre-created for November (e.g. by an admin) stays visible
    # during the rollover window instead of erroring.
    subs = kpi_service.ensure_period_submissions(db, emp, 2026, "November", today=date(2026, 11, 3))
    assert [s.id for s in subs] == [existing.id]


# ---------------------------------------------------------------------------
# Dashboard rollover flag
# ---------------------------------------------------------------------------


def test_rollover_on_flag(db):
    _, _, emp, _, _ = _setup_recurring(db)
    assert _rollover_on(db, emp, 2026, "November", date(2026, 11, 3)) == "2026-11-08"
    assert _rollover_on(db, emp, 2026, "November", date(2026, 11, 8)) is None
    assert _rollover_on(db, emp, 2026, "October", date(2026, 11, 3)) is None
    # An employee with no templates at all gets no flag.
    tenant2 = make_tenant(db, name="Other", slug="other")
    dept2 = make_department(db, tenant2)
    plain = make_user(db, tenant2, UserRole.EMPLOYEE, name="Plain Guy", department=dept2)
    assert _rollover_on(db, plain, 2026, "November", date(2026, 11, 3)) is None
    # An employee with only a department template still gets the flag —
    # the gate now covers every KPI type, not just recurring ones.
    dept_only_emp = make_user(db, tenant2, UserRole.EMPLOYEE, name="Dept Guy", department=dept2)
    make_template(db, tenant2, department=dept2)
    assert _rollover_on(db, dept_only_emp, 2026, "November", date(2026, 11, 3)) == "2026-11-08"


# ---------------------------------------------------------------------------
# The user's stated cycle, month by month (same rule every month)
# ---------------------------------------------------------------------------


def test_monthly_cycle_same_every_month_for_recurring(db):
    """October fillable until Nov 7, November the same way until Dec 7.

    Recurring submissions follow exactly the same grace rule as every other
    KPI type — the pattern repeats monthly with no special-casing.
    """
    tenant, _, emp, recurring, _ = _setup_recurring(db)
    oct_sub = make_submission(db, tenant, emp, recurring, period="October")
    kpi_service.save_self_scores(db, emp, {oct_sub.id: 80}, today=date(2026, 11, 7))
    with pytest.raises(HTTPException):
        kpi_service.save_self_scores(db, emp, {oct_sub.id: 85}, today=date(2026, 11, 8))

    nov_sub = make_submission(db, tenant, emp, recurring, period="November")
    kpi_service.save_self_scores(db, emp, {nov_sub.id: 80}, today=date(2026, 12, 7))
    with pytest.raises(HTTPException):
        kpi_service.save_self_scores(db, emp, {nov_sub.id: 85}, today=date(2026, 12, 8))


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
