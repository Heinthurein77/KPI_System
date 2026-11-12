"""Platform Super Admin login (POST /api/platform/auth/login). This endpoint
is deliberately separate from the tenant login and only ever matches rows
where tenant_id IS NULL — a normal tenant-scoped user's credentials, even if
otherwise correct, must never succeed against it."""

from app.models.audit_log import AuditLog
from app.models.tenant import TenantStatus
from app.models.user import UserRole
from app.services import audit_service
from app.tests.factories import DEFAULT_PASSWORD, auth_headers, make_tenant, make_user


def test_platform_login_success(db, client):
    admin = make_user(db, None, UserRole.SUPER_ADMIN, email="root@platform.example.com")

    resp = client.post(
        "/api/platform/auth/login",
        json={"email": admin.email, "password": DEFAULT_PASSWORD},
    )

    assert resp.status_code == 200
    body = resp.json()
    assert body["user"]["role"] == "super_admin"
    assert body["user"]["tenant_id"] is None
    assert "access_token" not in body  # cookie-only sessions: no token in the body


def test_platform_login_wrong_password(db, client):
    admin = make_user(db, None, UserRole.SUPER_ADMIN, email="root2@platform.example.com")

    resp = client.post(
        "/api/platform/auth/login",
        json={"email": admin.email, "password": "wrong-password"},
    )

    assert resp.status_code == 401


def test_platform_login_rejects_tenant_scoped_user(db, client):
    """A tenant-scoped user's email/password — even though correct for the
    tenant login flow — must not authenticate through the platform endpoint,
    since it only queries User rows where tenant_id IS NULL."""
    tenant = make_tenant(db)
    tenant_user = make_user(db, tenant, UserRole.TENANT_ADMIN, email="admin@acme.example.com")

    resp = client.post(
        "/api/platform/auth/login",
        json={"email": tenant_user.email, "password": DEFAULT_PASSWORD},
    )

    assert resp.status_code == 401


def test_platform_me_returns_super_admin_without_tenant_slug(db, client):
    """Regression: platform admins were logged out on every page refresh
    because the tenant-scoped /api/auth/me always 401s for tenant-less
    accounts. /api/platform/auth/me is the exempt equivalent."""
    admin = make_user(db, None, UserRole.SUPER_ADMIN, email="root@platform.example.com")
    # TestClient persists cookies across requests like a browser.
    r = client.post("/api/platform/auth/login", json={"email": admin.email, "password": DEFAULT_PASSWORD})
    assert r.status_code == 200
    # No X-Tenant-Slug header: exactly what a platform session sends.
    r = client.get("/api/platform/auth/me")
    assert r.status_code == 200
    assert r.json()["role"] == "super_admin"
    assert r.json()["tenant_id"] is None


def test_platform_me_rejects_tenant_user(db, client):
    tenant = make_tenant(db)
    user = make_user(db, tenant, UserRole.TENANT_ADMIN, name="Boss")
    r = client.post(
        "/api/auth/login",
        json={"email": user.email, "password": DEFAULT_PASSWORD},
        headers={"X-Tenant-Slug": tenant.slug},
    )
    assert r.status_code == 200
    r = client.get("/api/platform/auth/me", headers={"X-Tenant-Slug": tenant.slug})
    assert r.status_code in (401, 403)


def test_platform_me_unauthenticated_401(db, client):
    assert client.get("/api/platform/auth/me").status_code == 401


def test_platform_change_password_ok(db, client):
    admin = make_user(db, None, UserRole.SUPER_ADMIN, email="root-pw@platform.example.com")
    r = client.post("/api/platform/auth/login", json={"email": admin.email, "password": DEFAULT_PASSWORD})
    assert r.status_code == 200
    r = client.post(
        "/api/platform/auth/change-password",
        json={"current_password": DEFAULT_PASSWORD, "new_password": "BrandNewPass123"},
    )
    assert r.status_code == 204
    # The dead session cookie is cleared — the admin logs in again.
    assert "access_token=" in r.headers.get("set-cookie", "")
    assert "Max-Age=0" in r.headers["set-cookie"]
    # Old password no longer works; token version was bumped.
    r = client.post("/api/platform/auth/login", json={"email": admin.email, "password": DEFAULT_PASSWORD})
    assert r.status_code == 401
    r = client.post("/api/platform/auth/login", json={"email": admin.email, "password": "BrandNewPass123"})
    assert r.status_code == 200


def test_platform_change_password_wrong_current(db, client):
    admin = make_user(db, None, UserRole.SUPER_ADMIN, email="root-pw2@platform.example.com")
    client.post("/api/platform/auth/login", json={"email": admin.email, "password": DEFAULT_PASSWORD})
    r = client.post(
        "/api/platform/auth/change-password",
        json={"current_password": "wrong", "new_password": "BrandNewPass123"},
    )
    assert r.status_code == 400


def test_platform_change_password_rejects_tenant_user(db, client):
    tenant = make_tenant(db)
    user = make_user(db, tenant, UserRole.TENANT_ADMIN, name="Boss")
    client.post(
        "/api/auth/login",
        json={"email": user.email, "password": DEFAULT_PASSWORD},
        headers={"X-Tenant-Slug": tenant.slug},
    )
    r = client.post(
        "/api/platform/auth/change-password",
        json={"current_password": DEFAULT_PASSWORD, "new_password": "BrandNewPass123"},
        headers={"X-Tenant-Slug": tenant.slug},
    )
    assert r.status_code in (401, 403)


# ---------- Tenant delete audit (M7, PM review 2026-11-12) ----------


def _platform_admin(db):
    return make_user(db, None, UserRole.SUPER_ADMIN, email="root-delete@platform.example.com")


def test_delete_tenant_writes_audit_row(db, client):
    """Tenant delete is the most destructive action in the system, so it
    must leave a durable audit row. It is logged platform-scoped (the
    acting admin has tenant_id NULL — migration 0024), which is what lets
    the record survive the tenant it describes: a row logged under the
    tenant's own id would cascade-delete with it (pinned below)."""
    admin = _platform_admin(db)
    tenant = make_tenant(db, name="Doomed", slug="doomed", status=TenantStatus.SUSPENDED)

    resp = client.delete(
        f"/api/platform/tenants/{tenant.id}", headers=auth_headers(admin)
    )
    assert resp.status_code == 200
    assert resp.json() == {"status": "ok"}

    rows = db.query(AuditLog).filter(AuditLog.action == "tenant_deleted").all()
    assert len(rows) == 1
    row = rows[0]
    assert row.tenant_id is None
    assert row.actor_id == admin.id
    assert row.actor_name == admin.name
    assert row.entity_id == tenant.id
    assert row.entity_label == "Doomed"
    assert "doomed" in row.summary

    listing = client.get("/api/platform/tenants", headers=auth_headers(admin))
    assert "doomed" not in [t["slug"] for t in listing.json()]


def test_delete_tenant_refused_when_not_suspended_writes_no_audit_row(db, client):
    admin = _platform_admin(db)
    tenant = make_tenant(db, name="Alive", slug="alive", status=TenantStatus.ACTIVE)

    resp = client.delete(
        f"/api/platform/tenants/{tenant.id}", headers=auth_headers(admin)
    )
    assert resp.status_code == 400
    assert db.query(AuditLog).filter(AuditLog.action == "tenant_deleted").count() == 0


def test_platform_actor_audit_row_has_null_tenant_id(db):
    """Migration 0024 closed the M7 schema gap: audit_service.log takes the
    actor's tenant_id, and a platform super_admin has tenant_id NULL — the
    INSERT now commits fine with a NULL tenant_id instead of failing the
    old NOT NULL constraint."""
    admin = _platform_admin(db)
    tenant = make_tenant(db, name="Acme", slug="acme-blocked")

    audit_service.log(
        db, admin, "tenant_deleted", "tenant", tenant.id, "Acme",
        "Tenant deleted by platform admin.",
    )
    db.commit()

    rows = db.query(AuditLog).filter(AuditLog.action == "tenant_deleted").all()
    assert len(rows) == 1
    assert rows[0].tenant_id is None
    assert rows[0].actor_id == admin.id


def test_tenant_delete_audit_row_under_tenant_id_does_not_survive(db, client):
    """Pins why the tenant delete is logged platform-scoped (tenant_id
    NULL) rather than under the deleted tenant's own id: audit_logs
    .tenant_id is ON DELETE CASCADE by design — a tenant-scoped row dies
    with its tenant. That is the documented behaviour for tenant history;
    the deletion record itself survives because it is platform-scoped."""
    admin = _platform_admin(db)
    tenant = make_tenant(db, name="Acme", slug="acme-cascade", status=TenantStatus.SUSPENDED)
    tenant_admin = make_user(db, tenant, UserRole.TENANT_ADMIN)

    audit_service.log(
        db, tenant_admin, "tenant_deleted", "tenant", tenant.id, "Acme",
        "Tenant deleted by platform admin.",
    )
    db.commit()
    assert db.query(AuditLog).count() == 1

    resp = client.delete(f"/api/platform/tenants/{tenant.id}", headers=auth_headers(admin))
    assert resp.status_code == 200
    # The tenant-scoped row cascaded away; the platform-scoped deletion
    # record the endpoint itself wrote is the one that remains.
    rows = db.query(AuditLog).all()
    assert len(rows) == 1
    assert rows[0].tenant_id is None
    assert rows[0].action == "tenant_deleted"


# ---------- Platform login audit (M7 — migration 0024) ----------


def test_platform_login_success_and_failure_write_audit_rows(db, client):
    """Platform logins use audit_service.log like tenant logins (migration
    0024: platform-scoped rows carry tenant_id NULL). Unknown emails have
    no user row to be the actor, so they are not logged."""
    admin = _platform_admin(db)

    bad = client.post(
        "/api/platform/auth/login",
        json={"email": admin.email, "password": "wrong-password"},
    )
    assert bad.status_code == 401
    good = client.post(
        "/api/platform/auth/login",
        json={"email": admin.email, "password": DEFAULT_PASSWORD},
    )
    assert good.status_code == 200

    failed = db.query(AuditLog).filter(AuditLog.action == "login_failed").all()
    assert len(failed) == 1
    assert failed[0].tenant_id is None
    assert failed[0].actor_id == admin.id
    succeeded = db.query(AuditLog).filter(AuditLog.action == "login_success").all()
    assert len(succeeded) == 1
    assert succeeded[0].tenant_id is None
    assert succeeded[0].actor_id == admin.id


# ---------- Platform audit log (N3, PM follow-up review 2026-11-12) ----------


def _seed_platform_and_tenant_rows(db):
    """3 platform-scoped rows (tenant_id NULL) + 2 tenant-scoped rows.
    Platform rows are written oldest-first, so the last one written is
    the newest and must come first in the endpoint's newest-first order."""
    admin = _platform_admin(db)
    tenant = make_tenant(db, name="Acme", slug="acme-audit")
    tenant_admin = make_user(db, tenant, UserRole.TENANT_ADMIN)

    audit_service.log(
        db, admin, "login_success", "user", admin.id, admin.name,
        "Successful login as super_admin.",
    )
    audit_service.log(
        db, admin, "login_failed", "user", admin.id, admin.name,
        "Failed platform login attempt.",
    )
    audit_service.log(
        db, admin, "tenant_deleted", "tenant", tenant.id, "Doomed",
        'Tenant "Doomed" (slug=doomed) deleted by platform admin.',
    )
    audit_service.log(
        db, tenant_admin, "kpi_template_created", "kpi_template", 1, "Sales",
        "KPI metric created.",
    )
    audit_service.log(
        db, tenant_admin, "kpi_final_approved", "kpi_submission", 2, "Sales",
        "Final approved.",
    )
    db.commit()
    return admin, tenant_admin, tenant


def test_platform_audit_log_returns_only_platform_rows(db, client):
    """GET /api/platform/audit-log returns the platform-scoped rows
    (tenant_id IS NULL) newest first — and no tenant-scoped row leaks
    in, even though tenant rows exist in the same table."""
    admin, _tenant_admin, _tenant = _seed_platform_and_tenant_rows(db)

    resp = client.get("/api/platform/audit-log", headers=auth_headers(admin))
    assert resp.status_code == 200
    rows = resp.json()
    assert len(rows) == 3
    assert [r["action"] for r in rows] == ["tenant_deleted", "login_failed", "login_success"]
    assert all(r["actor_name"] == admin.name for r in rows)
    assert all(r["actor_role"] == "super_admin" for r in rows)
    assert rows[0]["entity_label"] == "Doomed"


def test_platform_audit_log_rejects_tenant_admin(db, client):
    """A tenant admin must not read the platform log. Platform routes are
    tenant-exempt, so a tenant-scoped account is rejected at auth time
    (401, before the role check) — same as /api/platform/auth/me
    (test_platform_me_rejects_tenant_user)."""
    _admin, tenant_admin, tenant = _seed_platform_and_tenant_rows(db)

    resp = client.get("/api/platform/audit-log", headers=auth_headers(tenant_admin, tenant))
    assert resp.status_code == 401


def test_platform_audit_log_unauthenticated_401(db, client):
    assert client.get("/api/platform/audit-log").status_code == 401


def test_platform_audit_log_paging_and_limit_clamp(db, client):
    """Same paging contract as GET /api/admin/audit-log: limit default
    200 / max 500, newest first, offset skips from the newest end."""
    admin, _tenant_admin, _tenant = _seed_platform_and_tenant_rows(db)
    headers = auth_headers(admin)

    page1 = client.get("/api/platform/audit-log", params={"limit": 2}, headers=headers)
    assert page1.status_code == 200
    assert [r["action"] for r in page1.json()] == ["tenant_deleted", "login_failed"]

    page2 = client.get("/api/platform/audit-log", params={"limit": 2, "offset": 1}, headers=headers)
    assert page2.status_code == 200
    assert [r["action"] for r in page2.json()] == ["login_failed", "login_success"]

    # limit above the 500 cap is clamped, not rejected — all 3 rows come back.
    over = client.get("/api/platform/audit-log", params={"limit": 999}, headers=headers)
    assert over.status_code == 200
    assert len(over.json()) == 3

    # limit below 1 is clamped to 1.
    under = client.get("/api/platform/audit-log", params={"limit": 0}, headers=headers)
    assert under.status_code == 200
    assert len(under.json()) == 1
    assert under.json()[0]["action"] == "tenant_deleted"
