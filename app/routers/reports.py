"""Annual KPI appraisal reports (PDF download)."""

from fastapi import APIRouter, Depends, HTTPException, Query, Request, status
from fastapi.responses import StreamingResponse
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.deps import get_current_user
from app.core.rate_limit import EXPORT_RATE_LIMIT, _authenticated_user_key, limiter
from app.database import get_db
from app.models.user import User, UserRole
from app.services import annual_report as annual_report_service
from app.services import audit_service
from app.services.excel_safe import safe_file_part as _safe_file_part

router = APIRouter(prefix="/api/reports", tags=["reports"])


@router.get("/annual/{employee_id}")
@limiter.limit(EXPORT_RATE_LIMIT, key_func=_authenticated_user_key)
def annual_report_pdf(
    request: Request,
    employee_id: int,
    year: int = Query(..., ge=2000, le=2100),
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
):
    """Download an employee's annual KPI appraisal as PDF.

    Tenant admins: any employee in their tenant.
    Dept admins: employees in their own department only.
    """
    if user.role not in (UserRole.TENANT_ADMIN, UserRole.DEPT_ADMIN):
        raise HTTPException(status.HTTP_403_FORBIDDEN, "Not allowed.")

    employee = db.scalar(
        select(User).where(User.id == employee_id, User.tenant_id == user.tenant_id)
    )
    if employee is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Employee not found.")

    if user.role == UserRole.DEPT_ADMIN:
        # S-07: department alone is not enough — the docstring says
        # employees only, so a peer dept-admin target is refused even
        # in the caller's own department. S-08: explicit None guard —
        # a NULL-department caller must not match a NULL-department
        # target (None == None).
        if (
            not user.department_id
            or employee.department_id != user.department_id
            or employee.role != UserRole.EMPLOYEE
        ):
            raise HTTPException(status.HTTP_403_FORBIDDEN, "Not allowed.")

    report = annual_report_service.build_annual_report(
        db, user.tenant_id, employee_id, year
    )
    pdf_bytes = annual_report_service.render_pdf(report)
    # S-09: a PII/score export leaves an accountability trail.
    audit_service.log(
        db, user, "annual_report_exported", "annual_report", employee.id,
        f"{report.employee_name} — {year}",
        f"Annual KPI PDF exported for {employee.name} ({year}).",
        department_id=employee.department_id,
        ip_address=request.client.host if request.client else None,
    )
    db.commit()
    filename = f"kpi-annual-{_safe_file_part(report.employee_name)}-{year}.pdf"
    return StreamingResponse(
        iter([pdf_bytes]),
        media_type="application/pdf",
        headers={"Content-Disposition": f'attachment; filename="{filename}"'},
    )
