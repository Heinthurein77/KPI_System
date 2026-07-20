from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session

from app.core.deps import get_current_user, require_dept_admin, require_super_admin
from app.database import get_db
from app.models.kpi_submission import KPISubmission
from app.models.user import User, UserRole
from app.schemas.kpi import (
    DeptApproveRequest,
    DeptSaveRequest,
    FinalApproveRequest,
    KPISubmissionOut,
    OverrideRequest,
    RejectRequest,
    SaveScoresRequest,
    SubmitRequest,
)
from app.services import kpi_service

router = APIRouter(prefix="/api/kpi", tags=["kpi"])


def _own_submissions_for_period(db: Session, user: User, year: int, period: str) -> list[KPISubmissionOut]:
    submissions = db.scalars(
        kpi_service.own_submissions_query(user).where(
            KPISubmission.year == year, KPISubmission.month_or_quarter == period
        )
    ).unique().all()
    submissions.sort(key=lambda s: s.kpi_template.metric_name)
    return [KPISubmissionOut.model_validate(s) for s in submissions]


@router.post("/employee/save")
def employee_save_scores(
    payload: SaveScoresRequest,
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
):
    # Regular Employees self-assess their recurring metrics; a Dept Admin can also
    # land here to self-score a custom KPI Super Admin assigned them directly.
    if user.role == UserRole.SUPER_ADMIN:
        raise HTTPException(403, "Super Admins do not self-assess.")

    if payload.scores:
        kpi_service.save_self_scores(db, user, {int(k): v for k, v in payload.scores.items()})

    return _own_submissions_for_period(db, user, payload.year, payload.period)


@router.post("/employee/submit")
def employee_submit(
    payload: SubmitRequest,
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
):
    if user.role == UserRole.SUPER_ADMIN:
        raise HTTPException(403, "Super Admins do not self-assess.")

    if payload.scores:
        kpi_service.save_self_scores(db, user, {int(k): v for k, v in payload.scores.items()})

    kpi_service.submit_for_dept_approval(db, user, payload.year, payload.period)
    return _own_submissions_for_period(db, user, payload.year, payload.period)


@router.post("/{submission_id}/dept-save", response_model=KPISubmissionOut)
def dept_save(
    submission_id: int,
    payload: DeptSaveRequest,
    db: Session = Depends(get_db),
    user: User = Depends(require_dept_admin),
):
    submission = kpi_service.get_submission_scoped(db, user, submission_id)
    kpi_service.dept_save_score(db, user, submission, payload.dept_score, payload.remarks)
    return submission


@router.post("/{submission_id}/dept-approve", response_model=KPISubmissionOut)
def dept_approve(
    submission_id: int,
    payload: DeptApproveRequest,
    db: Session = Depends(get_db),
    user: User = Depends(require_dept_admin),
):
    submission = kpi_service.get_submission_scoped(db, user, submission_id)
    kpi_service.dept_approve(db, user, submission, payload.dept_score, payload.remarks)
    return submission


@router.post("/{submission_id}/final-approve", response_model=KPISubmissionOut)
def final_approve(
    submission_id: int,
    payload: FinalApproveRequest,
    db: Session = Depends(get_db),
    user: User = Depends(require_super_admin),
):
    submission = kpi_service.get_submission_scoped(db, user, submission_id)
    kpi_service.final_approve(db, user, submission, payload.final_score, payload.remarks)
    return submission


@router.post("/{submission_id}/override", response_model=KPISubmissionOut)
def super_admin_override(
    submission_id: int,
    payload: OverrideRequest,
    db: Session = Depends(get_db),
    user: User = Depends(require_super_admin),
):
    submission = kpi_service.get_submission_scoped(db, user, submission_id)
    kpi_service.super_admin_override(db, user, submission, payload.final_score, payload.remarks)
    return submission


@router.post("/{submission_id}/reject", response_model=KPISubmissionOut)
def reject(
    submission_id: int,
    payload: RejectRequest,
    db: Session = Depends(get_db),
    user: User = Depends(require_dept_admin),
):
    submission = kpi_service.get_submission_scoped(db, user, submission_id)
    kpi_service.reject_submission(db, submission, payload.remarks)
    return submission
