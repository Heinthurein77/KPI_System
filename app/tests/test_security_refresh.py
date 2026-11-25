"""S-03 (security review 2026-11-25): 1h access tokens, rotating refresh
tokens, idle timeout, and revoke-all-sessions.

Covers: refresh issues a fresh pair and rotates (old token rejected),
access-token lifetime is 1h, idle refresh is refused, expired refresh is
refused, refresh dies on password change, revoke-all (self / tenant admin
/ platform admin), and logout killing the refresh token + audit row.
"""

from datetime import datetime, timedelta, timezone

from fastapi.testclient import TestClient

from app.core.security import decode_session_token
from app.main import app
from app.models.audit_log import AuditLog
from app.models.refresh_token import RefreshToken
from app.models.user import UserRole
from app.tests.factories import DEFAULT_PASSWORD, auth_headers, make_tenant, make_user


def _login(client, tenant, user):
    resp = client.post(
        "/api/auth/login",
        json={"email": user.email, "password": DEFAULT_PASSWORD},
        headers={"X-Tenant-Slug": tenant.slug},
    )
    assert resp.status_code == 200, resp.text
    return resp


def test_access_token_lifetime_is_one_hour(db, client):
    tenant = make_tenant(db)
    user = make_user(db, tenant, UserRole.EMPLOYEE)
    resp = _login(client, tenant, user)

    claims = decode_session_token(resp.cookies["access_token"])
    assert claims is not None
    remaining = (claims.exp - datetime.now(timezone.utc)).total_seconds()
    assert 3500 < remaining <= 3600


def test_refresh_rotates_and_old_token_is_rejected(db, client):
    tenant = make_tenant(db)
    user = make_user(db, tenant, UserRole.EMPLOYEE)
    _login(client, tenant, user)
    old_refresh = client.cookies.get("refresh_token")
    assert old_refresh

    resp = client.post("/api/auth/refresh")
    assert resp.status_code == 200
    assert resp.json()["user"]["email"] == user.email
    new_refresh = client.cookies.get("refresh_token")
    assert new_refresh and new_refresh != old_refresh

    # Replaying the used refresh token fails — rotation deleted its row.
    client.cookies.set("refresh_token", old_refresh)
    resp = client.post("/api/auth/refresh")
    assert resp.status_code == 401

    # The new token still works.
    client.cookies.set("refresh_token", new_refresh)
    resp = client.post("/api/auth/refresh")
    assert resp.status_code == 200


def test_refresh_without_cookie_is_401(client):
    resp = client.post("/api/auth/refresh")
    assert resp.status_code == 401


def test_refresh_idle_timeout_refused(db, client):
    tenant = make_tenant(db)
    user = make_user(db, tenant, UserRole.EMPLOYEE)
    _login(client, tenant, user)

    # Simulate 13 h of idleness on the stored row.
    row = db.query(RefreshToken).filter(RefreshToken.user_id == user.id).one()
    row.last_used_at = datetime.now(timezone.utc) - timedelta(hours=13)
    db.commit()

    resp = client.post("/api/auth/refresh")
    assert resp.status_code == 401
    assert "idle" in resp.json()["detail"].lower()
    assert db.query(RefreshToken).filter(RefreshToken.user_id == user.id).count() == 0


def test_refresh_expired_token_refused(db, client):
    tenant = make_tenant(db)
    user = make_user(db, tenant, UserRole.EMPLOYEE)
    _login(client, tenant, user)

    row = db.query(RefreshToken).filter(RefreshToken.user_id == user.id).one()
    row.expires_at = datetime.now(timezone.utc) - timedelta(seconds=1)
    db.commit()

    resp = client.post("/api/auth/refresh")
    assert resp.status_code == 401


def test_refresh_after_password_change_refused(db, client):
    tenant = make_tenant(db)
    user = make_user(db, tenant, UserRole.EMPLOYEE)
    _login(client, tenant, user)
    old_refresh = client.cookies.get("refresh_token")

    resp = client.post(
        "/api/auth/change-password",
        json={"current_password": DEFAULT_PASSWORD, "new_password": "BrandNewPass123"},
        headers={"X-Tenant-Slug": tenant.slug},
    )
    assert resp.status_code == 204

    # The password change bumped token_version: the snapshot on the old
    # refresh row no longer matches, and the row was deleted too.
    client.cookies.set("refresh_token", old_refresh)
    resp = client.post("/api/auth/refresh")
    assert resp.status_code == 401
    assert db.query(RefreshToken).filter(RefreshToken.user_id == user.id).count() == 0


def test_revoke_all_sessions_kills_every_session(db, client):
    tenant = make_tenant(db)
    user = make_user(db, tenant, UserRole.EMPLOYEE)
    other_client = TestClient(app)

    _login(client, tenant, user)
    _login(other_client, tenant, user)

    resp = client.post("/api/auth/revoke-all-sessions", headers={"X-Tenant-Slug": tenant.slug})
    assert resp.status_code == 204

    # Both sessions are dead — access token and refresh token alike.
    assert client.get("/api/auth/me", headers={"X-Tenant-Slug": tenant.slug}).status_code == 401
    assert other_client.get("/api/auth/me", headers={"X-Tenant-Slug": tenant.slug}).status_code == 401
    assert other_client.post("/api/auth/refresh").status_code == 401

    audit = db.query(AuditLog).filter(
        AuditLog.action == "sessions_revoked", AuditLog.actor_id == user.id
    ).all()
    assert len(audit) == 1


def test_tenant_admin_revokes_user_sessions(db, client):
    tenant = make_tenant(db)
    admin = make_user(db, tenant, UserRole.TENANT_ADMIN)
    employee = make_user(db, tenant, UserRole.EMPLOYEE)
    other_tenant = make_tenant(db, name="Other", slug="other")
    outsider = make_user(db, other_tenant, UserRole.EMPLOYEE)
    admin_client = TestClient(app)

    _login(client, tenant, employee)
    employee_client = client

    # NOTE: the revoke calls go through a separate client — the employee
    # session cookie in `client` would otherwise take precedence over the
    # admin's Bearer header (cookie-first auth, see deps._session_token).
    resp = admin_client.post(
        f"/api/auth/revoke-user-sessions/{employee.id}",
        headers=auth_headers(admin, tenant),
    )
    assert resp.status_code == 204
    assert employee_client.get("/api/auth/me", headers={"X-Tenant-Slug": tenant.slug}).status_code == 401
    assert employee_client.post("/api/auth/refresh").status_code == 401

    # Cross-tenant target 404s; an employee cannot revoke anyone. NOTE:
    # the employee's token_version was bumped by the revoke above, so a
    # Bearer token minted earlier (auth_headers caches version 0) is
    # stale — mint a fresh one at the current version for this check.
    resp = admin_client.post(
        f"/api/auth/revoke-user-sessions/{outsider.id}",
        headers=auth_headers(admin, tenant),
    )
    assert resp.status_code == 404
    from app.core.security import create_session_token

    db.refresh(employee)
    fresh_employee_headers = {
        "Authorization": f"Bearer {create_session_token(employee.id, employee.tenant_id, employee.token_version or 0)}",
        "X-Tenant-Slug": tenant.slug,
    }
    resp = admin_client.post(
        f"/api/auth/revoke-user-sessions/{admin.id}",
        headers=fresh_employee_headers,
    )
    assert resp.status_code == 403


def test_platform_admin_revokes_user_sessions(db, client):
    tenant = make_tenant(db)
    platform_admin = make_user(db, None, UserRole.SUPER_ADMIN, email="root-revoke@platform.example.com")
    employee = make_user(db, tenant, UserRole.EMPLOYEE)
    admin_client = TestClient(app)

    _login(client, tenant, employee)
    resp = admin_client.post(
        f"/api/platform/auth/revoke-user-sessions/{employee.id}",
        headers=auth_headers(platform_admin),
    )
    assert resp.status_code == 204
    assert client.get("/api/auth/me", headers={"X-Tenant-Slug": tenant.slug}).status_code == 401

    resp = admin_client.post(
        "/api/platform/auth/revoke-user-sessions/999999",
        headers=auth_headers(platform_admin),
    )
    assert resp.status_code == 404


def test_logout_kills_refresh_token_and_audits(db, client):
    tenant = make_tenant(db)
    user = make_user(db, tenant, UserRole.EMPLOYEE)
    _login(client, tenant, user)
    old_refresh = client.cookies.get("refresh_token")

    resp = client.post("/api/auth/logout")
    assert resp.status_code == 204

    client.cookies.set("refresh_token", old_refresh)
    assert client.post("/api/auth/refresh").status_code == 401

    rows = db.query(AuditLog).filter(
        AuditLog.action == "logout", AuditLog.actor_id == user.id
    ).all()
    assert len(rows) == 1


def test_refresh_token_stored_hashed(db, client):
    tenant = make_tenant(db)
    user = make_user(db, tenant, UserRole.EMPLOYEE)
    _login(client, tenant, user)

    raw = client.cookies.get("refresh_token")
    row = db.query(RefreshToken).filter(RefreshToken.user_id == user.id).one()
    assert row.token_hash != raw
    assert len(row.token_hash) == 64  # sha256 hex
