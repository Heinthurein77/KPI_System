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

router = APIRouter(prefix="/api/reports", tags=["reports"])


def _safe_file_part(value: str) -> str:
    """Slugify a user-supplied name for a Content-Disposition filename —
    same rule as attendance._safe_file_part, with an 'employee' fallback
    (a person's file must not be mislabelled 'department'). Strips quotes,
    CR/LF and anything outside [a-z0-9-_], so a crafted employee name
    cannot inject headers."""
    slug = "".join(c if c.isalnum() or c in "-_" else "-" for c in value.lower().replace(" ", "-"))
    return slug.strip("-") or "employee"


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
        if not user.department_id or employee.department_id != user.department_id:
            raise HTTPException(status.HTTP_403_FORBIDDEN, "Not allowed.")

    report = annual_report_service.build_annual_report(
        db, user.tenant_id, employee_id, year
    )
    pdf_bytes = annual_report_service.render_pdf(report)
    filename = f"kpi-annual-{_safe_file_part(report.employee_name)}-{year}.pdf"
    return StreamingResponse(
        iter([pdf_bytes]),
        media_type="application/pdf",
        headers={"Content-Disposition": f'attachment; filename="{filename}"'},
    )
