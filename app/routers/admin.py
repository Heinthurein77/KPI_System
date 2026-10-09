from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import StreamingResponse
from sqlalchemy import select
from sqlalchemy.orm import Session, joinedload
from app.core.deps import require_dept_admin, require_tenant_admin
from app.core.security import hash_password
from app.core.tenant import get_tenant_scoped_or_404
from app.database import get_db
from app.models.audit_log import AuditLog
from app.models.department import Department
from app.models.kpi_submission import KPIStatus, KPISubmission
from app.models.kpi_template import KPITemplate
from app.models.user import User, UserRole
from app.routers.dashboard import MONTH_NAMES, combined_final_score, current_month_period
from app.schemas.audit_log import AuditLogOut
from app.schemas.kpi import KPISubmissionOut, KpiTrendOut, KpiTrendPointOut
from app.schemas.kpi_template import (
    CreateCustomTemplateRequest,
    KPITemplateOut,
    RunRecurringKpisRequest,
)
from app.schemas.user import (
    CreateDepartmentRequest,
    CreateUserRequest,
    DepartmentOut,
    DepartmentWithCountOut,
    UpdateUserRequest,
    UserOut,
    UserSummaryOut,
)
from app.services import audit_service, kpi_export_service, kpi_service, recurring_service
from app.services.audit_service import fmt_score

router = APIRouter(prefix="/api/admin", tags=["admin"])


# ---------- Departments (Tenant Admin only) ----------

@router.get("/departments", response_model=list[DepartmentWithCountOut])
def list_departments(db: Session = Depends(get_db), user: User = Depends(require_tenant_admin)):
    departments = db.scalars(select(Department).where(Department.tenant_id == user.tenant_id)).all()
    return [
        DepartmentWithCountOut(id=d.id, name=d.name, employee_count=len(d.users)) for d in departments
    ]


@router.post("/departments", response_model=DepartmentOut)
def create_department(
    payload: CreateDepartmentRequest, db: Session = Depends(get_db), user: User = Depends(require_tenant_admin)
):
    department = Department(name=payload.name.strip(), tenant_id=user.tenant_id)
    db.add(department)
    db.commit()
    return department


@router.delete("/departments/{department_id}")
def delete_department(department_id: int, db: Session = Depends(get_db), user: User = Depends(require_tenant_admin)):
    department = get_tenant_scoped_or_404(db, Department, department_id, user.tenant_id, "Department not found.")

    has_users = db.scalar(
        select(User).where(User.department_id == department_id, User.tenant_id == user.tenant_id).limit(1)
    ) is not None
    has_templates = db.scalar(
        select(KPITemplate).where(
            KPITemplate.department_id == department_id, KPITemplate.tenant_id == user.tenant_id
        ).limit(1)
    ) is not None
    if has_users or has_templates:
        raise HTTPException(
            400,
            f'"{department.name}" still has users or KPI metrics assigned to it. '
            "Reassign or remove those first.",
        )

    db.delete(department)
    db.commit()
    return {"status": "ok"}


# ---------- Users ----------
# Tenant Admin manages everyone in their tenant. Dept Admin may only view/create/
# toggle Employee accounts within their own department — strict data isolation
# from other departments (and, transitively, from other tenants entirely).

@router.get("/users")
def list_users(db: Session = Depends(get_db), user: User = Depends(require_dept_admin)):
    if user.role == UserRole.DEPT_ADMIN:
        users = db.scalars(
            select(User).where(User.department_id == user.department_id, User.tenant_id == user.tenant_id)
        ).all()
        departments = []
        roles = [UserRole.EMPLOYEE]
    else:
        users = db.scalars(select(User).where(User.tenant_id == user.tenant_id)).all()
        departments = db.scalars(select(Department).where(Department.tenant_id == user.tenant_id)).all()
        roles = [UserRole.TENANT_ADMIN, UserRole.DEPT_ADMIN, UserRole.EMPLOYEE]

    return {
        "users": [UserOut.model_validate(u) for u in users],
        "departments": [DepartmentOut.model_validate(d) for d in departments],
        "roles": [r.value for r in roles],
    }


@router.get("/users/{user_id}", response_model=UserOut)
def get_user(user_id: int, db: Session = Depends(get_db), user: User = Depends(require_tenant_admin)):
    return get_tenant_scoped_or_404(db, User, user_id, user.tenant_id, "User not found.")


@router.get("/users/{user_id}/kpi-trend", response_model=KpiTrendOut)
def get_user_kpi_trend(
    user_id: int,
    year: int | None = None,
    db: Session = Depends(get_db),
    user: User = Depends(require_dept_admin),
):
    """12-month (Jan-Dec) combined KPI attainment for one employee in a given
    year, for the admin trend view — missing months come back as null-scored
    points rather than being omitted, so the chart always spans the full year.
    Dept Admin is scoped to their own department's Employees, same as
    everywhere else in this file; Tenant Admin sees anyone in the tenant."""
    target = get_tenant_scoped_or_404(db, User, user_id, user.tenant_id, "User not found.")

    if user.role == UserRole.DEPT_ADMIN and (
        target.department_id != user.department_id or target.role != UserRole.EMPLOYEE
    ):
        raise HTTPException(403, "Cannot view KPI trend outside your department.")

    submissions = db.scalars(
        select(KPISubmission)
        .options(joinedload(KPISubmission.kpi_template))
        .where(KPISubmission.employee_id == target.id, KPISubmission.tenant_id == user.tenant_id)
    ).unique().all()

    current_year = current_month_period()[0]
    available_years = sorted({s.year for s in submissions} | {current_year}, reverse=True)
    target_year = year if year in available_years else available_years[0]

    by_period: dict[tuple[int, str], list[KPISubmission]] = {}
    for s in submissions:
        if s.year == target_year:
            by_period.setdefault((s.year, s.month_or_quarter), []).append(s)

    points = []
    for period in MONTH_NAMES:
        group = by_period.get((target_year, period))
        combined = combined_final_score(group) if group else None
        points.append(
            KpiTrendPointOut(
                year=target_year,
                month_or_quarter=period,
                attainment=combined["attainment"] if combined else None,
                status=combined["status"] if combined else None,
                scored_count=combined["scored_count"] if combined else 0,
                total_count=len(group) if group else 0,
            )
        )

    scored_points = [p for p in points if p.attainment is not None]
    peak = max(scored_points, key=lambda p: p.attainment, default=None)
    lowest = min(scored_points, key=lambda p: p.attainment, default=None)
    annual_average = (
        round(sum(p.attainment for p in scored_points) / len(scored_points), 1) if scored_points else None
    )

    return KpiTrendOut(
        employee=target,
        year=target_year,
        available_years=available_years,
        points=points,
        peak=peak,
        lowest=lowest,
        annual_average=annual_average,
    )


@router.get("/users/{user_id}/submissions")
def get_user_submissions_for_period(
    user_id: int,
    year: int,
    period: str,
    db: Session = Depends(get_db),
    user: User = Depends(require_dept_admin),
):
    """A single employee's KPI submissions for one specific year/period — backs
    the "expand this month" detail view on the admin KPI Trend page. Same
    tenant/department scoping as get_user_kpi_trend."""
    target = get_tenant_scoped_or_404(db, User, user_id, user.tenant_id, "User not found.")

    if user.role == UserRole.DEPT_ADMIN and (
        target.department_id != user.department_id or target.role != UserRole.EMPLOYEE
    ):
        raise HTTPException(403, "Cannot view KPI data outside your department.")

    submissions = db.scalars(
        select(KPISubmission)
        .options(
            joinedload(KPISubmission.employee),
            joinedload(KPISubmission.kpi_template),
            joinedload(KPISubmission.department),
            joinedload(KPISubmission.dept_reviewer),
            joinedload(KPISubmission.final_reviewer),
        )
        .where(
            KPISubmission.employee_id == target.id,
            KPISubmission.tenant_id == user.tenant_id,
            KPISubmission.year == year,
            KPISubmission.month_or_quarter == period,
        )
    ).unique().all()
    submissions.sort(key=lambda s: s.kpi_template.metric_name)

    return {
        "employee": UserSummaryOut.model_validate(target),
        "year": year,
        "month_or_quarter": period,
        "submissions": [KPISubmissionOut.model_validate(s) for s in submissions],
    }


@router.get("/kpi-export")
def export_employee_kpi_report(
    employee_id: int,
    month: str,
    year: int,
    db: Session = Depends(get_db),
    user: User = Depends(require_dept_admin),
):
    """Download one employee's KPI scores for a selected monthly period.

    The export is read-only and uses the existing final-score calculation,
    with the same tenant and department access scope as existing KPI views.
    """
    if month not in MONTH_NAMES:
        raise HTTPException(422, f"month must be one of: {', '.join(MONTH_NAMES)}")

    target = get_tenant_scoped_or_404(db, User, employee_id, user.tenant_id, "User not found.")
    if user.role == UserRole.DEPT_ADMIN and (
        target.department_id != user.department_id or target.role != UserRole.EMPLOYEE
    ):
        raise HTTPException(403, "Cannot export KPI data outside your department.")

    submissions = db.scalars(
        select(KPISubmission)
        .options(joinedload(KPISubmission.kpi_template))
        .where(
            KPISubmission.employee_id == target.id,
            KPISubmission.tenant_id == user.tenant_id,
            KPISubmission.year == year,
            KPISubmission.month_or_quarter == month,
        )
    ).unique().all()
    submissions.sort(key=lambda s: s.kpi_template.metric_name)

    report = kpi_export_service.build_employee_kpi_report(
        target, submissions, year, month, combined_final_score(submissions)
    )
    filename = f"employee-kpi-{target.id}-{year}-{month.lower()}.xlsx"
    return StreamingResponse(
        report,
        media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        headers={"Content-Disposition": f'attachment; filename="{filename}"'},
    )


@router.get("/kpi-export/annual")
def export_employee_annual_kpi_report(
    employee_id: int,
    year: int,
    db: Session = Depends(get_db),
    user: User = Depends(require_dept_admin),
):
    """Download one employee's full-year KPI workbook, scoped like monthly exports."""
    target = get_tenant_scoped_or_404(db, User, employee_id, user.tenant_id, "User not found.")
    if user.role == UserRole.DEPT_ADMIN and (
        target.department_id != user.department_id or target.role != UserRole.EMPLOYEE
    ):
        raise HTTPException(403, "Cannot export KPI data outside your department.")

    submissions = db.scalars(
        select(KPISubmission)
        .options(joinedload(KPISubmission.kpi_template))
        .where(
            KPISubmission.employee_id == target.id,
            KPISubmission.tenant_id == user.tenant_id,
            KPISubmission.year == year,
            KPISubmission.month_or_quarter.in_(MONTH_NAMES),
        )
    ).unique().all()
    submissions.sort(key=lambda s: (MONTH_NAMES.index(s.month_or_quarter), s.kpi_template.metric_name))

    by_month = {month: [] for month in MONTH_NAMES}
    for submission in submissions:
        by_month[submission.month_or_quarter].append(submission)
    monthly_scores = {month: combined_final_score(by_month[month]) for month in MONTH_NAMES}

    report = kpi_export_service.build_employee_annual_kpi_report(
        target, submissions, year, MONTH_NAMES, monthly_scores
    )
    filename = f"employee-kpi-annual-{target.id}-{year}.xlsx"
    return StreamingResponse(
        report,
        media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        headers={"Content-Disposition": f'attachment; filename="{filename}"'},
    )


@router.post("/users", response_model=UserOut)
def create_user(
    payload: CreateUserRequest, db: Session = Depends(get_db), user: User = Depends(require_dept_admin)
):
    if payload.role == UserRole.SUPER_ADMIN:
        raise HTTPException(400, "Cannot assign the platform Super Admin role to a tenant user.")

    if user.role == UserRole.DEPT_ADMIN:
        if payload.role != UserRole.EMPLOYEE:
            raise HTTPException(403, "Department Admins can only create Employee accounts.")
        resolved_department_id = user.department_id
    elif payload.department_id is not None:
        resolved_department_id = get_tenant_scoped_or_404(
            db, Department, payload.department_id, user.tenant_id, "Department not found."
        ).id
    else:
        resolved_department_id = None

    target = User(
        tenant_id=user.tenant_id,
        name=payload.name.strip(),
        email=payload.email.strip().lower(),
        password_hash=hash_password(payload.password),
        role=payload.role,
        department_id=resolved_department_id if payload.role != UserRole.TENANT_ADMIN else None,
    )
    db.add(target)
    db.commit()
    return target


@router.post("/users/{user_id}/toggle-active", response_model=UserOut)
def toggle_user_active(user_id: int, db: Session = Depends(get_db), user: User = Depends(require_dept_admin)):
    target = get_tenant_scoped_or_404(db, User, user_id, user.tenant_id, "User not found.")

    if user.role == UserRole.DEPT_ADMIN and (
        target.department_id != user.department_id or target.role != UserRole.EMPLOYEE
    ):
        raise HTTPException(403, "Cannot manage users outside your department.")

    target.is_active = not target.is_active
    db.commit()
    return target


@router.put("/users/{user_id}", response_model=UserOut)
def edit_user(
    user_id: int,
    payload: UpdateUserRequest,
    db: Session = Depends(get_db),
    user: User = Depends(require_tenant_admin),
):
    if payload.role == UserRole.SUPER_ADMIN:
        raise HTTPException(400, "Cannot assign the platform Super Admin role to a tenant user.")

    target = get_tenant_scoped_or_404(db, User, user_id, user.tenant_id, "User not found.")

    if target.id == user.id and payload.role != UserRole.TENANT_ADMIN:
        raise HTTPException(400, "You can't demote your own account while signed in as it.")

    target.name = payload.name.strip()
    target.email = payload.email.strip().lower()
    target.role = payload.role
    if payload.department_id and payload.role != UserRole.TENANT_ADMIN:
        target.department_id = get_tenant_scoped_or_404(
            db, Department, payload.department_id, user.tenant_id, "Department not found."
        ).id
    else:
        target.department_id = None
    if payload.password:
        target.password_hash = hash_password(payload.password)
    db.commit()
    return target


@router.delete("/users/{user_id}")
def delete_user(user_id: int, db: Session = Depends(get_db), user: User = Depends(require_tenant_admin)):
    """Tenant Admin can delete any user in their tenant unconditionally — including
    their KPI history. The only guard left is against deleting your own currently-
    signed-in account, since that would lock you out with no way back in."""
    target = get_tenant_scoped_or_404(db, User, user_id, user.tenant_id, "User not found.")

    if target.id == user.id:
        raise HTTPException(400, "You can't delete your own account while signed in.")

    kpi_service.force_delete_user(db, user, target)
    return {"status": "ok"}


# ---------- KPI Templates (metrics) ----------
# Tenant Admin manages all metrics, including tenant-wide ones (no department).
# Dept Admin may add/remove metrics scoped to their own department only; they can
# see tenant-wide metrics (since those apply to their team too) but not edit them.

@router.get("/templates")
def list_templates(db: Session = Depends(get_db), user: User = Depends(require_dept_admin)):
    default_year, default_period = current_month_period()

    if user.role == UserRole.DEPT_ADMIN:
        kpi_templates = db.scalars(
            select(KPITemplate).where(
                KPITemplate.tenant_id == user.tenant_id,
                (KPITemplate.department_id == user.department_id) | (KPITemplate.department_id.is_(None)),
            )
        ).all()
        departments = []
        # Dept Admins may only target their own Employees.
        team = db.scalars(
            select(User).where(
                User.department_id == user.department_id,
                User.tenant_id == user.tenant_id,
                User.role == UserRole.EMPLOYEE,
            )
        ).all()
    else:
        kpi_templates = db.scalars(select(KPITemplate).where(KPITemplate.tenant_id == user.tenant_id)).all()
        departments = db.scalars(select(Department).where(Department.tenant_id == user.tenant_id)).all()
        # Tenant Admin may target any Employee or Dept Admin, tenant-wide.
        team = db.scalars(
            select(User).where(
                User.tenant_id == user.tenant_id, User.role.in_([UserRole.EMPLOYEE, UserRole.DEPT_ADMIN])
            )
        ).all()

    return {
        "kpi_templates": [KPITemplateOut.model_validate(t) for t in kpi_templates],
        "departments": [DepartmentOut.model_validate(d) for d in departments],
        "team": [UserSummaryOut.model_validate(u) for u in team],
        "months": MONTH_NAMES,
        "default_year": default_year,
        "default_period": default_period,
    }


@router.post("/templates/custom", response_model=KPITemplateOut)
def create_custom_template(
    payload: CreateCustomTemplateRequest,
    db: Session = Depends(get_db),
    user: User = Depends(require_dept_admin),
):
    employee = get_tenant_scoped_or_404(db, User, payload.employee_id, user.tenant_id, "User not found.")

    if user.role == UserRole.DEPT_ADMIN:
        # Dept Admins may only assign custom KPIs to their own Employees.
        if employee.role != UserRole.EMPLOYEE or employee.department_id != user.department_id:
            raise HTTPException(403, "Cannot create a custom KPI for an employee outside your department.")
    else:
        # Tenant Admin may target any Employee or Dept Admin, but not another Tenant
        # Admin (Tenant Admins aren't reviewed by anyone in this workflow).
        if employee.role not in (UserRole.EMPLOYEE, UserRole.DEPT_ADMIN):
            raise HTTPException(400, "Custom KPIs can only be assigned to Employees or Department Admins.")

    submission = kpi_service.create_custom_employee_kpi(
        db, user, employee, payload.metric_name.strip(), payload.target, payload.weight,
        payload.year, payload.period, is_recurring=payload.is_recurring,
    )
    return submission.kpi_template


@router.post("/recurring/run")
def run_recurring_kpis(
    payload: RunRecurringKpisRequest,
    db: Session = Depends(get_db),
    user: User = Depends(require_tenant_admin),
):
    """Materialise this tenant's opted-in custom KPIs for one monthly period.

    This endpoint is intentionally additive and idempotent: it only creates
    missing draft submissions and never changes existing KPI data or workflow
    state.  It can be called by a monthly scheduler or run manually.
    """
    if payload.period not in MONTH_NAMES:
        raise HTTPException(422, f"period must be one of: {', '.join(MONTH_NAMES)}")

    result = recurring_service.materialise_recurring_for_period(
        db, payload.year, payload.period, tenant_id=user.tenant_id
    )
    return {
        "year": result.year,
        "period": result.period,
        "created_count": result.created_count,
        "skipped_count": result.skipped_count,
        "errors": result.errors,
    }


@router.delete("/templates/{template_id}")
def delete_template(template_id: int, db: Session = Depends(get_db), user: User = Depends(require_dept_admin)):
    """Tenant Admin can delete any KPI metric in their tenant unconditionally —
    including its submission history — no department or history restrictions.
    Dept Admin keeps the protected path: own-department metrics only, and
    blocked once real KPI history exists."""
    template = get_tenant_scoped_or_404(db, KPITemplate, template_id, user.tenant_id, "Template not found.")

    if user.role == UserRole.TENANT_ADMIN:
        kpi_service.force_delete_template(db, user, template)
        return {"status": "ok"}

    if template.department_id != user.department_id:
        raise HTTPException(403, "Cannot manage metrics outside your department.")

    submissions = db.scalars(select(KPISubmission).where(KPISubmission.kpi_template_id == template_id)).all()
    blocking = [s for s in submissions if s.status != KPIStatus.DRAFT]
    if blocking:
        raise HTTPException(
            400,
            f'"{template.metric_name}" already has KPI submissions recorded against it and can\'t be deleted, '
            "to keep employee KPI history intact.",
        )

    audit_service.log(
        db, user, "kpi_template_deleted", "kpi_template", template.id, template.metric_name,
        f"KPI metric deleted (target={fmt_score(template.target)}, weight={fmt_score(template.weight)}).",
        department_id=template.department_id,
    )

    # Untouched draft rows aren't "history" yet — clean them up along with the template.
    for s in submissions:
        db.delete(s)

    db.delete(template)
    db.commit()
    return {"status": "ok"}


# ---------- Audit Log ----------
# Who changed what KPI/target data, and when. Tenant Admin only — an
# oversight tool, not a line-manager one, unlike the rest of this file's
# Dept-Admin-scoped endpoints.

@router.get("/audit-log", response_model=list[AuditLogOut])
def list_audit_log(
    limit: int = 200,
    db: Session = Depends(get_db),
    user: User = Depends(require_tenant_admin),
):
    query = (
        select(AuditLog)
        .where(AuditLog.tenant_id == user.tenant_id)
        .order_by(AuditLog.created_at.desc())
        .limit(min(max(limit, 1), 500))
    )
    entries = db.scalars(query).all()
    return [AuditLogOut.model_validate(e) for e in entries]
