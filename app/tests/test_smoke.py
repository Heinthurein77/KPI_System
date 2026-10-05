from app.tests.factories import auth_headers, make_department, make_tenant, make_user
from app.models.user import UserRole


def test_health(client):
    resp = client.get("/healthz")
    assert resp.status_code == 200


def test_login_flow(db, client):
    tenant = make_tenant(db)
    dept = make_department(db, tenant)
    user = make_user(db, tenant, UserRole.EMPLOYEE, department=dept)

    resp = client.post(
        "/api/auth/login",
        json={"email": user.email, "password": "Password123!"},
        headers={"X-Tenant-Slug": tenant.slug},
    )
    assert resp.status_code == 200
    body = resp.json()
    assert body["user"]["role"] == "employee"
    assert body["user"]["tenant_id"] == tenant.id


def test_direct_token_auth_works(db, client):
    tenant = make_tenant(db)
    user = make_user(db, tenant, UserRole.TENANT_ADMIN)
    resp = client.get("/api/dashboard", headers=auth_headers(user, tenant))
    assert resp.status_code == 200
