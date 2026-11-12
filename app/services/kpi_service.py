from datetime import date, datetime, timezone
from decimal import Decimal

from fastapi import HTTPException, status
from sqlalchemy import select, update
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session, joinedload

from app.core.config import settings
from app.core.timezone import today as app_today
from app.models.kpi_submission import KPIStatus, KPISubmission
from app.models.kpi_template import KPITemplate
from app.models.user import User, UserRole
from app.services import audit_service
from app.services import email_service
from app.services import notification_service
from app.services.audit_service import fmt_score
from app.services.recurring_service import is_template_active_for_period
from app.services.submission_grace import (
    MONTH_NAMES as GRACE_MONTH_NAMES,
)
from app.services.submission_grace import (
    assert_admin_creation_window_open,
    assert_employee_window_open,
    assert_not_future_month,
    is_within_grace,
    recurring_rollover_open,
)


WEIGHT_TOTAL_MESSAGE = (
    "Weight စုစုပေါင်း 100% ကျော်လွန်နေပါသည်။ "
    "Total KPI weights must not exceed 100%. Please reduce weights before saving."
)


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _claim_submission(
    db: Session, submission: KPISubmission, expected_statuses: list[KPIStatus], conflict_message: str
) -> None:
    """Atomically claim a submission for a state transition (C2).

    Issues a conditional UPDATE — `WHERE id=? AND status IN (...)` — and
    treats rowcount 0 as "already handled by another caller" (400). The
    UPDATE takes the row lock, which the transaction holds until commit,
    so a second concurrent caller blocks, then sees the status has moved
    and gets rowcount 0. This works on both SQLite and Postgres (no
    SELECT ... FOR UPDATE, which SQLite does not support). The UPDATE is
    a no-op value-wise (it writes back the current status); the row lock
    and the rowcount are the claim. Callers must call this BEFORE
    mutating the submission and must not commit between claim and mutate.
    """
    result = db.execute(
        update(KPISubmission)
        .where(
            KPISubmission.id == submission.id,
            KPISubmission.tenant_id == submission.tenant_id,
            KPISubmission.status.in_(expected_statuses),
        )
        .values(status=submission.status)
    )
    if result.rowcount == 0:
        db.rollback()
        raise HTTPException(status.HTTP_400_BAD_REQUEST, conflict_message)


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


def period_submission_snapshots(
    db: Session, employee: User, year: int, period: str
) -> list[KPISubmission]:
    """The period's submissions whose frozen weights the total-weight guard sums.

    Single source of truth for "what fills this period": the guard below and
    the period-weight endpoint both read through here, so the form's budget
    can never drift from what the backend enforces. A submission counts
    with its frozen ``weight_snapshot`` (``effective_weight``), never with
    the live template weight — a template edit after materialisation does
    not change this period.
    """
    return (
        db.scalars(
            select(KPISubmission)
            .options(joinedload(KPISubmission.kpi_template))
            .where(
                KPISubmission.employee_id == employee.id,
                KPISubmission.year == year,
                KPISubmission.month_or_quarter == period,
                KPISubmission.tenant_id == employee.tenant_id,
            )
        )
        .unique()
        .all()
    )


def period_snapshot_total(db: Session, employee: User, year: int, period: str) -> Decimal:
    """Sum of the period's frozen submission weights (the guard's number)."""
    return sum(
        (Decimal(str(s.effective_weight)) for s in period_submission_snapshots(db, employee, year, period)),
        Decimal("0"),
    )


def validate_period_total_weight(db: Session, employee: User, year: int, period: str) -> None:
    """Block save/submit if the period's KPI weights total more than 100.

    Individual metrics may carry less than 100% weight — the only hard rule
    is that the combined total must not exceed 100.  Under-allocation is
    intentional and valid (e.g. a new KPI added mid-year starts contributing
    from its first full period).

    This guard only reads existing submissions and their templates; it does
    not modify any data, scores, or workflow state.
    """
    submissions = period_submission_snapshots(db, employee, year, period)

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
    db: Session,
    employee: User,
    year: int,
    period: str,
    today: date | None = None,
    apply_rollover_gate: bool = True,
) -> list[KPITemplate]:
    """Every metric template that would materialise into a draft submission
    for this employee in this period. Read-only — creates nothing.

    Recurring department/company-wide metrics only apply to Employees — a Dept
    Admin's own KPI is always a custom one-off metric created specifically for
    them, locked to this exact year/period.

    Rollover-day gate (on by default): for the *current* month, NO template
    of any kind materialises before day SUBMISSION_GRACE_DAYS + 1 (the 8th
    with the default 7-day grace). Days 1..grace_days belong to closing the
    previous month, so the new month's whole KPI set — recurring, department
    and company alike — only starts from the 8th, automatically, every month.
    Past months — including the grace month — are unaffected. Callers pass
    apply_rollover_gate=False only to ask "what *would* apply once the
    rollover opens" (dashboard banner, gated-window handling).
    """
    today = today or app_today()
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
    applicable = [
        template for template in templates
        if not template.is_recurring
        or template.employee_id is None
        or is_template_active_for_period(template, year, period)
    ]

    if (
        apply_rollover_gate
        and year == today.year
        and period in GRACE_MONTH_NAMES
        and period == GRACE_MONTH_NAMES[today.month - 1]
        and not recurring_rollover_open(today, settings.SUBMISSION_GRACE_DAYS)
    ):
        # Rollover window: the current month's KPI set has not started yet.
        return []

    return applicable


def ensure_period_submissions(
    db: Session, employee: User, year: int, period: str, today: date | None = None
) -> list[KPISubmission]:
    """Create draft submissions for every metric template applicable to the employee, idempotently.

    Recurring department/company-wide metrics only apply to Employees — a Dept
    Admin's own KPI is always a custom one-off metric created specifically for
    them, locked to this exact year/period.

    During the rollover window (current month, days 1..grace_days) nothing
    new materialises, but already-existing submissions stay visible — the
    month's set simply has not started yet, which is not an error.
    """
    templates = applicable_templates(db, employee, year, period, today=today)

    existing = db.scalars(
        select(KPISubmission).where(
            KPISubmission.employee_id == employee.id,
            KPISubmission.year == year,
            KPISubmission.month_or_quarter == period,
            KPISubmission.tenant_id == employee.tenant_id,
        )
    ).all()

    if not templates:
        if employee.role == UserRole.EMPLOYEE and not applicable_templates(
            db, employee, year, period, today=today, apply_rollover_gate=False
        ):
            raise HTTPException(
                status.HTTP_400_BAD_REQUEST,
                "No KPI metrics have been configured for your department yet.",
            )
        return existing

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

    # M2: concurrent first loads race to insert the same drafts — the
    # unique constraint (uq_submission_period_metric) makes the loser
    # fail at commit. Roll back and return the winner's committed rows
    # instead of surfacing a 500.
    try:
        db.commit()
    except IntegrityError:
        db.rollback()
        return db.scalars(
            select(KPISubmission).where(
                KPISubmission.employee_id == employee.id,
                KPISubmission.year == year,
                KPISubmission.month_or_quarter == period,
                KPISubmission.tenant_id == employee.tenant_id,
            )
        ).all()
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
    today: date | None = None,
) -> KPISubmission:
    """Dept Admin creates a one-off (or recurring) custom metric for a single employee.

    When is_recurring=True the template is flagged for monthly carry-over by
    the recurrence service — the submission for the requested period is still
    materialised immediately so it's visible right away.

    Materializes the KPISubmission immediately so it's visible right away, rather
    than waiting for the employee to load their dashboard for that period.

    Admin creation window (M6): during days 1..SUBMISSION_GRACE_DAYS of a
    month, admins may add the PREVIOUS month's KPI, but not the current
    month's (its set materialises on day 8) nor months past their grace
    window. Future months stay allowed (admin pre-creation). Endpoints
    use the default (app timezone today); tests pass today explicitly.
    """
    today = today or app_today()
    assert_admin_creation_window_open(year, period, today, settings.SUBMISSION_GRACE_DAYS)
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
    db.flush()
    # Period-total guard at creation time: a single 150%-weight metric (or
    # a create that pushes the period total over 100%) is refused here,
    # instead of only surfacing later at save/submit. Callers roll back
    # on error (rebalance and bulk import wrap the whole transaction;
    # the single-create endpoint's session is discarded uncommitted).
    validate_period_total_weight(db, employee, year, period)
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
    today: date | None = None,
    expected_year: int | None = None,
    expected_period: str | None = None,
) -> None:
    comments = comments or {}
    today = today or app_today()
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
        # The caller claims a period (employee save/submit endpoints); every
        # touched submission must actually belong to it — a mismatched id
        # must not silently edit another month's row.
        if expected_year is not None and expected_period is not None:
            if (submission.year, submission.month_or_quarter) != (expected_year, expected_period):
                raise HTTPException(
                    status.HTTP_400_BAD_REQUEST,
                    f"Submission {submission.id} belongs to {submission.month_or_quarter} "
                    f"{submission.year}, not the claimed {expected_period} {expected_year}.",
                )
        # Future months are never employee-editable (admin pre-creation
        # territory), regardless of DRAFT/REJECTED status.
        assert_not_future_month(submission.year, submission.month_or_quarter, today)
        # Grace rule: a DRAFT may only be edited while its month's window is
        # open (month-end + SUBMISSION_GRACE_DAYS). A REJECTED submission may
        # still be corrected after grace — the delay there was the reviewer's.
        # (Unparseable period labels are left alone by the guard.)
        if submission.status == KPIStatus.DRAFT:
            assert_employee_window_open(
                submission.year,
                submission.month_or_quarter,
                today,
                settings.SUBMISSION_GRACE_DAYS,
            )
        if submission.id in scores:
            _validate_score_within_target(submission, scores[submission.id], "Self score")
            submission.self_score = scores[submission.id]
        if submission.id in comments:
            # Only keys actually sent by the client update the comment; an
            # empty string clears it. Keys not sent leave it untouched.
            submission.self_score_comment = comments[submission.id].strip() or None
    db.commit()


def submit_for_dept_approval(
    db: Session, employee: User, year: int, period: str, today: date | None = None
) -> dict:
    """Submit a period's DRAFT/REJECTED rows for approval.

    Returns {"submitted": [...ids], "skipped": [...ids]}. Skipped rows are
    DRAFT rows left untouched because their grace window closed — only the
    REJECTED subset is submitted in that case, so a late reviewer
    correction is never blocked by an unrelated stale draft.
    """
    today = today or app_today()
    # Defense in depth alongside save_self_scores: a future period can
    # never be submitted by an employee, even via a direct API call.
    assert_not_future_month(year, period, today)
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

    # Per-submission grace: the window is per period, so when it closed,
    # DRAFT rows stay DRAFT (untouched) and only the REJECTED subset is
    # submitted. Previously the whole submit was refused whenever any
    # DRAFT existed, so a rejected correction could never be resubmitted
    # after grace while a stale draft lingered. (Unparseable period
    # labels count as within grace, as in is_within_grace.)
    window_open = is_within_grace(year, period, today, settings.SUBMISSION_GRACE_DAYS)
    to_submit = [
        s for s in submittable
        if s.status == KPIStatus.REJECTED or window_open
    ]
    skipped = [s for s in submittable if s not in to_submit]
    if not to_submit:
        end = None
        from app.services.submission_grace import grace_end

        end = grace_end(year, period, settings.SUBMISSION_GRACE_DAYS)
        if end is not None:
            raise HTTPException(
                status.HTTP_400_BAD_REQUEST,
                f"The submission window for {period} {year} closed on {end.isoformat()}; "
                "only rejected items can still be resubmitted.",
            )
        raise HTTPException(
            status.HTTP_400_BAD_REQUEST,
            "No draft or rejected KPI found for this period.",
        )

    missing = [s for s in to_submit if s.self_score is None]
    if missing:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "Please score every metric before submitting.")

    # A Dept Admin cannot review their own KPI, so their own submissions skip
    # department review and go straight to the Tenant Admin for final approval.
    # Everyone else goes through department review first.
    skip_dept_review = employee.role == UserRole.DEPT_ADMIN
    next_status = KPIStatus.PENDING_FINAL_APPROVAL if skip_dept_review else KPIStatus.PENDING_DEPT_APPROVAL
    for submission in to_submit:
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
    return {"submitted": [s.id for s in to_submit], "skipped": [s.id for s in skipped]}


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
    # C2: claim the row first so two concurrent saves serialize on the
    # row lock instead of both writing + logging.
    _claim_submission(
        db, submission, [KPIStatus.PENDING_DEPT_APPROVAL],
        "Submission is not awaiting department review.",
    )
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
    commit: bool = True,
) -> list:
    """Dept Admin approves and forwards to the Super Admin for final review.

    With commit=False (bulk_approve), no commit and no email send happen;
    the created notification objects are returned so the caller can send
    their emails after its single commit. With commit=True (default),
    behavior is unchanged: commit + send, and [] is returned.
    """
    if submission.status != KPIStatus.PENDING_DEPT_APPROVAL:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "Submission is not awaiting department review.")
    if reviewer.id == submission.employee_id:
        raise HTTPException(status.HTTP_403_FORBIDDEN, "You cannot approve your own KPI.")
    # C2: atomic conditional-UPDATE claim — only the first concurrent
    # caller proceeds; the rest get 400 and produce no audit row or
    # notification.
    _claim_submission(
        db, submission, [KPIStatus.PENDING_DEPT_APPROVAL],
        "Submission is not awaiting department review.",
    )
    _validate_score_within_target(submission, dept_score, "Dept score")
    # Weight guard at approval time too: an over-100 period must not be
    # approvable into the record. (tenant_admin_override stays the
    # documented escape hatch for legacy over-100 periods.)
    validate_period_total_weight(
        db, submission.employee, submission.year, submission.month_or_quarter
    )
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
    if not commit:
        return emailed
    db.commit()
    email_service.send_for_notifications(db, emailed)
    return []


def final_approve(
    db: Session,
    reviewer: User,
    submission: KPISubmission,
    final_score: float | None,
    remarks: str | None,
    commit: bool = True,
) -> list:
    """Super Admin gives final approval, optionally adjusting the score.

    commit=False behaves like dept_approve's: no commit, no email send,
    notification objects returned for the caller's single commit + send.
    """
    if submission.status != KPIStatus.PENDING_FINAL_APPROVAL:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "Submission is not awaiting final approval.")
    # C2: atomic conditional-UPDATE claim (see dept_approve).
    _claim_submission(
        db, submission, [KPIStatus.PENDING_FINAL_APPROVAL],
        "Submission is not awaiting final approval.",
    )
    _validate_score_within_target(submission, final_score, "Final score")
    # Weight guard at approval time too (see dept_approve).
    validate_period_total_weight(
        db, submission.employee, submission.year, submission.month_or_quarter
    )
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
    if not commit:
        return emailed
    db.commit()
    email_service.send_for_notifications(db, emailed)
    return []


def bulk_approve(
    db: Session,
    reviewer: User,
    year: int,
    period: str,
    items: list[tuple[int, float | None, str | None]],
) -> dict:
    """Approve many submissions in one request.

    Dept Admins bulk dept-approve (pending_dept_approval → pending_final_approval);
    Tenant Admins bulk final-approve (pending_final_approval → approved).
    Every item is validated first (scope, period, status, score); if any item
    fails, nothing is processed. Approved items go through the exact same
    single-item functions, so notifications, audit logs and emails behave
    identically to one-by-one approval. Each item carries its own remarks.

    Atomic: all items share one commit at the end. If any item fails
    mid-loop, the whole batch rolls back — previously each item
    committed on its own, so a failure halfway left half the batch
    approved with no way to tell which half.
    """
    seen: set[int] = set()
    planned: list[tuple[str, KPISubmission, float | None, str | None]] = []
    for submission_id, score, remarks in items:
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
            planned.append(("dept", submission, score, remarks))
        else:  # TENANT_ADMIN (require_dept_admin admits both roles)
            if submission.status != KPIStatus.PENDING_FINAL_APPROVAL:
                raise HTTPException(
                    status.HTTP_400_BAD_REQUEST,
                    f"Submission {submission_id} is not awaiting final approval.",
                )
            _validate_score_within_target(submission, score, "Final score")
            planned.append(("final", submission, score, remarks))

    approved_ids: list[int] = []
    emailed_all: list = []
    try:
        for kind, submission, score, remarks in planned:
            if kind == "dept":
                emailed_all += dept_approve(db, reviewer, submission, score, remarks, commit=False)
            else:
                emailed_all += final_approve(db, reviewer, submission, score, remarks, commit=False)
            approved_ids.append(submission.id)
    except Exception:
        db.rollback()
        raise
    db.commit()
    email_service.send_for_notifications(db, emailed_all)
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
    # C2: atomic conditional-UPDATE claim, so a reject racing an approve
    # has a deterministic outcome — only the first caller wins.
    _claim_submission(
        db, submission,
        [KPIStatus.PENDING_DEPT_APPROVAL, KPIStatus.PENDING_FINAL_APPROVAL],
        "Submission is not in a reviewable state.",
    )
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
