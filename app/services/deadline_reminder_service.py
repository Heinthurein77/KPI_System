"""Deadline reminder service — nudge employees who haven't submitted their KPI.

This module is additive-only: it never modifies any existing submission,
template, approval flow, formula, or routing logic. It only *reads* the
current period's submissions and creates new ``deadline_reminder``
notifications (in-app + email) for employees who still have actionable work:

- DRAFT submissions  → started but not yet submitted
- REJECTED submissions → sent back, needs correction and resubmission
- NO submissions yet but applicable templates exist → never opened the app
  this period (no lazily created rows); these are exactly the people the
  nudge exists for

Public surface
--------------
send_deadline_reminders(db, today=None, tenant_id=None)
    The core function. Call it from:
      - the POST /api/admin/reminders/deadline endpoint (on demand, one tenant)
      - app/jobs/send_deadline_reminders.py                    (cron, all tenants)

Idempotency: before reminding a user, we check for an existing
``deadline_reminder`` notification pointing at the same dashboard period link,
so a daily cron never spams anyone twice for the same period.

Window: reminders go out within DEADLINE_REMINDER_DAYS_BEFORE days of
the end of the current calendar month, and also during the submission
grace week after a month ends (targeting that previous month) — see
``submission_grace.resolve_reminder_target``.
"""

from __future__ import annotations

import calendar
from dataclasses import dataclass, field
from datetime import date

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.config import settings
from app.models.kpi_submission import KPIStatus, KPISubmission
from app.models.notification import Notification
from app.models.tenant import Tenant, TenantStatus
from app.models.user import User, UserRole
from app.services import email_service, notification_service
from app.services import submission_grace
from app.services.kpi_service import applicable_templates

REMINDER_TYPE = "deadline_reminder"

# Statuses that mean "you still have something to do this period".
ACTIONABLE_STATUSES = (KPIStatus.DRAFT, KPIStatus.REJECTED)

@dataclass
class DeadlineReminderResult:
    window_open: bool
    year: int
    period: str
    reminded: int = 0
    already_reminded: int = 0
    per_tenant: dict = field(default_factory=dict)


def _days_until_month_end(today: date) -> int:
    last_day = calendar.monthrange(today.year, today.month)[1]
    return last_day - today.day


def _already_reminded(db: Session, user_id: int, link: str) -> bool:
    return (
        db.scalar(
            select(Notification.id).where(
                Notification.user_id == user_id,
                Notification.type == REMINDER_TYPE,
                Notification.link == link,
            )
        )
        is not None
    )


def send_deadline_reminders(
    db: Session,
    today: date | None = None,
    tenant_id: int | None = None,
) -> DeadlineReminderResult:
    """Remind employees with DRAFT/REJECTED submissions for the current period.

    Args:
        db: database session (caller commits).
        today: override for testing; defaults to date.today().
        tenant_id: scope to one tenant; None covers every tenant.
    """
    today = today or date.today()
    # Reminder target: the current month inside its pre-month-end window,
    # or the previous month while its submission grace window is still open
    # (so employees keep getting nudged while they can still act).
    year, period, in_grace, window_open = submission_grace.resolve_reminder_target(
        today,
        settings.DEADLINE_REMINDER_DAYS_BEFORE,
        settings.SUBMISSION_GRACE_DAYS,
    )
    result = DeadlineReminderResult(
        window_open=False, year=year, period=period
    )

    if not settings.DEADLINE_REMINDER_ENABLED:
        return result
    if not window_open:
        return result
    result.window_open = True

    link = f"/dashboard?year={year}&period={period}"
    if in_grace:
        grace_end = submission_grace.grace_end(
            year, period, settings.SUBMISSION_GRACE_DAYS
        )
        days_left = (grace_end - today).days
    else:
        days_left = _days_until_month_end(today)
    when = "today" if days_left == 0 else f"in {days_left} day{'s' if days_left != 1 else ''}"

    tenant_ids: list[int]
    if tenant_id is not None:
        tenant_ids = [tenant_id]
    else:
        # Suspended tenants get no reminders.
        tenant_ids = list(
            db.scalars(select(Tenant.id).where(Tenant.status == TenantStatus.ACTIVE))
        )

    created_all: list = []
    actionable = set(ACTIONABLE_STATUSES)
    for tid in tenant_ids:
        # Every active employee AND dept admin (who also self-assess) in the
        # tenant — not just the ones who already opened the app this period.
        # Users who never opened it have no (lazily created) submissions yet;
        # they are exactly the people this nudge exists for.
        users = db.scalars(
            select(User).where(
                User.tenant_id == tid,
                User.is_active == True,  # noqa: E712
                User.role.in_([UserRole.EMPLOYEE, UserRole.DEPT_ADMIN]),
            )
        ).all()

        # This period's submission statuses, grouped by employee.
        statuses_by_user: dict[int, set[KPIStatus]] = {}
        for emp_id, st in db.execute(
            select(KPISubmission.employee_id, KPISubmission.status).where(
                KPISubmission.tenant_id == tid,
                KPISubmission.year == year,
                KPISubmission.month_or_quarter == period,
            )
        ).all():
            statuses_by_user.setdefault(emp_id, set()).add(st)

        tenant_reminded = 0
        for user in users:
            statuses = statuses_by_user.get(user.id, set())
            if statuses and not (statuses & actionable):
                continue  # nothing actionable — already submitted/approved
            if _already_reminded(db, user.id, link):
                result.already_reminded += 1
                continue
            if not statuses:
                # Never started this period (no lazy-created rows). Only nudge
                # when they actually have KPIs to do — no templates, no nag.
                if not applicable_templates(db, user, year, period):
                    continue
                if in_grace:
                    title = f"KPI submission grace period ends {when}"
                    message = (
                        f"Your {period} {year} KPI grace period ends {when}, and "
                        "you haven't started it yet. Please open My KPI, enter "
                        "your scores and submit it for review."
                    )
                else:
                    title = f"KPI submission due {when}"
                    message = (
                        f"Your {period} {year} KPI is due {when}, and you haven't "
                        "started it yet. Please open My KPI, enter your scores and "
                        "submit it for review."
                    )
            else:
                if in_grace:
                    title = f"KPI submission grace period ends {when}"
                    message = (
                        f"Your {period} {year} KPI grace period ends {when}. "
                        "Please finish your scores and submit it for review."
                    )
                else:
                    title = f"KPI submission due {when}"
                    message = (
                        f"Your {period} {year} KPI is due {when}. "
                        "Please finish your scores and submit it for review."
                    )
            created = notification_service.notify_user(
                db,
                tenant_id=tid,
                user_id=user.id,
                type=REMINDER_TYPE,
                title=title,
                message=message,
                link=link,
            )
            created_all.extend(created)
            result.reminded += 1
            tenant_reminded += 1
        result.per_tenant[tid] = tenant_reminded

    db.commit()
    # Emails go out after the commit, best-effort (never raises).
    email_service.send_for_notifications(db, created_all)
    return result
