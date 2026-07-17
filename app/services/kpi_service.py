from datetime import datetime, timezone

from fastapi import HTTPException, status
from sqlalchemy import select
from sqlalchemy.orm import Session, joinedload

from app.models.kpi_submission import KPIStatus, KPISubmission
from app.models.kpi_template import KPITemplate
from app.models.user import User, UserRole


def _now() -> datetime:
    return datetime.now(timezone.utc)


def visible_submissions_query(user: User):
    """Scope submissions to what this role is allowed to see (data isolation)."""
    query = select(KPISubmission).options(
        joinedload(KPISubmission.employee),
        joinedload(KPISubmission.kpi_template),
        joinedload(KPISubmission.department),
    )

    if user.role == UserRole.SUPER_ADMIN:
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
    ).where(KPISubmission.employee_id == user.id)


def get_submission_scoped(db: Session, user: User, submission_id: int) -> KPISubmission:
    submission = db.get(
        KPISubmission,
        submission_id,
        options=[
            joinedload(KPISubmission.employee),
            joinedload(KPISubmission.kpi_template),
            joinedload(KPISubmission.department),
        ],
    )
    if submission is None:
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
        & (KPITemplate.locked_year == year)
        & (KPITemplate.locked_period == period)
    )
    if employee.role == UserRole.EMPLOYEE:
        condition = custom_condition | (
            KPITemplate.employee_id.is_(None)
            & ((KPITemplate.department_id == employee.department_id) | (KPITemplate.department_id.is_(None)))
        )
    else:
        condition = custom_condition

    templates = db.scalars(select(KPITemplate).where(condition)).all()

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
        )
    ).all()
    existing_template_ids = {s.kpi_template_id for s in existing}

    created: list[KPISubmission] = []
    for template in templates:
        if template.id in existing_template_ids:
            continue
        submission = KPISubmission(
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
    employee: User,
    metric_name: str,
    target: float,
    weight: float,
    year: int,
    period: str,
) -> KPISubmission:
    """Dept Admin creates a one-off custom metric for a single employee, locked to one period.

    Materializes the KPISubmission immediately so it's visible right away, rather
    than waiting for the employee to load their dashboard for that period.
    """
    existing_template = db.scalar(
        select(KPITemplate).where(
            KPITemplate.employee_id == employee.id,
            KPITemplate.metric_name == metric_name,
            KPITemplate.locked_year == year,
            KPITemplate.locked_period == period,
        )
    )
    if existing_template is not None:
        raise HTTPException(
            status.HTTP_400_BAD_REQUEST,
            f'A custom KPI named "{metric_name}" already exists for {employee.name} in {period} {year}.',
        )

    template = KPITemplate(
        metric_name=metric_name,
        target=target,
        weight=weight,
        department_id=employee.department_id,
        employee_id=employee.id,
        locked_year=year,
        locked_period=period,
    )
    db.add(template)
    db.flush()

    submission = KPISubmission(
        employee_id=employee.id,
        department_id=employee.department_id,
        kpi_template_id=template.id,
        year=year,
        month_or_quarter=period,
        status=KPIStatus.DRAFT,
    )
    db.add(submission)
    db.commit()
    return submission


def save_self_scores(db: Session, employee: User, scores: dict[int, float]) -> None:
    submissions = db.scalars(
        select(KPISubmission).where(
            KPISubmission.id.in_(scores.keys()),
            KPISubmission.employee_id == employee.id,
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

    for submission in submissions:
        submission.status = next_status
        submission.submitted_at = _now()
    db.commit()


def dept_save_score(
    db: Session, reviewer: User, submission: KPISubmission, dept_score: float, remarks: str | None
) -> None:
    """Dept Admin edits the score without forwarding yet."""
    if submission.status != KPIStatus.PENDING_DEPT_APPROVAL:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "Submission is not awaiting department review.")
    submission.dept_score = dept_score
    if remarks is not None:
        submission.remarks = remarks
    submission.dept_reviewed_by_id = reviewer.id
    submission.dept_reviewed_at = _now()
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
    submission.dept_score = dept_score if dept_score is not None else (
        submission.dept_score if submission.dept_score is not None else submission.self_score
    )
    if remarks is not None:
        submission.remarks = remarks
    submission.status = KPIStatus.PENDING_FINAL_APPROVAL
    submission.dept_reviewed_by_id = reviewer.id
    submission.dept_reviewed_at = _now()
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
    db.commit()


def super_admin_override(
    db: Session, admin: User, submission: KPISubmission, final_score: float, remarks: str | None
) -> None:
    """Super Admin can override the score and force-approve at any workflow stage."""
    submission.final_score = final_score
    if remarks is not None:
        submission.remarks = remarks
    submission.status = KPIStatus.APPROVED
    submission.final_reviewed_by_id = admin.id
    submission.final_reviewed_at = _now()
    db.commit()


def reject_submission(db: Session, submission: KPISubmission, remarks: str | None) -> None:
    if submission.status not in (KPIStatus.PENDING_DEPT_APPROVAL, KPIStatus.PENDING_FINAL_APPROVAL):
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "Submission is not in a reviewable state.")
    submission.status = KPIStatus.REJECTED
    if remarks is not None:
        submission.remarks = remarks
    db.commit()
