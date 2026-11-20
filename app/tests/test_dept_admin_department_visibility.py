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

    # Dept admin's own review queue excludes their own KPI — it lives only in
    # My KPI and they cannot approve it themselves.
    resp = client.get(
        "/api/dashboard", params={"year": 2026, "period": "October"},
        headers=auth_headers(dept_admin, tenant),
    )
    assert resp.status_code == 200
    assert not any(s["employee"]["name"] == "DA" for s in resp.json()["submissions"])


# ---------------------------------------------------------------------------
# New coverage (T-m2): the file's single test is one of the 11 deselected
# baseline tests, so dept-admin personal-KPI visibility had NO passing
# coverage in CI. These tests use the currently-open period instead of a
# hardcoded month, so they keep passing after the grace window closes.
# ---------------------------------------------------------------------------

from app.tests.factories import open_fillable_period


def _assign_and_submit_custom_kpi_open(db, client, tenant, tenant_admin, dept_admin):
    """Same flow as _assign_and_submit_custom_kpi, but for the period that
    is actually open today (current month from day 8, else the grace
    month). Returns (submission id, year, period)."""
    year, period = open_fillable_period()
    resp = client.post(
        "/api/admin/templates/custom",
        json={
            "employee_id": dept_admin.id,
            "metric_name": "Leadership",
            "target": 100,
            "weight": 10,
            "year": year,
            "period": period,
            "is_recurring": False,
        },
        headers=auth_headers(tenant_admin, tenant),
    )
    assert resp.status_code == 200, resp.text

    resp = client.get(
        "/api/my-kpi", params={"year": year, "period": period},
        headers=auth_headers(dept_admin, tenant),
    )
    assert resp.status_code == 200
    sub_id = resp.json()["submissions"][0]["id"]

    resp = client.post(
        "/api/kpi/employee/save",
        json={"year": year, "period": period, "scores": {str(sub_id): 80}},
        headers=auth_headers(dept_admin, tenant),
    )
    assert resp.status_code == 200, resp.text
    resp = client.post(
        "/api/kpi/employee/submit",
        json={"year": year, "period": period, "scores": {str(sub_id): 80}},
        headers=auth_headers(dept_admin, tenant),
    )
    assert resp.status_code == 200, resp.text
    return sub_id, year, period


def test_dept_admin_personal_kpi_visible_in_my_kpi(db, client):
    tenant = make_tenant(db)
    dept = make_department(db, tenant, name="Sales")
    tenant_admin = make_user(db, tenant, UserRole.TENANT_ADMIN, name="TA")
    dept_admin = make_user(db, tenant, UserRole.DEPT_ADMIN, name="DA", department=dept)

    sub_id, year, period = _assign_and_submit_custom_kpi_open(
        db, client, tenant, tenant_admin, dept_admin
    )

    resp = client.get(
        "/api/my-kpi", params={"year": year, "period": period},
        headers=auth_headers(dept_admin, tenant),
    )
    assert resp.status_code == 200
    mine = [s for s in resp.json()["submissions"] if s["id"] == sub_id]
    assert len(mine) == 1
    assert mine[0]["kpi_template"]["metric_name"] == "Leadership"


def test_dept_admin_personal_kpi_visible_in_unfiltered_tenant_admin_dashboard(db, client):
    tenant = make_tenant(db)
    dept = make_department(db, tenant, name="Sales")
    tenant_admin = make_user(db, tenant, UserRole.TENANT_ADMIN, name="TA")
    dept_admin = make_user(db, tenant, UserRole.DEPT_ADMIN, name="DA", department=dept)

    sub_id, year, period = _assign_and_submit_custom_kpi_open(
        db, client, tenant, tenant_admin, dept_admin
    )

    resp = client.get(
        "/api/dashboard", params={"year": year, "period": period},
        headers=auth_headers(tenant_admin, tenant),
    )
    assert resp.status_code == 200
    assert any(s["id"] == sub_id for s in resp.json()["submissions"])


def test_dept_admin_personal_kpi_hidden_from_other_dept_admin(db, client):
    tenant = make_tenant(db)
    sales = make_department(db, tenant, name="Sales")
    ops = make_department(db, tenant, name="Operations")
    tenant_admin = make_user(db, tenant, UserRole.TENANT_ADMIN, name="TA")
    dept_admin = make_user(db, tenant, UserRole.DEPT_ADMIN, name="DA", department=sales)
    other_admin = make_user(db, tenant, UserRole.DEPT_ADMIN, name="OA", department=ops)

    sub_id, year, period = _assign_and_submit_custom_kpi_open(
        db, client, tenant, tenant_admin, dept_admin
    )

    resp = client.get(
        "/api/dashboard", params={"year": year, "period": period},
        headers=auth_headers(other_admin, tenant),
    )
    assert resp.status_code == 200
    assert not any(s["id"] == sub_id for s in resp.json()["submissions"])
