"""In-app notifications for KPI workflow events.

The helpers below only ADD Notification rows to the session — they never
commit. Callers commit them together with the workflow change they describe,
so a notification is never stored for a change that rolled back (same pattern
as audit_service.log).
"""

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models.notification import Notification
from app.models.user import User, UserRole


def _add(
    db: Session,
    *,
    tenant_id: int,
    user_id: int,
    type: str,
    title: str,
    message: str,
    link: str | None = None,
) -> Notification:
    notification = Notification(
        tenant_id=tenant_id,
        user_id=user_id,
        type=type,
        title=title,
        message=message,
        link=link,
    )
    db.add(notification)
    return notification


def _active_role_users(
    db: Session,
    tenant_id: int,
    role: UserRole,
    department_id: int | None = None,
    exclude_user_id: int | None = None,
) -> list[User]:
    stmt = select(User).where(
        User.tenant_id == tenant_id,
        User.role == role,
        User.is_active == True,  # noqa: E712
    )
    if department_id is not None:
        stmt = stmt.where(User.department_id == department_id)
    if exclude_user_id is not None:
        stmt = stmt.where(User.id != exclude_user_id)
    return list(db.scalars(stmt).all())


def notify_user(
    db: Session,
    *,
    tenant_id: int,
    user_id: int,
    type: str,
    title: str,
    message: str,
    link: str | None = None,
) -> list[Notification]:
    """Notify one specific user. Returns the created notification."""
    return [_add(db, tenant_id=tenant_id, user_id=user_id, type=type, title=title, message=message, link=link)]


def notify_dept_admins(
    db: Session,
    *,
    tenant_id: int,
    department_id: int,
    exclude_user_id: int | None = None,
    type: str,
    title: str,
    message: str,
    link: str | None = None,
) -> list[Notification]:
    """Notify active Dept Admins of one department (optionally excluding the actor)."""
    created = []
    for admin in _active_role_users(db, tenant_id, UserRole.DEPT_ADMIN, department_id, exclude_user_id):
        created.append(
            _add(db, tenant_id=tenant_id, user_id=admin.id, type=type, title=title, message=message, link=link)
        )
    return created


def notify_tenant_admins(
    db: Session,
    *,
    tenant_id: int,
    exclude_user_id: int | None = None,
    type: str,
    title: str,
    message: str,
    link: str | None = None,
) -> list[Notification]:
    """Notify all active Tenant Admins of a tenant."""
    created = []
    for admin in _active_role_users(db, tenant_id, UserRole.TENANT_ADMIN, None, exclude_user_id):
        created.append(
            _add(db, tenant_id=tenant_id, user_id=admin.id, type=type, title=title, message=message, link=link)
        )
    return created
