"""Coverage for the core KPI workflow business logic in app/services/kpi_service.py.

These call the service functions directly against the `db` fixture rather than
going through HTTP, since this layer is mostly plain Python functions that take
a Session and ORM objects built via app.tests.factories.
"""

import pytest
from fastapi import HTTPException

from app.models.kpi_submission import KPIStatus
from app.models.user import UserRole
from app.services import kpi_service
from app.tests.factories import (
    make_department,
    make_submission,
    make_template,
    make_tenant,
    make_user,
)


# ---------------------------------------------------------------------------
# ensure_period_submissions
# ---------------------------------------------------------------------------


def test_ensure_period_submissions_creates_for_company_and_own_department_only(db):
    tenant = make_tenant(db)
    dept_a = make_department(db, tenant, name="Sales")
    dept_b = make_department(db, tenant, name="Support")
    employee = make_user(db, tenant, UserRole.EMPLOYEE, department=dept_a)

    company_wide = make_template(db, tenant, metric_name="Attendance", department=None)
    dept_a_template = make_template(db, tenant, metric_name="Sales Target", department=dept_a)
    dept_b_template = make_template(db, tenant, metric_name="Tickets Closed", department=dept_b)

    submissions = kpi_service.ensure_period_submissions(db, employee, 2026, "March")

    template_ids = {s.kpi_template_id for s in submissions}
    assert template_ids == {company_wide.id, dept_a_template.id}
    assert dept_b_template.id not in template_ids
    assert all(s.status == KPIStatus.DRAFT for s in submissions)


def test_ensure_period_submissions_idempotent(db):
    tenant = make_tenant(db)
    dept = make_department(db, tenant)
    employee = make_user(db, tenant, UserRole.EMPLOYEE, department=dept)
    make_template(db, tenant, metric_name="Attendance", department=None)
    make_template(db, tenant, metric_name="Quality", department=dept)

    first = kpi_service.ensure_period_submissions(db, employee, 2026, "April")
    second = kpi_service.ensure_period_submissions(db, employee, 2026, "April")

    assert {s.id for s in first} == {s.id for s in second}
    assert len(first) == 2

    from app.models.kpi_submission import KPISubmission
    from sqlalchemy import select

    all_rows = db.scalars(
        select(KPISubmission).where(
            KPISubmission.employee_id == employee.id,
            KPISubmission.year == 2026,
            KPISubmission.month_or_quarter == "April",
        )
    ).all()
    assert len(all_rows) == 2


def test_ensure_period_submissions_dept_admin_only_gets_own_locked_custom_template(db):
    tenant = make_tenant(db)
    dept = make_department(db, tenant)
    dept_admin = make_user(db, tenant, UserRole.DEPT_ADMIN, department=dept)

    # Recurring templates that a normal Employee would pick up.
    make_template(db, tenant, metric_name="Attendance", department=None)
    make_template(db, tenant, metric_name="Dept Metric", department=dept)
    # The Dept Admin's own custom metric, locked to this exact period.
    custom = make_template(
        db, tenant, metric_name="Leadership KPI", employee=dept_admin,
        locked_year=2026, locked_period="May",
    )
    # A custom template locked to a *different* period must not apply.
    make_template(
        db, tenant, metric_name="Old Custom KPI", employee=dept_admin,
        locked_year=2025, locked_period="May",
    )

    submissions = kpi_service.ensure_period_submissions(db, dept_admin, 2026, "May")

    assert len(submissions) == 1
    assert submissions[0].kpi_template_id == custom.id


def test_ensure_period_submissions_raises_400_when_no_templates_for_department(db):
    tenant = make_tenant(db)
    dept = make_department(db, tenant, name="Empty Dept")
    other_dept = make_department(db, tenant, name="Other Dept")
    employee = make_user(db, tenant, UserRole.EMPLOYEE, department=dept)
    # Only a template for a different department exists; nothing company-wide.
    make_template(db, tenant, metric_name="Other Metric", department=other_dept)

    with pytest.raises(HTTPException) as exc_info:
        kpi_service.ensure_period_submissions(db, employee, 2026, "June")

    assert exc_info.value.status_code == 400


# ---------------------------------------------------------------------------
# create_custom_employee_kpi
# ---------------------------------------------------------------------------


def test_create_custom_employee_kpi_creates_template_and_submission(db):
    tenant = make_tenant(db)
    dept = make_department(db, tenant)
    employee = make_user(db, tenant, UserRole.EMPLOYEE, department=dept)
    admin = make_user(db, tenant, UserRole.TENANT_ADMIN, name="Admin")

    submission = kpi_service.create_custom_employee_kpi(
        db, admin, employee, "Special Project", target=50.0, weight=2.0, year=2026, period="July",
    )

    assert submission.kpi_template.metric_name == "Special Project"
    assert submission.kpi_template.employee_id == employee.id
    assert submission.kpi_template.locked_year == 2026
    assert submission.kpi_template.locked_period == "July"
    assert submission.kpi_template.department_id == dept.id
    assert submission.status == KPIStatus.DRAFT
    assert submission.year == 2026
    assert submission.month_or_quarter == "July"


def test_create_custom_employee_kpi_duplicate_metric_name_raises_400(db):
    tenant = make_tenant(db)
    dept = make_department(db, tenant)
    employee = make_user(db, tenant, UserRole.EMPLOYEE, department=dept)
    admin = make_user(db, tenant, UserRole.TENANT_ADMIN, name="Admin")

    kpi_service.create_custom_employee_kpi(
        db, admin, employee, "Special Project", target=50.0, weight=2.0, year=2026, period="July",
    )

    with pytest.raises(HTTPException) as exc_info:
        kpi_service.create_custom_employee_kpi(
            db, admin, employee, "Special Project", target=75.0, weight=1.0, year=2026, period="July",
        )

    assert exc_info.value.status_code == 400
    assert "Special Project" in exc_info.value.detail


# ---------------------------------------------------------------------------
# save_self_scores
# ---------------------------------------------------------------------------


def test_save_self_scores_only_touches_own_submissions(db):
    tenant = make_tenant(db)
    dept = make_department(db, tenant)
    employee1 = make_user(db, tenant, UserRole.EMPLOYEE, name="Employee One", department=dept)
    employee2 = make_user(db, tenant, UserRole.EMPLOYEE, name="Employee Two", department=dept)
    template = make_template(db, tenant)

    sub1 = make_submission(db, tenant, employee1, template, period="August")
    sub2 = make_submission(db, tenant, employee2, template, period="August")

    kpi_service.save_self_scores(db, employee1, {sub1.id: 88.0, sub2.id: 99.0})

    db.refresh(sub1)
    db.refresh(sub2)
    assert sub1.self_score == 88.0
    # sub2 belongs to a different employee, so it must be untouched even though
    # its id was included in the scores dict.
    assert sub2.self_score is None


def test_save_self_scores_raises_400_if_not_draft(db):
    tenant = make_tenant(db)
    dept = make_department(db, tenant)
    employee = make_user(db, tenant, UserRole.EMPLOYEE, department=dept)
    template = make_template(db, tenant)
    submission = make_submission(
        db, tenant, employee, template, period="August",
        status=KPIStatus.PENDING_DEPT_APPROVAL, self_score=70.0,
    )

    with pytest.raises(HTTPException) as exc_info:
        kpi_service.save_self_scores(db, employee, {submission.id: 95.0})

    assert exc_info.value.status_code == 400


# ---------------------------------------------------------------------------
# submit_for_dept_approval
# ---------------------------------------------------------------------------


def test_submit_for_dept_approval_raises_404_when_no_submissions(db):
    tenant = make_tenant(db)
    dept = make_department(db, tenant)
    employee = make_user(db, tenant, UserRole.EMPLOYEE, department=dept)

    with pytest.raises(HTTPException) as exc_info:
        kpi_service.submit_for_dept_approval(db, employee, 2026, "September")

    assert exc_info.value.status_code == 404


def test_submit_for_dept_approval_raises_400_when_missing_self_score(db):
    tenant = make_tenant(db)
    dept = make_department(db, tenant)
    employee = make_user(db, tenant, UserRole.EMPLOYEE, department=dept)
    template1 = make_template(db, tenant, metric_name="Metric 1")
    template2 = make_template(db, tenant, metric_name="Metric 2")
    make_submission(db, tenant, employee, template1, period="September", self_score=80.0)
    make_submission(db, tenant, employee, template2, period="September", self_score=None)

    with pytest.raises(HTTPException) as exc_info:
        kpi_service.submit_for_dept_approval(db, employee, 2026, "September")

    assert exc_info.value.status_code == 400


def test_submit_for_dept_approval_employee_goes_to_pending_dept_approval(db):
    tenant = make_tenant(db)
    dept = make_department(db, tenant)
    employee = make_user(db, tenant, UserRole.EMPLOYEE, department=dept)
    template = make_template(db, tenant)
    submission = make_submission(db, tenant, employee, template, period="September", self_score=80.0)

    kpi_service.submit_for_dept_approval(db, employee, 2026, "September")

    db.refresh(submission)
    assert submission.status == KPIStatus.PENDING_DEPT_APPROVAL
    assert submission.submitted_at is not None


def test_submit_for_dept_approval_dept_admin_skips_to_pending_final_approval(db):
    tenant = make_tenant(db)
    dept = make_department(db, tenant)
    dept_admin = make_user(db, tenant, UserRole.DEPT_ADMIN, department=dept)
    template = make_template(db, tenant, employee=dept_admin, locked_year=2026, locked_period="September")
    submission = make_submission(db, tenant, dept_admin, template, period="September", self_score=90.0)

    kpi_service.submit_for_dept_approval(db, dept_admin, 2026, "September")

    db.refresh(submission)
    assert submission.status == KPIStatus.PENDING_FINAL_APPROVAL


# ---------------------------------------------------------------------------
# dept_save_score / dept_approve / final_approve / reject_submission
# ---------------------------------------------------------------------------


def _basic_setup(db, status=KPIStatus.PENDING_DEPT_APPROVAL, self_score=80.0, dept_score=None, final_score=None):
    tenant = make_tenant(db)
    dept = make_department(db, tenant)
    employee = make_user(db, tenant, UserRole.EMPLOYEE, department=dept)
    reviewer = make_user(db, tenant, UserRole.DEPT_ADMIN, name="Reviewer", department=dept)
    admin = make_user(db, tenant, UserRole.TENANT_ADMIN, name="Admin")
    template = make_template(db, tenant)
    submission = make_submission(
        db, tenant, employee, template, period="October",
        status=status, self_score=self_score, dept_score=dept_score, final_score=final_score,
    )
    return tenant, dept, employee, reviewer, admin, submission


def test_dept_save_score_wrong_status_raises_400(db):
    _, _, _, reviewer, _, submission = _basic_setup(db, status=KPIStatus.DRAFT)

    with pytest.raises(HTTPException) as exc_info:
        kpi_service.dept_save_score(db, reviewer, submission, 85.0, None)

    assert exc_info.value.status_code == 400


def test_dept_approve_wrong_status_raises_400(db):
    _, _, _, reviewer, _, submission = _basic_setup(db, status=KPIStatus.DRAFT)

    with pytest.raises(HTTPException) as exc_info:
        kpi_service.dept_approve(db, reviewer, submission, None, None)

    assert exc_info.value.status_code == 400


def test_dept_approve_score_fallback_explicit_param_wins(db):
    _, _, _, reviewer, _, submission = _basic_setup(
        db, status=KPIStatus.PENDING_DEPT_APPROVAL, self_score=70.0, dept_score=60.0,
    )

    kpi_service.dept_approve(db, reviewer, submission, 95.0, None)

    assert submission.dept_score == 95.0
    assert submission.status == KPIStatus.PENDING_FINAL_APPROVAL


def test_dept_approve_score_fallback_uses_existing_dept_score(db):
    _, _, _, reviewer, _, submission = _basic_setup(
        db, status=KPIStatus.PENDING_DEPT_APPROVAL, self_score=70.0, dept_score=60.0,
    )

    kpi_service.dept_approve(db, reviewer, submission, None, None)

    assert submission.dept_score == 60.0


def test_dept_approve_score_fallback_uses_self_score_when_nothing_else(db):
    _, _, _, reviewer, _, submission = _basic_setup(
        db, status=KPIStatus.PENDING_DEPT_APPROVAL, self_score=70.0, dept_score=None,
    )

    kpi_service.dept_approve(db, reviewer, submission, None, None)

    assert submission.dept_score == 70.0


def test_final_approve_wrong_status_raises_400(db):
    _, _, _, _, admin, submission = _basic_setup(db, status=KPIStatus.DRAFT)

    with pytest.raises(HTTPException) as exc_info:
        kpi_service.final_approve(db, admin, submission, None, None)

    assert exc_info.value.status_code == 400


def test_final_approve_score_fallback_explicit_param_wins(db):
    _, _, _, _, admin, submission = _basic_setup(
        db, status=KPIStatus.PENDING_FINAL_APPROVAL, self_score=70.0, dept_score=80.0,
    )

    kpi_service.final_approve(db, admin, submission, 99.0, None)

    assert submission.final_score == 99.0
    assert submission.status == KPIStatus.APPROVED


def test_final_approve_score_fallback_uses_dept_score(db):
    _, _, _, _, admin, submission = _basic_setup(
        db, status=KPIStatus.PENDING_FINAL_APPROVAL, self_score=70.0, dept_score=80.0,
    )

    kpi_service.final_approve(db, admin, submission, None, None)

    assert submission.final_score == 80.0


def test_final_approve_score_fallback_uses_self_score_when_nothing_else(db):
    _, _, _, _, admin, submission = _basic_setup(
        db, status=KPIStatus.PENDING_FINAL_APPROVAL, self_score=70.0, dept_score=None,
    )

    kpi_service.final_approve(db, admin, submission, None, None)

    assert submission.final_score == 70.0


@pytest.mark.parametrize("status", [KPIStatus.PENDING_DEPT_APPROVAL, KPIStatus.PENDING_FINAL_APPROVAL])
def test_reject_submission_allowed_from_pending_statuses(db, status):
    _, _, _, _, admin, submission = _basic_setup(db, status=status)

    kpi_service.reject_submission(db, admin, submission, "not good enough")

    assert submission.status == KPIStatus.REJECTED
    assert submission.remarks == "not good enough"


@pytest.mark.parametrize("status", [KPIStatus.DRAFT, KPIStatus.APPROVED])
def test_reject_submission_disallowed_from_other_statuses(db, status):
    _, _, _, _, admin, submission = _basic_setup(db, status=status)

    with pytest.raises(HTTPException) as exc_info:
        kpi_service.reject_submission(db, admin, submission, "nope")

    assert exc_info.value.status_code == 400


# ---------------------------------------------------------------------------
# tenant_admin_override
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("status", [KPIStatus.DRAFT, KPIStatus.PENDING_DEPT_APPROVAL])
def test_tenant_admin_override_forces_approved_regardless_of_status(db, status):
    _, _, _, _, admin, submission = _basic_setup(db, status=status)

    kpi_service.tenant_admin_override(db, admin, submission, 77.0, "override remarks")

    assert submission.status == KPIStatus.APPROVED
    assert submission.final_score == 77.0
    assert submission.remarks == "override remarks"
    assert submission.final_reviewed_by_id == admin.id


# ---------------------------------------------------------------------------
# get_submission_scoped
# ---------------------------------------------------------------------------


def test_get_submission_scoped_cross_tenant_is_404_not_403(db):
    tenant1 = make_tenant(db, name="Tenant One", slug="tenant-one")
    tenant2 = make_tenant(db, name="Tenant Two", slug="tenant-two")
    dept1 = make_department(db, tenant1)
    employee1 = make_user(db, tenant1, UserRole.EMPLOYEE, department=dept1)
    template1 = make_template(db, tenant1)
    submission = make_submission(db, tenant1, employee1, template1, period="November")

    other_admin = make_user(db, tenant2, UserRole.TENANT_ADMIN, name="Other Admin")

    with pytest.raises(HTTPException) as exc_info:
        kpi_service.get_submission_scoped(db, other_admin, submission.id)

    assert exc_info.value.status_code == 404


def test_get_submission_scoped_employee_cannot_fetch_others_submission(db):
    tenant = make_tenant(db)
    dept = make_department(db, tenant)
    employee1 = make_user(db, tenant, UserRole.EMPLOYEE, name="Employee One", department=dept)
    employee2 = make_user(db, tenant, UserRole.EMPLOYEE, name="Employee Two", department=dept)
    template = make_template(db, tenant)
    submission = make_submission(db, tenant, employee2, template, period="November")

    with pytest.raises(HTTPException) as exc_info:
        kpi_service.get_submission_scoped(db, employee1, submission.id)

    assert exc_info.value.status_code == 403


def test_get_submission_scoped_dept_admin_outside_department_forbidden(db):
    tenant = make_tenant(db)
    dept_a = make_department(db, tenant, name="Dept A")
    dept_b = make_department(db, tenant, name="Dept B")
    dept_admin = make_user(db, tenant, UserRole.DEPT_ADMIN, department=dept_a)
    employee = make_user(db, tenant, UserRole.EMPLOYEE, department=dept_b)
    template = make_template(db, tenant)
    submission = make_submission(db, tenant, employee, template, period="November", department=dept_b)

    with pytest.raises(HTTPException) as exc_info:
        kpi_service.get_submission_scoped(db, dept_admin, submission.id)

    assert exc_info.value.status_code == 403


def test_get_submission_scoped_self_review_blocked_for_dept_admin(db):
    tenant = make_tenant(db)
    dept = make_department(db, tenant)
    dept_admin = make_user(db, tenant, UserRole.DEPT_ADMIN, department=dept)
    template = make_template(db, tenant, employee=dept_admin, locked_year=2026, locked_period="November")
    # Own submission: department matches the Dept Admin's own department, so the
    # department check alone would pass -- the self-review check must still fire.
    submission = make_submission(
        db, tenant, dept_admin, template, period="November", department=dept,
    )

    with pytest.raises(HTTPException) as exc_info:
        kpi_service.get_submission_scoped(db, dept_admin, submission.id)

    assert exc_info.value.status_code == 403
    assert exc_info.value.detail == "You cannot review your own KPI submission."


def test_get_submission_scoped_self_review_blocked_for_tenant_admin(db):
    tenant = make_tenant(db)
    dept = make_department(db, tenant)
    admin = make_user(db, tenant, UserRole.TENANT_ADMIN, department=dept)
    template = make_template(db, tenant, employee=admin, locked_year=2026, locked_period="November")
    submission = make_submission(db, tenant, admin, template, period="November", department=dept)

    with pytest.raises(HTTPException) as exc_info:
        kpi_service.get_submission_scoped(db, admin, submission.id)

    assert exc_info.value.status_code == 403
    assert exc_info.value.detail == "You cannot review your own KPI submission."
