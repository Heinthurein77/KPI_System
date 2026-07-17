from fastapi import APIRouter, Depends, Form, HTTPException, Request
from fastapi.responses import RedirectResponse
from sqlalchemy.orm import Session

from app.core.deps import get_current_user, require_dept_admin, require_super_admin
from app.database import get_db
from app.models.user import User, UserRole
from app.services import kpi_service

router = APIRouter(prefix="/kpi", tags=["kpi"])


def _back_to_dashboard(request: Request, year: int, period: str) -> RedirectResponse:
    url = f"/dashboard?year={year}&period={period}"
    return RedirectResponse(url=url, status_code=303)


async def _parse_self_scores(request: Request) -> dict[int, float]:
    form = await request.form()
    scores: dict[int, float] = {}
    for key, value in form.multi_items():
        if key.startswith("self_score_") and value not in (None, ""):
            submission_id = int(key.removeprefix("self_score_"))
            scores[submission_id] = float(value)
    return scores


@router.post("/employee/save")
async def employee_save_scores(
    request: Request,
    year: int = Form(...),
    period: str = Form(...),
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
):
    # Regular Employees self-assess their recurring metrics; a Dept Admin can also
    # land here to self-score a custom KPI Super Admin assigned them directly.
    if user.role == UserRole.SUPER_ADMIN:
        raise HTTPException(403, "Super Admins do not self-assess.")

    scores = await _parse_self_scores(request)
    if scores:
        kpi_service.save_self_scores(db, user, scores)

    return _back_to_dashboard(request, year, period)


@router.post("/employee/submit")
async def employee_submit(
    request: Request,
    year: int = Form(...),
    period: str = Form(...),
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
):
    if user.role == UserRole.SUPER_ADMIN:
        raise HTTPException(403, "Super Admins do not self-assess.")

    scores = await _parse_self_scores(request)
    if scores:
        kpi_service.save_self_scores(db, user, scores)

    kpi_service.submit_for_dept_approval(db, user, year, period)
    return _back_to_dashboard(request, year, period)


@router.post("/{submission_id}/dept-save")
def dept_save(
    request: Request,
    submission_id: int,
    year: int = Form(...),
    period: str = Form(...),
    dept_score: float = Form(...),
    remarks: str | None = Form(None),
    db: Session = Depends(get_db),
    user: User = Depends(require_dept_admin),
):
    submission = kpi_service.get_submission_scoped(db, user, submission_id)
    kpi_service.dept_save_score(db, user, submission, dept_score, remarks)
    return _back_to_dashboard(request, year, period)


@router.post("/{submission_id}/dept-approve")
def dept_approve(
    request: Request,
    submission_id: int,
    year: int = Form(...),
    period: str = Form(...),
    dept_score: float | None = Form(None),
    remarks: str | None = Form(None),
    db: Session = Depends(get_db),
    user: User = Depends(require_dept_admin),
):
    submission = kpi_service.get_submission_scoped(db, user, submission_id)
    kpi_service.dept_approve(db, user, submission, dept_score, remarks)
    return _back_to_dashboard(request, year, period)


@router.post("/{submission_id}/final-approve")
def final_approve(
    request: Request,
    submission_id: int,
    year: int = Form(...),
    period: str = Form(...),
    final_score: float | None = Form(None),
    remarks: str | None = Form(None),
    db: Session = Depends(get_db),
    user: User = Depends(require_super_admin),
):
    submission = kpi_service.get_submission_scoped(db, user, submission_id)
    kpi_service.final_approve(db, user, submission, final_score, remarks)
    return _back_to_dashboard(request, year, period)


@router.post("/{submission_id}/override")
def super_admin_override(
    request: Request,
    submission_id: int,
    year: int = Form(...),
    period: str = Form(...),
    final_score: float = Form(...),
    remarks: str | None = Form(None),
    db: Session = Depends(get_db),
    user: User = Depends(require_super_admin),
):
    submission = kpi_service.get_submission_scoped(db, user, submission_id)
    kpi_service.super_admin_override(db, user, submission, final_score, remarks)
    return _back_to_dashboard(request, year, period)


@router.post("/{submission_id}/reject")
def reject(
    request: Request,
    submission_id: int,
    year: int = Form(...),
    period: str = Form(...),
    remarks: str | None = Form(None),
    db: Session = Depends(get_db),
    user: User = Depends(require_dept_admin),
):
    submission = kpi_service.get_submission_scoped(db, user, submission_id)
    kpi_service.reject_submission(db, submission, remarks)
    return _back_to_dashboard(request, year, period)
