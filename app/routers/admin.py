from fastapi import APIRouter, Depends, Form, HTTPException, Request
from fastapi.responses import RedirectResponse
from fastapi.templating import Jinja2Templates
from sqlalchemy import select
from sqlalchemy.orm import Session

from urllib.parse import quote

from app.core.deps import require_dept_admin, require_super_admin
from app.core.security import hash_password
from app.database import get_db
from app.models.department import Department
from app.models.kpi_submission import KPIStatus, KPISubmission
from app.models.kpi_template import KPITemplate
from app.models.user import User, UserRole
from app.routers.dashboard import MONTH_NAMES, current_month_period
from app.services import kpi_service

router = APIRouter(prefix="/admin", tags=["admin"])
templates = Jinja2Templates(directory="app/templates")


# ---------- Departments (Super Admin only) ----------

@router.get("/departments")
def list_departments(
    request: Request,
    error: str | None = None,
    db: Session = Depends(get_db),
    user: User = Depends(require_super_admin),
):
    departments = db.scalars(select(Department)).all()
    return templates.TemplateResponse(
        request, "admin/departments.html", {"departments": departments, "user": user, "error": error}
    )


@router.post("/departments")
def create_department(name: str = Form(...), db: Session = Depends(get_db), user: User = Depends(require_super_admin)):
    db.add(Department(name=name.strip()))
    db.commit()
    return RedirectResponse(url="/admin/departments", status_code=303)


@router.post("/departments/{department_id}/delete")
def delete_department(department_id: int, db: Session = Depends(get_db), user: User = Depends(require_super_admin)):
    department = db.get(Department, department_id)
    if department is None:
        return RedirectResponse(url="/admin/departments", status_code=303)

    has_users = db.scalar(select(User).where(User.department_id == department_id).limit(1)) is not None
    has_templates = db.scalar(select(KPITemplate).where(KPITemplate.department_id == department_id).limit(1)) is not None
    if has_users or has_templates:
        error = quote(
            f'"{department.name}" still has users or KPI metrics assigned to it. '
            "Reassign or remove those first."
        )
        return RedirectResponse(url=f"/admin/departments?error={error}", status_code=303)

    db.delete(department)
    db.commit()
    return RedirectResponse(url="/admin/departments", status_code=303)


# ---------- Users ----------
# Super Admin manages everyone. Dept Admin may only view/create/toggle Employee
# accounts within their own department — strict data isolation from other departments.

@router.get("/users")
def list_users(
    request: Request,
    error: str | None = None,
    db: Session = Depends(get_db),
    user: User = Depends(require_dept_admin),
):
    if user.role == UserRole.DEPT_ADMIN:
        users = db.scalars(select(User).where(User.department_id == user.department_id)).all()
        departments = []
        roles = [UserRole.EMPLOYEE]
    else:
        users = db.scalars(select(User)).all()
        departments = db.scalars(select(Department)).all()
        roles = list(UserRole)

    return templates.TemplateResponse(
        request,
        "admin/users.html",
        {"users": users, "departments": departments, "roles": roles, "user": user, "error": error},
    )


@router.post("/users")
def create_user(
    name: str = Form(...),
    email: str = Form(...),
    password: str = Form(...),
    role: UserRole = Form(...),
    department_id: str | None = Form(None),
    db: Session = Depends(get_db),
    user: User = Depends(require_dept_admin),
):
    if user.role == UserRole.DEPT_ADMIN:
        if role != UserRole.EMPLOYEE:
            raise HTTPException(403, "Department Admins can only create Employee accounts.")
        resolved_department_id = user.department_id
    else:
        resolved_department_id = int(department_id) if department_id else None

    db.add(
        User(
            name=name.strip(),
            email=email.strip().lower(),
            password_hash=hash_password(password),
            role=role,
            department_id=resolved_department_id if role != UserRole.SUPER_ADMIN else None,
        )
    )
    db.commit()
    return RedirectResponse(url="/admin/users", status_code=303)


@router.post("/users/{user_id}/toggle-active")
def toggle_user_active(user_id: int, db: Session = Depends(get_db), user: User = Depends(require_dept_admin)):
    target = db.get(User, user_id)
    if target is None:
        return RedirectResponse(url="/admin/users", status_code=303)

    if user.role == UserRole.DEPT_ADMIN and (
        target.department_id != user.department_id or target.role != UserRole.EMPLOYEE
    ):
        raise HTTPException(403, "Cannot manage users outside your department.")

    target.is_active = not target.is_active
    db.commit()
    return RedirectResponse(url="/admin/users", status_code=303)


@router.get("/users/{user_id}/edit")
def edit_user_form(
    user_id: int,
    request: Request,
    error: str | None = None,
    db: Session = Depends(get_db),
    user: User = Depends(require_super_admin),
):
    target = db.get(User, user_id)
    if target is None:
        raise HTTPException(404, "User not found.")
    departments = db.scalars(select(Department)).all()
    return templates.TemplateResponse(
        request,
        "admin/user_edit.html",
        {"target": target, "departments": departments, "roles": list(UserRole), "user": user, "error": error},
    )


@router.post("/users/{user_id}/edit")
def edit_user(
    user_id: int,
    name: str = Form(...),
    email: str = Form(...),
    role: UserRole = Form(...),
    department_id: str | None = Form(None),
    password: str | None = Form(None),
    db: Session = Depends(get_db),
    user: User = Depends(require_super_admin),
):
    target = db.get(User, user_id)
    if target is None:
        raise HTTPException(404, "User not found.")

    if target.id == user.id and role != UserRole.SUPER_ADMIN:
        error = quote("You can't demote your own account while signed in as it.")
        return RedirectResponse(url=f"/admin/users/{user_id}/edit?error={error}", status_code=303)

    target.name = name.strip()
    target.email = email.strip().lower()
    target.role = role
    target.department_id = int(department_id) if department_id and role != UserRole.SUPER_ADMIN else None
    if password:
        target.password_hash = hash_password(password)
    db.commit()
    return RedirectResponse(url="/admin/users", status_code=303)


@router.post("/users/{user_id}/delete")
def delete_user(user_id: int, db: Session = Depends(get_db), user: User = Depends(require_super_admin)):
    """Super Admin can delete any user unconditionally — including their KPI history.
    The only guard left is against deleting your own currently-signed-in account,
    since that would lock you out with no way back in."""
    target = db.get(User, user_id)
    if target is None:
        return RedirectResponse(url="/admin/users", status_code=303)

    if target.id == user.id:
        error = quote("You can't delete your own account while signed in.")
        return RedirectResponse(url=f"/admin/users?error={error}", status_code=303)

    kpi_service.force_delete_user(db, target)
    return RedirectResponse(url="/admin/users", status_code=303)


# ---------- KPI Templates (metrics) ----------
# Super Admin manages all metrics, including company-wide ones (no department).
# Dept Admin may add/remove metrics scoped to their own department only; they can
# see company-wide metrics (since those apply to their team too) but not edit them.

@router.get("/templates")
def list_templates(
    request: Request,
    error: str | None = None,
    db: Session = Depends(get_db),
    user: User = Depends(require_dept_admin),
):
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

    return templates.TemplateResponse(
        request,
        "admin/templates.html",
        {
            "kpi_templates": kpi_templates,
            "departments": departments,
            "team": team,
            "months": MONTH_NAMES,
            "default_year": default_year,
            "default_period": default_period,
            "user": user,
            "error": error,
        },
    )


@router.post("/templates")
def create_template(
    metric_name: str = Form(...),
    target: float = Form(...),
    weight: float = Form(...),
    department_id: str | None = Form(None),
    db: Session = Depends(get_db),
    user: User = Depends(require_dept_admin),
):
    if user.role == UserRole.DEPT_ADMIN:
        resolved_department_id = user.department_id
    else:
        resolved_department_id = int(department_id) if department_id else None

    db.add(
        KPITemplate(
            metric_name=metric_name.strip(),
            target=target,
            weight=weight,
            department_id=resolved_department_id,
        )
    )
    db.commit()
    return RedirectResponse(url="/admin/templates", status_code=303)


@router.post("/templates/custom")
def create_custom_template(
    employee_id: int = Form(...),
    metric_name: str = Form(...),
    target: float = Form(...),
    weight: float = Form(...),
    year: int = Form(...),
    period: str = Form(...),
    db: Session = Depends(get_db),
    user: User = Depends(require_dept_admin),
):
    employee = db.get(User, employee_id)
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

    kpi_service.create_custom_employee_kpi(
        db, employee, metric_name.strip(), target, weight, year, period
    )
    return RedirectResponse(url="/admin/templates", status_code=303)


@router.post("/templates/{template_id}/delete")
def delete_template(template_id: int, db: Session = Depends(get_db), user: User = Depends(require_dept_admin)):
    """Super Admin can delete any KPI metric unconditionally — including its submission
    history — no department or history restrictions. Dept Admin keeps the protected
    path: own-department metrics only, and blocked once real KPI history exists."""
    template = db.get(KPITemplate, template_id)
    if template is None:
        return RedirectResponse(url="/admin/templates", status_code=303)

    if user.role == UserRole.SUPER_ADMIN:
        kpi_service.force_delete_template(db, template)
        return RedirectResponse(url="/admin/templates", status_code=303)

    if template.department_id != user.department_id:
        raise HTTPException(403, "Cannot manage metrics outside your department.")

    submissions = db.scalars(select(KPISubmission).where(KPISubmission.kpi_template_id == template_id)).all()
    blocking = [s for s in submissions if s.status != KPIStatus.DRAFT]
    if blocking:
        error = quote(
            f'"{template.metric_name}" already has KPI submissions recorded against it and can\'t be deleted, '
            "to keep employee KPI history intact."
        )
        return RedirectResponse(url=f"/admin/templates?error={error}", status_code=303)

    # Untouched draft rows aren't "history" yet — clean them up along with the template.
    for s in submissions:
        db.delete(s)

    db.delete(template)
    db.commit()
    return RedirectResponse(url="/admin/templates", status_code=303)
