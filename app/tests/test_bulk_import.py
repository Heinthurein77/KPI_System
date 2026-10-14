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
        assert verify_password("password", u.password_hash)
    by_email = {u.email: u for u in users}
    assert by_email["aung@example.com"].position == "Sales Executive"
    assert by_email["may@example.com"].position == "Senior Sales"

    templates = db.query(KPITemplate).filter(KPITemplate.tenant_id == tenant.id).all()
    assert len(templates) == 3
    assert all(t.is_recurring for t in templates)

    today = date.today()
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
    assert data["initial_password"] == "password"


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
    assert r.status_code == 200
    assert r.json()["ok"] is False
    assert any("Not a valid Excel" in e["message"] for e in r.json()["errors"])


def test_template_unauthenticated_401(client):
    assert client.get("/api/admin/import/template").status_code == 401


def test_execute_sends_welcome_emails(db, dept_admin):
    from unittest.mock import patch

    tenant, _, admin = dept_admin
    parsed = bulk_import_service.parse_import_file(_valid_contents(), db, tenant.id)
    assert parsed.ok
    with patch("app.services.bulk_import_service.email_service.send_welcome_email") as mock_welcome:
        bulk_import_service.execute_import(db, admin, parsed)
    assert mock_welcome.call_count == 2
    sent_to = {c.args[0] for c in mock_welcome.call_args_list}
    assert sent_to == {"aung@example.com", "may@example.com"}
    for c in mock_welcome.call_args_list:
        assert c.args[2] == "password"
        assert c.kwargs["org_name"] == "Acme"
