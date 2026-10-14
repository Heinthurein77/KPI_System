"""Reviewer notes: two-way discussion thread on KPI submissions.

Notes are visible to the submission's owner and any reviewer with scope over
it (dept admin of the same department, tenant admin, super admin). Both sides
can post — employees can reply to reviewer questions and vice versa.
"""
from datetime import datetime

from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy.orm import Session

from app.core.deps import get_current_user
from app.database import get_db
from app.models.kpi_submission import KPISubmission
from app.models.review_note import ReviewNote
from app.models.user import User, UserRole

router = APIRouter(prefix="/api/kpi/submissions", tags=["review-notes"])


class ReviewNoteCreate(BaseModel):
    body: str = Field(min_length=1, max_length=2000)


class ReviewNoteOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    body: str
    author_name: str
    author_role: str
    created_at: datetime


def _get_submission_for_notes(db: Session, user: User, submission_id: int) -> KPISubmission:
    submission = db.get(KPISubmission, submission_id)
    if submission is None or submission.tenant_id != user.tenant_id:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Submission not found.")
    if user.role == UserRole.EMPLOYEE and submission.employee_id != user.id:
        raise HTTPException(status.HTTP_403_FORBIDDEN, "Not your submission.")
    if user.role == UserRole.DEPT_ADMIN and submission.department_id != user.department_id:
        raise HTTPException(status.HTTP_403_FORBIDDEN, "Submission outside your department.")
    return submission


def _to_out(note: ReviewNote, author: User) -> ReviewNoteOut:
    return ReviewNoteOut(
        id=note.id,
        body=note.body,
        author_name=author.name,
        author_role=author.role.value,
        created_at=note.created_at,
    )


@router.get("/{submission_id}/notes", response_model=list[ReviewNoteOut])
def list_notes(
    submission_id: int,
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
):
    _get_submission_for_notes(db, user, submission_id)
    rows = (
        db.query(ReviewNote, User)
        .join(User, ReviewNote.author_id == User.id)
        .where(ReviewNote.submission_id == submission_id)
        .order_by(ReviewNote.created_at.asc())
        .all()
    )
    return [_to_out(note, author) for note, author in rows]


@router.post("/{submission_id}/notes", response_model=ReviewNoteOut, status_code=status.HTTP_201_CREATED)
def add_note(
    submission_id: int,
    payload: ReviewNoteCreate,
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
):
    submission = _get_submission_for_notes(db, user, submission_id)
    body = payload.body.strip()
    if not body:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "Note cannot be empty.")
    note = ReviewNote(
        tenant_id=submission.tenant_id,
        submission_id=submission.id,
        author_id=user.id,
        body=body,
    )
    db.add(note)
    db.commit()
    db.refresh(note)
    return _to_out(note, user)
