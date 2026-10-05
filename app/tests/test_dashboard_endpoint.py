"""HTTP-level integration tests for GET /api/dashboard and GET /api/my-kpi:
platform-account role boundaries, the same-name-employee keying regression,
past-period read-only gating, and the status_filter/department_id query
params plus the Dept-Admin-excludes-own-row rule.
"""

from app.models.kpi_submission import KPIStatus
from app.models.user import UserRole
from app.tests.factories import (
    auth_headers,
    make_department,
    make_submission,
    make_template,
    make_tenant,
    make_user,
)


# ---------------------------------------------------------------------------
# 4. Role-boundary regression: a platform-level Super Admin (tenant_id=None)
#    hitting tenant-scoped endpoints with no X-Tenant-Slug header.
# ---------------------------------------------------------------------------


def test_super_admin_no_tenant_header_rejected_from_my_kpi(db, client):
    admin = make_user(db, None, UserRole.SUPER_ADMIN, email="root.dash1@platform.example.com")

    resp = client.get("/api/my-kpi", headers=auth_headers(admin))

    # NOTE: per app/core/deps.py get_current_user, a non-exempt route with no
    # tenant resolved for the request fails closed at authentication (401)
    # before /api/my-kpi's own "This account type cannot access this view."
    # 403 role check is ever reached. Either way the account never gets 200
    # or leaks data -- see test_rbac.py::test_platform_super_admin_blocked_from_my_kpi
    # for the same finding.
    assert resp.status_code == 401
    assert resp.status_code != 200


def test_super_admin_no_tenant_header_rejected_from_employee_save(db, client):
    admin = make_user(db, None, UserRole.SUPER_ADMIN, email="root.dash2@platform.example.com")

    resp = client.post(
        "/api/kpi/employee/save",
        json={"year": 2026, "period": "January", "scores": {}},
        headers=auth_headers(admin),
    )

    assert resp.status_code == 401
    assert resp.status_code != 200


def test_super_admin_no_tenant_header_rejected_from_dashboard(db, client):
    """GET /api/dashboard also carries its own allow-list 403 guard ("This
    account type cannot access the dashboard.") for a tenant-scoped session
    holding an unexpected role. For this specific platform-admin/no-header
    combination, though, the request never reaches that check: it is
    rejected one layer earlier, by get_current_user's fail-closed branch for
    an unresolved tenant on a non-exempt route (401). The task's assumption
    that this endpoint would still hand back its own 403 here does not hold
    under the current app/core/deps.py -- confirmed empirically below rather
    than assumed. The important property (never 200, never real data) still
    holds either way."""
    admin = make_user(db, None, UserRole.SUPER_ADMIN, email="root.dash3@platform.example.com")

    resp = client.get("/api/dashboard", headers=auth_headers(admin))

    assert resp.status_code == 401
    assert resp.status_code != 200


# ---------------------------------------------------------------------------
# 5. Same-name-employee regression: employee_combined must be keyed by
#    employee id, not name, so two same-named employees don't get merged.
# ---------------------------------------------------------------------------


def test_dashboard_employee_combined_keeps_same_name_employees_separate(db, client):
    tenant = make_tenant(db)
    dept = make_department(db, tenant)
    tenant_admin = make_user(db, tenant, UserRole.TENANT_ADMIN)

    emp1 = make_user(db, tenant, UserRole.EMPLOYEE, name="John Smith", email="john.smith.1@example.com", department=dept)
    emp2 = make_user(db, tenant, UserRole.EMPLOYEE, name="John Smith", email="john.smith.2@example.com", department=dept)

    tmpl1 = make_template(
        db, tenant, metric_name="Sales", target=100.0, weight=1.0,
        department=dept, employee=emp1, locked_year=2026, locked_period="March",
    )
    tmpl2 = make_template(
        db, tenant, metric_name="Sales", target=200.0, weight=1.0,
        department=dept, employee=emp2, locked_year=2026, locked_period="March",
    )

    # emp1: 95/100 -> 95% attainment (warning).
    make_submission(
        db, tenant, emp1, tmpl1, year=2026, period="March",
        status=KPIStatus.APPROVED, self_score=95.0, dept_score=95.0, final_score=95.0,
        department=dept,
    )
    # emp2: 220/200 -> 110% attainment (good). Deliberately different from emp1's.
    make_submission(
        db, tenant, emp2, tmpl2, year=2026, period="March",
        status=KPIStatus.APPROVED, self_score=220.0, dept_score=220.0, final_score=220.0,
        department=dept,
    )

    resp = client.get(
        "/api/dashboard",
        params={"year": 2026, "period": "March"},
        headers=auth_headers(tenant_admin, tenant),
    )
    assert resp.status_code == 200
    combined = resp.json()["employee_combined"]

    # Two distinct entries keyed by employee id -- not merged into one by name.
    assert set(combined.keys()) == {str(emp1.id), str(emp2.id)}

    entry1 = combined[str(emp1.id)]
    entry2 = combined[str(emp2.id)]
    assert entry1["name"] == "John Smith"
    assert entry2["name"] == "John Smith"
    assert entry1["attainment"] == 95.0
    assert entry1["status"] == "warning"
    assert entry2["attainment"] == 110.0
    assert entry2["status"] == "good"


# ---------------------------------------------------------------------------
# 6. is_current_or_future_period gating: a past period with nothing existing
#    yet must not auto-create drafts, and must go through the read-only path.
# ---------------------------------------------------------------------------


def test_dashboard_past_period_with_no_data_does_not_create_drafts(db, client):
    tenant = make_tenant(db)
    dept = make_department(db, tenant)
    employee = make_user(db, tenant, UserRole.EMPLOYEE, department=dept)
    # A template exists (so if drafts *were* wrongly auto-created, this proves it),
    # but year 2000 is unambiguously in the past regardless of when this test runs.
    make_template(db, tenant, metric_name="Revenue", target=100.0, weight=1.0)

    resp = client.get(
        "/api/dashboard",
        params={"year": 2000, "period": "January"},
        headers=auth_headers(employee, tenant),
    )
    assert resp.status_code == 200
    body = resp.json()

    assert body["is_current_period"] is False
    # dashboard.py's read-only branch for a non-fillable period queries existing
    # submissions only -- it never calls ensure_period_submissions, so with no
    # prior submissions for this period, the list is empty rather than freshly
    # materialized drafts.
    assert body["submissions"] == []
    assert body["combined_score"] is None


# ---------------------------------------------------------------------------
# 7. status_filter / department_id query params, and the Dept-Admin-excludes-
#    own-row rule on their team dashboard.
# ---------------------------------------------------------------------------


def test_dashboard_status_filter_and_department_filter_and_dept_admin_excludes_self(db, client):
    tenant = make_tenant(db)
    dept1 = make_department(db, tenant, name="Engineering")
    dept2 = make_department(db, tenant, name="Sales")
    tenant_admin = make_user(db, tenant, UserRole.TENANT_ADMIN)
    dept_admin1 = make_user(db, tenant, UserRole.DEPT_ADMIN, name="Dept Admin 1", department=dept1)
    employee1 = make_user(db, tenant, UserRole.EMPLOYEE, name="Employee 1", department=dept1)
    employee2 = make_user(db, tenant, UserRole.EMPLOYEE, name="Employee 2", department=dept2)

    template = make_template(db, tenant, metric_name="Output", target=100.0, weight=1.0)

    sub_employee1 = make_submission(
        db, tenant, employee1, template, year=2026, period="April",
        status=KPIStatus.DRAFT, department=dept1,
    )
    sub_employee2 = make_submission(
        db, tenant, employee2, template, year=2026, period="April",
        status=KPIStatus.PENDING_DEPT_APPROVAL, department=dept2,
    )
    sub_dept_admin1_own = make_submission(
        db, tenant, dept_admin1, template, year=2026, period="April",
        status=KPIStatus.DRAFT, department=dept1,
    )

    params_base = {"year": 2026, "period": "April"}

    # status_filter=draft (Tenant Admin) -> only the two draft rows.
    resp = client.get(
        "/api/dashboard",
        params={**params_base, "status_filter": "draft"},
        headers=auth_headers(tenant_admin, tenant),
    )
    assert resp.status_code == 200
    ids = {s["id"] for s in resp.json()["submissions"]}
    assert ids == {sub_employee1.id, sub_dept_admin1_own.id}

    # status_filter=pending_dept_approval (Tenant Admin) -> only employee2's row.
    resp = client.get(
        "/api/dashboard",
        params={**params_base, "status_filter": "pending_dept_approval"},
        headers=auth_headers(tenant_admin, tenant),
    )
    assert resp.status_code == 200
    ids = {s["id"] for s in resp.json()["submissions"]}
    assert ids == {sub_employee2.id}

    # department_id=dept1 (Tenant Admin, no status filter) -> only dept1's rows.
    resp = client.get(
        "/api/dashboard",
        params={**params_base, "department_id": dept1.id},
        headers=auth_headers(tenant_admin, tenant),
    )
    assert resp.status_code == 200
    ids = {s["id"] for s in resp.json()["submissions"]}
    assert ids == {sub_employee1.id, sub_dept_admin1_own.id}

    # Dept Admin's own team view: sees employee1 (same department) but never
    # their own row, even though it's in-department and would otherwise match.
    resp = client.get(
        "/api/dashboard",
        params=params_base,
        headers=auth_headers(dept_admin1, tenant),
    )
    assert resp.status_code == 200
    dept_admin_body = resp.json()
    ids = {s["id"] for s in dept_admin_body["submissions"]}
    assert ids == {sub_employee1.id}
    assert all(s["employee"]["id"] != dept_admin1.id for s in dept_admin_body["submissions"])
