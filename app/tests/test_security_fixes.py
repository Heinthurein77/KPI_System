"""Security review fixes (2026-11-10): M4, M5, M6, M7, M8, m1, m2, m6, m9, m10.

M4 is the critical one: X-Forwarded-For is trusted only from a trusted
proxy peer. TestClient's peer is configurable via ``client=(host, port)``,
so the negative test uses a public-IP peer with a forged header.
"""

from datetime import date as date_type

from fastapi.testclient import TestClient

from app.main import app
from app.models.kpi_submission import KPIStatus
from app.models.user import UserRole
from app.tests.factories import (
    DEFAULT_PASSWORD,
    auth_headers,
    make_department,
    make_submission,
    make_template,
    make_tenant,
    make_user,
)

OFFICE_IP = "203.0.113.10"
HOME_IP = "198.51.100.99"
# A public-IP socket peer: must NOT be trusted to set X-Forwarded-For.
NON_PROXY_PEER = ("198.51.100.99", 50000)
# A private-network socket peer (Caddy on the Docker bridge network).
PROXY_PEER = ("172.18.0.5", 50000)


def _set_office_wifi(client, admin, tenant):
    resp = client.patch(
        "/api/attendance/settings",
        json={"work_days": [0, 1, 2, 3, 4, 5, 6], "office_ips": [OFFICE_IP]},
        headers=auth_headers(admin, tenant),
    )
    assert resp.status_code == 200, resp.text


def _att_setup(db):
    tenant = make_tenant(db)
    dept = make_department(db, tenant, name="Engineering")
    admin = make_user(db, tenant, UserRole.TENANT_ADMIN, name="Admin")
    emp = make_user(db, tenant, UserRole.EMPLOYEE, name="Emp", department=dept)
    return tenant, dept, admin, emp


# ---------------------------------------------------------------- M4


def test_forged_xff_from_non_proxy_peer_is_ignored(client, db):
    """A direct client (public-IP peer) forging X-Forwarded-For: office IP
    must be treated as its real (home) IP -> check-in 403."""
    tenant, _, admin, emp = _att_setup(db)
    _set_office_wifi(client, admin, tenant)
    direct = TestClient(app, client=NON_PROXY_PEER)
    resp = direct.post(
        "/api/attendance/check-in",
        headers={**auth_headers(emp, tenant), "X-Forwarded-For": OFFICE_IP},
    )
    assert resp.status_code == 403, resp.text


def test_xff_from_trusted_proxy_peer_is_honoured(client, db):
    """Traffic arriving via Caddy (private-network peer) keeps working:
    X-Forwarded-For: office IP -> check-in 201."""
    tenant, _, admin, emp = _att_setup(db)
    _set_office_wifi(client, admin, tenant)
    via_proxy = TestClient(app, client=PROXY_PEER)
    resp = via_proxy.post(
        "/api/attendance/check-in",
        headers={**auth_headers(emp, tenant), "X-Forwarded-For": OFFICE_IP},
    )
    assert resp.status_code == 201, resp.text


def test_forged_xff_does_not_bypass_login_rate_limit(client, db):
    """Rotating a forged X-Forwarded-For from a non-proxy peer must not
    dodge the per-IP login limit: 11 wrong passwords -> 429 on the 11th."""
    tenant = make_tenant(db)
    make_user(db, tenant, UserRole.EMPLOYEE, email="limited2@example.com")
    direct = TestClient(app, client=NON_PROXY_PEER)
    codes = []
    for i in range(11):
        resp = direct.post(
            "/api/auth/login",
            json={"email": "limited2@example.com", "password": "WrongPassword1!"},
            headers={
                "X-Tenant-Slug": tenant.slug,
                "X-Forwarded-For": f"203.0.113.{i}",
            },
        )
        codes.append(resp.status_code)
    assert codes[:10] == [401] * 10
    assert codes[10] == 429


def test_per_account_login_limit_survives_ip_rotation(client, db):
    """10 failed logins, then the correct password from a *different* IP
    must still be 429: the per-account limit, not just the per-IP one."""
    tenant = make_tenant(db)
    user = make_user(db, tenant, UserRole.EMPLOYEE, email="acct@example.com")
    for _ in range(10):
        resp = client.post(
            "/api/auth/login",
            json={"email": "acct@example.com", "password": "WrongPassword1!"},
            headers={"X-Tenant-Slug": tenant.slug},
        )
        assert resp.status_code == 401, resp.text
    other_ip = TestClient(app, client=("192.0.2.55", 50000))
    resp = other_ip.post(
        "/api/auth/login",
        json={"email": user.email, "password": DEFAULT_PASSWORD},
        headers={"X-Tenant-Slug": tenant.slug},
    )
    assert resp.status_code == 429, resp.text


# ---------------------------------------------------------------- M5 / M6


def _audit_actions(client, admin, tenant):
    resp = client.get("/api/admin/audit-log", headers=auth_headers(admin, tenant))
    assert resp.status_code == 200, resp.text
    return [e["action"] for e in resp.json()]


def test_attendance_settings_patch_and_leave_are_audited(client, db):
    tenant, _, admin, emp = _att_setup(db)
    _set_office_wifi(client, admin, tenant)
    assert "attendance_settings_updated" in _audit_actions(client, admin, tenant)
    resp = client.post(
        "/api/attendance/leave",
        json={"record_date": "2026-11-20"},
        headers=auth_headers(emp, tenant),
    )
    assert resp.status_code == 201, resp.text
    assert "attendance_leave_created" in _audit_actions(client, admin, tenant)


def test_attendance_export_is_audited(client, db):
    tenant, _, admin, emp = _att_setup(db)
    from app.core.timezone import today as app_today

    today = app_today()
    resp = client.get(
        "/api/attendance/report/export",
        params={"year": today.year, "month": today.month},
        headers=auth_headers(admin, tenant),
    )
    assert resp.status_code == 200, resp.text
    assert "attendance_export" in _audit_actions(client, admin, tenant)


def test_delete_checkin_record_is_rejected_but_leave_delete_works(client, db):
    tenant, _, admin, emp = _att_setup(db)
    _set_office_wifi(client, admin, tenant)
    resp = client.post(
        "/api/attendance/check-in",
        headers={**auth_headers(emp, tenant), "X-Forwarded-For": OFFICE_IP},
    )
    assert resp.status_code == 201, resp.text
    record_id = resp.json()["id"]
    resp = client.delete(
        f"/api/attendance/records/{record_id}", headers=auth_headers(admin, tenant)
    )
    assert resp.status_code == 400, resp.text
    # A leave record can still be deleted, and the delete is audited.
    resp = client.post(
        "/api/attendance/leave",
        json={"record_date": "2026-11-20"},
        headers=auth_headers(emp, tenant),
    )
    leave_id = resp.json()["id"]
    resp = client.delete(
        f"/api/attendance/records/{leave_id}", headers=auth_headers(admin, tenant)
    )
    assert resp.status_code == 204, resp.text
    assert "attendance_record_deleted" in _audit_actions(client, admin, tenant)


# ---------------------------------------------------------------- M7 / m10


def test_cross_site_origin_state_change_is_rejected(client, db):
    tenant = make_tenant(db)
    user = make_user(db, tenant, UserRole.EMPLOYEE)
    resp = client.post(
        "/api/auth/change-password",
        json={"current_password": DEFAULT_PASSWORD, "new_password": "NewPassword456!"},
        headers={**auth_headers(user, tenant), "Origin": "https://evil.example"},
    )
    assert resp.status_code == 403, resp.text
    # Same-origin (the test host) still works.
    resp = client.post(
        "/api/auth/change-password",
        json={"current_password": DEFAULT_PASSWORD, "new_password": "NewPassword456!"},
        headers={**auth_headers(user, tenant), "Origin": "http://testserver"},
    )
    assert resp.status_code == 204, resp.text


def test_csp_header_present_and_hsts_only_when_secure(client):
    resp = client.get("/healthz")
    assert resp.status_code == 200
    csp = resp.headers.get("content-security-policy", "")
    assert "default-src 'self'" in csp
    assert "frame-ancestors 'none'" in csp
    assert "strict-transport-security" not in resp.headers
    resp = client.get("/healthz", headers={"X-Forwarded-Proto": "https"})
    assert "strict-transport-security" in resp.headers


# ---------------------------------------------------------------- M8


def test_change_password_rate_limited_after_5_wrong_attempts(client, db):
    tenant = make_tenant(db)
    user = make_user(db, tenant, UserRole.EMPLOYEE)
    headers = auth_headers(user, tenant)
    codes = []
    for _ in range(6):
        resp = client.post(
            "/api/auth/change-password",
            json={"current_password": "wrong-password", "new_password": "NewPassword456!"},
            headers=headers,
        )
        codes.append(resp.status_code)
    assert codes[:5] == [400] * 5
    assert codes[5] == 429, codes


def test_admin_reset_password_rate_limited(client, db):
    tenant = make_tenant(db)
    admin = make_user(db, tenant, UserRole.TENANT_ADMIN)
    target = make_user(db, tenant, UserRole.EMPLOYEE, name="Target")
    headers = auth_headers(admin, tenant)
    codes = []
    for _ in range(6):
        resp = client.post(
            f"/api/admin/users/{target.id}/reset-password",
            json={"password": "ResetPass123!"},
            headers=headers,
        )
        codes.append(resp.status_code)
    assert codes[:5] == [200] * 5
    assert codes[5] == 429, codes


# ---------------------------------------------------------------- m1 / m2


def test_annual_pdf_filename_sanitizes_evil_employee_name(client, db):
    tenant = make_tenant(db)
    dept = make_department(db, tenant)
    admin = make_user(db, tenant, UserRole.TENANT_ADMIN, name="TA")
    evil = make_user(
        db, tenant, UserRole.EMPLOYEE,
        name='Evil"x\r\nInjected: yes', department=dept,
    )
    tpl = make_template(db, tenant, department=dept, metric_name="Sales", target=100.0)
    make_submission(
        db, tenant, evil, tpl, year=2026, period="January",
        status=KPIStatus.APPROVED, self_score=80, dept_score=80, final_score=80,
    )
    resp = client.get(
        f"/api/reports/annual/{evil.id}",
        params={"year": 2026},
        headers=auth_headers(admin, tenant),
    )
    assert resp.status_code == 200, resp.text
    disp = resp.headers["content-disposition"]
    assert "\r" not in disp and "\n" not in disp
    assert disp == 'attachment; filename="kpi-annual-evil-x--injected--yes-2026.pdf"', disp


def test_export_filename_does_not_leak_other_department_name(client, db):
    tenant = make_tenant(db)
    sales = make_department(db, tenant, name="Sales")
    eng = make_department(db, tenant, name="Engineering")
    dadmin = make_user(db, tenant, UserRole.DEPT_ADMIN, name="DA", department=eng)
    make_user(db, tenant, UserRole.EMPLOYEE, name="E1", department=eng)
    from app.core.timezone import today as app_today

    today = app_today()
    resp = client.get(
        "/api/attendance/report/export",
        params={"year": today.year, "month": today.month, "department_id": sales.id},
        headers=auth_headers(dadmin, tenant),
    )
    assert resp.status_code == 200, resp.text
    assert "sales" not in resp.headers["content-disposition"].lower()


# ---------------------------------------------------------------- m6 / m9


def test_password_over_72_bytes_rejected_with_400(client, db):
    tenant = make_tenant(db)
    user = make_user(db, tenant, UserRole.EMPLOYEE)
    long_pw = "x" * 73
    resp = client.post(
        "/api/auth/change-password",
        json={"current_password": DEFAULT_PASSWORD, "new_password": long_pw},
        headers=auth_headers(user, tenant),
    )
    assert resp.status_code == 400, resp.text
    assert "72" in resp.json()["detail"]


def test_create_user_rejects_password_over_72_bytes(client, db):
    tenant = make_tenant(db)
    admin = make_user(db, tenant, UserRole.TENANT_ADMIN)
    dept = make_department(db, tenant)
    resp = client.post(
        "/api/admin/users",
        json={
            "name": "Long Pw", "email": "longpw@example.com",
            "password": "x" * 73, "role": "employee", "department_id": dept.id,
        },
        headers=auth_headers(admin, tenant),
    )
    assert resp.status_code == 400, resp.text


def test_import_upload_rejects_non_zip_bytes(client, db):
    tenant = make_tenant(db)
    dept = make_department(db, tenant)
    dadmin = make_user(db, tenant, UserRole.DEPT_ADMIN, name="DA", department=dept)
    resp = client.post(
        "/api/admin/import/upload?dry_run=true",
        files={"file": ("import.xlsx", b"not a zip file at all", "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet")},
        headers=auth_headers(dadmin, tenant),
    )
    assert resp.status_code == 400, resp.text
    assert "not a valid Excel" in resp.json()["detail"]


# ---------------------------------------------------------------- m3 (API surface)


def test_late_fields_absent_from_summary_and_today(client, db):
    tenant, _, admin, emp = _att_setup(db)
    _set_office_wifi(client, admin, tenant)
    resp = client.post(
        "/api/attendance/check-in",
        headers={**auth_headers(emp, tenant), "X-Forwarded-For": OFFICE_IP},
    )
    assert resp.status_code == 201, resp.text
    assert "late_minutes" not in resp.json()
    from app.core.timezone import today as app_today

    today = app_today()
    summary = client.get(
        "/api/attendance/summary",
        params={"year": today.year, "month": today.month},
        headers=auth_headers(emp, tenant),
    ).json()
    assert "total_late_minutes" not in summary
    assert "late_days" not in summary
