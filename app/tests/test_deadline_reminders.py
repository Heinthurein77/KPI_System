"""Tests for the deadline reminder service.

Covers: the reminder window, who gets reminded (DRAFT/REJECTED, plus
never-started users who have applicable templates), idempotency (one
reminder per user per period), and the disabled flag.
"""

from datetime import date
from unittest.mock import patch

import pytest

from app.models.kpi_submission import KPIStatus
from app.models.notification import Notification
from app.models.user import UserRole
from app.services import deadline_reminder_service
from app.tests.factories import (
    make_department,
    make_submission,
    make_template,
    make_tenant,
    make_user,
)


def _setup(db, period_month=10, period_name="October"):
    tenant = make_tenant(db)
    dept = make_department(db, tenant)
    emp_draft = make_user(db, tenant, UserRole.EMPLOYEE, name="Draft Guy", department=dept)
    emp_rejected = make_user(db, tenant, UserRole.EMPLOYEE, name="Rejected Gal", department=dept)
    emp_done = make_user(db, tenant, UserRole.EMPLOYEE, name="Done Person", department=dept)
    emp_idle = make_user(db, tenant, UserRole.EMPLOYEE, name="Idle Person", department=dept)
    tpl = make_template(db, tenant, department=dept)
    make_submission(db, tenant, emp_draft, tpl, year=2026, period=period_name, status=KPIStatus.DRAFT)
    make_submission(db, tenant, emp_rejected, tpl, year=2026, period=period_name, status=KPIStatus.REJECTED)
    make_submission(
        db, tenant, emp_done, tpl, year=2026, period=period_name,
        status=KPIStatus.PENDING_DEPT_APPROVAL, self_score=80,
    )
    return tenant, emp_draft, emp_rejected, emp_done, emp_idle


def _notified_user_ids(db, tenant):
    return {
        n.user_id
        for n in db.query(Notification).filter_by(tenant_id=tenant.id, type="deadline_reminder").all()
    }


def test_reminds_draft_rejected_and_never_started_inside_window(db):
    tenant, emp_draft, emp_rejected, emp_done, emp_idle = _setup(db)
    # Oct 29, 2026: 2 days before month end, inside the default 3-day window.
    # emp_idle never opened the app (no submissions) but has an applicable
    # department template, so they get the "haven't started" nudge too.
    with patch.object(deadline_reminder_service, "email_service") as mock_email:
        mock_email.send_for_notifications.return_value = None
        result = deadline_reminder_service.send_deadline_reminders(db, today=date(2026, 10, 29))
    assert result.window_open is True
    assert result.reminded == 3
    notified = _notified_user_ids(db, tenant)
    assert notified == {emp_draft.id, emp_rejected.id, emp_idle.id}


def test_never_started_without_templates_gets_no_reminder(db):
    tenant = make_tenant(db)
    dept = make_department(db, tenant)
    dept_empty = make_department(db, tenant, name="Empty Dept")
    emp_idle = make_user(db, tenant, UserRole.EMPLOYEE, name="Idle Person", department=dept)
    emp_no_kpis = make_user(db, tenant, UserRole.EMPLOYEE, name="No KPIs", department=dept_empty)
    make_template(db, tenant, department=dept)
    # emp_idle has an applicable template but no submissions -> reminded.
    # emp_no_kpis has neither -> no nag.
    with patch.object(deadline_reminder_service, "email_service") as mock_email:
        mock_email.send_for_notifications.return_value = None
        result = deadline_reminder_service.send_deadline_reminders(db, today=date(2026, 10, 29))
    assert result.reminded == 1
    assert _notified_user_ids(db, tenant) == {emp_idle.id}


def test_no_reminders_outside_window(db):
    tenant, *_ = _setup(db)
    # Oct 20: 11 days before month end — outside the window.
    result = deadline_reminder_service.send_deadline_reminders(db, today=date(2026, 10, 20))
    assert result.window_open is False
    assert result.reminded == 0
    assert _notified_user_ids(db, tenant) == set()


def test_idempotent_second_run(db):
    tenant, emp_draft, emp_rejected, _, emp_idle = _setup(db)
    with patch.object(deadline_reminder_service, "email_service") as mock_email:
        mock_email.send_for_notifications.return_value = None
        first = deadline_reminder_service.send_deadline_reminders(db, today=date(2026, 10, 29))
        second = deadline_reminder_service.send_deadline_reminders(db, today=date(2026, 10, 30))
    assert first.reminded == 3
    assert second.reminded == 0
    assert second.already_reminded == 3
    assert len(_notified_user_ids(db, tenant)) == 3


def test_disabled_flag_sends_nothing(db):
    tenant, *_ = _setup(db)
    with patch("app.services.deadline_reminder_service.settings") as mock_settings:
        mock_settings.DEADLINE_REMINDER_ENABLED = False
        mock_settings.DEADLINE_REMINDER_DAYS_BEFORE = 3
        result = deadline_reminder_service.send_deadline_reminders(db, today=date(2026, 10, 29))
    assert result.window_open is False
    assert result.reminded == 0
    assert _notified_user_ids(db, tenant) == set()


def test_tenant_scoping(db):
    tenant_a, *_ = _setup(db)
    tenant_b = make_tenant(db, name="Other", slug="other")
    dept_b = make_department(db, tenant_b, name="Other Dept")
    emp_b = make_user(db, tenant_b, UserRole.EMPLOYEE, name="Other Emp", department=dept_b)
    tpl_b = make_template(db, tenant_b, department=dept_b)
    make_submission(db, tenant_b, emp_b, tpl_b, year=2026, period="October", status=KPIStatus.DRAFT)

    with patch.object(deadline_reminder_service, "email_service") as mock_email:
        mock_email.send_for_notifications.return_value = None
        result = deadline_reminder_service.send_deadline_reminders(
            db, today=date(2026, 10, 29), tenant_id=tenant_b.id
        )
    assert result.reminded == 1
    assert _notified_user_ids(db, tenant_a) == set()
    assert _notified_user_ids(db, tenant_b) == {emp_b.id}


def test_reminder_endpoint_employee_forbidden(client, db):
    from app.tests.factories import auth_headers

    tenant = make_tenant(db)
    emp = make_user(db, tenant, UserRole.EMPLOYEE, name="Emp")
    r = client.post("/api/admin/reminders/deadline", headers=auth_headers(emp, tenant))
    assert r.status_code == 403


def test_reminder_endpoint_unauthenticated_401(client):
    assert client.post("/api/admin/reminders/deadline").status_code == 401


def test_reminder_endpoint_tenant_admin_ok(client, db):
    from app.tests.factories import auth_headers

    tenant = make_tenant(db)
    admin = make_user(db, tenant, UserRole.TENANT_ADMIN, name="Boss")
    # Outside the reminder window -> window_open false, nothing sent.
    import datetime
    from unittest.mock import patch

    with patch("app.services.deadline_reminder_service.date") as mock_date:
        mock_date.today.return_value = datetime.date(2026, 10, 1)
        r = client.post("/api/admin/reminders/deadline", headers=auth_headers(admin, tenant))
    assert r.status_code == 200
    assert r.json()["window_open"] is False
