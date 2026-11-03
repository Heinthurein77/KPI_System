"""Daily Task diary API (additive-only module; no KPI routes touched).

Visibility mirrors the KPI role model:
- employee: own entries only
- dept_admin: own department's entries (+ own diary, + supervisor feedback)
- tenant_admin: whole tenant
- super_admin: platform only, no tenant diary access (require_tenant_member)

Evidence upload: files are stored under uploads/daily_tasks/ (10 MB cap)
and served back through an authenticated, scope-checked endpoint.
Uploads are restricted to images, PDFs and Office documents — the
extension AND the file's magic bytes must agree, so a script renamed to
".png" (or an .html/.svg file) can never be stored and later served
inline from the app's own origin (stored-XSS path).
"""

from __future__ import annotations

import logging
import os
from datetime import date as date_type, datetime, timezone
from pathlib import Path

from fastapi import APIRouter, Depends, File, HTTPException, Query, UploadFile
from fastapi.responses import FileResponse
from sqlalchemy import func, or_, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.core.deps import get_current_user, get_current_user_no_tenant, require_diary_author, require_tenant_admin, require_tenant_member
from app.database import get_db
from app.models.audit_log import AuditLog
from app.models.daily_task import (
    TASK_STATUSES,
    DailyTaskEntry,
    DailyTaskMonthlyReview,
)
from app.models.department import Department
from app.models.user import User, UserRole
from app.schemas.audit_log import AuditLogOut
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
from app.services import audit_service
from app.services.daily_task_export import build_export

router = APIRouter(prefix="/api/daily-tasks", tags=["daily-tasks"])

logger = logging.getLogger("kpi_system")

MAX_EVIDENCE_BYTES = 10 * 1024 * 1024
UPLOAD_DIR = Path(os.getenv("DAILY_TASK_UPLOAD_DIR", "uploads/daily_tasks"))

# Evidence allowlist: extension -> (media type, magic-byte prefix or None,
# served inline?). Anything else — .html/.htm/.svg/.js/.exe/… — is
# rejected at upload, and anything already on disk with an unknown
# extension is only ever served as an opaque attachment (see
# download_evidence). GIF/WebP need more than a prefix check (handled in
# _sniff_evidence).
_EVIDENCE_PREVIEW_TYPES: dict[str, tuple[str, bytes | None]] = {
    ".png": ("image/png", b"\x89PNG\r\n\x1a\n"),
    ".jpg": ("image/jpeg", b"\xff\xd8\xff"),
    ".jpeg": ("image/jpeg", b"\xff\xd8\xff"),
    ".gif": ("image/gif", None),
    ".webp": ("image/webp", None),
    ".pdf": ("application/pdf", b"%PDF"),
}
_EVIDENCE_ATTACHMENT_TYPES: dict[str, tuple[str, bytes | None]] = {
    ".docx": ("application/vnd.openxmlformats-officedocument.wordprocessingml.document", b"PK\x03\x04"),
    ".xlsx": ("application/vnd.openxmlformats-officedocument.spreadsheetml.sheet", b"PK\x03\x04"),
    ".pptx": ("application/vnd.openxmlformats-officedocument.presentationml.presentation", b"PK\x03\x04"),
}


def _sniff_evidence(filename: str, content: bytes) -> tuple[str, bool]:
    """Validate an uploaded evidence file.

    Returns (media_type, serve_inline). Raises 400 for a disallowed
    extension or for content that does not match the claimed type — so a
    script renamed to .png, or an .html/.svg file, never reaches disk.
    """
    ext = Path(filename or "evidence").suffix.lower()
    spec = _EVIDENCE_PREVIEW_TYPES.get(ext)
    if spec is not None:
        media_type, magic = spec
        if ext == ".gif":
            ok = content[:6] in (b"GIF87a", b"GIF89a")
        elif ext == ".webp":
            ok = len(content) >= 12 and content[:4] == b"RIFF" and content[8:12] == b"WEBP"
        else:
            ok = magic is None or content.startswith(magic)
        if not ok:
            raise HTTPException(400, f"Evidence file content does not match its .{ext[1:]} extension.")
        return media_type, True
    spec = _EVIDENCE_ATTACHMENT_TYPES.get(ext)
    if spec is not None:
        media_type, magic = spec
        if magic is not None and not content.startswith(magic):
            raise HTTPException(400, f"Evidence file content does not match its .{ext[1:]} extension.")
        return media_type, False
    raise HTTPException(
        400,
        "Evidence files must be images (PNG/JPG/GIF/WebP), PDF, or Office "
        "documents (.docx/.xlsx/.pptx). HTML, SVG and script files are not allowed.",
    )


def _short(value) -> str:
    """Truncate a value for audit summaries so long diary text stays readable."""
    text = "" if value is None else str(value)
    return text if len(text) <= 40 else text[:37] + "…"


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
    # Nobody supervises themselves: a dept admin (or tenant admin) must
    # not write "supervisor feedback" on their own diary entry.
    if entry.user_id == user.id:
        return False
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
    # One diary row per user per day (also enforced by the DB unique
    # constraint): a double-submit/retry must get a clear 409, not a
    # second row that double-counts the monthly summary.
    existing = db.scalar(
        select(DailyTaskEntry).where(
            DailyTaskEntry.tenant_id == user.tenant_id,
            DailyTaskEntry.user_id == user.id,
            DailyTaskEntry.task_date == payload.task_date,
        )
    )
    if existing is not None:
        raise HTTPException(409, f"A diary entry for {payload.task_date.isoformat()} already exists.")
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
    db.flush()
    audit_service.log(
        db, user, "daily_task_created", "daily_task", entry.id,
        f"{user.name} — {entry.task_date.isoformat()}",
        f"Created diary entry for {entry.task_date.isoformat()} (status: {entry.status}).",
        department_id=entry.department_id,
    )
    try:
        db.commit()
    except IntegrityError:
        # Race between the existence check above and the commit: the
        # unique constraint is the backstop — report it as a conflict.
        db.rollback()
        raise HTTPException(409, f"A diary entry for {payload.task_date.isoformat()} already exists.")
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
    changed = []
    for key, value in data.items():
        old = getattr(entry, key)
        if old != value:
            changed.append(f"{key}: '{_short(old)}' → '{_short(value)}'")
        setattr(entry, key, value)
    if changed:
        audit_service.log(
            db, user, "daily_task_updated", "daily_task", entry.id,
            f"{user.name} — {entry.task_date.isoformat()}",
            f"Updated diary entry for {entry.task_date.isoformat()}: {'; '.join(changed)}.",
            department_id=entry.department_id,
        )
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
    # Commit the row deletion FIRST, then remove the file: if the commit
    # failed, the row would otherwise survive pointing at a deleted file
    # (permanent 404 on download). A failed file cleanup after a good
    # commit is only an orphaned file, so it is logged, never fatal.
    evidence_file = entry.evidence_file
    audit_service.log(
        db, user, "daily_task_deleted", "daily_task", entry.id,
        f"{user.name} — {entry.task_date.isoformat()}",
        f"Deleted diary entry for {entry.task_date.isoformat()} (work: '{_short(entry.work_done)}').",
        department_id=entry.department_id,
    )
    db.delete(entry)
    db.commit()
    if evidence_file:
        try:
            path = UPLOAD_DIR / evidence_file
            if path.exists():
                path.unlink()
        except OSError:
            logger.warning("Could not delete evidence file %s after entry %s was deleted", evidence_file, entry_id)
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
    audit_service.log(
        db, user, "daily_task_feedback", "daily_task", entry.id,
        f"{user.name} — {entry.task_date.isoformat()}",
        f"Supervisor feedback added to diary entry for {entry.task_date.isoformat()}.",
        department_id=entry.department_id,
    )
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
    # Stream the upload and abort past the cap instead of buffering an
    # unbounded body into memory first (a multi-GB upload would otherwise
    # spike worker memory before the size check ever ran).
    chunks: list[bytes] = []
    total = 0
    while True:
        chunk = await file.read(1024 * 1024)
        if not chunk:
            break
        total += len(chunk)
        if total > MAX_EVIDENCE_BYTES:
            raise HTTPException(400, "Evidence file must be 10 MB or smaller.")
        chunks.append(chunk)
    content = b"".join(chunks)
    media_type, _inline = _sniff_evidence(file.filename or "evidence", content)
    old_file = entry.evidence_file
    try:
        UPLOAD_DIR.mkdir(parents=True, exist_ok=True)
        safe_name = Path(file.filename or "evidence").name
        stored = f"{entry.id}_{safe_name}"
        (UPLOAD_DIR / stored).write_bytes(content)
    except OSError as exc:
        # E.g. the uploads volume is root-owned so the app user cannot write
        # into it. The diary entry itself is already saved; say exactly that
        # instead of letting the generic 500 handler hide the cause.
        raise HTTPException(
            500,
            "Could not save the evidence file on the server (storage permission "
            "problem). Your diary entry was saved without the file — please ask "
            "the administrator to fix the uploads folder ownership.",
        ) from exc
    entry.evidence_file = stored
    audit_service.log(
        db, user, "daily_task_evidence_uploaded", "daily_task", entry.id,
        f"{user.name} — {entry.task_date.isoformat()}",
        f"Evidence file '{safe_name}' attached to diary entry for {entry.task_date.isoformat()}.",
        department_id=entry.department_id,
    )
    db.commit()
    db.refresh(entry)
    # The old file is removed only after the new row committed, so a failed
    # commit can never leave the row pointing at a deleted file. A failed
    # cleanup here only orphans the old file — logged, never fatal.
    if old_file and old_file != stored:
        try:
            old_path = UPLOAD_DIR / old_file
            if old_path.exists():
                old_path.unlink()
        except OSError:
            logger.warning("Could not delete replaced evidence file %s for entry %s", old_file, entry_id)
    return _serialize(db, entry)


@router.get("/{entry_id}/evidence")
def download_evidence(
    entry_id: int,
    db: Session = Depends(get_db),
    # Deliberately NOT require_tenant_member: a browser navigation, <img>, or
    # <iframe> cannot send X-Tenant-Slug, and get_current_user fails closed
    # without a resolved tenant (401 "Not authenticated" even when logged in).
    # _get_scoped_entry still enforces entry.tenant_id == user.tenant_id (404)
    # and department/own visibility (403), so no cross-tenant access is gained.
    user: User = Depends(get_current_user_no_tenant),
):
    entry = _get_scoped_entry(db, user, entry_id)
    if not entry.evidence_file:
        raise HTTPException(404, "No evidence file attached.")
    path = UPLOAD_DIR / entry.evidence_file
    if not path.exists():
        raise HTTPException(404, "Evidence file not found on server.")
    # Never serve an unknown extension inline with a guessed media type:
    # a pre-allowlist file (or a renamed script) must only ever download
    # as an opaque attachment, never execute in the app's origin.
    ext = Path(entry.evidence_file).suffix.lower()
    preview = _EVIDENCE_PREVIEW_TYPES.get(ext)
    attachment = _EVIDENCE_ATTACHMENT_TYPES.get(ext)
    if preview is not None:
        media_type, inline = preview[0], True
    elif attachment is not None:
        media_type, inline = attachment[0], False
    else:
        media_type, inline = "application/octet-stream", False
    return FileResponse(
        path,
        media_type=media_type,
        filename=Path(entry.evidence_file).name,
        # Inline only for previewable types (images, PDF) so they open in
        # the browser; everything else forces a download.
        content_disposition_type="inline" if inline else "attachment",
    )


@router.get("/audit-log", response_model=list[AuditLogOut])
def daily_task_audit_log(
    year: int | None = Query(None),
    month: int | None = Query(None),
    user_id: int | None = Query(None),
    db: Session = Depends(get_db),
    user: User = Depends(require_tenant_admin),
):
    """Who did what to the diaries, in exact detail: creations, edits with
    old → new values, deletions with the deleted text, feedback, evidence
    uploads, and review updates.

    TENANT ADMIN ONLY. Dept admins and employees must not see this trail.
    """
    if user_id is not None and user_id not in _visible_user_ids(db, user):
        raise HTTPException(403, "Not allowed to view this user's diary trail.")
    stmt = (
        select(AuditLog)
        .where(
            AuditLog.tenant_id == user.tenant_id,
            AuditLog.entity_type.in_(["daily_task", "daily_task_review"]),
        )
        .order_by(AuditLog.created_at.desc())
        .limit(500)
    )
    if year is not None and month is not None:
        start, end = _month_bounds(year, month)
        day_start = datetime(start.year, start.month, start.day, tzinfo=timezone.utc)
        day_end = datetime(end.year, end.month, end.day, tzinfo=timezone.utc)
        stmt = stmt.where(AuditLog.created_at >= day_start, AuditLog.created_at < day_end)
    if user_id is not None:
        entry_ids = select(DailyTaskEntry.id).where(DailyTaskEntry.user_id == user_id)
        review_ids = select(DailyTaskMonthlyReview.id).where(
            DailyTaskMonthlyReview.user_id == user_id
        )
        stmt = stmt.where(
            or_(
                (AuditLog.entity_type == "daily_task") & (AuditLog.entity_id.in_(entry_ids)),
                (AuditLog.entity_type == "daily_task_review")
                & (AuditLog.entity_id.in_(review_ids)),
            )
        )
    return [AuditLogOut.model_validate(e) for e in db.scalars(stmt)]


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
    changed: list[str] = []
    # HOD evaluation is supervisor-only: an owner's own hod_evaluation
    # value is ignored, never stored from the author — and a supervisor
    # must not evaluate themselves either.
    if "hod_evaluation" in data:
        allowed = user_id != user.id and (
            user.role == UserRole.TENANT_ADMIN or (
                user.role == UserRole.DEPT_ADMIN
                and user.department_id is not None
                and db.get(User, user_id) is not None
                and db.get(User, user_id).department_id == user.department_id
            )
        )
        if allowed:
            if review.hod_evaluation != data["hod_evaluation"]:
                changed.append(f"hod_evaluation: '{_short(review.hod_evaluation)}' → '{_short(data['hod_evaluation'])}'")
            review.hod_evaluation = data.pop("hod_evaluation")
        else:
            data.pop("hod_evaluation")
    if is_owner or user.role == UserRole.TENANT_ADMIN:
        for key in ("achievements", "challenges_solutions", "improvements_next_focus"):
            if key in data:
                if getattr(review, key) != data[key]:
                    changed.append(f"{key}: '{_short(getattr(review, key))}' → '{_short(data[key])}'")
                setattr(review, key, data[key])
    elif user.role == UserRole.DEPT_ADMIN:
        # Dept admins only write the evaluation on a member's review.
        pass
    if changed:
        db.flush()
        target = db.get(User, user_id)
        audit_service.log(
            db, user, "daily_task_review_updated", "daily_task_review", review.id,
            f"{target.name if target else ''} — {year}-{month:02d}",
            f"Updated month-end review for {year}-{month:02d}: {'; '.join(changed)}.",
            department_id=target.department_id if target else None,
        )
    db.commit()
    db.refresh(review)
    return MonthlyReviewOut.model_validate(review)
