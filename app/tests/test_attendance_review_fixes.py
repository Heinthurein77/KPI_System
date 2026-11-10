"""Tests for the 2026-11-10 review fix pass (code quality + validation workstream).

Covers M9 (year validation), M10 (effective settings + office-IP
validation), M11 (holidays in /today + check-in/out), m11 (leave date
constraints), m12 (seconds stored on check-in/out), m19 (dept admin with
no department), m20 (export 404s), m7 (sole-admin guard), m8
(revoked-token prune + tokens without jti rejected), m15 (audit-log /
notification offset paging).
"""

from datetime import date as date_type, datetime, time as time_type, timedelta, timezone

import jwt

from app.core.config import settings
from app.core.security import JWT_ALGORITHM
from app.core.timezone import now as app_now, today as app_today
from app.jobs.prune_audit_logs import prune_revoked_tokens
from app.models.attendance import AttendanceRecord
from app.models.audit_log import AuditLog
from app.models.notification import Notification
from app.models.revoked_token import RevokedToken
from app.models.user import UserRole
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


def _insert_record(db, tenant, employee, day, status, check_in=None):
    record = AttendanceRecord(
        tenant_id=tenant.id,
        employee_id=employee.id,
        department_id=employee.department_id,
        record_date=day,
        check_in=check_in,
        status=status,
        late_minutes=0,
    )
    db.add(record)
    db.commit()
    return record


# ---------------------------------------------------------------- M9


def test_summary_rejects_out_of_range_year(client, db):
    tenant = make_tenant(db)
    admin = make_user(db, tenant, UserRole.TENANT_ADMIN)
    for year in (0, -1, 10000, 100000):
        resp = client.get(
            "/api/attendance/summary",
            params={"year": year, "month": 11},
            headers=auth_headers(admin, tenant),
        )
        assert resp.status_code == 400, (year, resp.text)
        assert "year" in resp.json()["detail"]


def test_summary_december_9999_works(client, db):
    tenant = make_tenant(db)
    admin = make_user(db, tenant, UserRole.TENANT_ADMIN)
    resp = client.get(
        "/api/attendance/summary",
        params={"year": 9999, "month": 12},
        headers=auth_headers(admin, tenant),
    )
    assert resp.status_code == 200, resp.text
    assert resp.json()["year"] == 9999 and resp.json()["month"] == 12


def test_export_rejects_out_of_range_year(client, db):
    tenant = make_tenant(db)
    admin = make_user(db, tenant, UserRole.TENANT_ADMIN)
    resp = client.get(
        "/api/attendance/report/export",
        params={"year": 0, "month": 11},
        headers=auth_headers(admin, tenant),
    )
    assert resp.status_code == 400, resp.text
    resp = client.get(
        "/api/attendance/report/export",
        params={"year": 10000, "month": 1},
        headers=auth_headers(admin, tenant),
    )
    assert resp.status_code == 400, resp.text


def test_export_december_9999_works(client, db):
    tenant = make_tenant(db)
    admin = make_user(db, tenant, UserRole.TENANT_ADMIN)
    resp = client.get(
        "/api/attendance/report/export",
        params={"year": 9999, "month": 12},
        headers=auth_headers(admin, tenant),
    )
    assert resp.status_code == 200, resp.text
    assert "spreadsheetml.sheet" in resp.headers["content-type"]


# ---------------------------------------------------------------- M10


def test_settings_patch_validates_effective_start_end(client, db):
    tenant = make_tenant(db)
    admin = make_user(db, tenant, UserRole.TENANT_ADMIN)
    # Stored 09:00-17:00; patching ONLY start_time to 18:00 inverts the day.
    resp = client.patch(
        "/api/attendance/settings",
        json={"start_time": "18:00"},
        headers=auth_headers(admin, tenant),
    )
    assert resp.status_code == 400, resp.text
    # Patching ONLY end_time to 08:00 inverts it the other way.
    resp = client.patch(
        "/api/attendance/settings",
        json={"end_time": "08:00"},
        headers=auth_headers(admin, tenant),
    )
    assert resp.status_code == 400, resp.text
    # A non-inverting single-side patch is still accepted.
    resp = client.patch(
        "/api/attendance/settings",
        json={"start_time": "08:00"},
        headers=auth_headers(admin, tenant),
    )
    assert resp.status_code == 200, resp.text
    assert resp.json()["start_time"] == "08:00"


def test_settings_patch_rejects_bad_office_ips(client, db):
    tenant = make_tenant(db)
    admin = make_user(db, tenant, UserRole.TENANT_ADMIN)
    for bad in ("not-an-ip", "999.999.999.999", "10.0.0.0/24", ""):
        if not bad:
            continue
        resp = client.patch(
            "/api/attendance/settings",
            json={"office_ips": [bad]},
            headers=auth_headers(admin, tenant),
        )
        assert resp.status_code == 422, (bad, resp.text)


def test_settings_patch_rejects_private_office_ips(client, db):
    tenant = make_tenant(db)
    admin = make_user(db, tenant, UserRole.TENANT_ADMIN)
    for private in ("10.0.0.5", "192.168.200.40", "172.16.5.5"):
        resp = client.patch(
            "/api/attendance/settings",
            json={"office_ips": [private]},
            headers=auth_headers(admin, tenant),
        )
        assert resp.status_code == 422, (private, resp.text)
        assert "private" in resp.text.lower()
    # A public IP is still accepted.
    resp = client.patch(
        "/api/attendance/settings",
        json={"office_ips": [OFFICE_IP]},
        headers=auth_headers(admin, tenant),
    )
    assert resp.status_code == 200, resp.text


def test_settings_patch_rejects_boolean_work_days(client, db):
    tenant = make_tenant(db)
    admin = make_user(db, tenant, UserRole.TENANT_ADMIN)
    resp = client.patch(
        "/api/attendance/settings",
        json={"work_days": [True, 1, 2]},
        headers=auth_headers(admin, tenant),
    )
    assert resp.status_code == 422, resp.text
    resp = client.patch(
        "/api/attendance/settings",
        json={"work_days": [0, 1, 2, 3, 4]},
        headers=auth_headers(admin, tenant),
    )
    assert resp.status_code == 200, resp.text


# ---------------------------------------------------------------- M11


def test_holiday_weekday_is_not_a_working_day(client, db):
    tenant = make_tenant(db)
    admin = make_user(db, tenant, UserRole.TENANT_ADMIN)
    employee = make_user(db, tenant, UserRole.EMPLOYEE)
    today = app_today()
    _set_settings(
        client, admin, tenant, work_days=[0, 1, 2, 3, 4, 5, 6],
        holidays=[today.isoformat()],
    )
    # /today says it is not a working day …
    resp = client.get("/api/attendance/today", headers=_headers(employee, tenant))
    assert resp.status_code == 200, resp.text
    assert resp.json()["is_working_day"] is False
    # … check-in is rejected …
    resp = client.post("/api/attendance/check-in", headers=_headers(employee, tenant))
    assert resp.status_code == 400, resp.text
    # … and no phantom record was created, so leave can still be marked
    # for a real working day afterwards.
    assert db.query(AttendanceRecord).count() == 0
    other_day = today + timedelta(days=1 if today.weekday() < 5 else 2)
    # (the next day is a working day: it is not a holiday and the
    # work_days setting above covers every weekday)
    resp = client.post(
        "/api/attendance/leave",
        json={"record_date": other_day.isoformat()},
        headers=auth_headers(employee, tenant),
    )
    assert resp.status_code == 201, resp.text


def test_check_out_rejected_when_record_day_becomes_holiday(client, db):
    tenant = make_tenant(db)
    admin = make_user(db, tenant, UserRole.TENANT_ADMIN)
    employee = make_user(db, tenant, UserRole.EMPLOYEE)
    _set_settings(client, admin, tenant)
    today = app_today()
    assert client.post(
        "/api/attendance/check-in", headers=_headers(employee, tenant)
    ).status_code == 201
    _set_settings(client, admin, tenant, holidays=[today.isoformat()])
    resp = client.post("/api/attendance/check-out", headers=_headers(employee, tenant))
    assert resp.status_code == 400, resp.text


# ---------------------------------------------------------------- m11


def test_leave_rejected_on_weekend_and_holiday(client, db):
    tenant = make_tenant(db)
    admin = make_user(db, tenant, UserRole.TENANT_ADMIN)
    employee = make_user(db, tenant, UserRole.EMPLOYEE)
    _set_settings(
        client, admin, tenant, work_days=[0, 1, 2, 3, 4], holidays=["2026-11-16"]
    )
    # 2026-11-14 is a Saturday.
    resp = client.post(
        "/api/attendance/leave",
        json={"record_date": "2026-11-14"},
        headers=auth_headers(employee, tenant),
    )
    assert resp.status_code == 400, resp.text
    # 2026-11-16 is a Monday, but a configured holiday.
    resp = client.post(
        "/api/attendance/leave",
        json={"record_date": "2026-11-16"},
        headers=auth_headers(employee, tenant),
    )
    assert resp.status_code == 400, resp.text
    # A plain working day still works.
    resp = client.post(
        "/api/attendance/leave",
        json={"record_date": "2026-11-17"},
        headers=auth_headers(employee, tenant),
    )
    assert resp.status_code == 201, resp.text


def test_patch_leave_rejects_cross_month_and_non_working_day(client, db):
    tenant = make_tenant(db)
    admin = make_user(db, tenant, UserRole.TENANT_ADMIN)
    employee = make_user(db, tenant, UserRole.EMPLOYEE)
    _set_settings(client, admin, tenant, work_days=[0, 1, 2, 3, 4])
    leave = _insert_record(db, tenant, employee, date_type(2026, 11, 2), "leave")
    # Across months is not allowed — delete + re-mark instead.
    resp = client.patch(
        f"/api/attendance/records/{leave.id}",
        json={"record_date": "2026-12-02"},
        headers=auth_headers(employee, tenant),
    )
    assert resp.status_code == 400, resp.text
    assert "same month" in resp.json()["detail"]
    # A Saturday in the same month is not allowed either.
    resp = client.patch(
        f"/api/attendance/records/{leave.id}",
        json={"record_date": "2026-11-07"},
        headers=auth_headers(employee, tenant),
    )
    assert resp.status_code == 400, resp.text


# ---------------------------------------------------------------- m12


def test_check_in_and_out_store_seconds(client, db):
    tenant = make_tenant(db)
    admin = make_user(db, tenant, UserRole.TENANT_ADMIN)
    employee = make_user(db, tenant, UserRole.EMPLOYEE)
    _set_settings(client, admin, tenant)
    before = app_now().time().replace(microsecond=0)
    resp = client.post("/api/attendance/check-in", headers=_headers(employee, tenant))
    assert resp.status_code == 201, resp.text
    after = app_now().time().replace(microsecond=0)
    record = db.query(AttendanceRecord).one()
    # Seconds are stored (not truncated to :00): the stored time falls
    # inside the window the request ran in.
    assert before <= record.check_in <= after
    # The API display is still HH:MM.
    assert resp.json()["check_in"] == record.check_in.strftime("%H:%M")
    before = app_now().time().replace(microsecond=0)
    resp = client.post("/api/attendance/check-out", headers=_headers(employee, tenant))
    assert resp.status_code == 200, resp.text
    after = app_now().time().replace(microsecond=0)
    db.refresh(record)
    assert before <= record.check_out <= after
    assert resp.json()["check_out"] == record.check_out.strftime("%H:%M")


# ---------------------------------------------------------------- m19


def test_dept_admin_without_department_sees_only_self(client, db):
    tenant = make_tenant(db)
    admin = make_user(db, tenant, UserRole.TENANT_ADMIN)
    dept = make_department(db, tenant)
    alice = make_user(db, tenant, UserRole.EMPLOYEE, department=dept, name="Alice Aung")
    # Legacy/NULL data: a dept admin with no department.
    dana = make_user(db, tenant, UserRole.DEPT_ADMIN, name="Dana Dept", department=None)
    resp = client.get(
        "/api/attendance/report",
        params={"year": 2026, "month": 11},
        headers=auth_headers(dana, tenant),
    )
    assert resp.status_code == 200, resp.text
    rows = resp.json()
    assert [r["user_id"] for r in rows] == [dana.id]
    # And she may not view another user's summary (no IS NULL leak).
    resp = client.get(
        "/api/attendance/summary",
        params={"year": 2026, "month": 11, "user_id": alice.id},
        headers=auth_headers(dana, tenant),
    )
    assert resp.status_code == 403, resp.text


# ---------------------------------------------------------------- m20


def _export_setup(db):
    tenant = make_tenant(db)
    admin = make_user(db, tenant, UserRole.TENANT_ADMIN, name="Hein Admin")
    sales = make_department(db, tenant, name="Sales")
    ops = make_department(db, tenant, name="Operations")
    dana = make_user(db, tenant, UserRole.DEPT_ADMIN, name="Dana Dept", department=sales)
    alice = make_user(db, tenant, UserRole.EMPLOYEE, name="Alice Aung", department=sales)
    return {
        "tenant": tenant, "admin": admin, "sales": sales, "ops": ops,
        "dana": dana, "alice": alice,
    }


def test_export_unknown_department_returns_404(client, db):
    fx = _export_setup(db)
    resp = client.get(
        "/api/attendance/report/export",
        params={"year": 2026, "month": 11, "department_id": 999999},
        headers=auth_headers(fx["admin"], fx["tenant"]),
    )
    assert resp.status_code == 404, resp.text


def test_export_unknown_user_returns_404(client, db):
    fx = _export_setup(db)
    resp = client.get(
        "/api/attendance/report/export",
        params={"year": 2026, "month": 11, "user_id": 999999},
        headers=auth_headers(fx["admin"], fx["tenant"]),
    )
    assert resp.status_code == 404, resp.text
    # Another tenant's department is unknown to this tenant too.
    other_tenant = make_tenant(db, name="Other", slug="other")
    other_dept = make_department(db, other_tenant, name="Other Dept")
    resp = client.get(
        "/api/attendance/report/export",
        params={"year": 2026, "month": 11, "department_id": other_dept.id},
        headers=auth_headers(fx["admin"], fx["tenant"]),
    )
    assert resp.status_code == 404, resp.text


def test_export_other_department_stays_headers_only_for_dept_admin(client, db):
    # A department that EXISTS but is outside the caller's scope still
    # yields a headers-only file (no name leak, no 404-oracle).
    from io import BytesIO

    from openpyxl import load_workbook

    fx = _export_setup(db)
    resp = client.get(
        "/api/attendance/report/export",
        params={"year": 2026, "month": 11, "department_id": fx["ops"].id},
        headers=auth_headers(fx["dana"], fx["tenant"]),
    )
    assert resp.status_code == 200, resp.text
    rows = list(load_workbook(BytesIO(resp.content)).active.iter_rows(values_only=True))
    assert [r for r in rows[4:] if any(v is not None for v in r)] == []
    assert "operations" not in resp.headers["content-disposition"].lower()


# ---------------------------------------------------------------- m7


def _admin_pair(db):
    tenant = make_tenant(db)
    admin = make_user(db, tenant, UserRole.TENANT_ADMIN, name="Admin A")
    other = make_user(db, tenant, UserRole.TENANT_ADMIN, name="Admin B")
    dept = make_department(db, tenant)
    return tenant, admin, other, dept


def test_toggle_last_other_admin_refused(client, db):
    tenant, admin, other, _dept = _admin_pair(db)
    resp = client.post(
        f"/api/admin/users/{other.id}/toggle-active",
        headers=auth_headers(admin, tenant),
    )
    assert resp.status_code == 400, resp.text
    assert "last other tenant admin" in resp.json()["detail"]
    db.refresh(other)
    assert other.is_active is True


def test_toggle_admin_allowed_when_third_admin_exists(client, db):
    tenant, admin, other, _dept = _admin_pair(db)
    make_user(db, tenant, UserRole.TENANT_ADMIN, name="Admin C")
    resp = client.post(
        f"/api/admin/users/{other.id}/toggle-active",
        headers=auth_headers(admin, tenant),
    )
    assert resp.status_code == 200, resp.text
    assert resp.json()["is_active"] is False


def test_toggle_inactive_admin_back_on_allowed(client, db):
    tenant, admin, _other, _dept = _admin_pair(db)
    inactive = make_user(
        db, tenant, UserRole.TENANT_ADMIN, name="Admin Off", is_active=False
    )
    resp = client.post(
        f"/api/admin/users/{inactive.id}/toggle-active",
        headers=auth_headers(admin, tenant),
    )
    assert resp.status_code == 200, resp.text
    assert resp.json()["is_active"] is True


def test_delete_last_other_admin_refused(client, db):
    tenant, admin, other, _dept = _admin_pair(db)
    resp = client.delete(
        f"/api/admin/users/{other.id}", headers=auth_headers(admin, tenant)
    )
    assert resp.status_code == 400, resp.text
    assert "last other tenant admin" in resp.json()["detail"]


def test_delete_admin_allowed_when_third_admin_exists(client, db):
    tenant, admin, other, _dept = _admin_pair(db)
    make_user(db, tenant, UserRole.TENANT_ADMIN, name="Admin C")
    resp = client.delete(
        f"/api/admin/users/{other.id}", headers=auth_headers(admin, tenant)
    )
    assert resp.status_code == 200, resp.text


def test_demote_last_other_admin_refused(client, db):
    tenant, admin, other, dept = _admin_pair(db)
    resp = client.put(
        f"/api/admin/users/{other.id}",
        json={
            "name": other.name,
            "email": other.email,
            "role": "employee",
            "department_id": dept.id,
        },
        headers=auth_headers(admin, tenant),
    )
    assert resp.status_code == 400, resp.text
    assert "last other tenant admin" in resp.json()["detail"]
    db.refresh(other)
    assert other.role == UserRole.TENANT_ADMIN


def test_edit_admin_without_role_change_allowed(client, db):
    tenant, admin, other, _dept = _admin_pair(db)
    resp = client.put(
        f"/api/admin/users/{other.id}",
        json={
            "name": "Admin B Renamed",
            "email": other.email,
            "role": "tenant_admin",
            "department_id": None,
        },
        headers=auth_headers(admin, tenant),
    )
    assert resp.status_code == 200, resp.text
    assert resp.json()["name"] == "Admin B Renamed"


# ---------------------------------------------------------------- m8


def test_prune_revoked_tokens_deletes_only_expired(db):
    tenant = make_tenant(db)
    user = make_user(db, tenant, UserRole.EMPLOYEE)
    now = datetime.now(timezone.utc)
    expired = RevokedToken(
        jti="expired-jti", user_id=user.id, expires_at=now - timedelta(hours=1)
    )
    live = RevokedToken(
        jti="live-jti", user_id=user.id, expires_at=now + timedelta(hours=23)
    )
    db.add(expired)
    db.add(live)
    db.commit()
    assert prune_revoked_tokens(db) == 1
    assert db.get(RevokedToken, "expired-jti") is None
    assert db.get(RevokedToken, "live-jti") is not None


def test_token_without_jti_is_rejected(client, db):
    tenant = make_tenant(db)
    employee = make_user(db, tenant, UserRole.EMPLOYEE)
    # A token minted before jti existed: valid signature + expiry, no jti.
    payload = {
        "sub": str(employee.id),
        "tenant_id": tenant.id,
        "ver": 0,
        "exp": datetime.now(timezone.utc) + timedelta(hours=1),
    }
    token = jwt.encode(payload, settings.SECRET_KEY, algorithm=JWT_ALGORITHM)
    resp = client.get(
        "/api/attendance/today",
        headers={"Authorization": f"Bearer {token}", "X-Tenant-Slug": tenant.slug},
    )
    assert resp.status_code == 401, resp.text


# ---------------------------------------------------------------- m15


def _make_audit_row(db, tenant, actor, seq):
    row = AuditLog(
        tenant_id=tenant.id,
        actor_id=actor.id,
        actor_name=actor.name,
        actor_role=actor.role.value,
        department_id=None,
        action="kpi_submitted",
        entity_type="kpi_submission",
        entity_id=seq,
        entity_label=f"Test metric {seq}",
        summary="test",
        created_at=datetime.now(timezone.utc) - timedelta(days=seq),
    )
    db.add(row)
    db.commit()
    return row


def test_audit_log_offset_paging(client, db):
    tenant = make_tenant(db)
    admin = make_user(db, tenant, UserRole.TENANT_ADMIN)
    for seq in (1, 2, 3):
        _make_audit_row(db, tenant, admin, seq)
    page1 = client.get(
        "/api/admin/audit-log",
        params={"limit": 2},
        headers=auth_headers(admin, tenant),
    )
    assert page1.status_code == 200, page1.text
    assert len(page1.json()) == 2
    page2 = client.get(
        "/api/admin/audit-log",
        params={"limit": 2, "offset": 2},
        headers=auth_headers(admin, tenant),
    )
    assert page2.status_code == 200, page2.text
    assert len(page2.json()) == 1
    assert page2.json()[0]["entity_id"] != page1.json()[0]["entity_id"]
    empty = client.get(
        "/api/admin/audit-log",
        params={"limit": 2, "offset": 99},
        headers=auth_headers(admin, tenant),
    )
    assert empty.json() == []


def test_notifications_offset_paging(client, db):
    tenant = make_tenant(db)
    employee = make_user(db, tenant, UserRole.EMPLOYEE)
    for seq in (1, 2, 3):
        db.add(
            Notification(
                tenant_id=tenant.id,
                user_id=employee.id,
                type="kpi_submitted",
                title=f"Note {seq}",
                message="test",
                link=None,
                is_read=False,
                created_at=datetime.now(timezone.utc) - timedelta(minutes=seq),
            )
        )
    db.commit()
    resp = client.get(
        "/api/notifications",
        params={"limit": 2, "offset": 1},
        headers=auth_headers(employee, tenant),
    )
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert len(body["notifications"]) == 2
    assert body["unread_count"] == 3
    assert body["notifications"][0]["title"] == "Note 2"
    resp = client.get(
        "/api/notifications",
        params={"limit": 2, "offset": 99},
        headers=auth_headers(employee, tenant),
    )
    assert resp.json()["notifications"] == []
