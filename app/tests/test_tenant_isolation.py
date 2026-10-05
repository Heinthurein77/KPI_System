"""Cross-tenant data isolation via get_tenant_scoped_or_404 (app/core/tenant.py).
A tenant admin reaching for another tenant's row by its real numeric id must
get a 404 (row doesn't exist, as far as they're concerned), never a 403 that
would confirm the id belongs to someone else."""

from app.models.user import UserRole
from app.tests.factories import auth_headers, make_department, make_tenant, make_user


def test_cross_tenant_get_user_404(db, client):
    tenant_a = make_tenant(db, name="Acme", slug="acme")
    tenant_b = make_tenant(db, name="Globex", slug="globex")
    admin_a = make_user(db, tenant_a, UserRole.TENANT_ADMIN)
    user_b = make_user(db, tenant_b, UserRole.EMPLOYEE)

    resp = client.get(f"/api/admin/users/{user_b.id}", headers=auth_headers(admin_a, tenant_a))

    assert resp.status_code == 404
    assert resp.status_code != 403


def test_cross_tenant_edit_user_404(db, client):
    tenant_a = make_tenant(db, name="Acme", slug="acme")
    tenant_b = make_tenant(db, name="Globex", slug="globex")
    admin_a = make_user(db, tenant_a, UserRole.TENANT_ADMIN)
    user_b = make_user(db, tenant_b, UserRole.EMPLOYEE)

    resp = client.put(
        f"/api/admin/users/{user_b.id}",
        json={"name": "Renamed", "email": user_b.email, "role": "employee"},
        headers=auth_headers(admin_a, tenant_a),
    )

    assert resp.status_code == 404
    assert resp.status_code != 403


def test_cross_tenant_department_not_found(db, client):
    """Same guard on a different resource type/route (delete department)."""
    tenant_a = make_tenant(db, name="Acme", slug="acme")
    tenant_b = make_tenant(db, name="Globex", slug="globex")
    admin_a = make_user(db, tenant_a, UserRole.TENANT_ADMIN)
    dept_b = make_department(db, tenant_b, name="Globex Eng")

    resp = client.delete(f"/api/admin/departments/{dept_b.id}", headers=auth_headers(admin_a, tenant_a))

    assert resp.status_code == 404
