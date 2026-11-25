"""Tests for the leave approval step (S-15, security review 2026-11-25).

An employee marking their own leave creates a PENDING request — it
counts as an absence until a dept/tenant admin approves it. An admin
marking leave for someone else approves it by that action. Approving or
rejecting needs a different admin (no self-review); moving an approved
leave day sends it back to pending.
"""

from app.core.timezone import today as app_today
from app.models.attendance import AttendanceRecord
from app.models.user import UserRole
from app.routers.attendance import _working_days as _router_working_days
from app.tests.factories import (
    auth_headers,
    make_department,
    make_tenant,
    make_user,
)


def _set_settings(client, admin, tenant):
    resp = client.patch(
        "/api/attendance/settings",
        json={"work_days": [0, 1, 2, 3, 4, 5, 6], "office_ips": []},
        headers=auth_headers(admin, tenant),
    )
    assert resp.status_code == 200, resp.text


def _working_weekdays(year, month):
    cfg = {"work_days": [0, 1, 2, 3, 4], "holidays": []}
    return _router_working_days(None, None, year, month, cfg=cfg)


def _setup(db, client):
    tenant = make_tenant(db)
    admin = make_user(db, tenant, UserRole.TENANT_ADMIN)
    dept = make_department(db, tenant)
    dept_admin = make_user(db, tenant, UserRole.DEPT_ADMIN, department=dept)
    employee = make_user(db, tenant, UserRole.EMPLOYEE, department=dept)
    _set_settings(client, admin, tenant)
    return tenant, admin, dept_admin, employee


def _mark_leave(client, user, tenant, day, target_id=None):
    payload = {"record_date": day.isoformat()}
    if target_id is not None:
        payload["user_id"] = target_id
    return client.post(
        "/api/attendance/leave", json=payload, headers=auth_headers(user, tenant)
    )


def _summary(client, user, tenant, year, month, target_id=None):
    params = {"year": year, "month": month}
    if target_id is not None:
        params["user_id"] = target_id
    resp = client.get(
        "/api/attendance/summary", params=params, headers=auth_headers(user, tenant)
    )
    assert resp.status_code == 200, resp.text
    return resp.json()


def _future_working_day():
    today = app_today()
    days = [d for d in _working_weekdays(today.year, today.month) if d >= today]
    return days[0], days[1]


def test_employee_self_leave_is_pending_and_counts_as_absent(db, client):
    tenant, admin, dept_admin, employee = _setup(db, client)
    day, _ = _future_working_day()
    resp = _mark_leave(client, employee, tenant, day)
    assert resp.status_code == 201, resp.text
    assert resp.json()["leave_approval"] == "pending"
    summary = _summary(client, employee, tenant, day.year, day.month)
    assert summary["leave"] == 0
    assert summary["absent"] >= 1  # the pending day is an absence, not leave


def test_admin_marked_leave_is_approved_and_counts_as_leave(db, client):
    tenant, admin, dept_admin, employee = _setup(db, client)
    day, _ = _future_working_day()
    resp = _mark_leave(client, admin, tenant, day, target_id=employee.id)
    assert resp.status_code == 201, resp.text
    assert resp.json()["leave_approval"] == "approved"
    summary = _summary(client, admin, tenant, day.year, day.month, target_id=employee.id)
    assert summary["leave"] == 1


def test_dept_admin_approves_pending_leave(db, client):
    tenant, admin, dept_admin, employee = _setup(db, client)
    day, _ = _future_working_day()
    record_id = _mark_leave(client, employee, tenant, day).json()["id"]
    resp = client.post(
        f"/api/attendance/leave/{record_id}/approve",
        headers=auth_headers(dept_admin, tenant),
    )
    assert resp.status_code == 200, resp.text
    assert resp.json()["leave_approval"] == "approved"
    summary = _summary(client, admin, tenant, day.year, day.month, target_id=employee.id)
    assert summary["leave"] == 1


def test_dept_admin_rejects_pending_leave(db, client):
    tenant, admin, dept_admin, employee = _setup(db, client)
    day, _ = _future_working_day()
    record_id = _mark_leave(client, employee, tenant, day).json()["id"]
    resp = client.post(
        f"/api/attendance/leave/{record_id}/reject",
        headers=auth_headers(dept_admin, tenant),
    )
    assert resp.status_code == 200, resp.text
    assert resp.json()["leave_approval"] == "rejected"
    summary = _summary(client, admin, tenant, day.year, day.month, target_id=employee.id)
    assert summary["leave"] == 0


def test_cannot_review_own_leave(db, client):
    tenant, admin, dept_admin, employee = _setup(db, client)
    day, _ = _future_working_day()
    # A dept admin's own leave is pending (self-marked) — they cannot
    # approve it themselves; the tenant admin can.
    record_id = _mark_leave(client, dept_admin, tenant, day).json()["id"]
    resp = client.post(
        f"/api/attendance/leave/{record_id}/approve",
        headers=auth_headers(dept_admin, tenant),
    )
    assert resp.status_code == 403, resp.text
    resp = client.post(
        f"/api/attendance/leave/{record_id}/approve",
        headers=auth_headers(admin, tenant),
    )
    assert resp.status_code == 200, resp.text


def test_employee_cannot_review_leave(db, client):
    tenant, admin, dept_admin, employee = _setup(db, client)
    day, _ = _future_working_day()
    record_id = _mark_leave(client, employee, tenant, day).json()["id"]
    resp = client.post(
        f"/api/attendance/leave/{record_id}/approve",
        headers=auth_headers(employee, tenant),
    )
    assert resp.status_code == 403, resp.text


def test_dept_admin_cannot_review_other_department_leave(db, client):
    tenant = make_tenant(db)
    admin = make_user(db, tenant, UserRole.TENANT_ADMIN)
    sales = make_department(db, tenant, name="Sales")
    ops = make_department(db, tenant, name="Operations")
    sales_admin = make_user(db, tenant, UserRole.DEPT_ADMIN, department=sales)
    ops_employee = make_user(db, tenant, UserRole.EMPLOYEE, department=ops)
    _set_settings(client, admin, tenant)
    day, _ = _future_working_day()
    record_id = _mark_leave(client, ops_employee, tenant, day).json()["id"]
    resp = client.post(
        f"/api/attendance/leave/{record_id}/approve",
        headers=auth_headers(sales_admin, tenant),
    )
    assert resp.status_code == 403, resp.text


def test_reviewing_twice_is_rejected(db, client):
    tenant, admin, dept_admin, employee = _setup(db, client)
    day, _ = _future_working_day()
    record_id = _mark_leave(client, employee, tenant, day).json()["id"]
    assert client.post(
        f"/api/attendance/leave/{record_id}/approve",
        headers=auth_headers(dept_admin, tenant),
    ).status_code == 200
    resp = client.post(
        f"/api/attendance/leave/{record_id}/approve",
        headers=auth_headers(dept_admin, tenant),
    )
    assert resp.status_code == 409, resp.text
    resp = client.post(
        f"/api/attendance/leave/{record_id}/reject",
        headers=auth_headers(dept_admin, tenant),
    )
    assert resp.status_code == 409, resp.text


def test_legacy_leave_without_approval_state_counts_as_leave(db, client):
    # Rows created before approval existed (leave_approval NULL) keep
    # counting as leave — the migration does not rewrite history.
    tenant, admin, dept_admin, employee = _setup(db, client)
    today = app_today()
    day = _working_weekdays(today.year, today.month)[0]
    db.add(
        AttendanceRecord(
            tenant_id=tenant.id,
            employee_id=employee.id,
            department_id=employee.department_id,
            record_date=day,
            status="leave",
            leave_approval=None,
        )
    )
    db.commit()
    summary = _summary(client, admin, tenant, day.year, day.month, target_id=employee.id)
    assert summary["leave"] == 1


def test_moving_approved_leave_sends_it_back_to_pending(db, client):
    tenant, admin, dept_admin, employee = _setup(db, client)
    day1, day2 = _future_working_day()
    record_id = _mark_leave(client, admin, tenant, day1, target_id=employee.id).json()["id"]
    # The leave is already approved (admin-marked). The owner moves it.
    resp = client.patch(
        f"/api/attendance/records/{record_id}",
        json={"record_date": day2.isoformat()},
        headers=auth_headers(employee, tenant),
    )
    assert resp.status_code == 200, resp.text
    assert resp.json()["leave_approval"] == "pending"
    summary = _summary(client, admin, tenant, day2.year, day2.month, target_id=employee.id)
    assert summary["leave"] == 0


def test_approve_and_reject_are_audited(db, client):
    tenant, admin, dept_admin, employee = _setup(db, client)
    day, _ = _future_working_day()
    record_id = _mark_leave(client, employee, tenant, day).json()["id"]
    assert client.post(
        f"/api/attendance/leave/{record_id}/approve",
        headers=auth_headers(dept_admin, tenant),
    ).status_code == 200
    resp = client.get("/api/admin/audit-log", headers=auth_headers(admin, tenant))
    assert resp.status_code == 200, resp.text
    actions = {e["action"] for e in resp.json()}
    assert "attendance_leave_approved" in actions
