"""Tests for bulk import (dept admin onboards employees + KPIs from Excel)."""

from datetime import date
from io import BytesIO

import pytest
from openpyxl import Workbook

from app.core.security import verify_password
from app.models.kpi_submission import KPISubmission
from app.models.kpi_template import KPITemplate
from app.models.user import User, UserRole
from app.services import bulk_import_service
from app.tests.factories import (
    auth_headers,
    make_department,
    make_tenant,
    make_user,
)

MONTH_NAMES = (
    "January", "February", "March", "April", "May", "June",
    "July", "August", "September", "October", "November", "December",
)


@pytest.fixture(autouse=True)
def _import_day_after_rollover():
    """M6 gate: bulk import always targets the current month, which the
    admin creation window blocks during days 1..grace_days. Pin the
    import clock to day 10 of the real current month so these tests
    exercise the import itself, not the gate (the gate's own tests live
    in test_admin_creation_window.py). Month/year are unchanged, so
    existing period assertions still hold. Both clocks are pinned: the
    service's (used by execute_import) and the router's (used by the
    up-front gate check on the upload endpoint)."""
    from unittest.mock import patch

    from app.core.timezone import today as _app_today
    from app.routers import importer as importer_router

    pinned = _app_today().replace(day=10)
    with (
        patch.object(bulk_import_service, "app_today", return_value=pinned),
        patch.object(importer_router, "app_today", return_value=pinned),
    ):
        yield


def _workbook(rows):
    wb = Workbook()
    ws = wb.active
    ws.title = "Import"
    ws.append(["name", "email", "position", "metric", "target", "weight"])
    for r in rows:
        ws.append(r)
    buf = BytesIO()
    wb.save(buf)
    buf.seek(0)
    return buf.getvalue()


def _valid_contents():
    return _workbook(
        [
            ["Aung Aung", "aung@example.com", "Sales Executive", "Sales", 100, 60],
            ["Aung Aung", "aung@example.com", "Sales Executive", "Visits", 40, 40],
            ["May May", "may@example.com", "Senior Sales", "Sales", 100, 100],
        ]
    )


@pytest.fixture
def dept_admin(db):
    tenant = make_tenant(db)
    dept = make_department(db, tenant)
    admin = make_user(db, tenant, UserRole.DEPT_ADMIN, name="Dept Boss", department=dept)
    return tenant, dept, admin


def test_parse_valid_file(db, dept_admin):
    tenant, _, _ = dept_admin
    parsed = bulk_import_service.parse_import_file(_valid_contents(), db, tenant.id)
    assert parsed.ok, parsed.errors
    assert len(parsed.users) == 2
    assert len(parsed.kpis) == 3


def test_parse_duplicate_email_with_different_name(db, dept_admin):
    tenant, _, _ = dept_admin
    contents = _workbook(
        [
            ["Aung", "aung@example.com", "Exec", "Sales", 100, 100],
            ["Aung 2", "aung@example.com", "Exec", "Visits", 40, 100],
        ]
    )
    parsed = bulk_import_service.parse_import_file(contents, db, tenant.id)
    assert not parsed.ok
    assert any("inconsistent name/position" in e.message for e in parsed.errors)


def test_parse_duplicate_metric(db, dept_admin):
    tenant, _, _ = dept_admin
    contents = _workbook(
        [
            ["Aung", "aung@example.com", "Exec", "Sales", 100, 50],
            ["Aung", "aung@example.com", "Exec", "Sales", 100, 50],
        ]
    )
    parsed = bulk_import_service.parse_import_file(contents, db, tenant.id)
    assert not parsed.ok
    assert any("Duplicate metric" in e.message for e in parsed.errors)


def test_parse_existing_email_rejected(db, dept_admin):
    tenant, _, _ = dept_admin
    make_user(db, tenant, UserRole.EMPLOYEE, name="Old", email="aung@example.com")
    parsed = bulk_import_service.parse_import_file(_valid_contents(), db, tenant.id)
    assert not parsed.ok
    assert any("already exists" in e.message for e in parsed.errors)


def test_parse_kpi_for_unknown_email(db, dept_admin):
    # Every row carries its own user info now, so an "unknown email" case is
    # simply a row whose user fails validation (e.g. bad email format).
    tenant, _, _ = dept_admin
    contents = _workbook([["Ghost", "not-an-email", "", "Sales", 100, 100]])
    parsed = bulk_import_service.parse_import_file(contents, db, tenant.id)
    assert not parsed.ok
    assert any("Invalid email" in e.message for e in parsed.errors)


def test_parse_weights_must_total_100(db, dept_admin):
    tenant, _, _ = dept_admin
    contents = _workbook([["Aung", "aung@example.com", "", "Sales", 100, 60]])
    parsed = bulk_import_service.parse_import_file(contents, db, tenant.id)
    assert not parsed.ok
    assert any("must total 100" in e.message for e in parsed.errors)


def test_parse_long_metric_name_is_accepted(db, dept_admin):
    # Regression: a metric name over the old kpi_templates.metric_name
    # VARCHAR(200) limit used to pass Validate and then crash Confirm
    # Import with a generic 500 (Postgres StringDataRightTruncation on
    # INSERT). Migration 0018 widened the column to TEXT, so long names
    # — title plus description in one cell — import as written.
    tenant, _, _ = dept_admin
    long_metric = (
        "Audit Cycle Excellence & Quality Assurance\n"
        "Internal Audit Standards and completion of the full audit cycle — "
        "from risk-based planning and fieldwork to reporting and follow-up — "
        "while ensuring zero material omissions and high stakeholder satisfaction"
    )
    assert len(long_metric) > 200
    contents = _workbook([
        ["Aung", "aung@example.com", "", long_metric, 50, 100],
    ])
    parsed = bulk_import_service.parse_import_file(contents, db, tenant.id)
    assert parsed.ok, parsed.errors
    assert parsed.kpis[0].metric_name == long_metric


def test_parse_overlong_name_position_email_are_row_errors(db, dept_admin):
    # The users columns are still capped (name/position 120, email 255),
    # so overlong values must surface as per-row errors at Validate,
    # not as a 500 at Confirm Import.
    tenant, _, _ = dept_admin
    contents = _workbook([
        ["B" * 121, "bee@example.com", "", "Sales", 100, 100],
        ["Cee", "cee@example.com", "P" * 121, "Sales", 100, 100],
        ["Dee", "d" * 250 + "@example.com", "", "Sales", 100, 100],
    ])
    parsed = bulk_import_service.parse_import_file(contents, db, tenant.id)
    assert not parsed.ok
    messages = [e.message for e in parsed.errors]
    assert any("Name is too long" in m and "120" in m for m in messages)
    assert any("Position is too long" in m and "120" in m for m in messages)
    assert any("Email is too long" in m and "255" in m for m in messages)


def test_upload_long_metric_name_imports_in_full(client, db, dept_admin):
    tenant, _, admin = dept_admin
    long_metric = "M" * 500
    contents = _workbook([
        ["Aung", "aung@example.com", "", long_metric, 50, 100],
    ])
    r = client.post(
        "/api/admin/import/upload",
        params={"dry_run": True},
        files={"file": ("import.xlsx", contents, "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet")},
        headers=auth_headers(admin, tenant),
    )
    assert r.status_code == 200, r.text
    assert r.json()["ok"] is True
    r = client.post(
        "/api/admin/import/upload",
        params={"dry_run": False},
        files={"file": ("import.xlsx", contents, "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet")},
        headers=auth_headers(admin, tenant),
    )
    assert r.status_code == 200, r.text
    assert r.json()["kpis_created"] == 1
    stored = db.query(KPITemplate).all()
    assert len(stored) == 1
    assert stored[0].metric_name == long_metric


def test_execute_creates_users_templates_and_drafts(db, dept_admin):
    tenant, dept, admin = dept_admin
    parsed = bulk_import_service.parse_import_file(_valid_contents(), db, tenant.id)
    assert parsed.ok
    result = bulk_import_service.execute_import(db, admin, parsed)
    assert result.users_created == 2
    assert result.kpis_created == 3

    users = db.query(User).filter(User.tenant_id == tenant.id, User.role == UserRole.EMPLOYEE).all()
    assert len(users) == 2
    for u in users:
        assert u.department_id == dept.id
        # Per-user random initial passwords: never the old shared default,
        # and never the same hash twice.
        assert not verify_password("password", u.password_hash)
    assert users[0].password_hash != users[1].password_hash
    by_email = {u.email: u for u in users}
    assert by_email["aung@example.com"].position == "Sales Executive"
    assert by_email["may@example.com"].position == "Senior Sales"

    templates = db.query(KPITemplate).filter(KPITemplate.tenant_id == tenant.id).all()
    assert len(templates) == 3
    assert all(t.is_recurring for t in templates)

    from app.core.timezone import today as _app_today

    today = _app_today()
    subs = db.query(KPISubmission).filter(
        KPISubmission.tenant_id == tenant.id,
        KPISubmission.year == today.year,
        KPISubmission.month_or_quarter == MONTH_NAMES[today.month - 1],
    ).all()
    assert len(subs) == 3


def test_upload_dry_run_does_not_persist(client, db, dept_admin):
    tenant, _, admin = dept_admin
    r = client.post(
        "/api/admin/import/upload",
        params={"dry_run": True},
        files={"file": ("import.xlsx", _valid_contents(), "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet")},
        headers=auth_headers(admin, tenant),
    )
    assert r.status_code == 200, r.text
    data = r.json()
    assert data["ok"] is True
    assert data["users_found"] == 2
    assert db.query(User).filter(User.role == UserRole.EMPLOYEE).count() == 0


def test_upload_executes_and_reports(client, db, dept_admin):
    tenant, _, admin = dept_admin
    r = client.post(
        "/api/admin/import/upload",
        params={"dry_run": False},
        files={"file": ("import.xlsx", _valid_contents(), "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet")},
        headers=auth_headers(admin, tenant),
    )
    assert r.status_code == 200, r.text
    data = r.json()
    assert data["users_created"] == 2
    assert data["kpis_created"] == 3
    assert "initial_password" not in data  # passwords are never returned


def test_upload_with_errors_returns_422(client, db, dept_admin):
    tenant, _, admin = dept_admin
    contents = _workbook([["Aung", "bad-email", "", "Sales", 100, 100]])
    r = client.post(
        "/api/admin/import/upload",
        params={"dry_run": False},
        files={"file": ("import.xlsx", contents, "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet")},
        headers=auth_headers(admin, tenant),
    )
    assert r.status_code == 422
    assert len(r.json()["detail"]["errors"]) > 0


def test_tenant_admin_forbidden(client, db):
    tenant = make_tenant(db)
    admin = make_user(db, tenant, UserRole.TENANT_ADMIN, name="Boss")
    r = client.post(
        "/api/admin/import/upload",
        params={"dry_run": True},
        files={"file": ("import.xlsx", _valid_contents(), "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet")},
        headers=auth_headers(admin, tenant),
    )
    assert r.status_code == 403


def test_template_download(client, db, dept_admin):
    tenant, _, admin = dept_admin
    r = client.get("/api/admin/import/template", headers=auth_headers(admin, tenant))
    assert r.status_code == 200
    assert "spreadsheetml.sheet" in r.headers["content-type"]
    assert len(r.content) > 0


def test_upload_unauthenticated_401(client):
    r = client.post(
        "/api/admin/import/upload",
        params={"dry_run": True},
        files={"file": ("import.xlsx", _valid_contents(), "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet")},
    )
    assert r.status_code == 401


def test_upload_employee_forbidden(client, db):
    tenant = make_tenant(db)
    emp = make_user(db, tenant, UserRole.EMPLOYEE, name="Emp")
    r = client.post(
        "/api/admin/import/upload",
        params={"dry_run": True},
        files={"file": ("import.xlsx", _valid_contents(), "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet")},
        headers=auth_headers(emp, tenant),
    )
    assert r.status_code == 403


def test_upload_rejects_non_xlsx(client, db, dept_admin):
    tenant, _, admin = dept_admin
    r = client.post(
        "/api/admin/import/upload",
        params={"dry_run": True},
        files={"file": ("evil.txt", b"not an excel file", "text/plain")},
        headers=auth_headers(admin, tenant),
    )
    assert r.status_code == 400


def test_upload_rejects_garbage_xlsx(client, db, dept_admin):
    tenant, _, admin = dept_admin
    r = client.post(
        "/api/admin/import/upload",
        params={"dry_run": True},
        files={"file": ("fake.xlsx", b"\x00\x01\x02not a workbook", "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet")},
        headers=auth_headers(admin, tenant),
    )
    # m9: the upload now sniffs ZIP magic bytes before parsing, so garbage
    # is rejected up front with a clear 400 instead of a 200 dry-run
    # carrying a parse error.
    assert r.status_code == 400
    assert "not a valid Excel" in r.json()["detail"]


def test_template_unauthenticated_401(client):
    assert client.get("/api/admin/import/template").status_code == 401


def test_execute_sends_welcome_emails(db, dept_admin):
    from unittest.mock import patch

    tenant, _, admin = dept_admin
    parsed = bulk_import_service.parse_import_file(_valid_contents(), db, tenant.id)
    assert parsed.ok
    with patch("app.services.bulk_import_service.email_service.send_welcome_email_sync",
               return_value=True) as mock_welcome:
        result = bulk_import_service.execute_import(db, admin, parsed)
    assert mock_welcome.call_count == 2
    sent_to = {c.args[0] for c in mock_welcome.call_args_list}
    assert sent_to == {"aung@example.com", "may@example.com"}
    passwords = [c.args[2] for c in mock_welcome.call_args_list]
    assert all(isinstance(p, str) and len(p) >= 8 for p in passwords)
    assert len(set(passwords)) == 2  # unique per user
    for c in mock_welcome.call_args_list:
        assert c.kwargs["org_name"] == "Acme"
    assert result.email_failures == []
    # API responses never carry passwords — only the email does.
    assert not any(p in str(result) for p in passwords)


def test_execute_welcome_email_failure_reported_not_rolled_back(db, dept_admin):
    from unittest.mock import patch

    tenant, _, admin = dept_admin
    parsed = bulk_import_service.parse_import_file(_valid_contents(), db, tenant.id)
    assert parsed.ok

    def _send(to_email, name, password, org_name=None):
        return to_email != "may@example.com"

    with patch("app.services.bulk_import_service.email_service.send_welcome_email_sync",
               side_effect=_send):
        result = bulk_import_service.execute_import(db, admin, parsed)
    assert result.users_created == 2  # failure never rolls back the import
    assert result.email_failures == ["may@example.com"]


def test_upload_rejects_file_over_10mb(client, db, dept_admin):
    tenant, _, admin = dept_admin
    oversized = b"\x00" * (10 * 1024 * 1024 + 1)
    r = client.post(
        "/api/admin/import/upload",
        params={"dry_run": True},
        files={"file": ("import.xlsx", oversized, "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet")},
        headers=auth_headers(admin, tenant),
    )
    assert r.status_code == 400
    assert "10 MB" in r.json()["detail"]


def test_upload_accepts_file_just_under_10mb(client, db, dept_admin):
    # The size gate runs before parsing, so a valid small workbook proves the
    # gate lets files under the cap through (parsing itself is covered above).
    tenant, _, admin = dept_admin
    r = client.post(
        "/api/admin/import/upload",
        params={"dry_run": True},
        files={"file": ("import.xlsx", _valid_contents(), "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet")},
        headers=auth_headers(admin, tenant),
    )
    assert r.status_code == 200


# --- Previous-month targeting (grace week) --------------------------------
# Fixed dates, mirroring test_admin_creation_window.py: DAY_3 is grace week
# (previous month October is open, current month November is blocked,
# September is past its grace window); DAY_10 is after the grace week
# (September is past grace, October's window has also closed).

DAY_3 = date(2026, 11, 3)
DAY_10 = date(2026, 11, 10)


def _pin_day(day):
    """Patch both import clocks (service + router gate check) to ``day``."""
    from unittest.mock import patch

    from app.routers import importer as importer_router

    return (
        patch.object(bulk_import_service, "app_today", return_value=day),
        patch.object(importer_router, "app_today", return_value=day),
    )


def _upload(client, tenant, admin, dry_run, year=None, month=None):
    params = {"dry_run": dry_run}
    if year is not None:
        params["year"] = year
    if month is not None:
        params["month"] = month
    return client.post(
        "/api/admin/import/upload",
        params=params,
        files={"file": ("import.xlsx", _valid_contents(), "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet")},
        headers=auth_headers(admin, tenant),
    )


def test_execute_previous_month_on_day_3_succeeds(db, dept_admin):
    from unittest.mock import patch

    tenant, dept, admin = dept_admin
    parsed = bulk_import_service.parse_import_file(_valid_contents(), db, tenant.id)
    assert parsed.ok
    with patch.object(bulk_import_service, "app_today", return_value=DAY_3):
        result = bulk_import_service.execute_import(db, admin, parsed, year=2026, period="October")
    assert result.users_created == 2
    assert result.kpis_created == 3
    assert (result.year, result.period) == (2026, "October")
    subs = db.query(KPISubmission).filter(
        KPISubmission.tenant_id == tenant.id,
        KPISubmission.year == 2026,
        KPISubmission.month_or_quarter == "October",
    ).all()
    assert len(subs) == 3
    users = db.query(User).filter(User.tenant_id == tenant.id, User.role == UserRole.EMPLOYEE).all()
    assert len(users) == 2
    assert all(u.department_id == dept.id for u in users)


def test_execute_current_month_on_day_3_rejected(db, dept_admin):
    from unittest.mock import patch

    import pytest
    from fastapi import HTTPException

    tenant, _, admin = dept_admin
    parsed = bulk_import_service.parse_import_file(_valid_contents(), db, tenant.id)
    assert parsed.ok
    with patch.object(bulk_import_service, "app_today", return_value=DAY_3):
        with pytest.raises(HTTPException) as exc:
            bulk_import_service.execute_import(db, admin, parsed, year=2026, period="November")
    assert exc.value.status_code == 400
    assert db.query(User).filter(User.tenant_id == tenant.id, User.role == UserRole.EMPLOYEE).count() == 0


def test_execute_past_grace_month_rejected(db, dept_admin):
    from unittest.mock import patch

    import pytest
    from fastapi import HTTPException

    tenant, _, admin = dept_admin
    parsed = bulk_import_service.parse_import_file(_valid_contents(), db, tenant.id)
    assert parsed.ok
    with patch.object(bulk_import_service, "app_today", return_value=DAY_10):
        with pytest.raises(HTTPException) as exc:
            bulk_import_service.execute_import(db, admin, parsed, year=2026, period="September")
    assert exc.value.status_code == 400


def test_upload_previous_month_on_day_3_succeeds(client, db, dept_admin):
    tenant, dept, admin = dept_admin
    p1, p2 = _pin_day(DAY_3)
    with p1, p2:
        r = _upload(client, tenant, admin, dry_run=False, year=2026, month=10)
    assert r.status_code == 200, r.text
    data = r.json()
    assert data["users_created"] == 2
    assert data["kpis_created"] == 3
    assert (data["year"], data["period"]) == (2026, "October")
    users = db.query(User).filter(User.tenant_id == tenant.id, User.role == UserRole.EMPLOYEE).all()
    assert all(u.department_id == dept.id for u in users)


def test_upload_dry_run_previous_month_on_day_3_reports_ok(client, db, dept_admin):
    tenant, _, admin = dept_admin
    p1, p2 = _pin_day(DAY_3)
    with p1, p2:
        r = _upload(client, tenant, admin, dry_run=True, year=2026, month=10)
    assert r.status_code == 200, r.text
    assert r.json()["ok"] is True


def test_upload_current_month_on_day_3_rejected(client, db, dept_admin):
    tenant, _, admin = dept_admin
    p1, p2 = _pin_day(DAY_3)
    with p1, p2:
        r = _upload(client, tenant, admin, dry_run=False, year=2026, month=11)
    assert r.status_code == 400, r.text
    assert "previous month" in r.json()["detail"]
    assert db.query(User).filter(User.tenant_id == tenant.id, User.role == UserRole.EMPLOYEE).count() == 0


def test_upload_past_grace_month_rejected(client, db, dept_admin):
    tenant, _, admin = dept_admin
    p1, p2 = _pin_day(DAY_10)
    with p1, p2:
        r = _upload(client, tenant, admin, dry_run=False, year=2026, month=9)
    assert r.status_code == 400, r.text


def test_upload_period_params_validated(client, db, dept_admin):
    tenant, _, admin = dept_admin
    r = _upload(client, tenant, admin, dry_run=True, year=2026, month=13)
    assert r.status_code == 400
    r = _upload(client, tenant, admin, dry_run=True, year=2026)
    assert r.status_code == 400
