"""Attendance Excel export tests (Detail only — one row per working day).

Covers: the exact Detail columns, one row per working day per person,
absent working days as Absent rows, weekends excluded, the department_id
filter, dept-admin scoping, tenant isolation, and the new
department/role fields on the /report rows.
"""

from datetime import date as date_type, time as time_type
from io import BytesIO

from openpyxl import load_workbook

from app.models.attendance import AttendanceRecord
from app.models.user import UserRole
from app.services.attendance_export_service import HEADERS
from app.services.attendance_service import working_days_in_month
from app.tests.factories import (
    auth_headers,
    make_department,
    make_tenant,
    make_user,
)

YEAR, MONTH = 2026, 11  # Nov 2026: 21 Mon-Fri working days (Nov 1 is a Sunday)


def _insert_record(db, tenant, employee, day, status, check_in=None, check_out=None, late=0):
    record = AttendanceRecord(
        tenant_id=tenant.id,
        employee_id=employee.id,
        department_id=employee.department_id,
        record_date=day,
        check_in=check_in,
        check_out=check_out,
        status=status,
        late_minutes=late,
    )
    db.add(record)
    db.commit()
    return record


def _setup(client, db):
    """Tenant with Sales (Alice, Bob, dept admin Dana) and Operations (Carol)."""
    tenant = make_tenant(db)
    admin = make_user(db, tenant, UserRole.TENANT_ADMIN, name="Hein Admin")
    sales = make_department(db, tenant, name="Sales")
    ops = make_department(db, tenant, name="Operations")
    dana = make_user(db, tenant, UserRole.DEPT_ADMIN, name="Dana Dept", department=sales)
    alice = make_user(db, tenant, UserRole.EMPLOYEE, name="Alice Aung", department=sales)
    bob = make_user(db, tenant, UserRole.EMPLOYEE, name="Bob Bo", department=sales)
    carol = make_user(db, tenant, UserRole.EMPLOYEE, name="Carol Chan", department=ops)
    _insert_record(
        db, tenant, alice, date_type(2026, 11, 2), "present",
        check_in=time_type(9, 5), check_out=time_type(17, 0), late=5,
    )
    _insert_record(
        db, tenant, bob, date_type(2026, 11, 3), "half",
        check_in=time_type(14, 0), check_out=time_type(17, 0), late=300,
    )
    _insert_record(db, tenant, carol, date_type(2026, 11, 4), "leave")
    return {
        "tenant": tenant, "admin": admin, "sales": sales, "ops": ops,
        "dana": dana, "alice": alice, "bob": bob, "carol": carol,
    }


def _working_days():
    return working_days_in_month(YEAR, MONTH, [0, 1, 2, 3, 4])


def _export(client, user, tenant, params=None):
    resp = client.get(
        "/api/attendance/report/export",
        params={"year": YEAR, "month": MONTH, **(params or {})},
        headers=auth_headers(user, tenant),
    )
    assert resp.status_code == 200, resp.text
    assert "spreadsheetml.sheet" in resp.headers["content-type"]
    sheet = load_workbook(BytesIO(resp.content)).active
    rows = list(sheet.iter_rows(values_only=True))
    return resp, rows


def _data_rows(rows):
    # Row 1 title, row 2 department line, row 3 blank, row 4 headers.
    assert [c for c in rows[3]] == HEADERS
    return [r for r in rows[4:] if any(v is not None for v in r)]


def test_report_includes_department_and_role(client, db):
    fx = _setup(client, db)
    resp = client.get(
        "/api/attendance/report",
        params={"year": YEAR, "month": MONTH},
        headers=auth_headers(fx["admin"], fx["tenant"]),
    )
    assert resp.status_code == 200, resp.text
    by_name = {r["employee_name"]: r for r in resp.json()}
    assert by_name["Alice Aung"]["department_name"] == "Sales"
    assert by_name["Alice Aung"]["department_id"] == fx["sales"].id
    assert by_name["Alice Aung"]["role"] == "employee"
    assert by_name["Carol Chan"]["department_name"] == "Operations"
    assert by_name["Dana Dept"]["role"] == "dept_admin"
    assert by_name["Dana Dept"]["department_name"] == "Sales"


def test_export_detail_columns_and_rows(client, db):
    fx = _setup(client, db)
    _, rows = _export(client, fx["admin"], fx["tenant"])
    data = _data_rows(rows)
    working = _working_days()
    assert len(working) == 21
    # 4 report users (Alice, Bob, Carol, Dana) x 21 working days.
    assert len(data) == 84
    # Late is recorded in the DB but never exported.
    assert HEADERS == [
        "Date", "Employee Name", "Department", "Role",
        "Check-in", "Check-out", "Status",
    ]

    def row_for(name, day):
        return next(r for r in data if r[1] == name and r[0] == day)

    alice_day1 = row_for("Alice Aung", "2026-11-02")
    assert alice_day1[2] == "Sales" and alice_day1[3] == "Employee"
    assert alice_day1[4] == "09:05" and alice_day1[5] == "17:00"
    assert alice_day1[6] == "Present"

    alice_day2 = row_for("Alice Aung", "2026-11-03")
    assert alice_day2[6] == "Absent"
    assert alice_day2[4] in (None, "") and alice_day2[5] in (None, "")

    bob_half = row_for("Bob Bo", "2026-11-03")
    assert bob_half[6] == "Half day"
    assert bob_half[4] == "14:00"

    carol_leave = row_for("Carol Chan", "2026-11-04")
    assert carol_leave[6] == "Leave"
    assert carol_leave[4] in (None, "")

    # Weekends are not rows; ordering is employee name, then date.
    dates = {r[0] for r in data}
    assert "2026-11-01" not in dates and "2026-11-07" not in dates
    names = [r[1] for r in data]
    assert names == sorted(names)
    alice_dates = [r[0] for r in data if r[1] == "Alice Aung"]
    assert alice_dates == [d.isoformat() for d in working]


def test_export_department_filter(client, db):
    fx = _setup(client, db)
    resp, rows = _export(
        client, fx["admin"], fx["tenant"], {"department_id": fx["sales"].id}
    )
    data = _data_rows(rows)
    assert len(data) == 63  # 3 Sales people x 21 working days
    assert {r[2] for r in data} == {"Sales"}
    assert {r[1] for r in data} == {"Alice Aung", "Bob Bo", "Dana Dept"}
    assert "sales" in resp.headers["content-disposition"]


def test_export_dept_admin_scoping(client, db):
    fx = _setup(client, db)
    # No filter: a dept admin only ever gets their own department.
    _, rows = _export(client, fx["dana"], fx["tenant"])
    data = _data_rows(rows)
    assert len(data) == 63
    assert {r[2] for r in data} == {"Sales"}
    # Asking for another department yields a headers-only file.
    _, rows = _export(
        client, fx["dana"], fx["tenant"], {"department_id": fx["ops"].id}
    )
    assert _data_rows(rows) == []


def test_export_employee_sees_only_self(client, db):
    fx = _setup(client, db)
    _, rows = _export(client, fx["alice"], fx["tenant"])
    data = _data_rows(rows)
    assert len(data) == 21
    assert {r[1] for r in data} == {"Alice Aung"}


def test_export_tenant_isolation(client, db):
    fx = _setup(client, db)
    other_tenant = make_tenant(db, name="Other", slug="other")
    other_admin = make_user(db, other_tenant, UserRole.TENANT_ADMIN, name="Other Admin")
    _, rows = _export(client, other_admin, other_tenant)
    assert _data_rows(rows) == []


def test_export_unknown_department_returns_404(client, db):
    # m20: an unknown department_id is a typo'd filter, not an empty
    # scope — 404 instead of a silently empty file.
    fx = _setup(client, db)
    resp = client.get(
        "/api/attendance/report/export",
        params={"year": YEAR, "month": MONTH, "department_id": 999999},
        headers=auth_headers(fx["admin"], fx["tenant"]),
    )
    assert resp.status_code == 404, resp.text


def test_export_user_id_person_only(client, db):
    fx = _setup(client, db)
    resp, rows = _export(
        client, fx["admin"], fx["tenant"], {"user_id": fx["alice"].id}
    )
    data = _data_rows(rows)
    assert len(data) == 21  # Alice x 21 working days
    assert {r[1] for r in data} == {"Alice Aung"}
    assert "alice-aung" in resp.headers["content-disposition"]
    assert "Employee: Alice Aung" in [c for r in rows[:2] for c in r if c]


def test_export_user_id_scoping(client, db):
    fx = _setup(client, db)
    # An employee asking for someone else gets a headers-only file.
    _, rows = _export(
        client, fx["alice"], fx["tenant"], {"user_id": fx["bob"].id}
    )
    assert _data_rows(rows) == []
    # A dept admin asking for someone outside their department: same.
    _, rows = _export(
        client, fx["dana"], fx["tenant"], {"user_id": fx["carol"].id}
    )
    assert _data_rows(rows) == []
    # A dept admin asking for someone in their own department: works.
    _, rows = _export(
        client, fx["dana"], fx["tenant"], {"user_id": fx["bob"].id}
    )
    data = _data_rows(rows)
    assert len(data) == 21
    assert {r[1] for r in data} == {"Bob Bo"}
    # An employee asking for themself: works.
    _, rows = _export(
        client, fx["alice"], fx["tenant"], {"user_id": fx["alice"].id}
    )
    assert len(_data_rows(rows)) == 21
    # Unknown user: 404 (m20), not a silently empty file.
    resp = client.get(
        "/api/attendance/report/export",
        params={"year": YEAR, "month": MONTH, "user_id": 999999},
        headers=auth_headers(fx["admin"], fx["tenant"]),
    )
    assert resp.status_code == 404, resp.text


def test_export_invalid_month(client, db):
    fx = _setup(client, db)
    resp = client.get(
        "/api/attendance/report/export",
        params={"year": YEAR, "month": 13},
        headers=auth_headers(fx["admin"], fx["tenant"]),
    )
    assert resp.status_code == 400
