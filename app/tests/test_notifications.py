"""Tests for in-app notifications: workflow hooks create them for the right
recipients, and the API lists / marks them read scoped to the current user."""

from sqlalchemy import select

from app.models.notification import Notification
from app.models.user import UserRole
from app.models.kpi_submission import KPIStatus
from app.services import kpi_service
from app.tests.factories import (
    auth_headers,
    make_department,
    make_submission,
    make_template,
    make_tenant,
    make_user,
)


def _setup(db):
    tenant = make_tenant(db)
    dept = make_department(db, tenant)
    employee = make_user(db, tenant, UserRole.EMPLOYEE, name="Employee", department=dept)
    dept_admin = make_user(db, tenant, UserRole.DEPT_ADMIN, name="Dept Admin", department=dept)
    tenant_admin = make_user(db, tenant, UserRole.TENANT_ADMIN, name="Tenant Admin")
    return tenant, dept, employee, dept_admin, tenant_admin


def _notifications_for(db, user):
    return db.scalars(
        select(Notification).where(Notification.user_id == user.id).order_by(Notification.id)
    ).all()


def _submit(db, tenant, employee, period="September"):
    template = make_template(db, tenant, department=employee.department)
    submission = make_submission(
        db, tenant, employee, template, period=period, self_score=80.0
    )
    kpi_service.submit_for_dept_approval(db, employee, 2026, period)
    return submission


def test_submit_notifies_dept_admins(db):
    tenant, dept, employee, dept_admin, tenant_admin = _setup(db)

    _submit(db, tenant, employee)

    dept_notes = _notifications_for(db, dept_admin)
    assert len(dept_notes) == 1
    assert dept_notes[0].type == "kpi_submitted"
    assert "Employee" in dept_notes[0].title
    assert dept_notes[0].is_read is False
    # The submitter is not notified about their own submission.
    assert _notifications_for(db, employee) == []
    # Tenant admin is not notified at the dept-review stage.
    assert _notifications_for(db, tenant_admin) == []


def test_dept_approve_notifies_employee_and_tenant_admins(db):
    tenant, dept, employee, dept_admin, tenant_admin = _setup(db)
    submission = _submit(db, tenant, employee)

    kpi_service.dept_approve(db, dept_admin, submission, 85.0, None)

    emp_notes = _notifications_for(db, employee)
    assert len(emp_notes) == 1
    assert emp_notes[0].type == "kpi_dept_approved"

    admin_notes = _notifications_for(db, tenant_admin)
    assert len(admin_notes) == 1
    assert "final approval" in admin_notes[0].title


def test_dept_admin_submit_notifies_tenant_admins_for_final_approval(db):
    tenant = make_tenant(db)
    dept = make_department(db, tenant)
    dept_admin = make_user(db, tenant, UserRole.DEPT_ADMIN, name="Dept Admin", department=dept)
    tenant_admin = make_user(db, tenant, UserRole.TENANT_ADMIN, name="Tenant Admin")
    template = make_template(db, tenant, employee=dept_admin, locked_year=2026, locked_period="September")
    make_submission(db, tenant, dept_admin, template, period="September", self_score=90.0)

    kpi_service.submit_for_dept_approval(db, dept_admin, 2026, "September")

    # The submitter isn't notified about their own submit...
    assert [n for n in _notifications_for(db, dept_admin) if n.type == "kpi_submitted"] == []
    # ...but tenant admins learn it needs final approval.
    admin_notes = [n for n in _notifications_for(db, tenant_admin) if n.type == "kpi_submitted"]
    assert len(admin_notes) == 1
    assert "final approval" in admin_notes[0].message


def test_reject_notifies_employee_with_remarks(db):
    tenant, dept, employee, dept_admin, tenant_admin = _setup(db)
    submission = _submit(db, tenant, employee)

    kpi_service.reject_submission(db, dept_admin, submission, "Scores too low, revise.")

    emp_notes = _notifications_for(db, employee)
    assert len(emp_notes) == 1
    assert emp_notes[0].type == "kpi_rejected"
    assert "Scores too low" in emp_notes[0].message
    assert "resubmit" in emp_notes[0].message


def test_final_approve_and_override_notify_employee(db):
    tenant, dept, employee, dept_admin, tenant_admin = _setup(db)
    submission = _submit(db, tenant, employee)
    kpi_service.dept_approve(db, dept_admin, submission, 85.0, None)

    kpi_service.final_approve(db, tenant_admin, submission, None, None)
    types = [n.type for n in _notifications_for(db, employee)]
    assert "kpi_final_approved" in types

    submission2 = _submit(db, tenant, employee, period="October")
    kpi_service.tenant_admin_override(db, tenant_admin, submission2, 90.0, None)
    types = [n.type for n in _notifications_for(db, employee)]
    assert "kpi_overridden" in types


def test_notification_api_list_and_mark_read(db, client):
    tenant, dept, employee, dept_admin, tenant_admin = _setup(db)
    _submit(db, tenant, employee)

    resp = client.get("/api/notifications", headers=auth_headers(dept_admin, tenant))
    assert resp.status_code == 200
    body = resp.json()
    assert body["unread_count"] == 1
    assert len(body["notifications"]) == 1
    note_id = body["notifications"][0]["id"]

    # Another user sees nothing.
    resp = client.get("/api/notifications", headers=auth_headers(employee, tenant))
    assert resp.json()["unread_count"] == 0

    # Cannot mark someone else's notification read.
    resp = client.post(
        f"/api/notifications/{note_id}/read", headers=auth_headers(employee, tenant)
    )
    assert resp.status_code == 404

    resp = client.post(
        f"/api/notifications/{note_id}/read", headers=auth_headers(dept_admin, tenant)
    )
    assert resp.status_code == 200
    assert resp.json()["is_read"] is True

    resp = client.get("/api/notifications", headers=auth_headers(dept_admin, tenant))
    assert resp.json()["unread_count"] == 0


def test_notification_api_read_all(db, client):
    tenant, dept, employee, dept_admin, tenant_admin = _setup(db)
    _submit(db, tenant, employee)
    _submit(db, tenant, employee, period="October")

    resp = client.get("/api/notifications", headers=auth_headers(dept_admin, tenant))
    assert resp.json()["unread_count"] == 2

    resp = client.post("/api/notifications/read-all", headers=auth_headers(dept_admin, tenant))
    assert resp.status_code == 200

    resp = client.get("/api/notifications", headers=auth_headers(dept_admin, tenant))
    assert resp.json()["unread_count"] == 0
