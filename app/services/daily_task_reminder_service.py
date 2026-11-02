"""Evening reminders for unfilled Daily Task diaries (pick 1).

Only diary authors (employees and dept admins) are reminded — tenant
admins never fill a diary. Weekdays only: the job itself does nothing on
Saturday/Sunday, and cron should run it Monday–Friday at 16:00::

    0 16 * * 1-5 docker compose exec -T web python -m app.jobs.send_daily_task_reminders

Idempotent: a user gets at most one reminder per day (checked against
notifications of type "daily_task_reminder" created today).
"""

from __future__ import annotations

from datetime import date as date_type, datetime, timezone

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models.daily_task import DailyTaskEntry
from app.models.notification import Notification
from app.models.tenant import Tenant
from app.models.user import User, UserRole
from app.services import notification_service


def send_daily_task_reminders(db: Session, today: date_type | None = None) -> dict:
    today = today or datetime.now(timezone.utc).date()
    if today.weekday() >= 5:
        # Saturday/Sunday: no diary reminders, even if cron runs daily.
        return {"date": today.isoformat(), "reminded": [], "skipped": ["weekend"]}
    day_start = datetime(today.year, today.month, today.day, tzinfo=timezone.utc)
    reminded: list[str] = []
    skipped: list[str] = []
    for tenant in db.scalars(select(Tenant)).all():
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
    db.commit()
    return {"date": today.isoformat(), "reminded": reminded, "skipped": skipped}
