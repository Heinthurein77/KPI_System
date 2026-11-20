"""Tenant-isolation extras: subdomain resolution, missing-slug 401, and a
suspended tenant mid-session.

The existing test_tenant_isolation.py only covers cross-tenant 404s via
admin user/department endpoints. These cover the middleware paths in
app/core/tenant.py + app/core/deps.py get_current_user: the hostname
subdomain, the fail-closed no-tenant branch, and the suspended-tenant
check that runs on every authenticated request.
"""

from fastapi.testclient import TestClient

from app.core.config import settings
from app.main import app
from app.models.tenant import TenantStatus
from app.models.user import UserRole
from app.tests.factories import auth_headers, make_tenant, make_user


def test_subdomain_resolves_tenant_without_slug_header(db, monkeypatch):
    monkeypatch.setattr(settings, "TENANT_BASE_DOMAIN", "example.com")
    tenant = make_tenant(db, name="Acme", slug="acme")
    admin = make_user(db, tenant, UserRole.TENANT_ADMIN)

    client = TestClient(app, base_url="http://acme.example.com")
    resp = client.get("/api/dashboard", headers={"Authorization": auth_headers(admin)["Authorization"]})

    assert resp.status_code == 200, resp.text


def test_wrong_subdomain_rejects_valid_token(db, monkeypatch):
    monkeypatch.setattr(settings, "TENANT_BASE_DOMAIN", "example.com")
    tenant = make_tenant(db, name="Acme", slug="acme")
    make_tenant(db, name="Globex", slug="globex")
    admin = make_user(db, tenant, UserRole.TENANT_ADMIN)

    # The token belongs to acme, but the request resolves to globex.
    client = TestClient(app, base_url="http://globex.example.com")
    resp = client.get("/api/dashboard", headers={"Authorization": auth_headers(admin)["Authorization"]})

    assert resp.status_code == 401


def test_missing_slug_401(db, client):
    tenant = make_tenant(db)
    admin = make_user(db, tenant, UserRole.TENANT_ADMIN)

    # No X-Tenant-Slug header and no subdomain: fail closed, never 200.
    resp = client.get("/api/dashboard", headers={"Authorization": auth_headers(admin)["Authorization"]})

    assert resp.status_code == 401


def test_suspended_tenant_rejected_mid_session(db, client):
    tenant = make_tenant(db)
    admin = make_user(db, tenant, UserRole.TENANT_ADMIN)
    headers = auth_headers(admin, tenant)

    resp = client.get("/api/dashboard", headers=headers)
    assert resp.status_code == 200

    # Suspend the tenant after the token was issued: the same token must
    # stop working on the very next request.
    tenant.status = TenantStatus.SUSPENDED
    db.commit()

    resp = client.get("/api/dashboard", headers=headers)
    assert resp.status_code == 401


def test_suspended_subdomain_tenant_rejected(db, monkeypatch):
    monkeypatch.setattr(settings, "TENANT_BASE_DOMAIN", "example.com")
    tenant = make_tenant(db, name="Acme", slug="acme", status=TenantStatus.SUSPENDED)
    admin = make_user(db, tenant, UserRole.TENANT_ADMIN)

    client = TestClient(app, base_url="http://acme.example.com")
    resp = client.get("/api/dashboard", headers={"Authorization": auth_headers(admin)["Authorization"]})

    assert resp.status_code == 401
