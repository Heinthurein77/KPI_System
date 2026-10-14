"""Regression tests: a Dept Admin's personally-assigned KPI must be visible in
department-scoped views.

Root cause found 2026-10-14: a Dept Admin created without a department got
department_id=None on their templates/submissions, so their KPI never
appeared under any department filter ("department မာမကျ"). Dept Admins are
now required to belong to a department at create/edit time; these tests lock
in the visibility behavior for properly-assigned accounts.
"""

from app.models.user import UserRole
from app.tests.factories import (
    auth_headers,
    make_department,
    make_tenant,
    make_user,
)


def _assign_and_submit_custom_kpi(db, client, tenant, tenant_admin, dept_admin):
    """Tenant admin assigns a custom KPI to the dept admin; the dept admin
    fills and submits it. Returns the submission id."""
    resp = client.post(
        "/api/admin/templates/custom",
        json={
            "employee_id": dept_admin.id,
            "metric_name": "Leadership",
            "target": 100,
            "weight": 10,
            "year": 2026,
            "period": "October",
            "is_recurring": False,
        },
        headers=auth_headers(tenant_admin, tenant),
    )
    assert resp.status_code == 200, resp.text

    resp = client.get(
        "/api/my-kpi", params={"year": 2026, "period": "October"},
        headers=auth_headers(dept_admin, tenant),
    )
    assert resp.status_code == 200
    sub_id = resp.json()["submissions"][0]["id"]

    resp = client.post(
        "/api/kpi/employee/save",
        json={"year": 2026, "period": "October", "scores": {str(sub_id): 80}},
        headers=auth_headers(dept_admin, tenant),
    )
    assert resp.status_code == 200, resp.text
    resp = client.post(
        "/api/kpi/employee/submit",
        json={"year": 2026, "period": "October", "scores": {str(sub_id): 80}},
        headers=auth_headers(dept_admin, tenant),
    )
    assert resp.status_code == 200, resp.text
    return sub_id


def test_dept_admin_personal_kpi_visible_in_department_filter(db, client):
    tenant = make_tenant(db)
    dept = make_department(db, tenant, name="Sales")
    tenant_admin = make_user(db, tenant, UserRole.TENANT_ADMIN, name="TA")
    dept_admin = make_user(db, tenant, UserRole.DEPT_ADMIN, name="DA", department=dept)

    _assign_and_submit_custom_kpi(db, client, tenant, tenant_admin, dept_admin)

    # Tenant admin's department-filtered dashboard includes the dept admin's KPI.
    resp = client.get(
        "/api/dashboard",
        params={"year": 2026, "period": "October", "department_id": dept.id},
        headers=auth_headers(tenant_admin, tenant),
    )
    assert resp.status_code == 200
    assert any(s["employee"]["name"] == "DA" for s in resp.json()["submissions"])

    # Dept admin's own review queue includes their submitted KPI (self-approval).
    resp = client.get(
        "/api/dashboard", params={"year": 2026, "period": "October"},
        headers=auth_headers(dept_admin, tenant),
    )
    assert resp.status_code == 200
    assert any(s["employee"]["name"] == "DA" for s in resp.json()["submissions"])
