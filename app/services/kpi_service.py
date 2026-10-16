from datetime import datetime, timezone
from decimal import Decimal

from fastapi import HTTPException, status
from sqlalchemy import select
from sqlalchemy.orm import Session, joinedload

from app.models.kpi_submission import KPIStatus, KPISubmission
from app.models.kpi_template import KPITemplate
from app.models.user import User, UserRole
from app.services import audit_service
from app.services import email_service
from app.services import notification_service
from app.services.audit_service import fmt_score
from app.services.recurring_service import is_template_active_for_period


WEIGHT_TOTAL_MESSAGE = (
    "Weight စုစုပေါင်း 100% ကျော်လွန်နေပါသည်။ "
    "Total KPI weights must not exceed 100%. Please reduce weights before saving."
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
    """Block save/submit if the period's KPI weights total more than 100.

    Individual metrics may carry less than 100% weight — the only hard rule
    is that the combined total must not exceed 100.  Under-allocation is
    intentional and valid (e.g. a new KPI added mid-year starts contributing
    from its first full period).

    This guard only reads existing submissions and their templates; it does
    not modify any data, scores, or workflow state.
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
    if not submissions:
        return

    total_weight = sum(
        (Decimal(str(submission.effective_weight)) for submission in submissions),
        Decimal("0"),
    )
    if total_weight > Decimal("100"):
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
    # Nobody may review their own KPI submission — a Dept Admin's own KPI skips
    # department review entirely and goes straight to the Tenant Admin.
    if submission.employee_id == user.id:
        raise HTTPException(status.HTTP_403_FORBIDDEN, "You cannot review your own KPI submission.")

    return submission


def applicable_templates(
    db: Session, employee: User, year: int, period: str
) -> list[KPITemplate]:
    """Every metric template that would materialise into a draft submission
    for this employee in this period. Read-only — creates nothing.

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
    # The guard only applies to employee-scoped (custom) templates:
    # department/company-wide templates have no locked start month and are
    # always applicable.
    return [
        template for template in templates
        if not template.is_recurring
        or template.employee_id is None
        or is_template_active_for_period(template, year, period)
    ]


def ensure_period_submissions(
    db: Session, employee: User, year: int, period: str
) -> list[KPISubmission]:
    """Create draft submissions for every metric template applicable to the employee, idempotently.

    Recurring department/company-wide metrics only apply to Employees — a Dept
    Admin's own KPI is always a custom one-off metric created specifically for
    them, locked to this exact year/period.
    """
    templates = applicable_templates(db, employee, year, period)

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
            weight_snapshot=template.weight,
            target_snapshot=template.target,
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
    commit: bool = True,
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
        weight_snapshot=template.weight,
        target_snapshot=template.target,
    )
    db.add(submission)
    recurring_note = " Marked as monthly recurring." if is_recurring else ""
    audit_service.log(
        db, actor, "kpi_template_created", "kpi_template", template.id,
        f"{metric_name} — {employee.name}",
        f"Custom KPI created for {employee.name}: target={fmt_score(target)}, weight={fmt_score(weight)}.{recurring_note}",
        department_id=employee.department_id,
    )
    if commit:
        db.commit()
    return submission


def save_self_scores(
    db: Session,
    employee: User,
    scores: dict[int, float],
    comments: dict[int, str] | None = None,
) -> None:
    comments = comments or {}
    submissions = db.scalars(
        select(KPISubmission)
        .options(joinedload(KPISubmission.kpi_template))
        .where(
            KPISubmission.id.in_(set(scores.keys()) | set(comments.keys())),
            KPISubmission.employee_id == employee.id,
            KPISubmission.tenant_id == employee.tenant_id,
        )
    ).all()
    for submission in submissions:
        if submission.status not in (KPIStatus.DRAFT, KPIStatus.REJECTED):
            raise HTTPException(
                status.HTTP_400_BAD_REQUEST, "Cannot edit a submission that has already been submitted."
            )
        if submission.id in scores:
            _validate_score_within_target(submission, scores[submission.id], "Self score")
            submission.self_score = scores[submission.id]
        if submission.id in comments:
            # Only keys actually sent by the client update the comment; an
            # empty string clears it. Keys not sent leave it untouched.
            submission.self_score_comment = comments[submission.id].strip() or None
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

    # Only DRAFT and REJECTED submissions may (re)enter the approval queue.
    # Anything already in review or APPROVED must not regress — approved data
    # is immutable.
    submittable = [
        s for s in submissions
        if s.status in (KPIStatus.DRAFT, KPIStatus.REJECTED)
    ]
    if not submittable:
        raise HTTPException(
            status.HTTP_400_BAD_REQUEST,
            "No draft or rejected KPI found for this period.",
        )

    missing = [s for s in submittable if s.self_score is None]
    if missing:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "Please score every metric before submitting.")

    # A Dept Admin cannot review their own KPI, so their own submissions skip
    # department review and go straight to the Tenant Admin for final approval.
    # Everyone else goes through department review first.
    skip_dept_review = employee.role == UserRole.DEPT_ADMIN
    next_status = KPIStatus.PENDING_FINAL_APPROVAL if skip_dept_review else KPIStatus.PENDING_DEPT_APPROVAL
    for submission in submittable:
        submission.status = next_status
        submission.submitted_at = _now()
        audit_service.log(
            db, employee, "kpi_submitted", "kpi_submission", submission.id, _entity_label(submission),
            f"Submitted (self score {fmt_score(submission.self_score)}) for "
            f"{'final' if skip_dept_review else 'department'} approval.",
            department_id=submission.department_id,
        )
    if skip_dept_review:
        emailed = notification_service.notify_tenant_admins(
            db,
            tenant_id=employee.tenant_id,
            exclude_user_id=employee.id,
            type="kpi_submitted",
            title=f"{employee.name} submitted KPI for {period} {year}",
            message=f"{employee.name} submitted their {period} {year} KPI for final approval.",
            link=f"/dashboard?year={year}&period={period}",
        )
    elif employee.department_id is not None:
        emailed = notification_service.notify_dept_admins(
            db,
            tenant_id=employee.tenant_id,
            department_id=employee.department_id,
            exclude_user_id=employee.id,
            type="kpi_submitted",
            title=f"{employee.name} submitted KPI for {period} {year}",
            message=f"{employee.name} submitted their {period} {year} KPI for department review.",
            link=f"/dashboard?year={year}&period={period}",
        )
    else:
        emailed = []
    db.commit()
    email_service.send_for_notifications(db, emailed)


def _entity_label(submission: KPISubmission) -> str:
    return (
        f"{submission.kpi_template.metric_name} — {submission.employee.name} "
        f"({submission.month_or_quarter} {submission.year})"
    )


def _validate_score_within_target(submission: KPISubmission, score: float | None, label: str) -> None:
    """Reject scores above the metric target — inflated scores poison attainment
    math and crash dashboard views that refuse actual > target."""
    if score is not None and score > submission.effective_target:
        raise HTTPException(
            status.HTTP_400_BAD_REQUEST,
            f"{label} ({fmt_score(score)}) cannot exceed the target of "
            f"{fmt_score(submission.effective_target)}.",
        )


def dept_save_score(
    db: Session, reviewer: User, submission: KPISubmission, dept_score: float, remarks: str | None
) -> None:
    """Dept Admin edits the score without forwarding yet."""
    if submission.status != KPIStatus.PENDING_DEPT_APPROVAL:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "Submission is not awaiting department review.")
    _validate_score_within_target(submission, dept_score, "Dept score")
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
    if reviewer.id == submission.employee_id:
        raise HTTPException(status.HTTP_403_FORBIDDEN, "You cannot approve your own KPI.")
    _validate_score_within_target(submission, dept_score, "Dept score")
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
    dashboard_link = f"/dashboard?year={submission.year}&period={submission.month_or_quarter}"
    emailed = notification_service.notify_user(
        db,
        tenant_id=submission.tenant_id,
        user_id=submission.employee_id,
        type="kpi_dept_approved",
        title=f"Your {submission.month_or_quarter} {submission.year} KPI was approved",
        message=f"{reviewer.name} approved your KPI; it is now awaiting final approval.",
        link=dashboard_link,
    )
    emailed += notification_service.notify_tenant_admins(
        db,
        tenant_id=submission.tenant_id,
        exclude_user_id=reviewer.id,
        type="kpi_dept_approved",
        title=f"{submission.employee.name}'s KPI needs final approval",
        message=f"{submission.employee.name}'s {submission.month_or_quarter} {submission.year} KPI passed "
        "department review and awaits final approval.",
        link=dashboard_link,
    )
    db.commit()
    email_service.send_for_notifications(db, emailed)


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
    _validate_score_within_target(submission, final_score, "Final score")
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
    if reviewer.id != submission.employee_id:
        emailed = notification_service.notify_user(
            db,
            tenant_id=submission.tenant_id,
            user_id=submission.employee_id,
            type="kpi_final_approved",
            title=f"Your {submission.month_or_quarter} {submission.year} KPI was approved",
            message=f"{reviewer.name} gave final approval at {fmt_score(submission.final_score)}.",
            link=f"/dashboard?year={submission.year}&period={submission.month_or_quarter}",
        )
    else:
        emailed = []
    db.commit()
    email_service.send_for_notifications(db, emailed)


def bulk_approve(
    db: Session,
    reviewer: User,
    year: int,
    period: str,
    items: list[tuple[int, float | None]],
) -> dict:
    """Approve many submissions in one request.

    Dept Admins bulk dept-approve (pending_dept_approval → pending_final_approval);
    Tenant Admins bulk final-approve (pending_final_approval → approved).
    Every item is validated first (scope, period, status, score); if any item
    fails, nothing is processed. Approved items go through the exact same
    single-item functions, so notifications, audit logs and emails behave
    identically to one-by-one approval.
    """
    seen: set[int] = set()
    planned: list[tuple[str, KPISubmission, float | None]] = []
    for submission_id, score in items:
        if submission_id in seen:
            raise HTTPException(status.HTTP_400_BAD_REQUEST, "Duplicate submission in request.")
        seen.add(submission_id)
        submission = get_submission_scoped(db, reviewer, submission_id)
        if submission.year != year or submission.month_or_quarter != period:
            raise HTTPException(status.HTTP_400_BAD_REQUEST, "Submission is not in this period.")
        if reviewer.role == UserRole.DEPT_ADMIN:
            if submission.status != KPIStatus.PENDING_DEPT_APPROVAL:
                raise HTTPException(
                    status.HTTP_400_BAD_REQUEST,
                    f"Submission {submission_id} is not awaiting department review.",
                )
            _validate_score_within_target(submission, score, "Dept score")
            planned.append(("dept", submission, score))
        else:  # TENANT_ADMIN (require_dept_admin admits both roles)
            if submission.status != KPIStatus.PENDING_FINAL_APPROVAL:
                raise HTTPException(
                    status.HTTP_400_BAD_REQUEST,
                    f"Submission {submission_id} is not awaiting final approval.",
                )
            _validate_score_within_target(submission, score, "Final score")
            planned.append(("final", submission, score))

    approved_ids: list[int] = []
    for kind, submission, score in planned:
        if kind == "dept":
            dept_approve(db, reviewer, submission, score, None)
        else:
            final_approve(db, reviewer, submission, score, None)
        approved_ids.append(submission.id)
    return {"approved": approved_ids, "count": len(approved_ids)}


def tenant_admin_override(
    db: Session, admin: User, submission: KPISubmission, final_score: float, remarks: str | None
) -> None:
    """Tenant Admin can override the score and force-approve at any workflow stage."""
    _validate_score_within_target(submission, final_score, "Override score")
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
    if admin.id != submission.employee_id:
        emailed = notification_service.notify_user(
            db,
            tenant_id=submission.tenant_id,
            user_id=submission.employee_id,
            type="kpi_overridden",
            title=f"Your {submission.month_or_quarter} {submission.year} KPI was overridden",
            message=f"{admin.name} overrode your KPI score to {fmt_score(final_score)}.",
            link=f"/dashboard?year={submission.year}&period={submission.month_or_quarter}",
        )
    else:
        emailed = []
    db.commit()
    email_service.send_for_notifications(db, emailed)


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
    if reviewer.id != submission.employee_id:
        reason = f" Reason: {remarks}" if remarks else ""
        emailed = notification_service.notify_user(
            db,
            tenant_id=submission.tenant_id,
            user_id=submission.employee_id,
            type="kpi_rejected",
            title=f"Your {submission.month_or_quarter} {submission.year} KPI was rejected",
            message=f"{reviewer.name} rejected your KPI.{reason} You can correct your scores and resubmit.",
            link=f"/dashboard?year={submission.year}&period={submission.month_or_quarter}",
        )
    else:
        emailed = []
    db.commit()
    email_service.send_for_notifications(db, emailed)
