from datetime import date
from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import select
from sqlalchemy.orm import Session
from app.core.deps import get_current_user
from app.database import get_db
from app.models.department import Department
from app.models.kpi_submission import KPIStatus, KPISubmission
from app.models.user import User, UserRole
from app.schemas.kpi import KPISubmissionOut
from app.schemas.user import DepartmentOut
from app.services import kpi_service
from typing import Dict, List, Optional, Any
router = APIRouter(prefix="/api", tags=["dashboard"])
MONTH_NAMES = [
    "January", "February", "March", "April", "May", "June",
    "July", "August", "September", "October", "November", "December",
]

def current_month_period(today: date | None = None) -> tuple[int, str]:
    today = today or date.today()
    return today.year, MONTH_NAMES[today.month - 1]


def is_current_or_future_period(year: int, period: str, today: date | None = None) -> bool:
    """Employees may fill in the current month or get a head start on future months —
    but not spontaneously generate draft KPIs for months that have already passed."""
    current_year, current_period = current_month_period(today)
    return (year, MONTH_NAMES.index(period)) >= (current_year, MONTH_NAMES.index(current_period))


def combined_final_score(
    submissions: List[Any],
    max_cap: Optional[float] = 120.0
) -> Optional[Dict[str, Any]]:
    """
    Weight-combined final KPI score (as % of target) across an employee's approved metrics.

    Args:
        submissions: List of submission objects containing final_score and kpi_template.
        max_cap: Maximum allowed attainment percentage per metric (e.g., 100.0 or 120.0).
                 Set to None to allow uncapped overachievement.
    """
    weighted_sum = 0.0
    weight_total = 0.0
    scored_count = 0

    for s in submissions:
        # Check if score exists and target is non-zero
        if s.final_score is None or not s.kpi_template or not s.kpi_template.target:
            continue

        target = float(s.kpi_template.target)
        actual = float(s.final_score)

        # 1. Calculate Raw Attainment (%)
        # Check metric direction if attribute exists (default: higher is better)
        is_lower_better = getattr(s.kpi_template, 'is_lower_better', False)

        if is_lower_better:
            # For metrics like Defect Rate, Error Count
            attainment = (target / actual * 100) if actual > 0 else 100.0
        else:
            # Standard metric (higher is better)
            attainment = (actual / target) * 100

        # 2. Apply Capping Rule (Prevents single-metric distortion)
        if max_cap is not None:
            attainment = min(attainment, max_cap)

        # 3. Apply Weightage
        weight = float(s.kpi_template.weight or 1.0)

        weighted_sum += attainment * weight
        weight_total += weight
        scored_count += 1

    # Return None if no valid scored metrics exist
    if weight_total == 0:
        return None

    # Normalized Weighted Average Score (%)
    combined_attainment = round(weighted_sum / weight_total, 2)

    # Status Evaluation
    if combined_attainment >= 100.0:
        status = "good"
    elif combined_attainment >= 85.0:
        status = "warning"
    else:
        status = "critical"

    return {
        "attainment": combined_attainment,
        "status": status,
        "scored_count": scored_count,
        "total_count": len(submissions)
    }

def per_employee_combined_scores(submissions) -> dict[int, dict | None]:
    # Keyed by employee id, not name — two employees can share a display name
    # (common once a tenant has more than a handful of people), and a name key
    # would silently merge their submissions into one combined score.
    by_employee: dict[int, list] = {}
    for s in submissions:
        by_employee.setdefault(s.employee_id, []).append(s)
    result: dict[int, dict | None] = {}
    for employee_id, group in by_employee.items():
        combined = combined_final_score(group)
        result[employee_id] = {**combined, "name": group[0].employee.name} if combined else None
    return result


def _serialize(submissions) -> list[KPISubmissionOut]:
    return [KPISubmissionOut.model_validate(s) for s in submissions]


@router.get("/my-kpi")
def my_kpi(
    year: int | None = None,
    period: str | None = None,
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
):
    """Self-assessment view for a Dept Admin's own custom KPIs (assigned by the Super Admin).

    Regular Employees use /api/dashboard for this; Dept Admins are normally routed to
    their team-review dashboard, so they need a separate place to score their own.
    """
    # Allow-list, not deny-list — same rationale as /api/dashboard below: a
    # platform-level account (tenant_id IS NULL) authenticates fine when no tenant
    # is resolved for the request, so this must reject it explicitly rather than
    # silently falling through to ensure_period_submissions with a null tenant.
    if user.role != UserRole.DEPT_ADMIN:
        raise HTTPException(403, "This account type cannot access this view.")

    default_year, default_period = current_month_period()
    year = year or default_year
    period = period or default_period

    is_fillable_period = is_current_or_future_period(year, period)
    if is_fillable_period:
        submissions = kpi_service.ensure_period_submissions(db, user, year, period)
    else:
        submissions = db.scalars(
            kpi_service.own_submissions_query(user).where(
                KPISubmission.year == year, KPISubmission.month_or_quarter == period
            )
        ).unique().all()
    submissions.sort(key=lambda s: s.kpi_template.metric_name)

    return {
        "active_year": year,
        "active_period": period,
        "months": MONTH_NAMES,
        "submissions": _serialize(submissions),
        "is_current_period": is_fillable_period,
        "combined_score": combined_final_score(submissions),
    }
 
@router.get("/dashboard")
def dashboard(
    year: int | None = None,
    period: str | None = None,
    status_filter: str | None = None,
    department_id: str | None = None,
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
):
    # Allow-list, not deny-list: any account role outside these three (e.g. a
    # mislabeled/platform-level user somehow holding a tenant-scoped session)
    # must be rejected explicitly rather than silently falling through to the
    # tenant-wide admin view below.
    if user.role not in (UserRole.EMPLOYEE, UserRole.DEPT_ADMIN, UserRole.TENANT_ADMIN):
        raise HTTPException(403, "This account type cannot access the dashboard.")

    default_year, default_period = current_month_period()
    year = year or default_year
    period = period or default_period
    resolved_department_id = int(department_id) if department_id else None

    context = {
        "role": user.role,
        "active_year": year,
        "active_period": period,
        "status_filter": status_filter or "",
        "active_department_id": resolved_department_id,
        "months": MONTH_NAMES,
    }

    if user.role == UserRole.EMPLOYEE:
        is_fillable_period = is_current_or_future_period(year, period)
        if is_fillable_period:
            submissions = kpi_service.ensure_period_submissions(db, user, year, period)
        else:
            submissions = db.scalars(
                kpi_service.visible_submissions_query(user).where(
                    KPISubmission.year == year, KPISubmission.month_or_quarter == period
                )
            ).unique().all()
        submissions.sort(key=lambda s: s.kpi_template.metric_name)
        context.update(
            submissions=_serialize(submissions),
            is_current_period=is_fillable_period,
            combined_score=combined_final_score(submissions),
        )
        return context

    query = kpi_service.visible_submissions_query(user).where(
        KPISubmission.year == year,
        KPISubmission.month_or_quarter == period,
    )
    if status_filter:
        query = query.where(KPISubmission.status == status_filter)
    if resolved_department_id and user.role == UserRole.TENANT_ADMIN:
        query = query.where(KPISubmission.department_id == resolved_department_id)
    if user.role == UserRole.DEPT_ADMIN:
        # A Dept Admin's own KPI isn't reviewed here — see /api/my-kpi — so keep it off
        # their team list to avoid an unactionable row that looks broken.
        query = query.where(KPISubmission.employee_id != user.id)

    submissions = db.scalars(query).unique().all()
    submissions.sort(key=lambda s: (s.employee.name, s.kpi_template.metric_name))

    context.update(
        submissions=_serialize(submissions),
        statuses=[s.value for s in KPIStatus],
        employee_combined=per_employee_combined_scores(submissions),
    )

    if user.role == UserRole.DEPT_ADMIN:
        return context

    departments = db.scalars(select(Department).where(Department.tenant_id == user.tenant_id)).all()
    context.update(
        departments=[DepartmentOut.model_validate(d) for d in departments],
        is_fresh_install=not departments,
    )
    return context
