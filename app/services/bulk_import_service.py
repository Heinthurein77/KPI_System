"""Bulk import service — dept admin onboards employees + their KPIs from one Excel file.

This module is additive-only: it never modifies any existing submission,
template, approval flow, formula, or routing logic. It *reuses* the existing
public services:

- ``kpi_service.create_custom_employee_kpi`` for each KPI row (so templates
  are created exactly as if the dept admin added them one by one, including
  the approved-month shift and duplicate-name guard).
- ``security.hash_password`` for the initial password.

File format (single sheet)
---------------------------
Import:  name | email | position | metric | target | weight

One row per KPI; user info repeats on each of their rows. The email is the
user's identity — the name and position must be identical on all rows with
the same email. Position is optional.

- Every imported user becomes an EMPLOYEE in the importing dept admin's own
  department, with the initial password ``"password"`` (they change it
  themselves afterwards via the Change Password page).
- Every KPI row becomes a *recurring* template locked to the current month,
  plus a draft submission for the current period — later months are carried
  forward by the existing recurrence mechanism.

Validation is all-or-nothing: ``parse_import_file`` checks every row first and
collects per-row errors; ``execute_import`` only runs when there are none.

Public surface
--------------
parse_import_file(contents) -> ParsedImport
execute_import(db, dept_admin, parsed) -> ImportResult
build_import_template() -> BytesIO  (blank template with examples)
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from datetime import date
from io import BytesIO

from openpyxl import Workbook, load_workbook
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.security import hash_password
from app.models.tenant import Tenant
from app.models.user import User, UserRole
from app.services import audit_service, email_service, kpi_service

SHEET_IMPORT = "Import"

IMPORT_HEADERS = ("name", "email", "position", "metric", "target", "weight")

#: Initial password for every imported user (they change it themselves).
DEFAULT_PASSWORD = "password"

EMAIL_RE = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+$")

# Zip-bomb / abuse guard: reject workbooks with more data rows than this.
MAX_IMPORT_ROWS = 5000

MONTH_NAMES = (
    "January", "February", "March", "April", "May", "June",
    "July", "August", "September", "October", "November", "December",
)


@dataclass
class ImportRowError:
    sheet: str
    row: int
    message: str


@dataclass
class ImportUser:
    name: str
    email: str
    position: str | None


@dataclass
class ImportKpi:
    email: str
    metric_name: str
    target: float
    weight: float


@dataclass
class ParsedImport:
    users: list[ImportUser] = field(default_factory=list)
    kpis: list[ImportKpi] = field(default_factory=list)
    errors: list[ImportRowError] = field(default_factory=list)

    @property
    def ok(self) -> bool:
        return not self.errors


@dataclass
class ImportResult:
    users_created: int
    kpis_created: int
    year: int
    period: str


def _norm_header(value) -> str:
    return str(value or "").strip().lower()


def _is_empty_row(values) -> bool:
    return all(v is None or str(v).strip() == "" for v in values)


def _as_number(value, what: str, err: ImportRowError | None = None):
    """Coerce an Excel cell to float, raising ValueError with a clear message."""
    if isinstance(value, (int, float)) and not isinstance(value, bool):
        return float(value)
    try:
        return float(str(value).strip())
    except (TypeError, ValueError):
        raise ValueError(f"{what} must be a number, got {value!r}")


def parse_import_file(contents: bytes, db: Session, tenant_id: int) -> ParsedImport:
    """Parse and fully validate an uploaded workbook. No database writes."""
    parsed = ParsedImport()
    try:
        wb = load_workbook(BytesIO(contents), read_only=True, data_only=True)
    except Exception:
        parsed.errors.append(ImportRowError(sheet="-", row=0, message="Not a valid Excel (.xlsx) file."))
        return parsed

    if not wb.worksheets:
        parsed.errors.append(ImportRowError(sheet="-", row=0, message="The workbook has no sheets."))
        return parsed
    # Prefer the sheet named "Import"; otherwise use the first sheet.
    ws = wb[SHEET_IMPORT] if SHEET_IMPORT in wb.sheetnames else wb.worksheets[0]
    sheet = ws.title

    rows = list(ws.iter_rows(values_only=True))
    if not rows:
        parsed.errors.append(ImportRowError(sheet=sheet, row=0, message="The sheet is empty."))
        return parsed
    # Zip-bomb guard: a small .xlsx can expand into millions of rows. Reject
    # before validating so a crafted file can't exhaust worker memory.
    if len(rows) > MAX_IMPORT_ROWS:
        parsed.errors.append(
            ImportRowError(
                sheet=sheet, row=0,
                message=f"Too many rows (max {MAX_IMPORT_ROWS}). Split the file and import in parts.",
            )
        )
        return parsed
    header = [_norm_header(v) for v in rows[0]]
    if header[:6] != list(IMPORT_HEADERS):
        parsed.errors.append(
            ImportRowError(
                sheet=sheet, row=1,
                message=f'Header must be "name | email | position | metric | target | weight", got {list(rows[0][:6])}.',
            )
        )
        return parsed

    existing_emails = {
        e.lower()
        for e in db.scalars(select(User.email).where(User.tenant_id == tenant_id)).all()
    }

    users_by_email: dict[str, ImportUser] = {}
    seen_metrics: set[tuple[str, str]] = set()
    for idx, row in enumerate(rows[1:], start=2):
        if _is_empty_row(row[:6]):
            continue
        name = str(row[0] or "").strip()
        email = str(row[1] or "").strip().lower()
        position = str(row[2] or "").strip() or None
        metric = str(row[3] or "").strip()

        if not name:
            parsed.errors.append(ImportRowError(sheet, idx, "Name is required."))
            continue
        if not email or not EMAIL_RE.match(email):
            parsed.errors.append(ImportRowError(sheet, idx, f'Invalid email "{row[1]}".'))
            continue
        if email in existing_emails:
            parsed.errors.append(
                ImportRowError(sheet, idx, f'Email "{email}" already exists in the system.')
            )
            continue
        if email in users_by_email:
            prev = users_by_email[email]
            if prev.name != name or prev.position != position:
                parsed.errors.append(
                    ImportRowError(
                        sheet, idx,
                        f'Email "{email}" has inconsistent name/position across rows.',
                    )
                )
                continue
        if not metric:
            parsed.errors.append(ImportRowError(sheet, idx, "Metric name is required."))
            continue
        try:
            target = _as_number(row[4], "Target")
            weight = _as_number(row[5], "Weight")
        except ValueError as exc:
            parsed.errors.append(ImportRowError(sheet, idx, str(exc)))
            continue
        if target <= 0:
            parsed.errors.append(ImportRowError(sheet, idx, "Target must be greater than 0."))
            continue
        if weight <= 0:
            parsed.errors.append(ImportRowError(sheet, idx, "Weight must be greater than 0."))
            continue
        if (email, metric.lower()) in seen_metrics:
            parsed.errors.append(
                ImportRowError(sheet, idx, f'Duplicate metric "{metric}" for {email}.')
            )
            continue
        users_by_email.setdefault(email, ImportUser(name=name, email=email, position=position))
        seen_metrics.add((email, metric.lower()))
        parsed.kpis.append(ImportKpi(email=email, metric_name=metric, target=target, weight=weight))

    parsed.users = list(users_by_email.values())

    if not parsed.users:
        parsed.errors.append(ImportRowError(sheet, 0, "No valid rows found."))
        return parsed

    # ---- Per-employee weight must total 100 ----
    weights: dict[str, float] = {}
    for kpi in parsed.kpis:
        weights[kpi.email] = weights.get(kpi.email, 0.0) + kpi.weight
    for email, total in weights.items():
        if abs(total - 100.0) > 0.01:
            parsed.errors.append(
                ImportRowError(
                    sheet, 0,
                    f'Weights for {email} total {round(total, 2)}, must total 100.',
                )
            )

    return parsed


def execute_import(db: Session, dept_admin: User, parsed: ParsedImport) -> ImportResult:
    """Create users + recurring KPI templates + draft submissions.

    ``parsed`` must have no errors. The whole import runs in a single
    transaction: users, templates, submissions and the audit entry are
    committed together at the end, so a mid-import failure rolls everything
    back instead of leaving half-created users. Welcome emails go out only
    after the commit succeeds.
    """
    if not parsed.ok:
        raise ValueError("Cannot execute an import that has validation errors.")
    if dept_admin.department_id is None:
        raise ValueError("Importing admin has no department.")

    today = date.today()
    year, period = today.year, MONTH_NAMES[today.month - 1]

    password_hash = hash_password(DEFAULT_PASSWORD)
    try:
        created_users: dict[str, User] = {}
        for imp_user in parsed.users:
            user = User(
                tenant_id=dept_admin.tenant_id,
                name=imp_user.name,
                email=imp_user.email,
                password_hash=password_hash,
                role=UserRole.EMPLOYEE,
                department_id=dept_admin.department_id,
                position=imp_user.position,
                is_active=True,
                must_change_password=True,
            )
            db.add(user)
            created_users[imp_user.email] = user
        db.flush()
        for user in created_users.values():
            db.refresh(user)

        kpis_created = 0
        for kpi in parsed.kpis:
            employee = created_users[kpi.email]
            kpi_service.create_custom_employee_kpi(
                db,
                dept_admin,
                employee,
                kpi.metric_name,
                kpi.target,
                kpi.weight,
                year,
                period,
                is_recurring=True,
                commit=False,
            )
            kpis_created += 1

        # Batch-level audit trail: who imported whom, in one entry.
        audit_service.log(
            db,
            dept_admin,
            "bulk_import",
            "bulk_import",
            0,
            f"{len(created_users)} users, {kpis_created} KPIs",
            f"Bulk import by {dept_admin.name}: "
            + ", ".join(sorted(created_users)),
            department_id=dept_admin.department_id,
        )
        db.commit()
    except Exception:
        db.rollback()
        raise

    # Welcome emails with login details (best-effort; skipped when SMTP unconfigured).
    tenant = db.get(Tenant, dept_admin.tenant_id)
    org_name = tenant.name if tenant else None
    for imp_user in parsed.users:
        email_service.send_welcome_email(imp_user.email, imp_user.name, DEFAULT_PASSWORD, org_name=org_name)

    return ImportResult(
        users_created=len(created_users),
        kpis_created=kpis_created,
        year=year,
        period=period,
    )


def build_import_template() -> BytesIO:
    """Blank import workbook with example rows and instructions."""
    wb = Workbook()

    ws = wb.active
    ws.title = SHEET_IMPORT
    ws.append(["name", "email", "position", "metric", "target", "weight"])
    ws.append(["Aung Aung", "aung.aung@example.com", "Sales Executive", "Monthly Sales", 100, 60])
    ws.append(["Aung Aung", "aung.aung@example.com", "Sales Executive", "Customer Visits", 40, 40])
    ws.append(["May May", "may.may@example.com", "Sales Executive", "Monthly Sales", 100, 100])
    for col, width in zip("ABCDEF", (20, 30, 20, 24, 12, 12)):
        ws.column_dimensions[col].width = width

    help_ws = wb.create_sheet("Read me")
    help_ws.column_dimensions["A"].width = 110
    for line in [
        "Bulk import instructions (for Department Admins)",
        "",
        "1. One row per KPI. If an employee has 2 KPIs, write 2 rows with the same",
        "   name, email and position (the email identifies the person - name and",
        "   position must match on all their rows). Position is optional.",
        "2. Everyone is added as an Employee in YOUR department with the initial",
        "   password: password (ask them to change it on first login via the",
        "   Change Password page).",
        "3. Each employee's weights must total exactly 100.",
        "4. Every KPI becomes a recurring monthly template starting this month, and a",
        "   draft submission is created right away so employees can start filling scores.",
        "5. If any row has an error, nothing is imported - fix the rows and upload",
        "   again. Use the Validate button to check before importing.",
        "6. Delete the example rows before uploading your real data.",
    ]:
        help_ws.append([line])

    out = BytesIO()
    wb.save(out)
    out.seek(0)
    return out
