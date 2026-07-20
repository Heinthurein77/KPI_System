from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.deps import require_dept_admin, require_super_admin
from app.core.security import hash_password
from app.database import get_db
from app.models.department import Department
from app.models.kpi_submission import KPIStatus, KPISubmission
from app.models.kpi_template import KPITemplate
from app.models.user import User, UserRole
from app.routers.dashboard import MONTH_NAMES, current_month_period
from app.schemas.kpi_template import (
    CreateCustomTemplateRequest,
    CreateTemplateRequest,
    KPITemplateOut,
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
from app.services import kpi_service

router = APIRouter(prefix="/api/admin", tags=["admin"])


# ---------- Departments (Super Admin only) ----------

@router.get("/departments", response_model=list[DepartmentWithCountOut])
def list_departments(db: Session = Depends(get_db), user: User = Depends(require_super_admin)):
    departments = db.scalars(select(Department)).all()
    return [
        DepartmentWithCountOut(id=d.id, name=d.name, employee_count=len(d.users)) for d in departments
    ]


@router.post("/departments", response_model=DepartmentOut)
def create_department(
    payload: CreateDepartmentRequest, db: Session = Depends(get_db), user: User = Depends(require_super_admin)
):
    department = Department(name=payload.name.strip())
    db.add(department)
    db.commit()
    return department


@router.delete("/departments/{department_id}")
def delete_department(department_id: int, db: Session = Depends(get_db), user: User = Depends(require_super_admin)):
    department = db.get(Department, department_id)
    if department is None:
        raise HTTPException(404, "Department not found.")

    has_users = db.scalar(select(User).where(User.department_id == department_id).limit(1)) is not None
    has_templates = db.scalar(select(KPITemplate).where(KPITemplate.department_id == department_id).limit(1)) is not None
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
# Super Admin manages everyone. Dept Admin may only view/create/toggle Employee
# accounts within their own department — strict data isolation from other departments.

@router.get("/users")
def list_users(db: Session = Depends(get_db), user: User = Depends(require_dept_admin)):
    if user.role == UserRole.DEPT_ADMIN:
        users = db.scalars(select(User).where(User.department_id == user.department_id)).all()
        departments = []
        roles = [UserRole.EMPLOYEE]
    else:
        users = db.scalars(select(User)).all()
        departments = db.scalars(select(Department)).all()
        roles = list(UserRole)

    return {
        "users": [UserOut.model_validate(u) for u in users],
        "departments": [DepartmentOut.model_validate(d) for d in departments],
        "roles": [r.value for r in roles],
    }


@router.get("/users/{user_id}", response_model=UserOut)
def get_user(user_id: int, db: Session = Depends(get_db), user: User = Depends(require_super_admin)):
    target = db.get(User, user_id)
    if target is None:
        raise HTTPException(404, "User not found.")
    return target


@router.post("/users", response_model=UserOut)
def create_user(
    payload: CreateUserRequest, db: Session = Depends(get_db), user: User = Depends(require_dept_admin)
):
    if user.role == UserRole.DEPT_ADMIN:
        if payload.role != UserRole.EMPLOYEE:
            raise HTTPException(403, "Department Admins can only create Employee accounts.")
        resolved_department_id = user.department_id
    else:
        resolved_department_id = payload.department_id

    target = User(
        name=payload.name.strip(),
        email=payload.email.strip().lower(),
        password_hash=hash_password(payload.password),
        role=payload.role,
        department_id=resolved_department_id if payload.role != UserRole.SUPER_ADMIN else None,
    )
    db.add(target)
    db.commit()
    return target


@router.post("/users/{user_id}/toggle-active", response_model=UserOut)
def toggle_user_active(user_id: int, db: Session = Depends(get_db), user: User = Depends(require_dept_admin)):
    target = db.get(User, user_id)
    if target is None:
        raise HTTPException(404, "User not found.")

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
    user: User = Depends(require_super_admin),
):
    target = db.get(User, user_id)
    if target is None:
        raise HTTPException(404, "User not found.")

    if target.id == user.id and payload.role != UserRole.SUPER_ADMIN:
        raise HTTPException(400, "You can't demote your own account while signed in as it.")

    target.name = payload.name.strip()
    target.email = payload.email.strip().lower()
    target.role = payload.role
    target.department_id = (
        payload.department_id if payload.department_id and payload.role != UserRole.SUPER_ADMIN else None
    )
    if payload.password:
        target.password_hash = hash_password(payload.password)
    db.commit()
    return target


@router.delete("/users/{user_id}")
def delete_user(user_id: int, db: Session = Depends(get_db), user: User = Depends(require_super_admin)):
    """Super Admin can delete any user unconditionally — including their KPI history.
    The only guard left is against deleting your own currently-signed-in account,
    since that would lock you out with no way back in."""
    target = db.get(User, user_id)
    if target is None:
        raise HTTPException(404, "User not found.")

    if target.id == user.id:
        raise HTTPException(400, "You can't delete your own account while signed in.")

    kpi_service.force_delete_user(db, target)
    return {"status": "ok"}


# ---------- KPI Templates (metrics) ----------
# Super Admin manages all metrics, including company-wide ones (no department).
# Dept Admin may add/remove metrics scoped to their own department only; they can
# see company-wide metrics (since those apply to their team too) but not edit them.

@router.get("/templates")
def list_templates(db: Session = Depends(get_db), user: User = Depends(require_dept_admin)):
    default_year, default_period = current_month_period()

    if user.role == UserRole.DEPT_ADMIN:
        kpi_templates = db.scalars(
            select(KPITemplate).where(
                (KPITemplate.department_id == user.department_id) | (KPITemplate.department_id.is_(None))
            )
        ).all()
        departments = []
        # Dept Admins may only target their own Employees.
        team = db.scalars(
            select(User).where(User.department_id == user.department_id, User.role == UserRole.EMPLOYEE)
        ).all()
    else:
        kpi_templates = db.scalars(select(KPITemplate)).all()
        departments = db.scalars(select(Department)).all()
        # Super Admin may target any Employee or Dept Admin, company-wide.
        team = db.scalars(
            select(User).where(User.role.in_([UserRole.EMPLOYEE, UserRole.DEPT_ADMIN]))
        ).all()

    return {
        "kpi_templates": [KPITemplateOut.model_validate(t) for t in kpi_templates],
        "departments": [DepartmentOut.model_validate(d) for d in departments],
        "team": [UserSummaryOut.model_validate(u) for u in team],
        "months": MONTH_NAMES,
        "default_year": default_year,
        "default_period": default_period,
    }


@router.post("/templates", response_model=KPITemplateOut)
def create_template(
    payload: CreateTemplateRequest, db: Session = Depends(get_db), user: User = Depends(require_dept_admin)
):
    if user.role == UserRole.DEPT_ADMIN:
        resolved_department_id = user.department_id
    else:
        resolved_department_id = payload.department_id

    template = KPITemplate(
        metric_name=payload.metric_name.strip(),
        target=payload.target,
        weight=payload.weight,
        department_id=resolved_department_id,
    )
    db.add(template)
    db.commit()
    return template


@router.post("/templates/custom", response_model=KPITemplateOut)
def create_custom_template(
    payload: CreateCustomTemplateRequest,
    db: Session = Depends(get_db),
    user: User = Depends(require_dept_admin),
):
    employee = db.get(User, payload.employee_id)
    if employee is None:
        raise HTTPException(404, "User not found.")

    if user.role == UserRole.DEPT_ADMIN:
        # Dept Admins may only assign custom KPIs to their own Employees.
        if employee.role != UserRole.EMPLOYEE or employee.department_id != user.department_id:
            raise HTTPException(403, "Cannot create a custom KPI for an employee outside your department.")
    else:
        # Super Admin may target any Employee or Dept Admin, but not another Super Admin
        # (Super Admins aren't reviewed by anyone in this workflow).
        if employee.role not in (UserRole.EMPLOYEE, UserRole.DEPT_ADMIN):
            raise HTTPException(400, "Custom KPIs can only be assigned to Employees or Department Admins.")

    submission = kpi_service.create_custom_employee_kpi(
        db, employee, payload.metric_name.strip(), payload.target, payload.weight, payload.year, payload.period
    )
    return submission.kpi_template


@router.delete("/templates/{template_id}")
def delete_template(template_id: int, db: Session = Depends(get_db), user: User = Depends(require_dept_admin)):
    """Super Admin can delete any KPI metric unconditionally — including its submission
    history — no department or history restrictions. Dept Admin keeps the protected
    path: own-department metrics only, and blocked once real KPI history exists."""
    template = db.get(KPITemplate, template_id)
    if template is None:
        raise HTTPException(404, "Template not found.")

    if user.role == UserRole.SUPER_ADMIN:
        kpi_service.force_delete_template(db, template)
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

    # Untouched draft rows aren't "history" yet — clean them up along with the template.
    for s in submissions:
        db.delete(s)

    db.delete(template)
    db.commit()
    return {"status": "ok"}
