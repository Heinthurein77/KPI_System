"""Security-review (2026-11-25) attendance endpoint tests — Track 3.

S-15 (leave date bounds + retroactive flagging), M-26 (report/export
caps), M-27 (WiFi-gate denial logging), M-28 (export membership
oracle), M-30 (after-midnight check-out in /today), M-31 (department
snapshot + tenant-admin leave), M-40 (rate limits on leave/settings/
KPI writes).
"""

import logging
from datetime import timedelta

import pytest

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

OFFICE_IP = "203.0.113.10"
HOME_IP = "198.51.100.99"


def _headers(user, tenant, ip=None):
    headers = auth_headers(user, tenant)
    if ip is not None:
        headers["X-Forwarded-For"] = ip
    return headers


def _set_settings(client, admin, tenant, **kwargs):
    payload = {"work_days": [0, 1, 2, 3, 4, 5, 6], "office_ips": [], **kwargs}
    resp = client.patch(
        "/api/attendance/settings", json=payload, headers=auth_headers(admin, tenant)
    )
    assert resp.status_code == 200, resp.text
    return resp.json()


def _working_weekdays(year, month):
    cfg = {"work_days": [0, 1, 2, 3, 4], "holidays": []}
    return _router_working_days(None, None, year, month, cfg=cfg)


def _first_weekday_of_month(year, month):
    return _working_weekdays(year, month)[0]


def _audit_entries(client, admin, tenant):
    resp = client.get("/api/admin/audit-log", headers=auth_headers(admin, tenant))
    assert resp.status_code == 200, resp.text
    return resp.json()


def _insert_record(db, tenant, employee, day, status, check_in=None):
    record = AttendanceRecord(
        tenant_id=tenant.id,
        employee_id=employee.id,
        department_id=employee.department_id,
        record_date=day,
        check_in=check_in,
        status=status,
    )
    db.add(record)
    db.commit()
    return record


# ------------------------------------------------------------------ S-15


def test_leave_rejected_for_past_month(client, db):
    tenant = make_tenant(db)
    admin = make_user(db, tenant, UserRole.TENANT_ADMIN)
    employee = make_user(db, tenant, UserRole.EMPLOYEE)
    _set_settings(client, admin, tenant)
    today = app_today()
    if today.month == 1:
        year, month = today.year - 1, 12
    else:
        year, month = today.year, today.month - 1
    day = _first_weekday_of_month(year, month)
    resp = client.post(
        "/api/attendance/leave",
        json={"record_date": day.isoformat()},
        headers=auth_headers(employee, tenant),
    )
    assert resp.status_code == 400, resp.text
    assert "current month" in resp.json()["detail"]


def test_leave_rejected_for_next_month_and_year_9999(client, db):
    tenant = make_tenant(db)
    admin = make_user(db, tenant, UserRole.TENANT_ADMIN)
    employee = make_user(db, tenant, UserRole.EMPLOYEE)
    _set_settings(client, admin, tenant)
    today = app_today()
    if today.month == 12:
        year, month = today.year + 1, 1
    else:
        year, month = today.year, today.month + 1
    day = _first_weekday_of_month(year, month)
    resp = client.post(
        "/api/attendance/leave",
        json={"record_date": day.isoformat()},
        headers=auth_headers(employee, tenant),
    )
    assert resp.status_code == 400, resp.text
    resp = client.post(
        "/api/attendance/leave",
        json={"record_date": "9999-12-31"},
        headers=auth_headers(employee, tenant),
    )
    assert resp.status_code == 400, resp.text


def test_retroactive_leave_within_month_is_flagged_in_audit(client, db):
    tenant = make_tenant(db)
    admin = make_user(db, tenant, UserRole.TENANT_ADMIN)
    employee = make_user(db, tenant, UserRole.EMPLOYEE)
    _set_settings(client, admin, tenant)
    today = app_today()
    past_days = [d for d in _working_weekdays(today.year, today.month) if d < today]
    if not past_days:
        pytest.skip("no past working day in this month yet")
    day = past_days[0]
    resp = client.post(
        "/api/attendance/leave",
        json={"record_date": day.isoformat()},
        headers=auth_headers(employee, tenant),
    )
    assert resp.status_code == 201, resp.text
    entries = [
        e for e in _audit_entries(client, admin, tenant)
        if e["action"] == "attendance_leave_created"
    ]
    assert entries, "leave creation must be audited"
    assert "RETROACTIVE" in entries[0]["summary"]


# ------------------------------------------------------------------ M-31


def test_leave_for_tenant_admin_is_rejected(client, db):
    tenant = make_tenant(db)
    admin = make_user(db, tenant, UserRole.TENANT_ADMIN)
    _set_settings(client, admin, tenant)
    today = app_today()
    day = _first_weekday_of_month(today.year, today.month)
    resp = client.post(
        "/api/attendance/leave",
        json={"record_date": day.isoformat()},
        headers=auth_headers(admin, tenant),
    )
    assert resp.status_code == 400, resp.text


def test_report_follows_record_department_after_transfer(client, db):
    """A transferred employee's history stays with the department the
    days were recorded in (snapshot), not the current department."""
    tenant = make_tenant(db)
    admin = make_user(db, tenant, UserRole.TENANT_ADMIN)
    sales = make_department(db, tenant, name="Sales")
    ops = make_department(db, tenant, name="Operations")
    dana = make_user(db, tenant, UserRole.DEPT_ADMIN, name="Dana", department=sales)
    alice = make_user(db, tenant, UserRole.EMPLOYEE, name="Alice", department=sales)
    _set_settings(client, admin, tenant)
    today = app_today()
    day = _first_weekday_of_month(today.year, today.month)
    _insert_record(db, tenant, alice, day, "leave")
    # Alice transfers to Operations after the leave was recorded.
    alice.department_id = ops.id
    db.commit()
    resp = client.get(
        "/api/attendance/report",
        params={"year": today.year, "month": today.month},
        headers=auth_headers(dana, tenant),
    )
    assert resp.status_code == 200, resp.text
    rows = {r["user_id"]: r for r in resp.json()}
    assert alice.id in rows, "Sales admin must still see Alice's Sales history"
    assert rows[alice.id]["department_name"] == "Sales"
    assert rows[alice.id]["leave"] == 1
    summary = client.get(
        "/api/attendance/summary",
        params={"year": today.year, "month": today.month, "user_id": alice.id},
        headers=auth_headers(dana, tenant),
    )
    assert summary.status_code == 200, summary.text
    assert summary.json()["department_name"] == "Sales"


# ------------------------------------------------------------------ M-26


def test_report_defaults_to_100_and_accepts_limit(client, db):
    tenant = make_tenant(db)
    admin = make_user(db, tenant, UserRole.TENANT_ADMIN)
    for i in range(3):
        make_user(db, tenant, UserRole.EMPLOYEE, name=f"Emp {i}")
    today = app_today()
    resp = client.get(
        "/api/attendance/report",
        params={"year": today.year, "month": today.month, "limit": 1},
        headers=auth_headers(admin, tenant),
    )
    assert resp.status_code == 200, resp.text
    assert len(resp.json()) == 1
    resp = client.get(
        "/api/attendance/report",
        params={"year": today.year, "month": today.month, "limit": 1001},
        headers=auth_headers(admin, tenant),
    )
    assert resp.status_code == 422, resp.text
    resp = client.get(
        "/api/attendance/report",
        params={"year": today.year, "month": today.month},
        headers=auth_headers(admin, tenant),
    )
    assert resp.status_code == 200, resp.text
    assert len(resp.json()) == 3


def test_export_is_capped_by_limit(client, db):
    from openpyxl import load_workbook
    import io

    tenant = make_tenant(db)
    admin = make_user(db, tenant, UserRole.TENANT_ADMIN)
    for i in range(3):
        make_user(db, tenant, UserRole.EMPLOYEE, name=f"Emp {i}")
    today = app_today()
    resp = client.get(
        "/api/attendance/report/export",
        params={"year": today.year, "month": today.month, "limit": 1},
        headers=auth_headers(admin, tenant),
    )
    assert resp.status_code == 200, resp.text
    wb = load_workbook(io.BytesIO(resp.content), read_only=True)
    ws = wb.active
    names = set()
    # Data rows start at row 5 (title, subtitle, blank, header above).
    for row in ws.iter_rows(min_row=5, values_only=True):
        if row and row[1]:
            names.add(row[1])
    assert len(names) == 1


# ------------------------------------------------------------------ M-27


def test_wifi_denial_is_logged_without_plaintext_ip(client, db, caplog):
    tenant = make_tenant(db)
    admin = make_user(db, tenant, UserRole.TENANT_ADMIN)
    employee = make_user(db, tenant, UserRole.EMPLOYEE)
    _set_settings(client, admin, tenant, office_ips=[OFFICE_IP])
    with caplog.at_level(logging.WARNING, logger="kpi_system"):
        resp = client.post(
            "/api/attendance/check-in",
            headers=_headers(employee, tenant, ip=HOME_IP),
        )
    assert resp.status_code == 403, resp.text
    denial = [r for r in caplog.records if "WiFi-gate denial" in r.message]
    assert denial, "denial must be logged"
    assert HOME_IP not in denial[0].message
    assert str(employee.id) in denial[0].message


# ------------------------------------------------------------------ M-28


def test_export_out_of_scope_target_is_404(client, db):
    tenant = make_tenant(db)
    admin = make_user(db, tenant, UserRole.TENANT_ADMIN)
    sales = make_department(db, tenant, name="Sales")
    ops = make_department(db, tenant, name="Operations")
    dana = make_user(db, tenant, UserRole.DEPT_ADMIN, name="Dana", department=sales)
    carol = make_user(db, tenant, UserRole.EMPLOYEE, name="Carol", department=ops)
    today = app_today()
    # A dept admin probing another department gets 404, not a
    # headers-only workbook.
    resp = client.get(
        "/api/attendance/report/export",
        params={"year": today.year, "month": today.month, "department_id": ops.id},
        headers=auth_headers(dana, tenant),
    )
    assert resp.status_code == 404, resp.text
    resp = client.get(
        "/api/attendance/report/export",
        params={"year": today.year, "month": today.month, "user_id": carol.id},
        headers=auth_headers(dana, tenant),
    )
    assert resp.status_code == 404, resp.text
    # The tenant admin can still export both scopes.
    for params in (
        {"department_id": ops.id},
        {"user_id": carol.id},
    ):
        resp = client.get(
            "/api/attendance/report/export",
            params={"year": today.year, "month": today.month, **params},
            headers=auth_headers(admin, tenant),
        )
        assert resp.status_code == 200, resp.text


# ------------------------------------------------------------------ M-30


def test_today_flags_open_yesterday_record(client, db):
    from datetime import time as time_type

    tenant = make_tenant(db)
    admin = make_user(db, tenant, UserRole.TENANT_ADMIN)
    employee = make_user(db, tenant, UserRole.EMPLOYEE)
    _set_settings(client, admin, tenant)
    today = app_today()
    yesterday = today - timedelta(days=1)
    _insert_record(
        db, tenant, employee, yesterday, "present",
        check_in=time_type(9, 0),
    )
    resp = client.get("/api/attendance/today", headers=auth_headers(employee, tenant))
    assert resp.status_code == 200, resp.text
    assert resp.json()["has_open_yesterday_record"] is True
    # The server fallback closes yesterday's record.
    resp = client.post(
        "/api/attendance/check-out", headers=auth_headers(employee, tenant)
    )
    assert resp.status_code == 200, resp.text
    assert resp.json()["record_date"] == yesterday.isoformat()
    assert resp.json()["check_out"] is not None


# ------------------------------------------------------------------ M-40


def test_settings_patch_is_rate_limited(client, db):
    tenant = make_tenant(db)
    admin = make_user(db, tenant, UserRole.TENANT_ADMIN)
    codes = []
    for _ in range(11):
        resp = client.patch(
            "/api/attendance/settings",
            json={"work_days": [0, 1, 2, 3, 4]},
            headers=auth_headers(admin, tenant),
        )
        codes.append(resp.status_code)
    assert codes[:10] == [200] * 10
    assert codes[10] == 429


def test_leave_create_is_rate_limited(client, db):
    tenant = make_tenant(db)
    admin = make_user(db, tenant, UserRole.TENANT_ADMIN)
    employee = make_user(db, tenant, UserRole.EMPLOYEE)
    _set_settings(client, admin, tenant)
    today = app_today()
    days = _working_weekdays(today.year, today.month)
    assert len(days) >= 11
    codes = []
    for day in days[:11]:
        resp = client.post(
            "/api/attendance/leave",
            json={"record_date": day.isoformat()},
            headers=auth_headers(employee, tenant),
        )
        codes.append(resp.status_code)
    assert codes[:10] == [201] * 10
    assert codes[10] == 429


def test_kpi_save_is_rate_limited(client, db):
    from app.tests.factories import open_fillable_period

    tenant = make_tenant(db)
    employee = make_user(db, tenant, UserRole.EMPLOYEE)
    year, period = open_fillable_period()
    codes = []
    for _ in range(21):
        resp = client.post(
            "/api/kpi/employee/save",
            json={"year": year, "period": period, "scores": {}, "comments": {}},
            headers=auth_headers(employee, tenant),
        )
        codes.append(resp.status_code)
    assert codes[:20] == [200] * 20
    assert codes[20] == 429
