"""Tests for reviewer notes: two-way discussion thread on KPI submissions.

Notes are visible to the submission's owner and any reviewer with scope over
it; both sides can post.
"""

from app.models.review_note import ReviewNote
from app.models.user import UserRole
from app.services import kpi_service
from app.tests.factories import (
    auth_headers,
    make_department,
    make_submission,
    make_template,
    make_tenant,
    make_user,
)


def _setup(db):
    tenant = make_tenant(db)
    dept = make_department(db, tenant)
    other_dept = make_department(db, tenant, name="Other Dept")
    employee = make_user(db, tenant, UserRole.EMPLOYEE, name="Employee", department=dept)
    other_employee = make_user(
        db, tenant, UserRole.EMPLOYEE, name="Other Employee", department=other_dept
    )
    dept_admin = make_user(db, tenant, UserRole.DEPT_ADMIN, name="Dept Admin", department=dept)
    other_dept_admin = make_user(
        db, tenant, UserRole.DEPT_ADMIN, name="Other Dept Admin", department=other_dept
    )
    tenant_admin = make_user(db, tenant, UserRole.TENANT_ADMIN, name="Tenant Admin")
    return tenant, dept, employee, other_employee, dept_admin, other_dept_admin, tenant_admin


def _auth(user, tenant):
    return auth_headers(user, tenant)


def _submitted(db, tenant, employee, period="September"):
    template = make_template(db, tenant, department=employee.department)
    submission = make_submission(db, tenant, employee, template, period=period, self_score=80.0)
    kpi_service.submit_for_dept_approval(db, employee, 2026, period)
    return submission


def test_employee_posts_and_reads_own_notes(client, db):
    tenant, dept, employee, *_ = _setup(db)
    submission = _submitted(db, tenant, employee)

    resp = client.post(
        f"/api/kpi/submissions/{submission.id}/notes",
        json={"body": "Why was my score reduced?"},
        headers=auth_headers(employee, tenant),
    )
    assert resp.status_code == 201
    data = resp.json()
    assert data["body"] == "Why was my score reduced?"
    assert data["author_name"] == "Employee"
    assert data["author_role"] == "employee"

    resp = client.get(
        f"/api/kpi/submissions/{submission.id}/notes", headers=auth_headers(employee, tenant)
    )
    assert resp.status_code == 200
    assert len(resp.json()) == 1


def test_two_way_thread_between_reviewer_and_employee(client, db):
    tenant, dept, employee, _other_emp, dept_admin, _other_da, _ta = _setup(db)
    submission = _submitted(db, tenant, employee)

    resp = client.post(
        f"/api/kpi/submissions/{submission.id}/notes",
        json={"body": "Your sales figure needs a source."},
        headers=auth_headers(dept_admin, tenant),
    )
    assert resp.status_code == 201
    assert resp.json()["author_role"] == "dept_admin"

    resp = client.post(
        f"/api/kpi/submissions/{submission.id}/notes",
        json={"body": "Attached the report link."},
        headers=auth_headers(employee, tenant),
    )
    assert resp.status_code == 201

    resp = client.get(
        f"/api/kpi/submissions/{submission.id}/notes", headers=auth_headers(employee, tenant)
    )
    notes = resp.json()
    assert len(notes) == 2
    # Oldest first.
    assert notes[0]["body"] == "Your sales figure needs a source."
    assert notes[1]["body"] == "Attached the report link."


def test_tenant_admin_can_post_on_any_submission(client, db):
    tenant, dept, employee, *_rest, tenant_admin = _setup(db)
    submission = _submitted(db, tenant, employee)

    resp = client.post(
        f"/api/kpi/submissions/{submission.id}/notes",
        json={"body": "Noted for final review."},
        headers=auth_headers(tenant_admin, tenant),
    )
    assert resp.status_code == 201


def test_other_employee_forbidden(client, db):
    tenant, dept, employee, other_employee, *_ = _setup(db)
    submission = _submitted(db, tenant, employee)

    resp = client.get(
        f"/api/kpi/submissions/{submission.id}/notes", headers=auth_headers(other_employee, tenant)
    )
    assert resp.status_code == 403

    resp = client.post(
        f"/api/kpi/submissions/{submission.id}/notes",
        json={"body": "Snooping."},
        headers=auth_headers(other_employee, tenant),
    )
    assert resp.status_code == 403


def test_dept_admin_outside_department_forbidden(client, db):
    tenant, dept, employee, _oe, _da, other_dept_admin, _ta = _setup(db)
    submission = _submitted(db, tenant, employee)

    resp = client.get(
        f"/api/kpi/submissions/{submission.id}/notes", headers=auth_headers(other_dept_admin, tenant)
    )
    assert resp.status_code == 403


def test_unauthenticated_rejected(client, db):
    tenant, dept, employee, *_ = _setup(db)
    submission = _submitted(db, tenant, employee)

    assert client.get(f"/api/kpi/submissions/{submission.id}/notes").status_code == 401
    assert (
        client.post(f"/api/kpi/submissions/{submission.id}/notes", json={"body": "Hi"}).status_code
        == 401
    )


def test_empty_body_rejected(client, db):
    tenant, dept, employee, *_ = _setup(db)
    submission = _submitted(db, tenant, employee)

    for body in ["", "   "]:
        resp = client.post(
            f"/api/kpi/submissions/{submission.id}/notes",
            json={"body": body},
            headers=auth_headers(employee, tenant),
        )
        assert resp.status_code in (400, 422)


def test_unknown_submission_returns_404(client, db):
    tenant, dept, employee, *_ = _setup(db)
    _submitted(db, tenant, employee)

    assert client.get("/api/kpi/submissions/999999/notes", headers=auth_headers(employee, tenant)).status_code == 404


def test_cross_tenant_submission_returns_404(client, db):
    tenant, dept, employee, *_ = _setup(db)
    submission = _submitted(db, tenant, employee)

    other_tenant = make_tenant(db, name="Other Co", slug="other-co")
    other_dept = make_department(db, other_tenant, name="Other Dept")
    outsider = make_user(
        db, other_tenant, UserRole.EMPLOYEE, name="Outsider", department=other_dept
    )
    resp = client.get(
        f"/api/kpi/submissions/{submission.id}/notes", headers=auth_headers(outsider, other_tenant)
    )
    assert resp.status_code == 404
