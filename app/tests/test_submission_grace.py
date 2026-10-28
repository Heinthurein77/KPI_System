"""Tests for the 7-day submission grace period (submission_grace.py).

Rule under test: after a month ends, employees may still enter scores for
that month for SUBMISSION_GRACE_DAYS (default 7) more days; after that the
month closes for first-time (DRAFT) entry. A REJECTED submission may still
be corrected and resubmitted after grace, and dept/final approval paths
are untouched by the rule.
"""

from datetime import date
from unittest.mock import patch

import pytest
from fastapi import HTTPException

from app.models.kpi_submission import KPIStatus
from app.models.user import UserRole
from app.routers.dashboard import _fillable_state
from app.services import deadline_reminder_service, submission_grace
from app.services import kpi_service
from app.tests.factories import (
    make_department,
    make_submission,
    make_template,
    make_tenant,
    make_user,
)

GRACE = 7  # default SUBMISSION_GRACE_DAYS


def _setup_draft(db, period="October", year=2026, status=KPIStatus.DRAFT, score=None):
    tenant = make_tenant(db)
    dept = make_department(db, tenant)
    emp = make_user(db, tenant, UserRole.EMPLOYEE, name="Grace Guy", department=dept)
    tpl = make_template(db, tenant, department=dept)
    sub = make_submission(
        db, tenant, emp, tpl, year=year, period=period, status=status, self_score=score
    )
    return tenant, emp, sub


# ---------------------------------------------------------------------------
# Date math
# ---------------------------------------------------------------------------


def test_period_end_and_grace_end():
    assert submission_grace.period_end(2026, "October") == date(2026, 10, 31)
    assert submission_grace.grace_end(2026, "October", GRACE) == date(2026, 11, 7)
    # Leap-year February.
    assert submission_grace.period_end(2024, "February") == date(2024, 2, 29)
    assert submission_grace.grace_end(2024, "February", GRACE) == date(2024, 3, 7)
    # December rolls into January of the next year.
    assert submission_grace.grace_end(2026, "December", GRACE) == date(2027, 1, 7)


def test_unparseable_period_is_left_alone():
    assert submission_grace.period_end(2026, "Q1") is None
    assert submission_grace.grace_end(2026, "Q1", GRACE) is None
    assert submission_grace.is_within_grace(2026, "Q1", date(2030, 1, 1), GRACE) is True
    submission_grace.assert_employee_window_open(2026, "Q1", date(2030, 1, 1), GRACE)


def test_is_grace_month():
    # Nov 1-7: October is the grace month; Nov 8: it is closed.
    assert submission_grace.is_grace_month(2026, "October", date(2026, 11, 1), GRACE) is True
    assert submission_grace.is_grace_month(2026, "October", date(2026, 11, 7), GRACE) is True
    assert submission_grace.is_grace_month(2026, "October", date(2026, 11, 8), GRACE) is False
    # The current month is never a grace month; future months are not either.
    assert submission_grace.is_grace_month(2026, "November", date(2026, 11, 3), GRACE) is False
    assert submission_grace.is_grace_month(2026, "December", date(2026, 11, 3), GRACE) is False
    # Year rollover: Jan 5, 2027 — December 2026 is the grace month.
    assert submission_grace.is_grace_month(2026, "December", date(2027, 1, 5), GRACE) is True


def test_fillable_state():
    fillable, grace_month, ends_on = _fillable_state(2026, "October", date(2026, 11, 3))
    assert fillable is True
    assert grace_month is True
    assert ends_on == "2026-11-07"
    fillable, grace_month, ends_on = _fillable_state(2026, "October", date(2026, 11, 8))
    assert fillable is False
    assert grace_month is False
    assert ends_on is None
    fillable, grace_month, _ = _fillable_state(2026, "November", date(2026, 11, 3))
    assert fillable is True
    assert grace_month is False


# ---------------------------------------------------------------------------
# Employee save / submit enforcement
# ---------------------------------------------------------------------------


def test_save_draft_blocked_after_grace(db):
    _, emp, sub = _setup_draft(db)
    with pytest.raises(HTTPException) as exc:
        kpi_service.save_self_scores(db, emp, {sub.id: 80}, today=date(2026, 11, 8))
    assert exc.value.status_code == 400
    assert "closed on 2026-11-07" in exc.value.detail


def test_save_draft_allowed_on_grace_end_day(db):
    _, emp, sub = _setup_draft(db)
    kpi_service.save_self_scores(db, emp, {sub.id: 80}, today=date(2026, 11, 7))
    db.refresh(sub)
    assert sub.self_score == 80


def test_save_rejected_allowed_after_grace(db):
    _, emp, sub = _setup_draft(db, status=KPIStatus.REJECTED)
    kpi_service.save_self_scores(db, emp, {sub.id: 70}, today=date(2026, 11, 20))
    db.refresh(sub)
    assert sub.self_score == 70


def test_submit_draft_blocked_after_grace(db):
    _, emp, _ = _setup_draft(db, score=80)
    with pytest.raises(HTTPException) as exc:
        kpi_service.submit_for_dept_approval(db, emp, 2026, "October", today=date(2026, 11, 8))
    assert exc.value.status_code == 400


def test_submit_rejected_only_allowed_after_grace(db):
    _, emp, sub = _setup_draft(db, status=KPIStatus.REJECTED, score=70)
    kpi_service.submit_for_dept_approval(db, emp, 2026, "October", today=date(2026, 11, 20))
    db.refresh(sub)
    assert sub.status == KPIStatus.PENDING_DEPT_APPROVAL


def test_submit_within_grace_allowed(db):
    _, emp, sub = _setup_draft(db, score=80)
    kpi_service.submit_for_dept_approval(db, emp, 2026, "October", today=date(2026, 11, 5))
    db.refresh(sub)
    assert sub.status == KPIStatus.PENDING_DEPT_APPROVAL


# ---------------------------------------------------------------------------
# Reminder window extension into the grace week
# ---------------------------------------------------------------------------


def test_reminder_target_during_grace_week():
    year, period, in_grace, window_open = submission_grace.resolve_reminder_target(
        date(2026, 11, 3), days_before=7, grace_days=GRACE
    )
    assert (year, period, in_grace, window_open) == (2026, "October", True, True)


def test_reminder_target_after_grace_closed():
    _, _, _, window_open = submission_grace.resolve_reminder_target(
        date(2026, 11, 8), days_before=7, grace_days=GRACE
    )
    assert window_open is False


def test_reminders_fire_for_previous_month_during_grace(db):
    tenant = make_tenant(db)
    dept = make_department(db, tenant)
    emp = make_user(db, tenant, UserRole.EMPLOYEE, name="Late Guy", department=dept)
    tpl = make_template(db, tenant, department=dept)
    make_submission(db, tenant, emp, tpl, year=2026, period="October", status=KPIStatus.DRAFT)
    with patch.object(deadline_reminder_service, "email_service") as mock_email:
        mock_email.send_for_notifications.return_value = None
        result = deadline_reminder_service.send_deadline_reminders(db, today=date(2026, 11, 3))
    assert result.window_open is True
    assert result.year == 2026
    assert result.period == "October"
    assert result.reminded == 1


def test_reminders_stop_after_grace(db):
    tenant = make_tenant(db)
    dept = make_department(db, tenant)
    emp = make_user(db, tenant, UserRole.EMPLOYEE, name="Late Guy", department=dept)
    tpl = make_template(db, tenant, department=dept)
    make_submission(db, tenant, emp, tpl, year=2026, period="October", status=KPIStatus.DRAFT)
    with patch.object(deadline_reminder_service, "email_service") as mock_email:
        mock_email.send_for_notifications.return_value = None
        result = deadline_reminder_service.send_deadline_reminders(db, today=date(2026, 11, 8))
    assert result.window_open is False
    assert result.reminded == 0
