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
import logging
from dataclasses import dataclass, field
from datetime import date

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.config import settings
from app.core.timezone import today as app_today
from app.models.kpi_submission import KPIStatus, KPISubmission
from app.models.notification import Notification
from app.models.tenant import Tenant, TenantStatus
from app.models.user import User, UserRole
from app.services import email_service, notification_service
from app.services import submission_grace
from app.services.kpi_service import applicable_templates

REMINDER_TYPE = "deadline_reminder"

logger = logging.getLogger(__name__)

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
    # Per-tenant failures (a tenant whose reminders errored, or emails
    # that failed in sync mode and will be retried next run).
    errors: list = field(default_factory=list)


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
    sync_email: bool = False,
) -> DeadlineReminderResult:
    """Remind employees with DRAFT/REJECTED submissions for the current period.

    Args:
        db: database session (this function commits per tenant).
        today: override for testing; defaults to the app timezone date (see app/core/timezone.py).
        tenant_id: scope to one tenant; None covers every tenant.
        sync_email: when True (the cron path), emails are sent
            synchronously and a failed email's notification row is deleted,
            so the next run retries it instead of the failure being lost
            behind the idempotency marker. When False (the on-demand
            endpoint), emails go out in a background thread as before.
    """
    today = today or app_today()
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
        try:
            tenant_created, tenant_reminded, tenant_already = _remind_tenant(
                db, tid, year, period, in_grace, when, link, actionable
            )
        except Exception:
            # One tenant's failure must not lose every other tenant's
            # reminders: roll back just this tenant and keep going.
            db.rollback()
            logger.exception("Deadline reminders failed for tenant %s; continuing", tid)
            result.errors.append({"tenant_id": tid, "error": "see server log"})
            result.per_tenant[tid] = 0
            continue
        db.commit()
        result.reminded += tenant_reminded
        result.already_reminded += tenant_already
        result.per_tenant[tid] = tenant_reminded
        if sync_email:
            failed = email_service.send_for_notifications_sync(db, tenant_created)
            if failed:
                # Idempotency is only marked AFTER delivery: drop the rows
                # whose email failed, so the next run retries them (both
                # the in-app notification and the email) instead of the
                # failure being silently lost forever.
                email_to_notes: dict[str, list] = {}
                for n in tenant_created:
                    u = db.get(User, n.user_id)
                    if u is not None and u.email:
                        email_to_notes.setdefault(u.email, []).append(n)
                dropped = 0
                for email in failed:
                    for n in email_to_notes.get(email, []):
                        db.delete(n)
                        dropped += 1
                db.commit()
                result.reminded -= dropped
                result.per_tenant[tid] = tenant_reminded - dropped
                result.errors.append({"tenant_id": tid, "failed_emails": failed})
        else:
            created_all.extend(tenant_created)

    if not sync_email:
        # Web-request path: emails go out after the commits, best-effort
        # (never raises) in a background thread; the process lives on.
        email_service.send_for_notifications(db, created_all)
    return result


def _remind_tenant(db, tid, year, period, in_grace, when, link, actionable):
    """Create one tenant's reminder notifications (caller commits).

    Returns (created_notifications, reminded_count, already_reminded_count).
    """
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

    created_all: list = []
    tenant_reminded = 0
    tenant_already = 0
    for user in users:
        statuses = statuses_by_user.get(user.id, set())
        if statuses and not (statuses & actionable):
            continue  # nothing actionable — already submitted/approved
        if _already_reminded(db, user.id, link):
            tenant_already += 1
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
        tenant_reminded += 1
    return created_all, tenant_reminded, tenant_already
