"""Direct tests for app/services/notification_service.py.

The service had zero direct tests — only indirect exercise through the
KPI workflow and the holiday notifier. These pin the routing rules:
who gets notified, who is excluded, and that helpers only stage rows
(the caller commits), so a notification is never stored for a change
that rolled back.
"""

from sqlalchemy import select

from app.database import SessionLocal
from app.models.notification import Notification
from app.models.user import UserRole
from app.services import notification_service
from app.tests.factories import make_department, make_tenant, make_user


def _setup(db):
    tenant = make_tenant(db)
    other_tenant = make_tenant(db, name="Globex", slug="globex")
    dept = make_department(db, tenant)
    employee = make_user(db, tenant, UserRole.EMPLOYEE, name="Employee", department=dept)
    dept_admin = make_user(db, tenant, UserRole.DEPT_ADMIN, name="Dept Admin", department=dept)
    tenant_admin = make_user(db, tenant, UserRole.TENANT_ADMIN, name="Tenant Admin")
    inactive_admin = make_user(
        db, tenant, UserRole.TENANT_ADMIN, name="Inactive Admin", is_active=False
    )
    outsider = make_user(db, other_tenant, UserRole.EMPLOYEE, name="Outsider")
    return {
        "tenant": tenant,
        "other_tenant": other_tenant,
        "dept": dept,
        "employee": employee,
        "dept_admin": dept_admin,
        "tenant_admin": tenant_admin,
        "inactive_admin": inactive_admin,
        "outsider": outsider,
    }


def _notes_for(db, user):
    return db.scalars(
        select(Notification).where(Notification.user_id == user.id).order_by(Notification.id)
    ).all()


def test_notify_user_creates_one_unread_row(db):
    fx = _setup(db)

    created = notification_service.notify_user(
        db, tenant_id=fx["tenant"].id, user_id=fx["employee"].id,
        type="kpi_submitted", title="T", message="M", link="/dashboard",
    )
    db.commit()

    assert len(created) == 1
    notes = _notes_for(db, fx["employee"])
    assert len(notes) == 1
    assert notes[0].type == "kpi_submitted"
    assert notes[0].title == "T"
    assert notes[0].link == "/dashboard"
    assert notes[0].is_read is False


def test_notify_user_stages_without_committing(db):
    fx = _setup(db)

    notification_service.notify_user(
        db, tenant_id=fx["tenant"].id, user_id=fx["employee"].id,
        type="kpi_submitted", title="T", message="M",
    )
    # A separate session cannot see the row until the caller commits.
    other = SessionLocal()
    try:
        assert other.scalar(select(Notification).where(
            Notification.user_id == fx["employee"].id
        )) is None
    finally:
        other.close()
    db.commit()
    assert len(_notes_for(db, fx["employee"])) == 1


def test_notify_dept_admins_scoped_to_department_and_active_only(db):
    fx = _setup(db)
    other_dept = make_department(db, fx["tenant"], name="Other")
    other_dept_admin = make_user(
        db, fx["tenant"], UserRole.DEPT_ADMIN, name="Other DA", department=other_dept
    )

    created = notification_service.notify_dept_admins(
        db, tenant_id=fx["tenant"].id, department_id=fx["dept"].id,
        type="kpi_submitted", title="T", message="M",
    )
    db.commit()

    assert [n.user_id for n in created] == [fx["dept_admin"].id]
    assert _notes_for(db, fx["dept_admin"]) != []
    assert _notes_for(db, other_dept_admin) == []
    assert _notes_for(db, fx["employee"]) == []


def test_notify_dept_admins_excludes_actor(db):
    fx = _setup(db)

    created = notification_service.notify_dept_admins(
        db, tenant_id=fx["tenant"].id, department_id=fx["dept"].id,
        exclude_user_id=fx["dept_admin"].id,
        type="kpi_submitted", title="T", message="M",
    )
    db.commit()

    assert created == []
    assert _notes_for(db, fx["dept_admin"]) == []


def test_notify_tenant_admins_only_active_admins_of_tenant(db):
    fx = _setup(db)

    created = notification_service.notify_tenant_admins(
        db, tenant_id=fx["tenant"].id,
        type="kpi_dept_approved", title="T", message="M",
    )
    db.commit()

    assert [n.user_id for n in created] == [fx["tenant_admin"].id]
    assert _notes_for(db, fx["inactive_admin"]) == []
    assert _notes_for(db, fx["employee"]) == []


def test_notify_tenant_users_reaches_every_active_user_except_actor(db):
    fx = _setup(db)

    created = notification_service.notify_tenant_users(
        db, tenant_id=fx["tenant"].id, exclude_user_id=fx["tenant_admin"].id,
        type="attendance_holiday", title="Holiday", message="M", link="/attendance",
    )
    db.commit()

    recipients = {n.user_id for n in created}
    assert recipients == {fx["employee"].id, fx["dept_admin"].id}
    assert fx["inactive_admin"].id not in recipients
    assert fx["outsider"].id not in recipients
    assert _notes_for(db, fx["outsider"]) == []
