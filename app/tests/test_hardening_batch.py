"""Tests for the 1-14 hardening batch.

Covers: httpOnly cookie auth, token_version session invalidation, atomic
bulk import, import row cap, rate limits on upload/reminders, per-employee
analytics buckets, reminder scope (dept admins in, suspended tenants out),
edit_user duplicate email, email format validation, password min length,
employee department requirement, Excel formula-injection sanitizing.
"""

from io import BytesIO

import pytest
from openpyxl import Workbook
from sqlalchemy import select

from app.models.kpi_submission import KPIStatus
from app.models.tenant import TenantStatus
from app.models.user import User, UserRole
from app.services import bulk_import_service
from app.tests.factories import (
    DEFAULT_PASSWORD,
    auth_headers,
    make_department,
    make_submission,
    make_template,
    make_tenant,
    make_user,
)


def _tenant_admin(db, name="Boss"):
    tenant = make_tenant(db)
    dept = make_department(db, tenant)
    admin = make_user(db, tenant, UserRole.TENANT_ADMIN, name=name, department=dept)
    return tenant, dept, admin


# --- #2: httpOnly cookie auth ------------------------------------------------


def test_login_sets_httponly_session_cookie(client, db):
    tenant = make_tenant(db)
    user = make_user(db, tenant, UserRole.EMPLOYEE)
    r = client.post(
        "/api/auth/login",
        json={"email": user.email, "password": DEFAULT_PASSWORD},
        headers={"X-Tenant-Slug": tenant.slug},
    )
    assert r.status_code == 200
    cookie = r.headers.get("set-cookie", "")
    assert "access_token=" in cookie
    assert "HttpOnly" in cookie


def test_cookie_auth_works_without_authorization_header(client, db):
    tenant = make_tenant(db)
    user = make_user(db, tenant, UserRole.EMPLOYEE)
    # TestClient persists cookies across requests like a browser.
    r = client.post(
        "/api/auth/login",
        json={"email": user.email, "password": DEFAULT_PASSWORD},
        headers={"X-Tenant-Slug": tenant.slug},
    )
    assert r.status_code == 200
    me = client.get("/api/auth/me", headers={"X-Tenant-Slug": tenant.slug})
    assert me.status_code == 200
    assert me.json()["email"] == user.email


def test_logout_clears_session_cookie(client, db):
    tenant = make_tenant(db)
    user = make_user(db, tenant, UserRole.EMPLOYEE)
    client.post(
        "/api/auth/login",
        json={"email": user.email, "password": DEFAULT_PASSWORD},
        headers={"X-Tenant-Slug": tenant.slug},
    )
    r = client.post("/api/auth/logout", headers={"X-Tenant-Slug": tenant.slug})
    assert r.status_code == 204
    assert 'access_token=""' in r.headers.get("set-cookie", "") or "access_token=;" in r.headers.get("set-cookie", "")
    me = client.get("/api/auth/me", headers={"X-Tenant-Slug": tenant.slug})
    assert me.status_code == 401


# --- #3: token_version invalidates old sessions -------------------------------


def test_password_change_invalidates_old_token(client, db):
    tenant = make_tenant(db)
    user = make_user(db, tenant, UserRole.EMPLOYEE)
    headers = auth_headers(user, tenant)
    old_token = headers["Authorization"].split(" ", 1)[1]

    r = client.post(
        "/api/auth/change-password",
        json={"current_password": DEFAULT_PASSWORD, "new_password": "BrandNewPass1!"},
        headers=headers,
    )
    assert r.status_code == 204

    # The pre-change token must now be rejected.
    stale = client.get("/api/auth/me", headers={"X-Tenant-Slug": tenant.slug, "Authorization": f"Bearer {old_token}"})
    assert stale.status_code == 401


def test_admin_reset_invalidates_user_sessions(client, db):
    tenant, dept, admin = _tenant_admin(db)
    user = make_user(db, tenant, UserRole.EMPLOYEE, name="Reset Me", department=dept)
    user_headers = auth_headers(user, tenant)
    old_token = user_headers["Authorization"].split(" ", 1)[1]

    r = client.post(
        f"/api/admin/users/{user.id}/reset-password",
        json={"password": "ResetPass1!"},
        headers=auth_headers(admin, tenant),
    )
    assert r.status_code == 200

    stale = client.get("/api/auth/me", headers={"X-Tenant-Slug": tenant.slug, "Authorization": f"Bearer {old_token}"})
    assert stale.status_code == 401


# --- #1: atomic bulk import ----------------------------------------------------


def test_failed_import_rolls_back_users(db):
    from unittest.mock import patch

    tenant = make_tenant(db)
    dept = make_department(db, tenant)
    admin = make_user(db, tenant, UserRole.DEPT_ADMIN, name="Dept Boss", department=dept)

    wb = Workbook()
    ws = wb.active
    ws.append(["name", "email", "position", "metric", "target", "weight"])
    ws.append(["Aung Aung", "aung@example.com", "Sales Executive", "Sales", 100, 100])
    buf = BytesIO()
    wb.save(buf)
    parsed = bulk_import_service.parse_import_file(buf.getvalue(), db, tenant.id)
    assert parsed.ok

    # Sabotage the KPI step so the import blows up after the users were added.
    real_create = bulk_import_service.kpi_service.create_custom_employee_kpi

    def _boom(*args, **kwargs):
        raise RuntimeError("simulated mid-import failure")

    with patch.object(bulk_import_service.kpi_service, "create_custom_employee_kpi", _boom):
        with pytest.raises(RuntimeError):
            bulk_import_service.execute_import(db, admin, parsed)

    # Atomic: the half-created user must have been rolled back too.
    assert db.scalar(select(User).where(User.email == "aung@example.com")) is None


# --- #10: import row cap --------------------------------------------------------


def test_import_rejects_too_many_rows(db):
    tenant = make_tenant(db)
    wb = Workbook()
    ws = wb.active
    ws.append(["name", "email", "position", "metric", "target", "weight"])
    for i in range(5001):
        ws.append([f"User {i}", f"user{i}@example.com", "Staff", "Sales", 100, 100])
    buf = BytesIO()
    wb.save(buf)
    parsed = bulk_import_service.parse_import_file(buf.getvalue(), db, tenant.id)
    assert not parsed.ok
    assert any("Too many rows" in e.message for e in parsed.errors)


# --- #11: rate limits on upload + reminders -------------------------------------


def test_upload_rate_limited(client, db):
    tenant = make_tenant(db)
    dept = make_department(db, tenant)
    admin = make_user(db, tenant, UserRole.DEPT_ADMIN, name="Dept Boss", department=dept)
    headers = auth_headers(admin, tenant)

    codes = []
    for _ in range(6):
        r = client.post(
            "/api/admin/import/upload",
            params={"dry_run": True},
            files={"file": ("empty.xlsx", b"not-excel", "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet")},
            headers=headers,
        )
        codes.append(r.status_code)
    assert codes[-1] == 429
    assert codes[0] != 429  # first attempts are processed, not blocked


# --- #12: per-employee analytics buckets ----------------------------------------


def test_overview_buckets_are_per_employee(client, db):
    tenant = make_tenant(db)
    dept = make_department(db, tenant)
    admin = make_user(db, tenant, UserRole.TENANT_ADMIN, name="Boss", department=dept)
    emp = make_user(db, tenant, UserRole.EMPLOYEE, name="Multi KPI", department=dept)
    tpl1 = make_template(db, tenant, metric_name="Revenue")
    tpl2 = make_template(db, tenant, metric_name="Calls")

    # One employee, two KPIs: 100 and 0 -> mean 50 -> exactly one bucket vote.
    make_submission(db, tenant, emp, tpl1, year=2026, period="October",
                    status=KPIStatus.APPROVED, final_score=100, department=dept)
    make_submission(db, tenant, emp, tpl2, year=2026, period="October",
                    status=KPIStatus.APPROVED, final_score=0, department=dept)
    db.commit()

    r = client.get("/api/analytics/overview", params={"year": 2026, "period": "October"},
                   headers=auth_headers(admin, tenant))
    assert r.status_code == 200, r.text
    data = r.json()
    assert sum(data["buckets"].values()) == 1
    assert data["avg_score"] == pytest.approx(50.0)


# --- #13: reminder scope --------------------------------------------------------


def test_reminders_include_dept_admins(client, db):
    from datetime import date

    from app.services import deadline_reminder_service

    tenant = make_tenant(db)
    dept = make_department(db, tenant)
    make_user(db, tenant, UserRole.TENANT_ADMIN, name="Boss", department=dept)
    da = make_user(db, tenant, UserRole.DEPT_ADMIN, name="Dept Boss", department=dept)
    tpl = make_template(db, tenant)
    make_submission(db, tenant, da, tpl, year=2026, period="October",
                    status=KPIStatus.DRAFT, self_score=50, department=dept)
    db.commit()

    # 1 day before month-end -> reminder window is open.
    result = deadline_reminder_service.send_deadline_reminders(db, tenant_id=tenant.id, today=date(2026, 10, 30))
    assert result.reminded >= 1


def test_reminders_skip_suspended_tenants(db):
    from datetime import date

    from app.services import deadline_reminder_service

    tenant = make_tenant(db)
    tenant.status = TenantStatus.SUSPENDED
    dept = make_department(db, tenant)
    emp = make_user(db, tenant, UserRole.EMPLOYEE, name="Emp", department=dept)
    tpl = make_template(db, tenant)
    make_submission(db, tenant, emp, tpl, year=2026, period="October",
                    status=KPIStatus.DRAFT, self_score=50, department=dept)
    db.commit()

    result = deadline_reminder_service.send_deadline_reminders(db, today=date(2026, 10, 30))
    assert result.reminded == 0


# --- #8/#9/#6/#4: user validation -----------------------------------------------


def test_edit_user_duplicate_email_rejected(client, db):
    tenant, dept, admin = _tenant_admin(db)
    make_user(db, tenant, UserRole.EMPLOYEE, name="Alice", email="alice@example.com", department=dept)
    bob = make_user(db, tenant, UserRole.EMPLOYEE, name="Bob", email="bob@example.com", department=dept)

    r = client.put(
        f"/api/admin/users/{bob.id}",
        json={"name": "Bob", "email": "alice@example.com", "role": "employee",
              "department_id": dept.id, "position": None, "password": None},
        headers=auth_headers(admin, tenant),
    )
    assert r.status_code == 400
    assert "already exists" in r.json()["detail"]


def test_create_user_rejects_bad_email(client, db):
    tenant, dept, admin = _tenant_admin(db)
    r = client.post(
        "/api/admin/users",
        json={"name": "Bad", "email": "not-an-email", "password": "ValidPass1!",
              "role": "employee", "department_id": dept.id},
        headers=auth_headers(admin, tenant),
    )
    assert r.status_code == 422


def test_create_user_rejects_short_password(client, db):
    tenant, dept, admin = _tenant_admin(db)
    r = client.post(
        "/api/admin/users",
        json={"name": "Short", "email": "short@example.com", "password": "short",
              "role": "employee", "department_id": dept.id},
        headers=auth_headers(admin, tenant),
    )
    assert r.status_code == 422


def test_create_employee_requires_department(client, db):
    tenant, _, admin = _tenant_admin(db)
    r = client.post(
        "/api/admin/users",
        json={"name": "NoDept", "email": "nodept@example.com", "password": "ValidPass1!",
              "role": "employee", "department_id": None},
        headers=auth_headers(admin, tenant),
    )
    assert r.status_code == 400
    assert "department" in r.json()["detail"].lower()


# --- #5: formula injection -------------------------------------------------------


def test_export_sanitizes_formula_cells():
    from app.services.kpi_export_service import _safe

    assert _safe('=HYPERLINK("https://evil.example","x")') == "'=HYPERLINK(\"https://evil.example\",\"x\")"
    assert _safe("+1+1") == "'+1+1"
    assert _safe("-2+3") == "'-2+3"
    assert _safe("@SUM(A1)") == "'@SUM(A1)"
    assert _safe("Normal text") == "Normal text"
    assert _safe(123) == 123
    assert _safe(None) is None
