from datetime import datetime

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, ConfigDict
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.core.deps import get_current_user
from app.database import get_db
from app.models.notification import Notification
from app.models.user import User
from app.services import audit_service

router = APIRouter(prefix="/api/notifications", tags=["notifications"])


class NotificationOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    type: str
    title: str
    message: str
    link: str | None
    is_read: bool
    created_at: datetime


class NotificationListOut(BaseModel):
    notifications: list[NotificationOut]
    unread_count: int


class NotificationSettingsOut(BaseModel):
    email_enabled: bool


class NotificationSettingsUpdate(BaseModel):
    email_enabled: bool


@router.get("/settings", response_model=NotificationSettingsOut)
def get_notification_settings(user: User = Depends(get_current_user)):
    """The current user's notification preferences."""
    return NotificationSettingsOut(email_enabled=user.email_notifications)


@router.put("/settings", response_model=NotificationSettingsOut)
def update_notification_settings(
    payload: NotificationSettingsUpdate,
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
):
    """Toggle workflow notification emails (in-app notifications are unaffected)."""
    # S-09: a settings change that controls whether the user receives
    # email is an auditable event — a silent toggle could hide workflow
    # notifications from the user.
    old = user.email_notifications
    user.email_notifications = payload.email_enabled
    audit_service.log(
        db, user, "notification_settings_updated", "user", user.id, user.name,
        f"Notification emails changed from {old} to {payload.email_enabled} by {user.name}.",
        department_id=user.department_id,
    )
    db.commit()
    db.refresh(user)
    return NotificationSettingsOut(email_enabled=user.email_notifications)


@router.get("", response_model=NotificationListOut)
def list_notifications(
    limit: int = 50,
    offset: int = 0,
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
):
    """The current user's own notifications, newest first, plus the unread count."""
    notifications = db.scalars(
        select(Notification)
        .where(Notification.user_id == user.id)
        .order_by(Notification.created_at.desc(), Notification.id.desc())
        # B-m5: clamp below as well as above, like the audit-log
        # endpoints (a negative limit used to pass through to the DB).
        .limit(min(max(limit, 1), 200))
        .offset(max(offset, 0))
    ).all()
    unread_count = db.scalar(
        select(func.count())
        .select_from(Notification)
        .where(Notification.user_id == user.id, Notification.is_read == False)  # noqa: E712
    )
    return NotificationListOut(
        notifications=[NotificationOut.model_validate(n) for n in notifications],
        unread_count=unread_count or 0,
    )


@router.post("/{notification_id}/read", response_model=NotificationOut)
def mark_read(notification_id: int, db: Session = Depends(get_db), user: User = Depends(get_current_user)):
    notification = db.scalar(
        select(Notification).where(Notification.id == notification_id, Notification.user_id == user.id)
    )
    if notification is None:
        raise HTTPException(404, "Notification not found.")
    notification.is_read = True
    db.commit()
    db.refresh(notification)
    return NotificationOut.model_validate(notification)


@router.post("/read-all", response_model=dict)
def mark_all_read(db: Session = Depends(get_db), user: User = Depends(get_current_user)):
    db.query(Notification).where(Notification.user_id == user.id, Notification.is_read == False).update(  # noqa: E712
        {Notification.is_read: True}
    )
    db.commit()
    return {"status": "ok"}
