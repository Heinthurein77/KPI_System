"""Coverage for the user `position` field on the admin user endpoints:
create, edit, and read-back via GET /api/admin/users/{id}.
"""

from app.models.user import UserRole
from app.tests.factories import auth_headers, make_department, make_tenant, make_user


def test_create_user_with_position(db, client):
    tenant = make_tenant(db)
    dept = make_department(db, tenant)
    admin = make_user(db, tenant, UserRole.TENANT_ADMIN)

    resp = client.post(
        "/api/admin/users",
        json={
            "name": "Pos Hire",
            "email": "pos.hire@example.com",
            "password": "Password123!",
            "role": "employee",
            "department_id": dept.id,
            "position": "Senior Developer",
        },
        headers=auth_headers(admin, tenant),
    )
    assert resp.status_code == 200
    assert resp.json()["position"] == "Senior Developer"


def test_create_user_without_position_defaults_to_none(db, client):
    tenant = make_tenant(db)
    dept = make_department(db, tenant)
    admin = make_user(db, tenant, UserRole.TENANT_ADMIN)

    resp = client.post(
        "/api/admin/users",
        json={
            "name": "No Pos",
            "email": "no.pos@example.com",
            "password": "Password123!",
            "role": "employee",
            "department_id": dept.id,
        },
        headers=auth_headers(admin, tenant),
    )
    assert resp.status_code == 200
    assert resp.json()["position"] is None


def test_edit_user_position(db, client):
    tenant = make_tenant(db)
    dept = make_department(db, tenant)
    admin = make_user(db, tenant, UserRole.TENANT_ADMIN)
    target = make_user(db, tenant, UserRole.EMPLOYEE, department=dept)

    resp = client.put(
        f"/api/admin/users/{target.id}",
        json={
            "name": target.name,
            "email": target.email,
            "role": "employee",
            "department_id": dept.id,
            "position": "Team Lead",
        },
        headers=auth_headers(admin, tenant),
    )
    assert resp.status_code == 200
    assert resp.json()["position"] == "Team Lead"

    # read back
    resp = client.get(
        f"/api/admin/users/{target.id}", headers=auth_headers(admin, tenant)
    )
    assert resp.status_code == 200
    assert resp.json()["position"] == "Team Lead"
