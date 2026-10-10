from datetime import date
from typing import Any, Dict, List, Optional
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

router = APIRouter(prefix="/api", tags=["dashboard"])

MONTH_NAMES = [
    "January", "February", "March", "April", "May", "June",
    "July", "August", "September", "October", "November", "December",
]


def current_month_period(today: date | None = None) -> tuple[int, str]:
    today = today or date.today()
    return today.year, MONTH_NAMES[today.month - 1]


def is_current_or_future_period(year: int, period: str, today: date | None = None) -> bool:
    """True for the current month and any future month (used by recurring service helpers)."""
    current_year, current_period = current_month_period(today)
    return (year, MONTH_NAMES.index(period)) >= (current_year, MONTH_NAMES.index(current_period))


def is_exact_current_month(year: int, period: str, today: date | None = None) -> bool:
    """True ONLY when year/period matches today's calendar month.

    Used to decide whether employees may enter scores and whether draft
    submissions should be auto-created.  Employees can update scores at
    any point during the current month; future months are locked.
    """
    current_year, current_period = current_month_period(today)
    return year == current_year and period == current_period


def is_future_month(year: int, period: str, today: date | None = None) -> bool:
    """True when year/period is strictly after the current calendar month."""
    current_year, current_period = current_month_period(today)
    return (year, MONTH_NAMES.index(period)) > (current_year, MONTH_NAMES.index(current_period))





def calculate_kpi(kpi_list: List[Dict[str, Any]]) -> tuple[float, str]:
    # Weight စုစုပေါင်း 100 မပြည့်ပါက Error ပြရန်
    total_weight = sum(item["weight"] for item in kpi_list)
    if total_weight == 0:
        return 0.0, "N/A"

# Weight 100% မပြည့်ပါကလည်း Error မတက်စေဘဲ တွက်ချက်ပေးရန် သို့မဟုတ် 0 ပေးရန်
    if total_weight != 100:
    # ဥပမာ - Weight အချိုးအစားအတိုင်း အမှတ်တွက်ပေးခြင
        pass
    total_score = 0.0
    for item in kpi_list:
        actual = item["actual"]
        target = item["target"]
        weight = item["weight"]

        # Actual က Target ထက် ကျော်လွန်ပါက Error တက်စေရန်
        if actual > target:
            raise ValueError(f"Actual ({actual}) cannot be greater than Target ({target}).")

        total_score += (actual / target) * weight

    # Rating သတ်မှတ်ခြင်း
    if total_score <= 50:
        rating = "Need to improve "
    elif total_score <= 75:
        rating = "Normal"
    else:
        rating = "Performance "

    return round(total_score, 2), rating


def combined_final_score(submissions: List[Any]) -> Optional[Dict[str, Any]]:
    """
    Weight-combined final KPI score using calculate_kpi validation rules.
    """
    valid_items = []
    for s in submissions:
        if s.final_score is None or not s.kpi_template or not s.kpi_template.target:
            continue

        valid_items.append({
            "actual": float(s.final_score),
            "target": float(s.kpi_template.target),
            "weight": float(s.kpi_template.weight or 0.0),
        })

    if not valid_items:
        return None

    total_score, rating = calculate_kpi(valid_items)

    return {
        "attainment": total_score,
        "status": rating,
        "scored_count": len(valid_items),
        "total_count": len(submissions),
    }


def per_employee_combined_scores(submissions) -> dict[int, dict | None]:
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
    """Self-assessment view for a Dept Admin's own custom KPIs (assigned by the Super Admin)."""
    if user.role != UserRole.DEPT_ADMIN:
        raise HTTPException(403, "This account type cannot access this view.")

    default_year, default_period = current_month_period()
    year = year or default_year
    period = period or default_period

    is_fillable_period = is_exact_current_month(year, period)
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
        "is_month_end": is_fillable_period,        # True for the entire current month
        "is_future_period": is_future_month(year, period),
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
        is_fillable_period = is_exact_current_month(year, period)
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
            is_month_end=is_fillable_period,           # True for the entire current month
            is_future_period=is_future_month(year, period),
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