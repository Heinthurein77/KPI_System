"""Daily Task diary API (additive-only module; no KPI routes touched).

Visibility mirrors the KPI role model:
- employee: own entries only
- dept_admin: own department's entries (+ own diary, + supervisor feedback)
- tenant_admin: whole tenant
- super_admin: platform only, no tenant diary access (require_tenant_member)

Evidence upload: files are stored under uploads/daily_tasks/ (10 MB cap)
and served back through an authenticated, scope-checked endpoint.
"""

from __future__ import annotations

import os
from datetime import date as date_type
from pathlib import Path

from fastapi import APIRouter, Depends, File, HTTPException, Query, UploadFile
from fastapi.responses import FileResponse
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.core.deps import get_current_user, require_diary_author, require_tenant_member
from app.database import get_db
from app.models.daily_task import (
    TASK_STATUSES,
    DailyTaskEntry,
    DailyTaskMonthlyReview,
)
from app.models.department import Department
from app.models.user import User, UserRole
from app.schemas.daily_task import (
    DailyTaskEntryCreate,
    DailyTaskEntryOut,
    DailyTaskEntryUpdate,
    DailyTaskFeedbackUpdate,
    MonthlyReviewOut,
    MonthlyReviewUpdate,
    MonthlySummaryOut,
    MonthlyUserSummary,
    validate_status,
)
from app.services import notification_service
from app.services.daily_task_export import build_export

router = APIRouter(prefix="/api/daily-tasks", tags=["daily-tasks"])

MAX_EVIDENCE_BYTES = 10 * 1024 * 1024
UPLOAD_DIR = Path(os.getenv("DAILY_TASK_UPLOAD_DIR", "uploads/daily_tasks"))


def _visible_user_ids(db: Session, user: User) -> list[int]:
    """User IDs whose diary entries `user` may read."""
    if user.role == UserRole.TENANT_ADMIN:
        stmt = select(User.id).where(
            User.tenant_id == user.tenant_id, User.is_active == True  # noqa: E712
        )
    elif user.role == UserRole.DEPT_ADMIN:
        stmt = select(User.id).where(
            User.tenant_id == user.tenant_id,
            User.department_id == user.department_id,
            User.is_active == True,  # noqa: E712
        )
    else:
        return [user.id]
    return list(db.scalars(stmt))


def _can_give_feedback(user: User, entry: DailyTaskEntry) -> bool:
    if user.role == UserRole.TENANT_ADMIN:
        return True
    return (
        user.role == UserRole.DEPT_ADMIN
        and user.department_id is not None
        and user.department_id == entry.department_id
    )


def _serialize(db: Session, entry: DailyTaskEntry) -> DailyTaskEntryOut:
    author = db.get(User, entry.user_id)
    dept_name = None
    if entry.department_id:
        dept = db.get(Department, entry.department_id)
        dept_name = dept.name if dept else None
    return DailyTaskEntryOut(
        id=entry.id,
        user_id=entry.user_id,
        author_name=author.name if author else "",
        department_name=dept_name,
        position=author.position if author else None,
        task_date=entry.task_date,
        plan_linkage=entry.plan_linkage,
        work_done=entry.work_done,
        status=entry.status,
        figure_output=entry.figure_output,
        evidence_link=entry.evidence_link,
        evidence_file=entry.evidence_file,
        has_evidence_file=bool(entry.evidence_file),
        difficulties=entry.difficulties,
        support_needed=entry.support_needed,
        self_reflection=entry.self_reflection,
        tomorrow_plan_1=entry.tomorrow_plan_1,
        tomorrow_plan_2=entry.tomorrow_plan_2,
        tomorrow_plan_3=entry.tomorrow_plan_3,
        supervisor_feedback=entry.supervisor_feedback,
    )


def _month_bounds(year: int, month: int) -> tuple[date_type, date_type]:
    if not 1 <= month <= 12:
        raise HTTPException(400, "month must be 1-12")
    start = date_type(year, month, 1)
    end = date_type(year + 1, 1, 1) if month == 12 else date_type(year, month + 1, 1)
    return start, end


@router.get("", response_model=list[DailyTaskEntryOut])
def list_entries(
    year: int = Query(...),
    month: int = Query(...),
    user_id: int | None = Query(None),
    db: Session = Depends(get_db),
    user: User = Depends(require_tenant_member),
):
    start, end = _month_bounds(year, month)
    visible = _visible_user_ids(db, user)
    if user_id is not None and user_id not in visible:
        raise HTTPException(403, "Not allowed to view this user's diary.")
    stmt = (
        select(DailyTaskEntry)
        .where(
            DailyTaskEntry.tenant_id == user.tenant_id,
            DailyTaskEntry.task_date >= start,
            DailyTaskEntry.task_date < end,
        )
        .order_by(DailyTaskEntry.task_date, DailyTaskEntry.id)
    )
    if user_id is not None:
        stmt = stmt.where(DailyTaskEntry.user_id == user_id)
    else:
        stmt = stmt.where(DailyTaskEntry.user_id.in_(visible))
    return [_serialize(db, e) for e in db.scalars(stmt)]


@router.post("", response_model=DailyTaskEntryOut, status_code=201)
def create_entry(
    payload: DailyTaskEntryCreate,
    db: Session = Depends(get_db),
    user: User = Depends(require_diary_author),
):
    try:
        validate_status(payload.status)
    except ValueError as exc:
        raise HTTPException(400, str(exc)) from exc
    entry = DailyTaskEntry(
        tenant_id=user.tenant_id,
        user_id=user.id,
        department_id=user.department_id,
        task_date=payload.task_date,
        plan_linkage=payload.plan_linkage,
        work_done=payload.work_done,
        status=payload.status,
        figure_output=payload.figure_output,
        evidence_link=payload.evidence_link,
        difficulties=payload.difficulties,
        support_needed=payload.support_needed,
        self_reflection=payload.self_reflection,
        tomorrow_plan_1=payload.tomorrow_plan_1,
        tomorrow_plan_2=payload.tomorrow_plan_2,
        tomorrow_plan_3=payload.tomorrow_plan_3,
    )
    db.add(entry)
    db.commit()
    db.refresh(entry)
    return _serialize(db, entry)


def _get_scoped_entry(db: Session, user: User, entry_id: int) -> DailyTaskEntry:
    entry = db.get(DailyTaskEntry, entry_id)
    if not entry or entry.tenant_id != user.tenant_id:
        raise HTTPException(404, "Diary entry not found.")
    if entry.user_id not in _visible_user_ids(db, user):
        raise HTTPException(403, "Not allowed to access this entry.")
    return entry


@router.patch("/{entry_id}", response_model=DailyTaskEntryOut)
def update_entry(
    entry_id: int,
    payload: DailyTaskEntryUpdate,
    db: Session = Depends(get_db),
    user: User = Depends(require_diary_author),
):
    entry = _get_scoped_entry(db, user, entry_id)
    if entry.user_id != user.id:
        raise HTTPException(403, "Only the author can edit a diary entry.")
    data = payload.model_dump(exclude_unset=True)
    if "status" in data:
        try:
            validate_status(data["status"])
        except ValueError as exc:
            raise HTTPException(400, str(exc)) from exc
    for key, value in data.items():
        setattr(entry, key, value)
    db.commit()
    db.refresh(entry)
    return _serialize(db, entry)


@router.delete("/{entry_id}", status_code=204)
def delete_entry(
    entry_id: int,
    db: Session = Depends(get_db),
    user: User = Depends(require_diary_author),
):
    entry = _get_scoped_entry(db, user, entry_id)
    if entry.user_id != user.id and user.role != UserRole.TENANT_ADMIN:
        raise HTTPException(403, "Only the author or a tenant admin can delete.")
    if entry.evidence_file:
        path = UPLOAD_DIR / entry.evidence_file
        if path.exists():
            path.unlink()
    db.delete(entry)
    db.commit()
    return None


@router.post("/{entry_id}/feedback", response_model=DailyTaskEntryOut)
def give_feedback(
    entry_id: int,
    payload: DailyTaskFeedbackUpdate,
    db: Session = Depends(get_db),
    user: User = Depends(require_tenant_member),
):
    entry = _get_scoped_entry(db, user, entry_id)
    if not _can_give_feedback(user, entry):
        raise HTTPException(403, "Only the department supervisor or tenant admin can give feedback.")
    entry.supervisor_feedback = payload.supervisor_feedback
    entry.feedback_by_id = user.id
    notification_service.notify_user(
        db,
        tenant_id=user.tenant_id,
        user_id=entry.user_id,
        type="daily_task_feedback",
        title="Supervisor feedback on your diary",
        message=f"Your diary entry for {entry.task_date.isoformat()} received supervisor feedback.",
        link=f"/daily-tasks?year={entry.task_date.year}&month={entry.task_date.month}",
    )
    db.commit()
    db.refresh(entry)
    return _serialize(db, entry)


@router.post("/{entry_id}/evidence", response_model=DailyTaskEntryOut)
async def upload_evidence(
    entry_id: int,
    file: UploadFile = File(...),
    db: Session = Depends(get_db),
    user: User = Depends(require_diary_author),
):
    entry = _get_scoped_entry(db, user, entry_id)
    if entry.user_id != user.id:
        raise HTTPException(403, "Only the author can attach evidence.")
    content = await file.read()
    if len(content) > MAX_EVIDENCE_BYTES:
        raise HTTPException(400, "Evidence file must be 10 MB or smaller.")
    UPLOAD_DIR.mkdir(parents=True, exist_ok=True)
    safe_name = Path(file.filename or "evidence").name
    stored = f"{entry.id}_{safe_name}"
    (UPLOAD_DIR / stored).write_bytes(content)
    if entry.evidence_file and entry.evidence_file != stored:
        old = UPLOAD_DIR / entry.evidence_file
        if old.exists():
            old.unlink()
    entry.evidence_file = stored
    db.commit()
    db.refresh(entry)
    return _serialize(db, entry)


@router.get("/{entry_id}/evidence")
def download_evidence(
    entry_id: int,
    db: Session = Depends(get_db),
    user: User = Depends(require_tenant_member),
):
    entry = _get_scoped_entry(db, user, entry_id)
    if not entry.evidence_file:
        raise HTTPException(404, "No evidence file attached.")
    path = UPLOAD_DIR / entry.evidence_file
    if not path.exists():
        raise HTTPException(404, "Evidence file not found on server.")
    return FileResponse(path, filename=Path(entry.evidence_file).name)


@router.get("/export/file")
def export_diary(
    year: int = Query(...),
    month: int = Query(...),
    user_id: int | None = Query(None),
    db: Session = Depends(get_db),
    user: User = Depends(require_tenant_member),
):
    _month_bounds(year, month)
    visible = _visible_user_ids(db, user)
    if user_id is not None and user_id not in visible:
        raise HTTPException(403, "Not allowed to export this user's diary.")
    ids = [user_id] if user_id is not None else visible
    users = db.scalars(
        select(User).where(User.id.in_(ids)).order_by(User.name)
    ).all()
    buf = build_export(db, user.tenant_id, users, year, month)
    from fastapi.responses import StreamingResponse

    return StreamingResponse(
        buf,
        media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        headers={"Content-Disposition": f"attachment; filename=daily-diary-{year}-{month:02d}.xlsx"},
    )


@router.get("/summary/month", response_model=MonthlySummaryOut)
def monthly_summary(
    year: int = Query(...),
    month: int = Query(...),
    db: Session = Depends(get_db),
    user: User = Depends(require_tenant_member),
):
    start, end = _month_bounds(year, month)
    visible = _visible_user_ids(db, user)
    users = db.scalars(
        select(User)
        .where(
            User.id.in_(visible),
            User.role.in_([UserRole.EMPLOYEE, UserRole.DEPT_ADMIN]),
        )
        .order_by(User.name)
    ).all()
    rows: list[MonthlyUserSummary] = []
    for u in users:
        entries = db.scalars(
            select(DailyTaskEntry).where(
                DailyTaskEntry.tenant_id == user.tenant_id,
                DailyTaskEntry.user_id == u.id,
                DailyTaskEntry.task_date >= start,
                DailyTaskEntry.task_date < end,
            )
        ).all()
        days = len({e.task_date for e in entries})
        completed = sum(1 for e in entries if e.status == "completed")
        in_prog = sum(1 for e in entries if e.status in ("in_progress", "pending"))
        not_started = sum(1 for e in entries if e.status == "not_started")
        rows.append(
            MonthlyUserSummary(
                user_id=u.id,
                author_name=u.name,
                working_days=days,
                completed=completed,
                in_progress_or_pending=in_prog,
                not_started=not_started,
                total=len(entries),
            )
        )
    return MonthlySummaryOut(year=year, month=month, users=rows)


@router.get("/review/month", response_model=MonthlyReviewOut | None)
def get_monthly_review(
    year: int = Query(...),
    month: int = Query(...),
    user_id: int = Query(...),
    db: Session = Depends(get_db),
    user: User = Depends(require_tenant_member),
):
    if user_id not in _visible_user_ids(db, user):
        raise HTTPException(403, "Not allowed to view this review.")
    review = db.scalars(
        select(DailyTaskMonthlyReview).where(
            DailyTaskMonthlyReview.tenant_id == user.tenant_id,
            DailyTaskMonthlyReview.user_id == user_id,
            DailyTaskMonthlyReview.year == year,
            DailyTaskMonthlyReview.month == month,
        )
    ).first()
    if not review:
        return None
    return MonthlyReviewOut.model_validate(review)


@router.put("/review/month", response_model=MonthlyReviewOut)
def put_monthly_review(
    year: int = Query(...),
    month: int = Query(...),
    user_id: int = Query(...),
    payload: MonthlyReviewUpdate = None,  # type: ignore[assignment]
    db: Session = Depends(get_db),
    user: User = Depends(require_tenant_member),
):
    if user_id not in _visible_user_ids(db, user):
        raise HTTPException(403, "Not allowed to edit this review.")
    review = db.scalars(
        select(DailyTaskMonthlyReview).where(
            DailyTaskMonthlyReview.tenant_id == user.tenant_id,
            DailyTaskMonthlyReview.user_id == user_id,
            DailyTaskMonthlyReview.year == year,
            DailyTaskMonthlyReview.month == month,
        )
    ).first()
    if not review:
        review = DailyTaskMonthlyReview(
            tenant_id=user.tenant_id, user_id=user_id, year=year, month=month
        )
        db.add(review)
    data = payload.model_dump(exclude_unset=True)
    is_owner = user_id == user.id
    # HOD evaluation is supervisor-only: an owner's own hod_evaluation
    # value is ignored, never stored from the author.
    if "hod_evaluation" in data:
        allowed = user.role == UserRole.TENANT_ADMIN or (
            user.role == UserRole.DEPT_ADMIN
            and user.department_id is not None
            and db.get(User, user_id) is not None
            and db.get(User, user_id).department_id == user.department_id
        )
        if allowed:
            review.hod_evaluation = data.pop("hod_evaluation")
        else:
            data.pop("hod_evaluation")
    if is_owner or user.role == UserRole.TENANT_ADMIN:
        for key in ("achievements", "challenges_solutions", "improvements_next_focus"):
            if key in data:
                setattr(review, key, data[key])
    elif user.role == UserRole.DEPT_ADMIN:
        # Dept admins only write the evaluation on a member's review.
        pass
    db.commit()
    db.refresh(review)
    return MonthlyReviewOut.model_validate(review)
