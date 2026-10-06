from datetime import datetime, timezone
from decimal import Decimal

from fastapi import HTTPException, status
from sqlalchemy import select
from sqlalchemy.orm import Session, joinedload

from app.models.kpi_submission import KPIStatus, KPISubmission
from app.models.kpi_template import KPITemplate
from app.models.user import User, UserRole
from app.services import audit_service
from app.services.audit_service import fmt_score
from app.services.recurring_service import is_template_active_for_period


WEIGHT_TOTAL_MESSAGE = (
    "Weight စုစုပေါင်းသည် 100% ဖြစ်ရပါမည်။ "
    "(100 ထက် ကျော်လွန်နေပါသည် သို့မဟုတ် 100 မပြည့်သေးပါ)"
)


def _now() -> datetime:
    return datetime.now(timezone.utc)


def force_delete_template(db: Session, actor: User, template: KPITemplate) -> None:
    """Permanently delete a KPI template and every submission recorded against it,
    regardless of status. Tenant Admin's delete is unconditional by design — this is
    what backs it. (Dept Admin keeps the separate history-protected delete path.)"""
    audit_service.log(
        db, actor, "kpi_template_deleted", "kpi_template", template.id, template.metric_name,
        f"KPI metric deleted (target={fmt_score(template.target)}, weight={fmt_score(template.weight)}), "
        "including all submission history.",
        department_id=template.department_id,
    )
    db.query(KPISubmission).filter(KPISubmission.kpi_template_id == template.id).delete(
        synchronize_session=False
    )
    db.delete(template)
    db.commit()


def force_delete_user(db: Session, actor: User, target: User) -> None:
    """Permanently delete a user and their own KPI submission history. Submissions
    they only *reviewed* (not their own) belong to someone else's record, so those
    are kept — just detached from this reviewer. Backs Super Admin's unconditional
    user delete (the only user-delete path — Dept Admin never had delete access)."""
    audit_service.log(
        db, actor, "user_deleted", "user", target.id, target.name,
        f"User deleted ({target.role.value}), including their own KPI submission history.",
        department_id=target.department_id,
    )
    db.query(KPISubmission).filter(KPISubmission.employee_id == target.id).delete(
        synchronize_session=False
    )
    db.query(KPISubmission).filter(KPISubmission.dept_reviewed_by_id == target.id).update(
        {"dept_reviewed_by_id": None}, synchronize_session=False
    )
    db.query(KPISubmission).filter(KPISubmission.final_reviewed_by_id == target.id).update(
        {"final_reviewed_by_id": None}, synchronize_session=False
    )
    db.delete(target)
    db.commit()


def visible_submissions_query(user: User):
    """Scope submissions to what this role is allowed to see (data isolation)."""
    query = select(KPISubmission).options(
        joinedload(KPISubmission.employee),
        joinedload(KPISubmission.kpi_template),
        joinedload(KPISubmission.department),
        joinedload(KPISubmission.dept_reviewer),
        joinedload(KPISubmission.final_reviewer),
    ).where(KPISubmission.tenant_id == user.tenant_id)

    if user.role == UserRole.TENANT_ADMIN:
        return query
    if user.role == UserRole.DEPT_ADMIN:
        return query.where(KPISubmission.department_id == user.department_id)
    return query.where(KPISubmission.employee_id == user.id)


def own_submissions_query(user: User):
    """A user's own KPI submissions, regardless of role — used for self-assessment views."""
    return select(KPISubmission).options(
        joinedload(KPISubmission.employee),
        joinedload(KPISubmission.kpi_template),
        joinedload(KPISubmission.department),
        joinedload(KPISubmission.dept_reviewer),
        joinedload(KPISubmission.final_reviewer),
    ).where(KPISubmission.employee_id == user.id, KPISubmission.tenant_id == user.tenant_id)


def validate_period_total_weight(db: Session, employee: User, year: int, period: str) -> None:
    """Block edits for a period unless its existing KPI weights total exactly 100.

    This guard only reads the current submissions and their existing templates.
    It deliberately leaves the score calculation, template data, and workflow
    state untouched.
    """
    submissions = db.scalars(
        select(KPISubmission)
        .options(joinedload(KPISubmission.kpi_template))
        .where(
            KPISubmission.employee_id == employee.id,
            KPISubmission.year == year,
            KPISubmission.month_or_quarter == period,
            KPISubmission.tenant_id == employee.tenant_id,
        )
    ).unique().all()

    # Preserve the existing no-submission behavior of the save/submit flows.
    # There is no assigned weight to validate until a period has KPI rows.
    if not submissions:
        return

    total_weight = sum(
        (Decimal(str(submission.kpi_template.weight)) for submission in submissions),
        Decimal("0"),
    )
    if total_weight != Decimal("100"):
        raise HTTPException(status.HTTP_400_BAD_REQUEST, WEIGHT_TOTAL_MESSAGE)


def get_submission_scoped(db: Session, user: User, submission_id: int) -> KPISubmission:
    submission = db.get(
        KPISubmission,
        submission_id,
        options=[
            joinedload(KPISubmission.employee),
            joinedload(KPISubmission.kpi_template),
            joinedload(KPISubmission.department),
            joinedload(KPISubmission.dept_reviewer),
            joinedload(KPISubmission.final_reviewer),
        ],
    )
    if submission is None or submission.tenant_id != user.tenant_id:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Submission not found.")

    if user.role == UserRole.EMPLOYEE and submission.employee_id != user.id:
        raise HTTPException(status.HTTP_403_FORBIDDEN, "Not your submission.")
    if user.role == UserRole.DEPT_ADMIN and submission.department_id != user.department_id:
        raise HTTPException(status.HTTP_403_FORBIDDEN, "Submission outside your department.")
    if submission.employee_id == user.id:
        raise HTTPException(status.HTTP_403_FORBIDDEN, "You cannot review your own KPI submission.")

    return submission


def ensure_period_submissions(
    db: Session, employee: User, year: int, period: str
) -> list[KPISubmission]:
    """Create draft submissions for every metric template applicable to the employee, idempotently.

    Recurring department/company-wide metrics only apply to Employees — a Dept
    Admin's own KPI is always a custom one-off metric created specifically for
    them, locked to this exact year/period.
    """
    custom_condition = (
        (KPITemplate.employee_id == employee.id)
        & (
            ((KPITemplate.locked_year == year) & (KPITemplate.locked_period == period))
            | (KPITemplate.is_recurring.is_(True))
        )
    )
    if employee.role == UserRole.EMPLOYEE:
        condition = custom_condition | (
            KPITemplate.employee_id.is_(None)
            & ((KPITemplate.department_id == employee.department_id) | (KPITemplate.department_id.is_(None)))
        )
    else:
        condition = custom_condition

    templates = db.scalars(
        select(KPITemplate).where(KPITemplate.tenant_id == employee.tenant_id, condition)
    ).all()

    # A recurring custom template becomes applicable from its configured month
    # forward.  Keeping this guard in the existing lazy materialisation path
    # makes monthly carry-over automatic even if the bulk job has not run yet.
    templates = [
        template for template in templates
        if not template.is_recurring or is_template_active_for_period(template, year, period)
    ]

    if not templates:
        if employee.role == UserRole.EMPLOYEE:
            raise HTTPException(
                status.HTTP_400_BAD_REQUEST,
                "No KPI metrics have been configured for your department yet.",
            )
        return []

    existing = db.scalars(
        select(KPISubmission).where(
            KPISubmission.employee_id == employee.id,
            KPISubmission.year == year,
            KPISubmission.month_or_quarter == period,
            KPISubmission.tenant_id == employee.tenant_id,
        )
    ).all()
    existing_template_ids = {s.kpi_template_id for s in existing}

    created: list[KPISubmission] = []
    for template in templates:
        if template.id in existing_template_ids:
            continue
        submission = KPISubmission(
            tenant_id=employee.tenant_id,
            employee_id=employee.id,
            department_id=employee.department_id,
            kpi_template_id=template.id,
            year=year,
            month_or_quarter=period,
            status=KPIStatus.DRAFT,
        )
        db.add(submission)
        created.append(submission)

    db.commit()
    return existing + created


def create_custom_employee_kpi(
    db: Session,
    actor: User,
    employee: User,
    metric_name: str,
    target: float,
    weight: float,
    year: int,
    period: str,
    is_recurring: bool = True,
) -> KPISubmission:
    """Dept Admin creates a one-off (or recurring) custom metric for a single employee.

    When is_recurring=True the template is flagged for monthly carry-over by
    the recurrence service — the submission for the requested period is still
    materialised immediately so it's visible right away.

    Materializes the KPISubmission immediately so it's visible right away, rather
    than waiting for the employee to load their dashboard for that period.
    """
    existing_template = db.scalar(
        select(KPITemplate).where(
            KPITemplate.employee_id == employee.id,
            KPITemplate.metric_name == metric_name,
            KPITemplate.locked_year == year,
            KPITemplate.locked_period == period,
            KPITemplate.tenant_id == employee.tenant_id,
        )
    )
    if existing_template is not None:
        raise HTTPException(
            status.HTTP_400_BAD_REQUEST,
            f'A custom KPI named "{metric_name}" already exists for {employee.name} in {period} {year}.',
        )

    template = KPITemplate(
        tenant_id=employee.tenant_id,
        metric_name=metric_name,
        target=target,
        weight=weight,
        department_id=employee.department_id,
        employee_id=employee.id,
        locked_year=year,
        locked_period=period,
        is_recurring=is_recurring,
    )
    db.add(template)
    db.flush()

    submission = KPISubmission(
        tenant_id=employee.tenant_id,
        employee_id=employee.id,
        department_id=employee.department_id,
        kpi_template_id=template.id,
        year=year,
        month_or_quarter=period,
        status=KPIStatus.DRAFT,
    )
    db.add(submission)
    recurring_note = " Marked as monthly recurring." if is_recurring else ""
    audit_service.log(
        db, actor, "kpi_template_created", "kpi_template", template.id,
        f"{metric_name} — {employee.name}",
        f"Custom KPI created for {employee.name}: target={fmt_score(target)}, weight={fmt_score(weight)}.{recurring_note}",
        department_id=employee.department_id,
    )
    db.commit()
    return submission


def save_self_scores(db: Session, employee: User, scores: dict[int, float]) -> None:
    submissions = db.scalars(
        select(KPISubmission).where(
            KPISubmission.id.in_(scores.keys()),
            KPISubmission.employee_id == employee.id,
            KPISubmission.tenant_id == employee.tenant_id,
        )
    ).all()
    for submission in submissions:
        if submission.status != KPIStatus.DRAFT:
            raise HTTPException(
                status.HTTP_400_BAD_REQUEST, "Cannot edit a submission that has already been submitted."
            )
        submission.self_score = scores[submission.id]
    db.commit()


def submit_for_dept_approval(db: Session, employee: User, year: int, period: str) -> None:
    submissions = db.scalars(
        select(KPISubmission).where(
            KPISubmission.employee_id == employee.id,
            KPISubmission.year == year,
            KPISubmission.month_or_quarter == period,
            KPISubmission.tenant_id == employee.tenant_id,
        )
    ).all()
    if not submissions:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "No draft KPI found for this period.")

    missing = [s for s in submissions if s.self_score is None]
    if missing:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "Please score every metric before submitting.")

    # A Dept Admin's own KPI (e.g. a custom metric Super Admin assigned them) skips
    # department-level review — they'd otherwise be reviewing themselves — and goes
    # straight to the Super Admin for final approval.
    next_status = (
        KPIStatus.PENDING_FINAL_APPROVAL if employee.role == UserRole.DEPT_ADMIN else KPIStatus.PENDING_DEPT_APPROVAL
    )

    stage = "final approval" if next_status == KPIStatus.PENDING_FINAL_APPROVAL else "department approval"
    for submission in submissions:
        submission.status = next_status
        submission.submitted_at = _now()
        audit_service.log(
            db, employee, "kpi_submitted", "kpi_submission", submission.id, _entity_label(submission),
            f"Submitted (self score {fmt_score(submission.self_score)}) for {stage}.",
            department_id=submission.department_id,
        )
    db.commit()


def _entity_label(submission: KPISubmission) -> str:
    return (
        f"{submission.kpi_template.metric_name} — {submission.employee.name} "
        f"({submission.month_or_quarter} {submission.year})"
    )


def dept_save_score(
    db: Session, reviewer: User, submission: KPISubmission, dept_score: float, remarks: str | None
) -> None:
    """Dept Admin edits the score without forwarding yet."""
    if submission.status != KPIStatus.PENDING_DEPT_APPROVAL:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "Submission is not awaiting department review.")
    old_score = submission.dept_score
    submission.dept_score = dept_score
    if remarks is not None:
        submission.remarks = remarks
    submission.dept_reviewed_by_id = reviewer.id
    submission.dept_reviewed_at = _now()
    audit_service.log(
        db, reviewer, "kpi_dept_score_saved", "kpi_submission", submission.id, _entity_label(submission),
        f"Dept score saved: {fmt_score(old_score)} → {fmt_score(dept_score)}",
        department_id=submission.department_id,
    )
    db.commit()


def dept_approve(
    db: Session,
    reviewer: User,
    submission: KPISubmission,
    dept_score: float | None,
    remarks: str | None,
) -> None:
    """Dept Admin approves and forwards to the Super Admin for final review."""
    if submission.status != KPIStatus.PENDING_DEPT_APPROVAL:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "Submission is not awaiting department review.")
    old_score = submission.dept_score
    submission.dept_score = dept_score if dept_score is not None else (
        submission.dept_score if submission.dept_score is not None else submission.self_score
    )
    if remarks is not None:
        submission.remarks = remarks
    submission.status = KPIStatus.PENDING_FINAL_APPROVAL
    submission.dept_reviewed_by_id = reviewer.id
    submission.dept_reviewed_at = _now()
    audit_service.log(
        db, reviewer, "kpi_dept_approved", "kpi_submission", submission.id, _entity_label(submission),
        f"Dept approved (score {fmt_score(old_score)} → {fmt_score(submission.dept_score)}), "
        "forwarded for final approval.",
        department_id=submission.department_id,
    )
    db.commit()


def final_approve(
    db: Session,
    reviewer: User,
    submission: KPISubmission,
    final_score: float | None,
    remarks: str | None,
) -> None:
    """Super Admin gives final approval, optionally adjusting the score."""
    if submission.status != KPIStatus.PENDING_FINAL_APPROVAL:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "Submission is not awaiting final approval.")
    submission.final_score = final_score if final_score is not None else (
        submission.dept_score if submission.dept_score is not None else submission.self_score
    )
    if remarks is not None:
        submission.remarks = remarks
    submission.status = KPIStatus.APPROVED
    submission.final_reviewed_by_id = reviewer.id
    submission.final_reviewed_at = _now()
    audit_service.log(
        db, reviewer, "kpi_final_approved", "kpi_submission", submission.id, _entity_label(submission),
        f"Final approved at {fmt_score(submission.final_score)}.",
        department_id=submission.department_id,
    )
    db.commit()


def tenant_admin_override(
    db: Session, admin: User, submission: KPISubmission, final_score: float, remarks: str | None
) -> None:
    """Tenant Admin can override the score and force-approve at any workflow stage."""
    old_score = submission.final_score if submission.final_score is not None else (
        submission.dept_score if submission.dept_score is not None else submission.self_score
    )
    submission.final_score = final_score
    if remarks is not None:
        submission.remarks = remarks
    submission.status = KPIStatus.APPROVED
    submission.final_reviewed_by_id = admin.id
    submission.final_reviewed_at = _now()
    audit_service.log(
        db, admin, "kpi_overridden", "kpi_submission", submission.id, _entity_label(submission),
        f"Score overridden: {fmt_score(old_score)} → {fmt_score(final_score)}.",
        department_id=submission.department_id,
    )
    db.commit()


def reject_submission(
    db: Session, reviewer: User, submission: KPISubmission, remarks: str | None
) -> None:
    if submission.status not in (KPIStatus.PENDING_DEPT_APPROVAL, KPIStatus.PENDING_FINAL_APPROVAL):
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "Submission is not in a reviewable state.")
    submission.status = KPIStatus.REJECTED
    if remarks is not None:
        submission.remarks = remarks
    audit_service.log(
        db, reviewer, "kpi_rejected", "kpi_submission", submission.id, _entity_label(submission),
        "Submission rejected.",
        department_id=submission.department_id,
    )
    db.commit()
