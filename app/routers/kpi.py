from fastapi import APIRouter, Depends, HTTPException, Request
from sqlalchemy.orm import Session
from app.core.deps import get_current_user, require_dept_admin, require_tenant_admin
from app.core.rate_limit import BULK_APPROVE_RATE_LIMIT, _authenticated_user_key, limiter
from app.database import get_db
from app.models.kpi_submission import KPISubmission
from app.models.user import User, UserRole
from app.schemas.kpi import (
    BulkApproveRequest,
    DeptApproveRequest,
    DeptSaveRequest,
    FinalApproveRequest,
    KPISubmissionOut,
    OverrideRequest,
    RejectRequest,
    SaveScoresRequest,
    SubmitRequest,
    WithdrawRequest,
)
from app.services import kpi_service

router = APIRouter(prefix="/api/kpi", tags=["kpi"])

# M-40: per-user write limit for the KPI endpoints (bulk-approve was
# the only limited one). 20/minute is generous for form saves — a
# human cannot save faster — but bounds a scripted flood.
KPI_WRITE_RATE_LIMIT = "20/minute"


def _own_submissions_for_period(db: Session, user: User, year: int, period: str) -> list[KPISubmissionOut]:
    submissions = db.scalars(
        kpi_service.own_submissions_query(user).where(
            KPISubmission.year == year, KPISubmission.month_or_quarter == period
        )
    ).unique().all()
    submissions.sort(key=lambda s: s.kpi_template.metric_name)
    return [KPISubmissionOut.model_validate(s) for s in submissions]


@router.post("/employee/save")
@limiter.limit(KPI_WRITE_RATE_LIMIT, key_func=_authenticated_user_key)
def employee_save_scores(
    request: Request,
    payload: SaveScoresRequest,
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
):
    # Allow-list, not deny-list: only Employees and Dept Admins self-assess. A
    # Tenant Admin never does, and neither does a platform-level account
    # (tenant_id IS NULL) that happens to authenticate here when no tenant is
    # resolved for the request.
    if user.role not in (UserRole.EMPLOYEE, UserRole.DEPT_ADMIN):
        raise HTTPException(403, "This account type does not self-assess.")

    kpi_service.validate_period_total_weight(db, user, payload.year, payload.period)
    if payload.scores or payload.comments:
        kpi_service.save_self_scores(
            db,
            user,
            {int(k): v for k, v in payload.scores.items()},
            {int(k): v for k, v in payload.comments.items()},
            expected_year=payload.year,
            expected_period=payload.period,
        )

    return _own_submissions_for_period(db, user, payload.year, payload.period)


@router.post("/employee/submit")
@limiter.limit(KPI_WRITE_RATE_LIMIT, key_func=_authenticated_user_key)
def employee_submit(
    request: Request,
    payload: SubmitRequest,
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
):
    if user.role not in (UserRole.EMPLOYEE, UserRole.DEPT_ADMIN):
        raise HTTPException(403, "This account type does not self-assess.")

    kpi_service.validate_period_total_weight(db, user, payload.year, payload.period)
    if payload.scores or payload.comments:
        kpi_service.save_self_scores(
            db,
            user,
            {int(k): v for k, v in payload.scores.items()},
            {int(k): v for k, v in payload.comments.items()},
            expected_year=payload.year,
            expected_period=payload.period,
        )

    # S-m3: return the partial-submit result — a grace-skipped submit
    # must not look identical to a full one. The submission list stays
    # under "submissions" (same rows as before) so existing clients keep
    # working; "submitted"/"skipped" carry the service's id lists.
    result = kpi_service.submit_for_dept_approval(db, user, payload.year, payload.period)
    return {
        "submissions": _own_submissions_for_period(db, user, payload.year, payload.period),
        "submitted": result["submitted"],
        "skipped": result["skipped"],
    }


@router.post("/employee/withdraw")
@limiter.limit(KPI_WRITE_RATE_LIMIT, key_func=_authenticated_user_key)
def employee_withdraw(
    request: Request,
    payload: WithdrawRequest,
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
):
    """Withdraw the employee's own DRAFT rows for a period (S-M2).

    The user-decided exit for a DRAFT stuck after its grace window
    closed: the employee withdraws it themselves — no score invented,
    nothing force-approved. Only Employees and Dept Admins self-assess,
    and only their OWN drafts are ever touched (the service filters by
    employee_id). Withdrawal bypasses the grace window (it is not an
    edit); a future month is still refused.
    """
    if user.role not in (UserRole.EMPLOYEE, UserRole.DEPT_ADMIN):
        raise HTTPException(403, "This account type does not self-assess.")

    result = kpi_service.withdraw_draft(db, user, payload.year, payload.period)
    return {
        "submissions": _own_submissions_for_period(db, user, payload.year, payload.period),
        "withdrawn": result["withdrawn"],
    }


@router.post("/bulk-approve")
@limiter.limit(BULK_APPROVE_RATE_LIMIT, key_func=_authenticated_user_key)
def bulk_approve(
    request: Request,
    payload: BulkApproveRequest,
    db: Session = Depends(get_db),
    user: User = Depends(require_dept_admin),
):
    return kpi_service.bulk_approve(
        db, user, payload.year, payload.period,
        [(item.submission_id, item.score, item.remarks) for item in payload.items],
    )


@router.post("/{submission_id}/dept-save", response_model=KPISubmissionOut)
@limiter.limit(KPI_WRITE_RATE_LIMIT, key_func=_authenticated_user_key)
def dept_save(
    request: Request,
    submission_id: int,
    payload: DeptSaveRequest,
    db: Session = Depends(get_db),
    user: User = Depends(require_dept_admin),
):
    submission = kpi_service.get_submission_scoped(db, user, submission_id)
    kpi_service.dept_save_score(
        db, user, submission, payload.dept_score, payload.remarks,
        expected_year=payload.year, expected_period=payload.period,
    )
    return submission


@router.post("/{submission_id}/dept-approve", response_model=KPISubmissionOut)
@limiter.limit(KPI_WRITE_RATE_LIMIT, key_func=_authenticated_user_key)
def dept_approve(
    request: Request,
    submission_id: int,
    payload: DeptApproveRequest,
    db: Session = Depends(get_db),
    user: User = Depends(require_dept_admin),
):
    submission = kpi_service.get_submission_scoped(db, user, submission_id)
    kpi_service.dept_approve(
        db, user, submission, payload.dept_score, payload.remarks,
        expected_year=payload.year, expected_period=payload.period,
    )
    return submission


@router.post("/{submission_id}/final-approve", response_model=KPISubmissionOut)
@limiter.limit(KPI_WRITE_RATE_LIMIT, key_func=_authenticated_user_key)
def final_approve(
    request: Request,
    submission_id: int,
    payload: FinalApproveRequest,
    db: Session = Depends(get_db),
    user: User = Depends(require_tenant_admin),
):
    submission = kpi_service.get_submission_scoped(db, user, submission_id)
    kpi_service.final_approve(
        db, user, submission, payload.final_score, payload.remarks,
        expected_year=payload.year, expected_period=payload.period,
    )
    return submission


@router.post("/{submission_id}/override", response_model=KPISubmissionOut)
@limiter.limit(KPI_WRITE_RATE_LIMIT, key_func=_authenticated_user_key)
def tenant_admin_override(
    request: Request,
    submission_id: int,
    payload: OverrideRequest,
    db: Session = Depends(get_db),
    user: User = Depends(require_tenant_admin),
):
    submission = kpi_service.get_submission_scoped(db, user, submission_id)
    kpi_service.tenant_admin_override(
        db, user, submission, payload.final_score, payload.remarks,
        expected_year=payload.year, expected_period=payload.period,
    )
    return submission


@router.post("/{submission_id}/reject", response_model=KPISubmissionOut)
@limiter.limit(KPI_WRITE_RATE_LIMIT, key_func=_authenticated_user_key)
def reject(
    request: Request,
    submission_id: int,
    payload: RejectRequest,
    db: Session = Depends(get_db),
    user: User = Depends(require_dept_admin),
):
    submission = kpi_service.get_submission_scoped(db, user, submission_id)
    kpi_service.reject_submission(
        db, user, submission, payload.remarks,
        expected_year=payload.year, expected_period=payload.period,
    )
    return submission
