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
    """Tenant-wide KPI overview for one period."""
    year, period = _validated_year_period(year, period)

    employees = {
        u.id: u
        for u in db.scalars(
            select(User).where(
                User.tenant_id == user.tenant_id,
                User.is_active == True,  # noqa: E712
                User.role == UserRole.EMPLOYEE,
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

    # Per-employee: submitted if ANY of their submissions left DRAFT.
    submitted_employees: set[int] = set()
    dept_scores: dict[int | None, list[float]] = {}
    buckets: dict[str, int] = {label: 0 for label in BUCKET_LABELS}
    all_scores: list[float] = []

    for sub in submissions:
        if sub.employee_id not in employees:
            continue
        if _submitted(sub):
            submitted_employees.add(sub.employee_id)
            score = sub.effective_score
            if score is not None:
                all_scores.append(score)
                buckets[_bucket(score)] += 1
                dept_scores.setdefault(sub.department_id, []).append(score)

    employee_count = len(employees)
    submitted_count = len(submitted_employees)

    by_department: list[DepartmentStatOut] = []
    for dept_id, dept in departments.items():
        emp_ids = [uid for uid, u in employees.items() if u.department_id == dept_id]
        scores = dept_scores.get(dept_id, [])
        by_department.append(
            DepartmentStatOut(
                department_id=dept_id,
                department_name=dept.name,
                employee_count=len(emp_ids),
                submitted_count=len([uid for uid in emp_ids if uid in submitted_employees]),
                avg_score=round(sum(scores) / len(scores), 2) if scores else None,
            )
        )
    by_department.sort(key=lambda d: d.department_name)

    return OverviewOut(
        year=year,
        period=period,
        employee_count=employee_count,
        submitted_count=submitted_count,
        pending_count=employee_count - submitted_count,
        submission_rate=round(submitted_count / employee_count * 100, 1) if employee_count else 0.0,
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
        scores = [
            s.effective_score
            for s in submissions
            if _submitted(s) and s.effective_score is not None
        ]
        submitted_employees = {s.employee_id for s in submissions if _submitted(s)}
        points.append(
            TrendPointOut(
                year=year,
                period=period,
                avg_score=round(sum(scores) / len(scores), 2) if scores else None,
                submitted_count=len(submitted_employees),
            )
        )
    return points
