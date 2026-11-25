"""S-02 (security review 2026-11-25): TOTP enrolment + verification.

Covers: enrolment (tenant admin + platform admin), wrong/right code on
enrolment confirmation, login with/without TOTP, recovery-code use
(single-use), disable, role gating, and encrypted-at-rest storage.
"""

import pyotp

from app.core.security import decrypt_totp_secret
from app.models.user import UserRole
from app.tests.factories import DEFAULT_PASSWORD, auth_headers, make_tenant, make_user


def _enable_totp(db, client, user, tenant=None):
    """Drive the enrolment endpoints for an already-logged-in user; returns
    (secret, recovery_codes)."""
    resp = client.post("/api/auth/totp/enroll", headers=auth_headers(user, tenant))
    assert resp.status_code == 200, resp.text
    body = resp.json()
    secret = body["secret"]
    code = pyotp.TOTP(secret).now()
    resp = client.post(
        "/api/auth/totp/verify-enrolment",
        json={"code": code},
        headers=auth_headers(user, tenant),
    )
    assert resp.status_code == 200, resp.text
    return secret, resp.json()["recovery_codes"]


def test_totp_enroll_and_verify_for_tenant_admin(db, client):
    tenant = make_tenant(db)
    admin = make_user(db, tenant, UserRole.TENANT_ADMIN)

    resp = client.post("/api/auth/totp/enroll", headers=auth_headers(admin, tenant))
    assert resp.status_code == 200
    body = resp.json()
    assert body["secret"]
    assert body["otpauth_url"].startswith("otpauth://totp/")
    # The email is URL-encoded inside the otpauth:// URI (%40 for @).
    from urllib.parse import quote

    assert quote(admin.email, safe="") in body["otpauth_url"]

    # The secret is stored encrypted at rest — never plaintext in the row.
    db.refresh(admin)
    assert admin.totp_secret_encrypted
    assert admin.totp_secret_encrypted != body["secret"]
    assert decrypt_totp_secret(admin.totp_secret_encrypted) == body["secret"]
    assert admin.totp_enabled is False

    status = client.get("/api/auth/totp/status", headers=auth_headers(admin, tenant)).json()
    assert status == {
        "enabled": False,
        "enrolled": True,
        "recovery_codes_remaining": 0,
        "enforced_for_admins": False,
    }

    # Wrong code does not enable.
    resp = client.post(
        "/api/auth/totp/verify-enrolment",
        json={"code": "000000"},
        headers=auth_headers(admin, tenant),
    )
    assert resp.status_code == 400
    db.refresh(admin)
    assert admin.totp_enabled is False

    # Right code enables and issues single-use recovery codes.
    resp = client.post(
        "/api/auth/totp/verify-enrolment",
        json={"code": pyotp.TOTP(body["secret"]).now()},
        headers=auth_headers(admin, tenant),
    )
    assert resp.status_code == 200
    codes = resp.json()["recovery_codes"]
    assert len(codes) == 8
    assert len(set(codes)) == 8
    db.refresh(admin)
    assert admin.totp_enabled is True
    status = client.get("/api/auth/totp/status", headers=auth_headers(admin, tenant)).json()
    assert status["enabled"] is True
    assert status["recovery_codes_remaining"] == 8


def test_totp_enroll_forbidden_for_employee(db, client):
    tenant = make_tenant(db)
    employee = make_user(db, tenant, UserRole.EMPLOYEE)

    resp = client.post("/api/auth/totp/enroll", headers=auth_headers(employee, tenant))
    assert resp.status_code == 403


def test_login_without_totp_still_works_when_not_enrolled(db, client):
    tenant = make_tenant(db)
    user = make_user(db, tenant, UserRole.EMPLOYEE)

    resp = client.post(
        "/api/auth/login",
        json={"email": user.email, "password": DEFAULT_PASSWORD},
        headers={"X-Tenant-Slug": tenant.slug},
    )
    assert resp.status_code == 200
    assert resp.json()["totp_required"] is False
    assert "access_token" in resp.cookies


def test_login_with_totp_challenge_then_verify(db, client):
    tenant = make_tenant(db)
    admin = make_user(db, tenant, UserRole.TENANT_ADMIN)
    secret, _codes = _enable_totp(db, client, admin, tenant)

    # Password step: no session cookie yet, totp_required flag instead.
    resp = client.post(
        "/api/auth/login",
        json={"email": admin.email, "password": DEFAULT_PASSWORD},
        headers={"X-Tenant-Slug": tenant.slug},
    )
    assert resp.status_code == 200
    assert resp.json()["totp_required"] is True
    assert "access_token" not in resp.cookies
    assert "totp_pending" in resp.cookies

    # Wrong code fails; the session is still not established.
    resp = client.post("/api/auth/login/totp", json={"code": "000000"})
    assert resp.status_code == 401

    # Right code completes the login and sets the session cookie.
    resp = client.post("/api/auth/login/totp", json={"code": pyotp.TOTP(secret).now()})
    assert resp.status_code == 200
    assert resp.json()["totp_required"] is False
    assert "access_token" in resp.cookies

    me = client.get("/api/auth/me", headers={"X-Tenant-Slug": tenant.slug})
    assert me.status_code == 200
    assert me.json()["email"] == admin.email


def test_login_with_totp_recovery_code_is_single_use(db, client):
    tenant = make_tenant(db)
    admin = make_user(db, tenant, UserRole.TENANT_ADMIN)
    _secret, codes = _enable_totp(db, client, admin, tenant)
    code = codes[0]

    client.post(
        "/api/auth/login",
        json={"email": admin.email, "password": DEFAULT_PASSWORD},
        headers={"X-Tenant-Slug": tenant.slug},
    )
    resp = client.post("/api/auth/login/totp", json={"recovery_code": code})
    assert resp.status_code == 200
    status = client.get(
        "/api/auth/totp/status",
        headers={"X-Tenant-Slug": tenant.slug, **{"Authorization": f"Bearer {resp.cookies['access_token']}"}},
    )
    assert status.json()["recovery_codes_remaining"] == 7

    # The same recovery code cannot be used twice: fresh login, same code.
    client.post("/api/auth/logout")
    client.post(
        "/api/auth/login",
        json={"email": admin.email, "password": DEFAULT_PASSWORD},
        headers={"X-Tenant-Slug": tenant.slug},
    )
    resp = client.post("/api/auth/login/totp", json={"recovery_code": code})
    assert resp.status_code == 401


def test_totp_step_without_pending_token_fails(db, client):
    tenant = make_tenant(db)
    admin = make_user(db, tenant, UserRole.TENANT_ADMIN)
    _enable_totp(db, client, admin, tenant)

    # No password step first — there is no pending cookie to present.
    resp = client.post("/api/auth/login/totp", json={"code": "123456"})
    assert resp.status_code == 401


def test_totp_disable_requires_password_and_unenrols(db, client):
    tenant = make_tenant(db)
    admin = make_user(db, tenant, UserRole.TENANT_ADMIN)
    _enable_totp(db, client, admin, tenant)

    resp = client.post(
        "/api/auth/totp/disable",
        json={"password": "wrong-password"},
        headers=auth_headers(admin, tenant),
    )
    assert resp.status_code == 400

    resp = client.post(
        "/api/auth/totp/disable",
        json={"password": DEFAULT_PASSWORD},
        headers=auth_headers(admin, tenant),
    )
    assert resp.status_code == 204
    db.refresh(admin)
    assert admin.totp_enabled is False
    assert admin.totp_secret_encrypted is None

    # Login is password-only again.
    resp = client.post(
        "/api/auth/login",
        json={"email": admin.email, "password": DEFAULT_PASSWORD},
        headers={"X-Tenant-Slug": tenant.slug},
    )
    assert resp.status_code == 200
    assert resp.json()["totp_required"] is False


def test_platform_totp_enroll_and_login(db, client):
    admin = make_user(db, None, UserRole.SUPER_ADMIN, email="root-totp@platform.example.com")

    resp = client.post("/api/platform/auth/totp/enroll", headers=auth_headers(admin))
    assert resp.status_code == 200
    secret = resp.json()["secret"]

    resp = client.post(
        "/api/platform/auth/totp/verify-enrolment",
        json={"code": pyotp.TOTP(secret).now()},
        headers=auth_headers(admin),
    )
    assert resp.status_code == 200
    assert len(resp.json()["recovery_codes"]) == 8

    # Platform password step returns the same totp_required challenge...
    resp = client.post(
        "/api/platform/auth/login",
        json={"email": admin.email, "password": DEFAULT_PASSWORD},
    )
    assert resp.status_code == 200
    assert resp.json()["totp_required"] is True

    # ...completed through the shared TOTP step endpoint.
    resp = client.post("/api/auth/login/totp", json={"code": pyotp.TOTP(secret).now()})
    assert resp.status_code == 200
    assert "access_token" in resp.cookies

    me = client.get("/api/platform/auth/me")
    assert me.status_code == 200


def test_totp_recovery_codes_regenerate_invalidates_old(db, client):
    tenant = make_tenant(db)
    admin = make_user(db, tenant, UserRole.TENANT_ADMIN)
    _secret, old_codes = _enable_totp(db, client, admin, tenant)

    resp = client.post(
        "/api/auth/totp/recovery-codes/regenerate",
        json={"password": DEFAULT_PASSWORD},
        headers=auth_headers(admin, tenant),
    )
    assert resp.status_code == 200
    new_codes = resp.json()["recovery_codes"]
    assert new_codes != old_codes

    # Old code no longer works at the TOTP step.
    client.post(
        "/api/auth/login",
        json={"email": admin.email, "password": DEFAULT_PASSWORD},
        headers={"X-Tenant-Slug": tenant.slug},
    )
    resp = client.post("/api/auth/login/totp", json={"recovery_code": old_codes[0]})
    assert resp.status_code == 401
    resp = client.post("/api/auth/login/totp", json={"recovery_code": new_codes[0]})
    assert resp.status_code == 200
