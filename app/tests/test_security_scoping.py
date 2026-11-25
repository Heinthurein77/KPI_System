"""Track 2 (security fix-all, 2026-11-25 review): authorization scoping.

Covers S-06 (peer dept-admin visibility on the team dashboard), S-07
(peer dept-admin annual PDF), S-08 (NULL-department dept admin bypass),
M-09 (list_users peer dept-admins), M-10 (cross-stage reject) and M-11
(annual-report department lookup without a tenant filter).
"""

import pytest

from app.models.kpi_submission import KPIStatus
from app.models.user import UserRole
from app.services import annual_report as annual_report_service
from app.services import kpi_service
from app.tests.factories import (
    auth_headers,
    make_department,
    make_submission,
    make_template,
    make_tenant,
    make_user,
)


def _setup_dept(db, dept_name="Engineering"):
    tenant = make_tenant(db)
    dept = make_department(db, tenant, name=dept_name)
    tenant_admin = make_user(db, tenant, UserRole.TENANT_ADMIN)
    dept_admin = make_user(db, tenant, UserRole.DEPT_ADMIN, name="Dept Admin", department=dept)
    peer_admin = make_user(
        db, tenant, UserRole.DEPT_ADMIN, name="Peer Admin", department=dept
    )
    employee = make_user(db, tenant, UserRole.EMPLOYEE, name="Employee", department=dept)
    template = make_template(db, tenant, metric_name="Output", department=dept)
    return tenant, dept, tenant_admin, dept_admin, peer_admin, employee, template


def _dashboard_ids(client, user, tenant, year=2026, period="April"):
    resp = client.get(
        "/api/dashboard",
        params={"year": year, "period": period},
        headers=auth_headers(user, tenant),
    )
    assert resp.status_code == 200, resp.text
    return {s["id"] for s in resp.json()["submissions"]}


# ---------------------------------------------------------------- S-06


def test_s06_dept_admin_team_dashboard_hides_peer_dept_admin(db, client):
    tenant, dept, _, dept_admin, peer_admin, employee, template = _setup_dept(db)
    emp_sub = make_submission(
        db, tenant, employee, template, year=2026, period="April",
        status=KPIStatus.PENDING_DEPT_APPROVAL, department=dept,
    )
    peer_sub = make_submission(
        db, tenant, peer_admin, template, year=2026, period="April",
        status=KPIStatus.PENDING_DEPT_APPROVAL, department=dept,
    )
    own_sub = make_submission(
        db, tenant, dept_admin, template, year=2026, period="April",
        status=KPIStatus.DRAFT, department=dept,
    )

    ids = _dashboard_ids(client, dept_admin, tenant)
    assert ids == {emp_sub.id}
    assert peer_sub.id not in ids
    assert own_sub.id not in ids


def test_s06_tenant_admin_dashboard_still_sees_everyone(db, client):
    tenant, dept, tenant_admin, _, peer_admin, employee, template = _setup_dept(db)
    emp_sub = make_submission(
        db, tenant, employee, template, year=2026, period="April",
        status=KPIStatus.PENDING_DEPT_APPROVAL, department=dept,
    )
    peer_sub = make_submission(
        db, tenant, peer_admin, template, year=2026, period="April",
        status=KPIStatus.PENDING_DEPT_APPROVAL, department=dept,
    )

    ids = _dashboard_ids(client, tenant_admin, tenant)
    assert {emp_sub.id, peer_sub.id} <= ids


# ---------------------------------------------------------------- S-07


def test_s07_dept_admin_cannot_download_peer_dept_admin_annual_pdf(db, client):
    tenant, _, _, dept_admin, peer_admin, employee, _ = _setup_dept(db)
    resp = client.get(
        f"/api/reports/annual/{peer_admin.id}",
        params={"year": 2026},
        headers=auth_headers(dept_admin, tenant),
    )
    assert resp.status_code == 403

    resp = client.get(
        f"/api/reports/annual/{employee.id}",
        params={"year": 2026},
        headers=auth_headers(dept_admin, tenant),
    )
    assert resp.status_code == 200
    assert resp.headers["content-type"] == "application/pdf"


# ---------------------------------------------------------------- M-09


def test_m09_list_users_hides_peer_dept_admins_for_dept_admin(db, client):
    tenant, dept, _, dept_admin, peer_admin, employee, _ = _setup_dept(db)
    resp = client.get("/api/admin/users", headers=auth_headers(dept_admin, tenant))
    assert resp.status_code == 200
    ids = {u["id"] for u in resp.json()["users"]}
    assert employee.id in ids
    assert peer_admin.id not in ids


# ---------------------------------------------------------------- M-10


def test_m10_dept_admin_cannot_reject_submission_awaiting_final_approval(db):
    tenant, dept, _, dept_admin, _, employee, template = _setup_dept(db)
    submission = make_submission(
        db, tenant, employee, template, year=2026, period="October",
        status=KPIStatus.PENDING_FINAL_APPROVAL, self_score=80.0, dept_score=80.0,
    )
    with pytest.raises(Exception) as exc_info:
        kpi_service.reject_submission(db, dept_admin, submission, "too late")
    assert exc_info.value.status_code == 400
    db.refresh(submission)
    assert submission.status == KPIStatus.PENDING_FINAL_APPROVAL


def test_m10_dept_admin_can_reject_submission_awaiting_dept_approval(db):
    tenant, dept, _, dept_admin, _, employee, template = _setup_dept(db)
    submission = make_submission(
        db, tenant, employee, template, year=2026, period="October",
        status=KPIStatus.PENDING_DEPT_APPROVAL, self_score=80.0,
    )
    kpi_service.reject_submission(db, dept_admin, submission, "redo")
    assert submission.status == KPIStatus.REJECTED


def test_m10_tenant_admin_can_reject_from_either_pending_stage(db):
    tenant, dept, tenant_admin, _, _, employee, template = _setup_dept(db)
    for i, status in enumerate(
        (KPIStatus.PENDING_DEPT_APPROVAL, KPIStatus.PENDING_FINAL_APPROVAL)
    ):
        # One template per submission: the (employee, template, period)
        # triple is unique.
        tpl = template if i == 0 else make_template(
            db, tenant, metric_name="Second", department=dept
        )
        submission = make_submission(
            db, tenant, employee, tpl, year=2026, period="October",
            status=status, self_score=80.0,
        )
        kpi_service.reject_submission(db, tenant_admin, submission, "redo")
        assert submission.status == KPIStatus.REJECTED


# ---------------------------------------------------------------- M-11


def test_m11_annual_report_ignores_cross_tenant_department(db):
    tenant_a = make_tenant(db, name="Acme", slug="acme")
    tenant_b = make_tenant(db, name="Globex", slug="globex")
    dept_b = make_department(db, tenant_b, name="Other Tenant Dept")
    # Corrupt linkage: employee in tenant A points at tenant B's dept.
    employee = make_user(db, tenant_a, UserRole.EMPLOYEE, department=None)
    employee.department_id = dept_b.id
    db.commit()

    report = annual_report_service.build_annual_report(db, tenant_a.id, employee.id, 2026)
    assert report.department_name == "-"


def test_m11_annual_report_shows_own_tenant_department(db):
    tenant, dept, _, _, _, employee, _ = _setup_dept(db)
    report = annual_report_service.build_annual_report(db, tenant.id, employee.id, 2026)
    assert report.department_name == dept.name


# ---------------------------------------------------------------- S-08


def _null_dept_admin(db, tenant):
    """A legacy dept admin with department_id=None."""
    return make_user(db, tenant, UserRole.DEPT_ADMIN, name="No Dept Admin")


def test_s08_null_dept_admin_dashboard_is_self_only(db, client):
    tenant = make_tenant(db)
    dept = make_department(db, tenant)
    null_admin = _null_dept_admin(db, tenant)
    employee = make_user(db, tenant, UserRole.EMPLOYEE, department=dept)
    template = make_template(db, tenant, department=dept)
    emp_sub = make_submission(
        db, tenant, employee, template, year=2026, period="April",
        status=KPIStatus.PENDING_DEPT_APPROVAL, department=dept,
    )
    own_sub = make_submission(
        db, tenant, null_admin, template, year=2026, period="April",
        status=KPIStatus.DRAFT, department=None,
    )

    ids = _dashboard_ids(client, null_admin, tenant)
    # Self-only, and the dept admin's own rows live in /my-kpi, not here.
    assert emp_sub.id not in ids
    assert own_sub.id not in ids
    assert ids == set()


def test_s08_null_dept_admin_cannot_view_other_employee_kpi_trend(db, client):
    tenant = make_tenant(db)
    null_admin = _null_dept_admin(db, tenant)
    # An employee with no department (legacy row): None == None must
    # not read as "same department".
    null_employee = make_user(db, tenant, UserRole.EMPLOYEE, name="No Dept Emp")

    resp = client.get(
        f"/api/admin/users/{null_employee.id}/kpi-trend",
        headers=auth_headers(null_admin, tenant),
    )
    assert resp.status_code == 403


def test_s08_null_dept_admin_list_users_is_self_only(db, client):
    tenant = make_tenant(db)
    null_admin = _null_dept_admin(db, tenant)
    other_null = make_user(db, tenant, UserRole.EMPLOYEE, name="Other Null")

    resp = client.get("/api/admin/users", headers=auth_headers(null_admin, tenant))
    assert resp.status_code == 200
    ids = {u["id"] for u in resp.json()["users"]}
    assert ids == {null_admin.id}
    assert other_null.id not in ids


def test_s08_null_dept_admin_cannot_approve_or_toggle_or_export(db, client):
    tenant = make_tenant(db)
    dept = make_department(db, tenant)
    null_admin = _null_dept_admin(db, tenant)
    employee = make_user(db, tenant, UserRole.EMPLOYEE, department=dept)
    null_employee = make_user(db, tenant, UserRole.EMPLOYEE, name="Null Emp")
    template = make_template(db, tenant, department=dept)
    submission = make_submission(
        db, tenant, employee, template, year=2026, period="October",
        status=KPIStatus.PENDING_DEPT_APPROVAL, self_score=80.0, department=dept,
    )

    # Approve path (get_submission_scoped): NULL dept must not match.
    resp = client.post(
        f"/api/kpi/{submission.id}/dept-approve",
        json={"year": 2026, "period": "October", "dept_score": 80.0},
        headers=auth_headers(null_admin, tenant),
    )
    assert resp.status_code == 403

    # Toggle path: NULL dept must not match even a NULL-dept employee.
    resp = client.post(
        f"/api/admin/users/{null_employee.id}/toggle-active",
        headers=auth_headers(null_admin, tenant),
    )
    assert resp.status_code == 403

    # Monthly export path.
    resp = client.get(
        "/api/admin/kpi-export",
        params={"employee_id": null_employee.id, "month": "October", "year": 2026},
        headers=auth_headers(null_admin, tenant),
    )
    assert resp.status_code == 403


def test_s08_null_dept_admin_cannot_create_templates(db, client):
    tenant = make_tenant(db)
    null_admin = _null_dept_admin(db, tenant)

    # Company-wide metric: the old code resolved department None and
    # created a tenant-wide template.
    resp = client.post(
        "/api/admin/templates",
        json={"metric_name": "Sneaky", "target": 100.0, "weight": 1.0, "department_id": None},
        headers=auth_headers(null_admin, tenant),
    )
    assert resp.status_code == 403

    # Custom KPI for a NULL-department employee: None == None must not
    # read as "same department".
    null_employee = make_user(db, tenant, UserRole.EMPLOYEE, name="Null Emp")
    resp = client.post(
        "/api/admin/templates/custom",
        json={
            "employee_id": null_employee.id,
            "metric_name": "Sneaky Custom",
            "target": 100,
            "weight": 10,
            "year": 2026,
            "period": "October",
            "is_recurring": False,
        },
        headers=auth_headers(null_admin, tenant),
    )
    assert resp.status_code == 403


def test_s08_null_dept_admin_cannot_manage_tenant_wide_template(db, client):
    tenant = make_tenant(db)
    null_admin = _null_dept_admin(db, tenant)
    dept = make_department(db, tenant)
    employee = make_user(db, tenant, UserRole.EMPLOYEE, department=dept)
    # Tenant-wide recurring custom template for the employee.
    template = make_template(
        db, tenant, metric_name="Custom", department=None, employee=employee,
        locked_year=2026, locked_period="October", is_recurring=True,
    )

    resp = client.patch(
        f"/api/admin/templates/{template.id}/weight",
        json={"weight": 5.0},
        headers=auth_headers(null_admin, tenant),
    )
    assert resp.status_code == 403

    resp = client.patch(
        f"/api/admin/templates/{template.id}/name",
        json={"metric_name": "Renamed"},
        headers=auth_headers(null_admin, tenant),
    )
    assert resp.status_code == 403

    resp = client.delete(
        f"/api/admin/templates/{template.id}",
        headers=auth_headers(null_admin, tenant),
    )
    assert resp.status_code == 403
