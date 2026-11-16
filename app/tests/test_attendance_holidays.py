"""Named holidays + banner data + bell notifications for Attendance.

Covers: legacy plain-string holidays read as {date, name: ""} (working-day
math unchanged — including a directly-seeded legacy row like the live
2026-11-23/2026-11-24 list), named-holiday round-trip, validation (bad
date / long name -> 422, duplicates keep the named entry), the /today
upcoming_holidays 7-day window, the attendance_holiday notification (one
row per active tenant user except the saving admin; none when only other
settings change), and the audit row still being written.
"""

from datetime import timedelta

from app.core.timezone import today as app_today
from app.models.attendance import AttendanceSettings
from app.models.audit_log import AuditLog
from app.models.notification import Notification
from app.models.user import UserRole
from app.tests.factories import auth_headers, make_tenant, make_user


def _patch_settings(client, admin, tenant, **kwargs):
    resp = client.patch(
        "/api/attendance/settings", json=kwargs, headers=auth_headers(admin, tenant)
    )
    assert resp.status_code == 200, resp.text
    return resp.json()


def _notifications(db, tenant):
    return (
        db.query(Notification)
        .filter_by(tenant_id=tenant.id, type="attendance_holiday")
        .all()
    )


# ------------------------------------------------------- legacy compat


def test_legacy_string_holidays_read_as_named_objects(client, db):
    tenant = make_tenant(db)
    admin = make_user(db, tenant, UserRole.TENANT_ADMIN)
    employee = make_user(db, tenant, UserRole.EMPLOYEE)
    saved = _patch_settings(client, admin, tenant, holidays=["2026-11-16"])
    assert saved["holidays"] == [{"date": "2026-11-16", "name": ""}]
    # Working-day math is unchanged: the legacy holiday is still a
    # non-working day (2026-11-16 is a Monday).
    resp = client.post(
        "/api/attendance/leave",
        json={"record_date": "2026-11-16"},
        headers=auth_headers(employee, tenant),
    )
    assert resp.status_code == 400, resp.text


def test_legacy_string_list_seeded_directly_still_works(client, db):
    # A row written before names existed (the live shape: plain strings,
    # e.g. the user's 2026-11-23 / 2026-11-24 list) must keep working
    # with no migration and no rewrite.
    tenant = make_tenant(db)
    admin = make_user(db, tenant, UserRole.TENANT_ADMIN)
    employee = make_user(db, tenant, UserRole.EMPLOYEE)
    row = AttendanceSettings(
        tenant_id=tenant.id, holidays_json='["2026-11-23", "2026-11-24"]'
    )
    db.add(row)
    db.commit()
    assert row.holidays == [
        {"date": "2026-11-23", "name": ""},
        {"date": "2026-11-24", "name": ""},
    ]
    resp = client.get("/api/attendance/settings", headers=auth_headers(admin, tenant))
    assert resp.status_code == 200
    assert resp.json()["holidays"] == [
        {"date": "2026-11-23", "name": ""},
        {"date": "2026-11-24", "name": ""},
    ]
    # /today still answers, and the legacy holidays are non-working days.
    resp = client.get("/api/attendance/today", headers=auth_headers(employee, tenant))
    assert resp.status_code == 200
    resp = client.post(
        "/api/attendance/leave",
        json={"record_date": "2026-11-23"},
        headers=auth_headers(employee, tenant),
    )
    assert resp.status_code == 400, resp.text


def test_corrupt_holiday_entries_are_skipped_not_fatal(client, db):
    tenant = make_tenant(db)
    admin = make_user(db, tenant, UserRole.TENANT_ADMIN)
    row = AttendanceSettings(
        tenant_id=tenant.id,
        holidays_json='["2026-11-23", {"date": "not-a-date", "name": "Bad"}, 42]',
    )
    db.add(row)
    db.commit()
    assert row.holidays == [{"date": "2026-11-23", "name": ""}]
    resp = client.get("/api/attendance/settings", headers=auth_headers(admin, tenant))
    assert resp.status_code == 200
    assert resp.json()["holidays"] == [{"date": "2026-11-23", "name": ""}]


# ------------------------------------------------------------ named write


def test_named_holidays_round_trip(client, db):
    tenant = make_tenant(db)
    admin = make_user(db, tenant, UserRole.TENANT_ADMIN)
    saved = _patch_settings(
        client, admin, tenant,
        holidays=[
            {"date": "2026-12-31", "name": ""},
            {"date": "2026-12-25", "name": "Christmas"},
        ],
    )
    # Sorted by date, names kept.
    assert saved["holidays"] == [
        {"date": "2026-12-25", "name": "Christmas"},
        {"date": "2026-12-31", "name": ""},
    ]
    row = db.query(AttendanceSettings).filter_by(tenant_id=tenant.id).one()
    assert row.holidays == saved["holidays"]


def test_holiday_name_too_long_returns_422(client, db):
    tenant = make_tenant(db)
    admin = make_user(db, tenant, UserRole.TENANT_ADMIN)
    resp = client.patch(
        "/api/attendance/settings",
        json={"holidays": [{"date": "2026-12-25", "name": "x" * 81}]},
        headers=auth_headers(admin, tenant),
    )
    assert resp.status_code == 422, resp.text


def test_holiday_bad_date_returns_422(client, db):
    tenant = make_tenant(db)
    admin = make_user(db, tenant, UserRole.TENANT_ADMIN)
    for bad in ("2026-02-30", "not-a-date", "25-12-2026"):
        resp = client.patch(
            "/api/attendance/settings",
            json={"holidays": [bad]},
            headers=auth_headers(admin, tenant),
        )
        assert resp.status_code == 422, resp.text
    resp = client.patch(
        "/api/attendance/settings",
        json={"holidays": [{"date": "2026-12-25"}]},
        headers=auth_headers(admin, tenant),
    )
    assert resp.status_code == 200, resp.text  # name is optional


def test_holiday_duplicate_dates_keep_the_named_entry(client, db):
    tenant = make_tenant(db)
    admin = make_user(db, tenant, UserRole.TENANT_ADMIN)
    saved = _patch_settings(
        client, admin, tenant,
        holidays=[
            {"date": "2026-12-25", "name": ""},
            {"date": "2026-12-25", "name": "Christmas"},
        ],
    )
    assert saved["holidays"] == [{"date": "2026-12-25", "name": "Christmas"}]


# ------------------------------------------------------- /today window


def test_today_upcoming_holidays_window(client, db):
    tenant = make_tenant(db)
    admin = make_user(db, tenant, UserRole.TENANT_ADMIN)
    employee = make_user(db, tenant, UserRole.EMPLOYEE)
    today = app_today()
    _patch_settings(
        client, admin, tenant,
        holidays=[
            {"date": (today - timedelta(days=1)).isoformat(), "name": "Past"},
            {"date": today.isoformat(), "name": "Today"},
            {"date": (today + timedelta(days=3)).isoformat(), "name": "Soon"},
            {"date": (today + timedelta(days=7)).isoformat(), "name": "Edge"},
            {"date": (today + timedelta(days=8)).isoformat(), "name": "Far"},
        ],
    )
    resp = client.get("/api/attendance/today", headers=auth_headers(employee, tenant))
    assert resp.status_code == 200
    assert resp.json()["upcoming_holidays"] == [
        {"date": today.isoformat(), "name": "Today"},
        {"date": (today + timedelta(days=3)).isoformat(), "name": "Soon"},
        {"date": (today + timedelta(days=7)).isoformat(), "name": "Edge"},
    ]


# --------------------------------------------------------- notifications


def _tenant_with_users(db):
    tenant = make_tenant(db)
    admin = make_user(db, tenant, UserRole.TENANT_ADMIN, name="Hein Admin")
    admin2 = make_user(db, tenant, UserRole.TENANT_ADMIN, name="Second Admin")
    dept_admin = make_user(db, tenant, UserRole.DEPT_ADMIN, name="Dana Dept")
    emp1 = make_user(db, tenant, UserRole.EMPLOYEE, name="Alice Aung")
    emp2 = make_user(db, tenant, UserRole.EMPLOYEE, name="Bob Bo")
    inactive = make_user(
        db, tenant, UserRole.EMPLOYEE, name="Inactive Ina", is_active=False
    )
    return {
        "tenant": tenant, "admin": admin, "admin2": admin2,
        "dept_admin": dept_admin, "emp1": emp1, "emp2": emp2,
        "inactive": inactive,
    }


def test_holiday_add_notifies_every_active_user_except_actor(client, db):
    fx = _tenant_with_users(db)
    today = app_today()
    holiday = (today + timedelta(days=2)).isoformat()
    _patch_settings(
        client, fx["admin"], fx["tenant"],
        holidays=[{"date": holiday, "name": "Thingyan"}],
    )
    notes = _notifications(db, fx["tenant"])
    recipients = {n.user_id for n in notes}
    assert recipients == {
        fx["admin2"].id, fx["dept_admin"].id, fx["emp1"].id, fx["emp2"].id,
    }
    assert len(notes) == 4  # one row per user, not per holiday
    note = notes[0]
    assert note.title == "Holiday announced: Thingyan"
    assert note.link == "/attendance"
    pretty = (today + timedelta(days=2)).strftime("%A, %d %b %Y")
    assert f"Thingyan — {pretty}" in note.message


def test_no_notification_when_only_other_settings_change(client, db):
    fx = _tenant_with_users(db)
    today = app_today()
    _patch_settings(
        client, fx["admin"], fx["tenant"],
        holidays=[{"date": (today + timedelta(days=2)).isoformat(), "name": "Thingyan"}],
    )
    assert len(_notifications(db, fx["tenant"])) == 4
    # A PATCH that does not touch holidays notifies nobody.
    _patch_settings(client, fx["admin"], fx["tenant"], start_time="08:30")
    assert len(_notifications(db, fx["tenant"])) == 4
    # Re-saving the identical holidays list notifies nobody either.
    _patch_settings(
        client, fx["admin"], fx["tenant"],
        holidays=[{"date": (today + timedelta(days=2)).isoformat(), "name": "Thingyan"}],
    )
    assert len(_notifications(db, fx["tenant"])) == 4


def test_removal_and_rename_notify_with_schedule_updated_title(client, db):
    fx = _tenant_with_users(db)
    today = app_today()
    day_a = (today + timedelta(days=2)).isoformat()
    day_b = (today + timedelta(days=3)).isoformat()
    _patch_settings(
        client, fx["admin"], fx["tenant"],
        holidays=[
            {"date": day_a, "name": "Thingyan"},
            {"date": day_b, "name": "Old Name"},
        ],
    )
    assert len(_notifications(db, fx["tenant"])) == 4
    # Removing one holiday: schedule-updated title, removal listed.
    _patch_settings(
        client, fx["admin"], fx["tenant"],
        holidays=[{"date": day_a, "name": "Thingyan"}],
    )
    notes = _notifications(db, fx["tenant"])
    assert len(notes) == 8
    removal = [n for n in notes if n.title == "Holiday schedule updated"]
    assert len(removal) == 4
    assert "Old Name" in removal[0].message
    # Renaming the remaining holiday: same title, new name listed.
    _patch_settings(
        client, fx["admin"], fx["tenant"],
        holidays=[{"date": day_a, "name": "Thingyan Festival"}],
    )
    notes = _notifications(db, fx["tenant"])
    assert len(notes) == 12
    renamed = [n for n in notes if "Thingyan Festival" in n.message]
    assert len(renamed) == 4
    assert renamed[0].title == "Holiday schedule updated"


def test_holiday_change_still_writes_audit_row(client, db):
    fx = _tenant_with_users(db)
    today = app_today()
    _patch_settings(
        client, fx["admin"], fx["tenant"],
        holidays=[{"date": (today + timedelta(days=2)).isoformat(), "name": "Thingyan"}],
    )
    rows = (
        db.query(AuditLog)
        .filter_by(tenant_id=fx["tenant"].id, action="attendance_settings_updated")
        .all()
    )
    assert len(rows) == 1
    assert "holidays" in rows[0].summary
