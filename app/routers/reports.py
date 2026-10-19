"""Annual KPI appraisal reports (PDF download)."""

from fastapi import APIRouter, Depends, HTTPException, Query, status
from fastapi.responses import StreamingResponse
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.deps import get_current_user
from app.database import get_db
from app.models.user import User, UserRole
from app.services import annual_report as annual_report_service

router = APIRouter(prefix="/api/reports", tags=["reports"])


@router.get("/annual/{employee_id}")
def annual_report_pdf(
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
        if not user.department_id or employee.department_id != user.department_id:
            raise HTTPException(status.HTTP_403_FORBIDDEN, "Not allowed.")

    report = annual_report_service.build_annual_report(
        db, user.tenant_id, employee_id, year
    )
    pdf_bytes = annual_report_service.render_pdf(report)
    filename = f"kpi-annual-{report.employee_name}-{year}.pdf".replace(" ", "_")
    return StreamingResponse(
        iter([pdf_bytes]),
        media_type="application/pdf",
        headers={"Content-Disposition": f'attachment; filename="{filename}"'},
    )
