"""Deadline reminder service — nudge employees who haven't submitted their KPI.

This module is additive-only: it never modifies any existing submission,
template, approval flow, formula, or routing logic. It only *reads* the
current period's submissions and creates new ``deadline_reminder``
notifications (in-app + email) for employees who still have actionable work:

- DRAFT submissions  → started but not yet submitted
- REJECTED submissions → sent back, needs correction and resubmission

Public surface
--------------
send_deadline_reminders(db, today=None, tenant_id=None)
    The core function. Call it from:
      - the POST /api/admin/reminders/deadline endpoint (on demand, one tenant)
      - app/jobs/send_deadline_reminders.py                    (cron, all tenants)

Idempotency: before reminding a user, we check for an existing
``deadline_reminder`` notification pointing at the same dashboard period link,
so a daily cron never spams anyone twice for the same period.

Window: reminders only go out within DEADLINE_REMINDER_DAYS_BEFORE days of
the end of the current calendar month (configurable, default 3).
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
from app.models.tenant import Tenant
from app.models.user import User, UserRole
from app.services import email_service, notification_service

REMINDER_TYPE = "deadline_reminder"

# Statuses that mean "you still have something to do this period".
ACTIONABLE_STATUSES = (KPIStatus.DRAFT, KPIStatus.REJECTED)

MONTH_NAMES = (
    "January", "February", "March", "April", "May", "June",
    "July", "August", "September", "October", "November", "December",
)


@dataclass
class DeadlineReminderResult:
    window_open: bool
    year: int
    period: str
    reminded: int = 0
    already_reminded: int = 0
    per_tenant: dict = field(default_factory=dict)


def _current_year_period(today: date) -> tuple[int, str]:
    return today.year, MONTH_NAMES[today.month - 1]


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
    year, period = _current_year_period(today)
    result = DeadlineReminderResult(
        window_open=False, year=year, period=period
    )

    if not settings.DEADLINE_REMINDER_ENABLED:
        return result
    if _days_until_month_end(today) > settings.DEADLINE_REMINDER_DAYS_BEFORE:
        return result
    result.window_open = True

    link = f"/dashboard?year={year}&period={period}"
    days_left = _days_until_month_end(today)
    when = "today" if days_left == 0 else f"in {days_left} day{'s' if days_left != 1 else ''}"

    tenant_ids: list[int]
    if tenant_id is not None:
        tenant_ids = [tenant_id]
    else:
        tenant_ids = list(db.scalars(select(Tenant.id)))

    created_all: list = []
    for tid in tenant_ids:
        # Active employees with at least one actionable submission this period.
        user_ids = db.scalars(
            select(KPISubmission.employee_id)
            .join(User, User.id == KPISubmission.employee_id)
            .where(
                KPISubmission.tenant_id == tid,
                KPISubmission.year == year,
                KPISubmission.month_or_quarter == period,
                KPISubmission.status.in_(ACTIONABLE_STATUSES),
                User.is_active == True,  # noqa: E712
                User.role == UserRole.EMPLOYEE,
            )
            .distinct()
        ).all()

        tenant_reminded = 0
        for user_id in user_ids:
            if _already_reminded(db, user_id, link):
                result.already_reminded += 1
                continue
            created = notification_service.notify_user(
                db,
                tenant_id=tid,
                user_id=user_id,
                type=REMINDER_TYPE,
                title=f"KPI submission due {when}",
                message=(
                    f"Your {period} {year} KPI is due {when}. "
                    "Please finish your scores and submit it for review."
                ),
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
