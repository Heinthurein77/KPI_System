"""Attendance module tests (additive-only; no existing test touched).

Covers: the IP gate (allow/reject, unenforced-when-unconfigured), the
half-day trigger both ways, half = 0.5 math, leave = absent, late recorded
but never deducted, month-end %, tenant isolation, and the Attendance-only
auto-write (other KPIs untouched, DRAFT-only, idempotent).
"""

from datetime import date as date_type, time as time_type

import pytest

from app.models.attendance import AttendanceRecord, AttendanceSettings
from app.models.kpi_submission import KPIStatus
from app.services.attendance_service import (
    day_status,
    late_minutes,
    midday,
    monthly_summary,
    parse_time,
    working_days_in_month,
)
from app.tests.factories import (
    MONTH_NAMES,
    auth_headers,
    make_department,
    make_submission,
    make_template,
    make_tenant,
    make_user,
)
from app.models.kpi_submission import KPISubmission
from app.models.user import UserRole

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
    return working_days_in_month(year, month, [0, 1, 2, 3, 4])


# ------------------------------------------------------------ pure service


def test_midday_and_late_minutes():
    start, end = parse_time("09:00"), parse_time("17:00")
    assert midday(start, end) == time_type(13, 0)
    assert late_minutes(time_type(9, 0), start) == 0
    assert late_minutes(time_type(8, 30), start) == 0
    assert late_minutes(time_type(9, 20), start) == 20
    assert late_minutes(time_type(13, 30), start) == 270


def test_half_day_trigger_check_in_at_or_after_midday():
    start, end = parse_time("09:00"), parse_time("17:00")
    assert day_status(time_type(9, 0), time_type(17, 0), start, end) == "present"
    assert day_status(time_type(9, 30), time_type(17, 0), start, end) == "present"
    # Late before midday is still a full day — late never deducts.
    assert day_status(time_type(12, 59), time_type(17, 0), start, end) == "present"
    assert day_status(time_type(13, 0), time_type(17, 0), start, end) == "half"
    assert day_status(time_type(15, 0), time_type(17, 0), start, end) == "half"


def test_half_day_trigger_check_out_before_midday():
    start, end = parse_time("09:00"), parse_time("17:00")
    assert day_status(time_type(9, 0), time_type(17, 0), start, end) == "present"
    assert day_status(time_type(9, 0), time_type(12, 59), start, end) == "half"
    assert day_status(time_type(9, 0), time_type(11, 0), start, end) == "half"
    # Check-out exactly at midday is a full day.
    assert day_status(time_type(9, 0), time_type(13, 0), start, end) == "present"


def test_monthly_summary_spec_example():
    # 20 full + 1 half + 1 absent out of 22 working days = 93.2%.
    # October 2026 has exactly 22 weekdays.
    days = working_days_in_month(2026, 10, [0, 1, 2, 3, 4])
    assert len(days) == 22
    records = {d: "present" for d in days[:20]}
    records[days[20]] = "half"
    result = monthly_summary(records, days)
    assert result == {
        "working_days": 22, "present": 20, "half": 1,
        "leave": 0, "absent": 1, "attendance_pct": 93.2,
    }


def test_monthly_summary_leave_counts_as_absent():
    days = working_days_in_month(2026, 10, [0, 1, 2, 3, 4])
    records = {d: "present" for d in days[:20]}
    records[days[20]] = "leave"
    records[days[21]] = "leave"
    result = monthly_summary(records, days)
    assert result["present"] == 20
    assert result["leave"] == 2
    assert result["absent"] == 0
    # 20 present + 2 leave out of 22 = 90.9% (leave earns no credit).
    assert result["attendance_pct"] == 90.9


def test_working_days_exclude_weekends_and_holidays():
    days = working_days_in_month(2026, 10, [0, 1, 2, 3, 4], {date_type(2026, 10, 5)})
    assert all(d.weekday() < 5 for d in days)
    assert date_type(2026, 10, 5) not in days  # Monday holiday removed
    assert len(days) == 21  # 22 weekdays in Oct 2026 minus 1 holiday


# ------------------------------------------------------------ settings API


def test_settings_defaults_and_tenant_admin_only(client, db):
    tenant = make_tenant(db)
    admin = make_user(db, tenant, UserRole.TENANT_ADMIN)
    employee = make_user(db, tenant, UserRole.EMPLOYEE)
    resp = client.get("/api/attendance/settings", headers=auth_headers(admin, tenant))
    assert resp.status_code == 200
    assert resp.json()["work_days"] == [0, 1, 2, 3, 4]
    assert resp.json()["start_time"] == "09:00"
    assert resp.json()["end_time"] == "17:00"
    assert resp.json()["midday"] == "13:00"
    assert resp.json()["wifi_enforced"] is False
    # Employees may not read or change settings.
    assert client.get("/api/attendance/settings", headers=auth_headers(employee, tenant)).status_code == 403
    assert client.patch(
        "/api/attendance/settings", json={"start_time": "08:00"},
        headers=auth_headers(employee, tenant),
    ).status_code == 403


def test_settings_update_validates_and_scopes_per_tenant(client, db):
    tenant = make_tenant(db)
    admin = make_user(db, tenant, UserRole.TENANT_ADMIN)
    resp = client.patch(
        "/api/attendance/settings",
        json={"start_time": "18:00", "end_time": "17:00"},
        headers=auth_headers(admin, tenant),
    )
    assert resp.status_code == 400
    resp = client.patch(
        "/api/attendance/settings",
        json={"work_days": [0, 1, 2, 3, 4], "office_ips": [OFFICE_IP], "holidays": ["2026-11-15"]},
        headers=auth_headers(admin, tenant),
    )
    assert resp.status_code == 200, resp.text
    assert resp.json()["office_ips"] == [OFFICE_IP]
    assert resp.json()["wifi_enforced"] is True
    row = db.query(AttendanceSettings).filter_by(tenant_id=tenant.id).one()
    assert row.office_ips == [OFFICE_IP]


# ------------------------------------------------------------ IP gate


def test_check_in_allowed_when_no_ips_configured(client, db):
    tenant = make_tenant(db)
    admin = make_user(db, tenant, UserRole.TENANT_ADMIN)
    employee = make_user(db, tenant, UserRole.EMPLOYEE)
    _set_settings(client, admin, tenant)  # all-week work days, no IPs
    resp = client.post("/api/attendance/check-in", headers=_headers(employee, tenant, HOME_IP))
    assert resp.status_code == 201, resp.text
    assert resp.json()["status"] in ("present", "half")
    assert resp.json()["check_in"] is not None


def test_check_in_rejected_off_office_wifi(client, db):
    tenant = make_tenant(db)
    admin = make_user(db, tenant, UserRole.TENANT_ADMIN)
    employee = make_user(db, tenant, UserRole.EMPLOYEE)
    _set_settings(client, admin, tenant, office_ips=[OFFICE_IP])
    resp = client.post("/api/attendance/check-in", headers=_headers(employee, tenant, HOME_IP))
    assert resp.status_code == 403
    assert "office WiFi" in resp.json()["detail"]
    resp = client.post("/api/attendance/check-in", headers=_headers(employee, tenant, OFFICE_IP))
    assert resp.status_code == 201, resp.text


def test_today_reports_wifi_status(client, db):
    tenant = make_tenant(db)
    admin = make_user(db, tenant, UserRole.TENANT_ADMIN)
    employee = make_user(db, tenant, UserRole.EMPLOYEE)
    _set_settings(client, admin, tenant, office_ips=[OFFICE_IP])
    resp = client.get("/api/attendance/today", headers=_headers(employee, tenant, HOME_IP))
    assert resp.status_code == 200
    assert resp.json()["wifi_enforced"] is True
    assert resp.json()["ip_allowed"] is False
    resp = client.get("/api/attendance/today", headers=_headers(employee, tenant, OFFICE_IP))
    assert resp.json()["ip_allowed"] is True


def test_check_in_twice_and_check_out_flow(client, db):
    tenant = make_tenant(db)
    admin = make_user(db, tenant, UserRole.TENANT_ADMIN)
    employee = make_user(db, tenant, UserRole.EMPLOYEE)
    _set_settings(client, admin, tenant)
    assert client.post("/api/attendance/check-in", headers=_headers(employee, tenant)).status_code == 201
    assert client.post("/api/attendance/check-in", headers=_headers(employee, tenant)).status_code == 409
    resp = client.post("/api/attendance/check-out", headers=_headers(employee, tenant))
    assert resp.status_code == 200, resp.text
    assert resp.json()["check_out"] is not None
    assert client.post("/api/attendance/check-out", headers=_headers(employee, tenant)).status_code == 409


def test_check_in_rejected_on_non_working_day(client, db):
    from app.core.timezone import today as app_today

    tenant = make_tenant(db)
    admin = make_user(db, tenant, UserRole.TENANT_ADMIN)
    employee = make_user(db, tenant, UserRole.EMPLOYEE)
    today = app_today()
    _set_settings(client, admin, tenant, work_days=[d for d in range(7) if d != today.weekday()])
    resp = client.post("/api/attendance/check-in", headers=_headers(employee, tenant))
    assert resp.status_code == 400


def test_tenant_admin_cannot_check_in(client, db):
    tenant = make_tenant(db)
    admin = make_user(db, tenant, UserRole.TENANT_ADMIN)
    _set_settings(client, admin, tenant)
    assert client.post("/api/attendance/check-in", headers=_headers(admin, tenant)).status_code == 403


# ------------------------------------------------------------ leave & summary


def _insert_record(db, tenant, employee, day, status, check_in=None, late=0):
    record = AttendanceRecord(
        tenant_id=tenant.id,
        employee_id=employee.id,
        department_id=employee.department_id,
        record_date=day,
        check_in=check_in,
        status=status,
        late_minutes=late,
    )
    db.add(record)
    db.commit()
    return record


def test_leave_marks_day_and_counts_as_absent(client, db):
    from app.core.timezone import today as app_today

    tenant = make_tenant(db)
    admin = make_user(db, tenant, UserRole.TENANT_ADMIN)
    employee = make_user(db, tenant, UserRole.EMPLOYEE)
    _set_settings(client, admin, tenant, work_days=[0, 1, 2, 3, 4])
    today = app_today()
    year, month = today.year, today.month
    leave_day = _working_weekdays(year, month)[0]
    resp = client.post(
        "/api/attendance/leave",
        json={"record_date": leave_day.isoformat()},
        headers=auth_headers(employee, tenant),
    )
    assert resp.status_code == 201, resp.text
    assert resp.json()["status"] == "leave"
    summary = client.get(
        "/api/attendance/summary", params={"year": year, "month": month},
        headers=auth_headers(employee, tenant),
    ).json()
    assert summary["leave"] == 1
    assert summary["attendance_pct"] < 100.0


def test_cannot_check_in_on_leave_day(client, db):
    from app.core.timezone import today as app_today

    tenant = make_tenant(db)
    admin = make_user(db, tenant, UserRole.TENANT_ADMIN)
    employee = make_user(db, tenant, UserRole.EMPLOYEE)
    _set_settings(client, admin, tenant)
    today = app_today()
    if today.weekday() not in (0, 1, 2, 3, 4):
        pytest.skip("today is not a working day")
    _insert_record(db, tenant, employee, today, "leave")
    assert client.post("/api/attendance/check-in", headers=_headers(employee, tenant)).status_code == 409


def test_summary_counts_present_half_absent_and_late(client, db):
    from app.core.timezone import today as app_today

    tenant = make_tenant(db)
    admin = make_user(db, tenant, UserRole.TENANT_ADMIN)
    employee = make_user(db, tenant, UserRole.EMPLOYEE)
    _set_settings(client, admin, tenant, work_days=[0, 1, 2, 3, 4])
    today = app_today()
    year, month = today.year, today.month
    days = _working_weekdays(year, month)
    assert len(days) >= 3
    # 1 present (25 min late — recorded, not deducted), 1 half, rest absent.
    _insert_record(db, tenant, employee, days[0], "present", check_in=time_type(9, 25), late=25)
    _insert_record(db, tenant, employee, days[1], "half", check_in=time_type(14, 0), late=300)
    summary = client.get(
        "/api/attendance/summary", params={"year": year, "month": month},
        headers=auth_headers(employee, tenant),
    ).json()
    assert summary["working_days"] == len(days)
    assert summary["present"] == 1
    assert summary["half"] == 1
    assert summary["absent"] == len(days) - 2
    assert summary["total_late_minutes"] == 325
    assert summary["late_days"] == 2
    assert summary["attendance_pct"] == round(1.5 / len(days) * 100, 1)


# ------------------------------------------------------------ tenant isolation


def test_tenant_isolation(client, db):
    tenant_a = make_tenant(db, name="A", slug="a")
    tenant_b = make_tenant(db, name="B", slug="b")
    admin_a = make_user(db, tenant_a, UserRole.TENANT_ADMIN)
    employee_a = make_user(db, tenant_a, UserRole.EMPLOYEE, name="Alice")
    employee_b = make_user(db, tenant_b, UserRole.EMPLOYEE, name="Bob")
    _set_settings(client, admin_a, tenant_a)
    from app.core.timezone import today as app_today

    today = app_today()
    _insert_record(db, tenant_a, employee_a, today, "present", check_in=time_type(9, 0))
    # B's employee cannot read A's employee summary.
    resp = client.get(
        "/api/attendance/summary",
        params={"year": today.year, "month": today.month, "user_id": employee_a.id},
        headers=auth_headers(employee_b, tenant_b),
    )
    assert resp.status_code in (403, 404)
    # B's settings are independent: B has no IPs configured (unenforced).
    admin_b = make_user(db, tenant_b, UserRole.TENANT_ADMIN, name="Admin B")
    resp = client.get("/api/attendance/settings", headers=auth_headers(admin_b, tenant_b))
    assert resp.json()["wifi_enforced"] is False


# ------------------------------------------------------------ month-end calc


def _calc_setup(db, client, with_template=True):
    from app.core.timezone import today as app_today

    tenant = make_tenant(db)
    admin = make_user(db, tenant, UserRole.TENANT_ADMIN)
    employee = make_user(db, tenant, UserRole.EMPLOYEE)
    other_template = make_template(db, tenant, metric_name="Sales", target=100.0, weight=10.0)
    attendance_template = None
    if with_template:
        attendance_template = make_template(
            db, tenant, metric_name="Attendance", target=100.0, weight=10.0
        )
    today = app_today()
    return tenant, admin, employee, other_template, attendance_template, today.year, today.month


def test_calc_writes_attendance_self_score_only(client, db):
    tenant, admin, employee, other_template, att_template, year, month = _calc_setup(db, client)
    _set_settings(client, admin, tenant, work_days=[0, 1, 2, 3, 4])
    days = _working_weekdays(year, month)
    _insert_record(db, tenant, employee, days[0], "present", check_in=time_type(9, 0))
    _insert_record(db, tenant, employee, days[1], "half", check_in=time_type(14, 0), late=300)
    # A pre-existing DRAFT submission for another KPI, with scores set.
    other_sub = make_submission(
        db, tenant, employee, other_template, year=year, period=MONTH_NAMES[month - 1],
        status=KPIStatus.DRAFT, self_score=80.0, dept_score=75.0, final_score=None,
    )
    resp = client.post(
        "/api/attendance/calc", params={"year": year, "month": month},
        headers=auth_headers(admin, tenant),
    )
    assert resp.status_code == 200, resp.text
    result = resp.json()["results"][0]
    expected = round(1.5 / len(days) * 100, 1)
    assert result["attendance_pct"] == expected
    assert result["written"] is True
    # The Attendance submission was created as DRAFT with the % as self_score.
    sub = db.query(KPISubmission).filter_by(kpi_template_id=att_template.id).one()
    assert sub.status == KPIStatus.DRAFT
    assert sub.self_score == expected
    assert sub.dept_score is None and sub.final_score is None
    # The other KPI submission is completely untouched.
    db.refresh(other_sub)
    assert other_sub.self_score == 80.0
    assert other_sub.dept_score == 75.0
    # Idempotent: a second run writes the same value, still DRAFT.
    resp2 = client.post(
        "/api/attendance/calc", params={"year": year, "month": month},
        headers=auth_headers(admin, tenant),
    )
    assert resp2.json()["results"][0]["written"] is True
    assert resp2.json()["results"][0]["attendance_pct"] == expected


def test_calc_skips_non_draft_submission(client, db):
    tenant, admin, employee, _other, att_template, year, month = _calc_setup(db, client)
    _set_settings(client, admin, tenant, work_days=[0, 1, 2, 3, 4])
    days = _working_weekdays(year, month)
    _insert_record(db, tenant, employee, days[0], "present", check_in=time_type(9, 0))
    make_submission(
        db, tenant, employee, att_template, year=year, period=MONTH_NAMES[month - 1],
        status=KPIStatus.APPROVED, self_score=50.0, final_score=50.0,
    )
    resp = client.post(
        "/api/attendance/calc", params={"year": year, "month": month},
        headers=auth_headers(admin, tenant),
    )
    assert resp.status_code == 200
    assert resp.json()["results"][0]["written"] is False
    assert "APPROVED" in resp.json()["results"][0]["reason"] or "approved" in resp.json()["results"][0]["reason"]
    sub = db.query(KPISubmission).filter_by(kpi_template_id=att_template.id).one()
    assert sub.self_score == 50.0 and sub.final_score == 50.0


def test_calc_without_attendance_template_writes_nothing(client, db):
    tenant, admin, employee, _other, _none, year, month = _calc_setup(db, client, with_template=False)
    _set_settings(client, admin, tenant, work_days=[0, 1, 2, 3, 4])
    days = _working_weekdays(year, month)
    _insert_record(db, tenant, employee, days[0], "present", check_in=time_type(9, 0))
    resp = client.post(
        "/api/attendance/calc", params={"year": year, "month": month},
        headers=auth_headers(admin, tenant),
    )
    assert resp.status_code == 200
    assert resp.json()["results"][0]["written"] is False
    assert "No company-wide" in resp.json()["results"][0]["reason"]


def test_calc_rejects_future_month_and_non_admin(client, db):
    from app.core.timezone import today as app_today

    tenant, admin, employee, _o, _a, year, month = _calc_setup(db, client)
    today = app_today()
    future_month = today.month + 1 if today.month < 12 else 1
    future_year = today.year if today.month < 12 else today.year + 1
    resp = client.post(
        "/api/attendance/calc", params={"year": future_year, "month": future_month},
        headers=auth_headers(admin, tenant),
    )
    assert resp.status_code == 400
    resp = client.post(
        "/api/attendance/calc", params={"year": year, "month": month},
        headers=auth_headers(employee, tenant),
    )
    assert resp.status_code == 403
