"""Tenant analytics: KPI overview and score trends.

Read-only endpoints for tenant admins. They aggregate KPISubmission rows but
never modify anything — no calculation logic lives here; scores are read from
the submissions' effective_score (final → dept → self, the most authoritative
available), exactly as stored by the existing workflow.

Rating buckets mirror the app's own dashboard rating:
  <= 50  → "Need to improve"
  <= 75  → "Normal"
  >  75  → "Performance"
"""

from datetime import date

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.deps import get_current_user, require_tenant_admin
from app.database import get_db
from app.models.department import Department
from app.models.kpi_submission import KPIStatus, KPISubmission
from app.models.user import User, UserRole

router = APIRouter(prefix="/api/analytics", tags=["analytics"])

MONTH_NAMES = (
    "January", "February", "March", "April", "May", "June",
    "July", "August", "September", "October", "November", "December",
)

BUCKET_LABELS = ("Need to improve", "Normal", "Performance")


def _bucket(score: float) -> str:
    if score <= 50:
        return "Need to improve"
    if score <= 75:
        return "Normal"
    return "Performance"


def _validated_year_period(year: int | None, period: str | None) -> tuple[int, str]:
    today = date.today()
    year = year or today.year
    period = period or MONTH_NAMES[today.month - 1]
    if period not in MONTH_NAMES:
        raise HTTPException(422, f"Invalid period '{period}'. Must be a full month name.")
    return year, period


def _submitted(sub: KPISubmission) -> bool:
    """A submission counts as submitted once it leaves DRAFT."""
    return sub.status != KPIStatus.DRAFT


class DepartmentStatOut(BaseModel):
    department_id: int
    department_name: str
    employee_count: int
    submitted_count: int
    avg_score: float | None


class OverviewOut(BaseModel):
    year: int
    period: str
    employee_count: int
    submitted_count: int
    approved_count: int
    pending_count: int
    submission_rate: float
    avg_score: float | None
    buckets: dict[str, int]
    by_department: list[DepartmentStatOut]


class TrendPointOut(BaseModel):
    year: int
    period: str
    avg_score: float | None
    submitted_count: int


def _period_submissions(db: Session, tenant_id: int, year: int, period: str) -> list[KPISubmission]:
    return list(
        db.scalars(
            select(KPISubmission).where(
                KPISubmission.tenant_id == tenant_id,
                KPISubmission.year == year,
                KPISubmission.month_or_quarter == period,
            )
        )
    )


@router.get("/overview", response_model=OverviewOut, dependencies=[Depends(require_tenant_admin)])
def analytics_overview(
    year: int | None = None,
    period: str | None = None,
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
):
    """Tenant-wide KPI overview for one period (employees + dept admins)."""
    year, period = _validated_year_period(year, period)

    members = {
        u.id: u
        for u in db.scalars(
            select(User).where(
                User.tenant_id == user.tenant_id,
                User.is_active == True,  # noqa: E712
                User.role.in_([UserRole.EMPLOYEE, UserRole.DEPT_ADMIN]),
            )
        )
    }
    departments = {
        d.id: d
        for d in db.scalars(
            select(Department).where(Department.tenant_id == user.tenant_id)
        )
    }

    submissions = _period_submissions(db, user.tenant_id, year, period)

    # Per-member aggregation: a person with 5 KPIs gets ONE score (the
    # mean of their approved effective scores), so they cast exactly one
    # bucket vote and one department vote — previously each submission voted
    # separately, overweighting people with more KPIs.
    #
    # Scores use APPROVED submissions only: in-flight (pending) or rejected
    # self-scores are not official numbers and must not move tenant averages,
    # buckets, or the trend. Submission compliance (submitted/pending counts)
    # separately tracks anything past DRAFT.
    approved_scores: dict[int, list[float]] = {}
    submitted_members: set[int] = set()
    for sub in submissions:
        if sub.employee_id not in members:
            continue
        if _submitted(sub):
            submitted_members.add(sub.employee_id)
            if sub.status == KPIStatus.APPROVED:
                score = sub.effective_score
                if score is not None:
                    approved_scores.setdefault(sub.employee_id, []).append(score)

    buckets: dict[str, int] = {label: 0 for label in BUCKET_LABELS}
    all_scores: list[float] = []
    dept_scores: dict[int | None, list[float]] = {}
    for emp_id, scores in approved_scores.items():
        mean_score = sum(scores) / len(scores)
        all_scores.append(mean_score)
        buckets[_bucket(mean_score)] += 1
        dept_scores.setdefault(members[emp_id].department_id, []).append(mean_score)

    member_count = len(members)
    submitted_count = len(submitted_members)

    by_department: list[DepartmentStatOut] = []
    for dept_id, dept in departments.items():
        member_ids = [uid for uid, u in members.items() if u.department_id == dept_id]
        scores = dept_scores.get(dept_id, [])
        by_department.append(
            DepartmentStatOut(
                department_id=dept_id,
                department_name=dept.name,
                employee_count=len(member_ids),
                submitted_count=len([uid for uid in member_ids if uid in submitted_members]),
                avg_score=round(sum(scores) / len(scores), 2) if scores else None,
            )
        )
    by_department.sort(key=lambda d: d.department_name)

    return OverviewOut(
        year=year,
        period=period,
        employee_count=member_count,
        submitted_count=submitted_count,
        approved_count=len(approved_scores),
        pending_count=member_count - submitted_count,
        submission_rate=round(submitted_count / member_count * 100, 1) if member_count else 0.0,
        avg_score=round(sum(all_scores) / len(all_scores), 2) if all_scores else None,
        buckets=buckets,
        by_department=by_department,
    )


@router.get("/trend", response_model=list[TrendPointOut], dependencies=[Depends(require_tenant_admin)])
def analytics_trend(
    periods: int = 6,
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
):
    """Average submitted score per month for the last N calendar months."""
    periods = max(1, min(periods, 24))
    today = date.today()
    months: list[tuple[int, str]] = []
    y, m = today.year, today.month
    for _ in range(periods):
        months.append((y, MONTH_NAMES[m - 1]))
        m -= 1
        if m == 0:
            m, y = 12, y - 1
    months.reverse()

    points: list[TrendPointOut] = []
    for year, period in months:
        submissions = _period_submissions(db, user.tenant_id, year, period)
        # Trend shows official numbers only — approved submissions.
        scores = [
            s.effective_score
            for s in submissions
            if s.status == KPIStatus.APPROVED and s.effective_score is not None
        ]
        submitted_members = {s.employee_id for s in submissions if _submitted(s)}
        points.append(
            TrendPointOut(
                year=year,
                period=period,
                avg_score=round(sum(scores) / len(scores), 2) if scores else None,
                submitted_count=len(submitted_members),
            )
        )
    return points
