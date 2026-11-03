"""Evening reminders for unfilled Daily Task diaries (pick 1).

Only diary authors (employees and dept admins) are reminded — tenant
admins never fill a diary. Weekdays only: the job itself does nothing on
Saturday/Sunday, and cron should run it Monday–Friday at 16:00::

    0 16 * * 1-5 docker compose exec -T web python -m app.jobs.send_daily_task_reminders

Idempotent: a user gets at most one reminder per day (checked against
notifications of type "daily_task_reminder" created today).

Note: this job sends NO email today (there is no existing email behavior
here to fix); reminders are in-app notifications, created per tenant.
"""

from __future__ import annotations

import logging
from datetime import date as date_type, datetime, timezone

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models.daily_task import DailyTaskEntry
from app.models.notification import Notification
from app.models.tenant import Tenant, TenantStatus
from app.models.user import User, UserRole
from app.core.timezone import today as app_today
from app.services import notification_service

logger = logging.getLogger(__name__)


def send_daily_task_reminders(db: Session, today: date_type | None = None) -> dict:
    today = today or app_today()
    if today.weekday() >= 5:
        # Saturday/Sunday: no diary reminders, even if cron runs daily.
        return {"date": today.isoformat(), "reminded": [], "skipped": ["weekend"]}
    day_start = datetime(today.year, today.month, today.day, tzinfo=timezone.utc)
    reminded: list[str] = []
    skipped: list[str] = []
    errors: list[dict] = []
    # Suspended tenants get no reminders (previously every tenant was
    # iterated, including suspended ones).
    for tenant in db.scalars(
        select(Tenant).where(Tenant.status == TenantStatus.ACTIVE)
    ).all():
        try:
            _remind_tenant(db, tenant, today, day_start, reminded, skipped)
        except Exception:
            # One tenant's failure must not lose every other tenant's
            # reminders: roll back just this tenant and keep going.
            db.rollback()
            logger.exception("Daily task reminders failed for tenant %s; continuing", tenant.id)
            errors.append({"tenant_id": tenant.id, "error": "see server log"})
            continue
        db.commit()
    result: dict = {"date": today.isoformat(), "reminded": reminded, "skipped": skipped}
    if errors:
        result["errors"] = errors
    return result


def _remind_tenant(db, tenant, today, day_start, reminded, skipped) -> None:
    """Create one tenant's diary reminders (caller commits)."""
    users = db.scalars(
        select(User).where(
            User.tenant_id == tenant.id,
            User.role.in_([UserRole.EMPLOYEE, UserRole.DEPT_ADMIN]),
            User.is_active == True,  # noqa: E712
        )
    ).all()
    for user in users:
        has_entry = db.scalars(
            select(DailyTaskEntry.id).where(
                DailyTaskEntry.tenant_id == tenant.id,
                DailyTaskEntry.user_id == user.id,
                DailyTaskEntry.task_date == today,
            )
        ).first()
        if has_entry:
            skipped.append(user.email)
            continue
        already = db.scalars(
            select(Notification.id).where(
                Notification.tenant_id == tenant.id,
                Notification.user_id == user.id,
                Notification.type == "daily_task_reminder",
                Notification.created_at >= day_start,
            )
        ).first()
        if already:
            skipped.append(user.email)
            continue
        notification_service.notify_user(
            db,
            tenant_id=tenant.id,
            user_id=user.id,
            type="daily_task_reminder",
            title="Daily diary not filled yet",
            message=f"You have not filled today's working diary ({today.isoformat()}) yet. Please fill it before the day ends.",
            link="/daily-tasks",
        )
        reminded.append(user.email)
