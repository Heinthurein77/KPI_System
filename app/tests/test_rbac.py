"""RoleChecker enforcement (app/core/deps.py) and the platform-admin
self-assessment guards added in this session (app/routers/dashboard.py,
app/routers/kpi.py) that reject a platform-level account from views meant
only for tenant-scoped roles."""

from app.models.user import UserRole
from app.tests.factories import auth_headers, make_tenant, make_user


def test_tenant_admin_gated_endpoint_rejects_dept_admin(db, client):
    tenant = make_tenant(db)
    dept_admin = make_user(db, tenant, UserRole.DEPT_ADMIN)

    resp = client.post(
        "/api/admin/departments",
        json={"name": "New Department"},
        headers=auth_headers(dept_admin, tenant),
    )

    assert resp.status_code == 403


def test_tenant_admin_gated_endpoint_rejects_employee(db, client):
    tenant = make_tenant(db)
    employee = make_user(db, tenant, UserRole.EMPLOYEE)

    resp = client.post(
        "/api/admin/departments",
        json={"name": "New Department"},
        headers=auth_headers(employee, tenant),
    )

    assert resp.status_code == 403


def test_platform_gated_endpoint_rejects_tenant_admin_token(db, client):
    """/api/platform/tenants requires require_platform_super_admin. A
    tenant-scoped JWT can't even authenticate in the platform context
    (get_current_user's else-branch only admits tenant_id IS NULL users), so
    this fails at authentication (401), never reaching the 403 role check."""
    tenant = make_tenant(db)
    tenant_admin = make_user(db, tenant, UserRole.TENANT_ADMIN)

    resp = client.get("/api/platform/tenants", headers=auth_headers(tenant_admin, tenant))

    assert resp.status_code == 401


def test_platform_super_admin_blocked_from_my_kpi(db, client):
    """Regression test: a platform Super Admin's token (tenant_id None), sent
    with no X-Tenant-Slug header, must not be able to reach /api/my-kpi.

    NOTE: as of the current app/core/deps.py, get_current_user fails closed
    (401) for *any* non-exempt route with no tenant resolved, regardless of
    the caller's tenant_id — a stricter guard than the route-level "This
    account type cannot access this view." 403 that /api/my-kpi itself still
    carries (see test_my_kpi_rejects_non_dept_admin_role below, which
    exercises that message on a request that *does* have a resolved tenant).
    That message therefore never fires for this platform-admin/no-header
    combination anymore: authentication itself now rejects it first. Either
    way, the outcome the regression cares about — a platform account can't
    reach this view — still holds."""
    admin = make_user(db, None, UserRole.SUPER_ADMIN, email="root@platform.example.com")

    resp = client.get("/api/my-kpi", headers=auth_headers(admin))

    assert resp.status_code == 401


def test_platform_super_admin_blocked_from_employee_save(db, client):
    """Regression test: same platform Super Admin must not be able to
    self-assess via /api/kpi/employee/save. See the note on
    test_platform_super_admin_blocked_from_my_kpi above — this is now
    rejected at authentication (401) rather than at the route's role check."""
    admin = make_user(db, None, UserRole.SUPER_ADMIN, email="root2@platform.example.com")

    resp = client.post(
        "/api/kpi/employee/save",
        json={"year": 2026, "period": "January", "scores": {}},
        headers=auth_headers(admin),
    )

    assert resp.status_code == 401


def test_my_kpi_rejects_non_dept_admin_role(db, client):
    """The route-level guard on /api/my-kpi ('This account type cannot access
    this view.') is still live for a request that *does* carry a resolved
    tenant context — e.g. a plain Employee, who authenticates fine but isn't
    a Dept Admin."""
    tenant = make_tenant(db)
    employee = make_user(db, tenant, UserRole.EMPLOYEE)

    resp = client.get("/api/my-kpi", headers=auth_headers(employee, tenant))

    assert resp.status_code == 403
    assert resp.json()["detail"] == "This account type cannot access this view."


def test_employee_save_rejects_tenant_admin_role(db, client):
    """Same for /api/kpi/employee/save's 'This account type does not
    self-assess.' guard: a Tenant Admin authenticates fine (valid tenant
    context) but isn't an Employee/Dept Admin."""
    tenant = make_tenant(db)
    tenant_admin = make_user(db, tenant, UserRole.TENANT_ADMIN)

    resp = client.post(
        "/api/kpi/employee/save",
        json={"year": 2026, "period": "January", "scores": {}},
        headers=auth_headers(tenant_admin, tenant),
    )

    assert resp.status_code == 403
    assert resp.json()["detail"] == "This account type does not self-assess."
