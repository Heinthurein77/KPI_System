from datetime import date

from fastapi import APIRouter, Depends, Request
from fastapi.responses import RedirectResponse
from fastapi.templating import Jinja2Templates
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.deps import get_current_user
from app.database import get_db
from app.models.department import Department
from app.models.kpi_submission import KPIStatus, KPISubmission
from app.models.user import User, UserRole
from app.services import kpi_service

router = APIRouter(tags=["dashboard"])
templates = Jinja2Templates(directory="app/templates")


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


def combined_final_score(submissions) -> dict | None:
    """Weight-combined final KPI score (as % of target) across an employee's approved metrics."""
    weighted_sum = 0.0
    weight_total = 0.0
    scored_count = 0
    for s in submissions:
        if s.final_score is None or not s.kpi_template.target:
            continue
        attainment = s.final_score / s.kpi_template.target * 100
        weight = s.kpi_template.weight or 1.0
        weighted_sum += attainment * weight
        weight_total += weight
        scored_count += 1

    if weight_total == 0:
        return None

    combined = weighted_sum / weight_total
    status = "good" if combined >= 100 else ("warning" if combined >= 85 else "critical")
    return {"attainment": combined, "status": status, "scored_count": scored_count, "total_count": len(submissions)}


def per_employee_combined_scores(submissions) -> dict[str, dict | None]:
    by_employee: dict[str, list] = {}
    for s in submissions:
        by_employee.setdefault(s.employee.name, []).append(s)
    return {name: combined_final_score(group) for name, group in by_employee.items()}


@router.get("/")
def root():
    return RedirectResponse(url="/dashboard")


@router.get("/my-kpi")
def my_kpi(
    request: Request,
    year: int | None = None,
    period: str | None = None,
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
):
    """Self-assessment view for a Dept Admin's own custom KPIs (assigned by the Super Admin).

    Regular Employees use /dashboard for this; Dept Admins are normally routed to
    their team-review dashboard, so they need a separate place to score their own.
    """
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

    context = {
        "user": user,
        "active_year": year,
        "active_period": period,
        "months": MONTH_NAMES,
        "submissions": submissions,
        "is_current_period": is_fillable_period,
        "combined_score": combined_final_score(submissions),
    }
    return templates.TemplateResponse(request, "dashboard/employee.html", context)


@router.get("/dashboard")
def dashboard(
    request: Request,
    year: int | None = None,
    period: str | None = None,
    status_filter: str | None = None,
    department_id: str | None = None,
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
):
    default_year, default_period = current_month_period()
    year = year or default_year
    period = period or default_period
    resolved_department_id = int(department_id) if department_id else None

    context = {
        "user": user,
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
            submissions=submissions,
            is_current_period=is_fillable_period,
            combined_score=combined_final_score(submissions),
        )
        return templates.TemplateResponse(request, "dashboard/employee.html", context)

    query = kpi_service.visible_submissions_query(user).where(
        KPISubmission.year == year,
        KPISubmission.month_or_quarter == period,
    )
    if status_filter:
        query = query.where(KPISubmission.status == status_filter)
    if resolved_department_id and user.role == UserRole.SUPER_ADMIN:
        query = query.where(KPISubmission.department_id == resolved_department_id)
    if user.role == UserRole.DEPT_ADMIN:
        # A Dept Admin's own KPI isn't reviewed here — see /my-kpi — so keep it off
        # their team list to avoid an unactionable row that looks broken.
        query = query.where(KPISubmission.employee_id != user.id)

    submissions = db.scalars(query).unique().all()
    submissions.sort(key=lambda s: (s.employee.name, s.kpi_template.metric_name))

    context.update(
        submissions=submissions,
        statuses=list(KPIStatus),
        employee_combined=per_employee_combined_scores(submissions),
    )

    if user.role == UserRole.DEPT_ADMIN:
        return templates.TemplateResponse(request, "dashboard/dept_admin.html", context)

    departments = db.scalars(select(Department)).all()
    context.update(departments=departments)
    return templates.TemplateResponse(request, "dashboard/super_admin.html", context)
