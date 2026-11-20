"""GET /api/app-config (S-M3, senior review 2026-11-20): the single
read-only endpoint that exposes the server's submission grace length and
Asia/Yangon date to the frontend, so the UI never hardcodes the grace
window or trusts the browser clock. Read-only, on the existing session
auth — no new auth surface, and the grace rule itself is unchanged."""

from app.core.config import settings
from app.core.timezone import today as app_today
from app.models.user import UserRole
from app.tests.factories import auth_headers, make_tenant, make_user


def test_app_config_returns_server_grace_days_and_today(db, client):
    tenant = make_tenant(db)
    user = make_user(db, tenant, UserRole.TENANT_ADMIN, email="cfg1@acme.example.com")

    resp = client.get("/api/app-config", headers=auth_headers(user, tenant))

    assert resp.status_code == 200
    body = resp.json()
    assert body["submission_grace_days"] == settings.SUBMISSION_GRACE_DAYS
    assert body["today"] == app_today().isoformat()
    assert body["timezone"] == settings.TIMEZONE


def test_app_config_requires_auth(db, client):
    resp = client.get("/api/app-config")

    assert resp.status_code == 401


def test_app_config_rejects_platform_admin_without_tenant(db, client):
    # Same fail-closed tenant resolution as every other tenant-scoped route.
    admin = make_user(db, None, UserRole.SUPER_ADMIN, email="cfg2@platform.example.com")

    resp = client.get("/api/app-config", headers=auth_headers(admin))

    assert resp.status_code == 401
    assert resp.status_code != 200
