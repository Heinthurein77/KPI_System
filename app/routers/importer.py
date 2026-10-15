"""Bulk import endpoints — department admins only.

- GET  /api/admin/import/template — blank .xlsx template with examples.
- POST /api/admin/import/upload?dry_run=true|false — validate (dry run) or
  validate + execute a bulk import of employees + their KPIs.

Only DEPT_ADMIN may use these endpoints (tenant admins create departments and
dept admin accounts manually). Every imported user becomes an EMPLOYEE in the
importing admin's own department.
"""

from fastapi import APIRouter, Depends, File, HTTPException, Request, UploadFile
from fastapi.responses import StreamingResponse
from sqlalchemy.orm import Session

from app.core.deps import get_current_user, require_dept_admin
from app.core.rate_limit import UPLOAD_RATE_LIMIT, limiter
from app.database import get_db
from app.models.user import User, UserRole
from app.services import bulk_import_service

router = APIRouter(prefix="/api/admin/import", tags=["import"])

XLSX_MEDIA_TYPE = "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
MAX_FILE_BYTES = 10 * 1024 * 1024


def _require_importer(user: User) -> User:
    """Bulk import is a dept-admin-only tool, scoped to their own department."""
    if user.role != UserRole.DEPT_ADMIN:
        raise HTTPException(403, "Only department admins can use bulk import.")
    if user.department_id is None:
        raise HTTPException(400, "Your account has no department assigned.")
    return user


def _errors_payload(parsed) -> list[dict]:
    return [
        {"sheet": e.sheet, "row": e.row, "message": e.message} for e in parsed.errors
    ]


@router.get("/template")
def download_import_template(
    db: Session = Depends(get_db), user: User = Depends(require_dept_admin)
):
    """Download the blank bulk-import workbook (with example rows)."""
    _require_importer(user)
    buf = bulk_import_service.build_import_template()
    return StreamingResponse(
        buf,
        media_type=XLSX_MEDIA_TYPE,
        headers={"Content-Disposition": 'attachment; filename="bulk-import-template.xlsx"'},
    )


@router.post("/upload")
@limiter.limit(UPLOAD_RATE_LIMIT)
async def upload_import(
    request: Request,
    dry_run: bool = True,
    file: UploadFile = File(...),
    db: Session = Depends(get_db),
    user: User = Depends(require_dept_admin),
):
    """Validate (dry_run=true) or validate + execute (dry_run=false) a bulk import."""
    _require_importer(user)
    filename = (file.filename or "").lower()
    if not filename.endswith(".xlsx"):
        raise HTTPException(400, "Please upload an Excel (.xlsx) file.")
    contents = await file.read()
    if not contents:
        raise HTTPException(400, "The uploaded file is empty.")
    if len(contents) > MAX_FILE_BYTES:
        raise HTTPException(400, "File too large (max 10 MB).")

    parsed = bulk_import_service.parse_import_file(contents, db, user.tenant_id)

    if dry_run:
        return {
            "dry_run": True,
            "ok": parsed.ok,
            "users_found": len(parsed.users),
            "kpis_found": len(parsed.kpis),
            "errors": _errors_payload(parsed),
        }
    if not parsed.ok:
        raise HTTPException(422, detail={"errors": _errors_payload(parsed)})

    result = bulk_import_service.execute_import(db, user, parsed)
    return {
        "dry_run": False,
        "ok": True,
        "users_created": result.users_created,
        "kpis_created": result.kpis_created,
        "year": result.year,
        "period": result.period,
        "initial_password": bulk_import_service.DEFAULT_PASSWORD,
        "errors": [],
    }
