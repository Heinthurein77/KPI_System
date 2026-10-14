"""Bulk import service — dept admin onboards employees + their KPIs from one Excel file.

This module is additive-only: it never modifies any existing submission,
template, approval flow, formula, or routing logic. It *reuses* the existing
public services:

- ``kpi_service.create_custom_employee_kpi`` for each KPI row (so templates
  are created exactly as if the dept admin added them one by one, including
  the approved-month shift and duplicate-name guard).
- ``security.hash_password`` for the initial password.

File format (two sheets)
------------------------
Users:  name | email
KPIs:   email | metric | target | weight

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
from app.models.user import User, UserRole
from app.services import kpi_service

SHEET_USERS = "Users"
SHEET_KPIS = "KPIs"

USER_HEADERS = ("name", "email")
KPI_HEADERS = ("email", "metric", "target", "weight")

#: Initial password for every imported user (they change it themselves).
DEFAULT_PASSWORD = "password"

EMAIL_RE = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+$")

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

    sheets = {ws.title: ws for ws in wb.worksheets}
    if SHEET_USERS not in sheets:
        parsed.errors.append(ImportRowError(sheet="-", row=0, message=f'Missing sheet "{SHEET_USERS}".'))
    if SHEET_KPIS not in sheets:
        parsed.errors.append(ImportRowError(sheet="-", row=0, message=f'Missing sheet "{SHEET_KPIS}".'))
    if parsed.errors:
        return parsed

    # ---- Users sheet ----
    users_ws = sheets[SHEET_USERS]
    rows = list(users_ws.iter_rows(values_only=True))
    if not rows:
        parsed.errors.append(ImportRowError(sheet=SHEET_USERS, row=0, message="Users sheet is empty."))
        return parsed
    header = [_norm_header(v) for v in rows[0]]
    if header[:2] != list(USER_HEADERS):
        parsed.errors.append(
            ImportRowError(
                sheet=SHEET_USERS, row=1,
                message=f'Header must be "name | email", got {list(rows[0][:2])}.',
            )
        )
        return parsed

    seen_emails: set[str] = set()
    existing_emails = {
        e.lower()
        for e in db.scalars(select(User.email).where(User.tenant_id == tenant_id)).all()
    }
    for idx, row in enumerate(rows[1:], start=2):
        if _is_empty_row(row[:2]):
            continue
        name = str(row[0] or "").strip()
        email = str(row[1] or "").strip().lower()
        if not name:
            parsed.errors.append(ImportRowError(SHEET_USERS, idx, "Name is required."))
            continue
        if not email or not EMAIL_RE.match(email):
            parsed.errors.append(ImportRowError(SHEET_USERS, idx, f'Invalid email "{row[1]}".'))
            continue
        if email in seen_emails:
            parsed.errors.append(ImportRowError(SHEET_USERS, idx, f'Duplicate email "{email}" in the file.'))
            continue
        if email in existing_emails:
            parsed.errors.append(
                ImportRowError(SHEET_USERS, idx, f'Email "{email}" already exists in the system.')
            )
            continue
        seen_emails.add(email)
        parsed.users.append(ImportUser(name=name, email=email))

    if not parsed.users:
        parsed.errors.append(ImportRowError(SHEET_USERS, 0, "No valid user rows found."))

    # ---- KPIs sheet ----
    kpis_ws = sheets[SHEET_KPIS]
    rows = list(kpis_ws.iter_rows(values_only=True))
    if not rows:
        parsed.errors.append(ImportRowError(sheet=SHEET_KPIS, row=0, message="KPIs sheet is empty."))
        return parsed
    header = [_norm_header(v) for v in rows[0]]
    if header[:4] != list(KPI_HEADERS):
        parsed.errors.append(
            ImportRowError(
                sheet=SHEET_KPIS, row=1,
                message=f'Header must be "email | metric | target | weight", got {list(rows[0][:4])}.',
            )
        )
        return parsed

    seen_metrics: set[tuple[str, str]] = set()
    for idx, row in enumerate(rows[1:], start=2):
        if _is_empty_row(row[:4]):
            continue
        email = str(row[0] or "").strip().lower()
        metric = str(row[1] or "").strip()
        if email not in seen_emails:
            parsed.errors.append(
                ImportRowError(SHEET_KPIS, idx, f'Email "{row[0]}" is not in the Users sheet.')
            )
            continue
        if not metric:
            parsed.errors.append(ImportRowError(SHEET_KPIS, idx, "Metric name is required."))
            continue
        try:
            target = _as_number(row[2], "Target")
            weight = _as_number(row[3], "Weight")
        except ValueError as exc:
            parsed.errors.append(ImportRowError(SHEET_KPIS, idx, str(exc)))
            continue
        if target <= 0:
            parsed.errors.append(ImportRowError(SHEET_KPIS, idx, "Target must be greater than 0."))
            continue
        if weight <= 0:
            parsed.errors.append(ImportRowError(SHEET_KPIS, idx, "Weight must be greater than 0."))
            continue
        if (email, metric.lower()) in seen_metrics:
            parsed.errors.append(
                ImportRowError(SHEET_KPIS, idx, f'Duplicate metric "{metric}" for {email}.')
            )
            continue
        seen_metrics.add((email, metric.lower()))
        parsed.kpis.append(ImportKpi(email=email, metric_name=metric, target=target, weight=weight))

    if not parsed.kpis:
        parsed.errors.append(ImportRowError(SHEET_KPIS, 0, "No valid KPI rows found."))

    # ---- Per-employee weight must total 100 ----
    weights: dict[str, float] = {}
    for kpi in parsed.kpis:
        weights[kpi.email] = weights.get(kpi.email, 0.0) + kpi.weight
    for email, total in weights.items():
        if abs(total - 100.0) > 0.01:
            parsed.errors.append(
                ImportRowError(
                    SHEET_KPIS, 0,
                    f'Weights for {email} total {round(total, 2)}, must total 100.',
                )
            )

    return parsed


def execute_import(db: Session, dept_admin: User, parsed: ParsedImport) -> ImportResult:
    """Create users + recurring KPI templates + draft submissions.

    ``parsed`` must have no errors. Users are created first (one commit);
    each KPI row then goes through ``kpi_service.create_custom_employee_kpi``
    exactly as a manual assignment would.
    """
    if not parsed.ok:
        raise ValueError("Cannot execute an import that has validation errors.")
    if dept_admin.department_id is None:
        raise ValueError("Importing admin has no department.")

    today = date.today()
    year, period = today.year, MONTH_NAMES[today.month - 1]

    password_hash = hash_password(DEFAULT_PASSWORD)
    created_users: dict[str, User] = {}
    for imp_user in parsed.users:
        user = User(
            tenant_id=dept_admin.tenant_id,
            name=imp_user.name,
            email=imp_user.email,
            password_hash=password_hash,
            role=UserRole.EMPLOYEE,
            department_id=dept_admin.department_id,
            is_active=True,
        )
        db.add(user)
        created_users[imp_user.email] = user
    db.commit()
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
        )
        kpis_created += 1

    return ImportResult(
        users_created=len(created_users),
        kpis_created=kpis_created,
        year=year,
        period=period,
    )


def build_import_template() -> BytesIO:
    """Blank import workbook with example rows and instructions."""
    wb = Workbook()

    users_ws = wb.active
    users_ws.title = SHEET_USERS
    users_ws.append(["name", "email"])
    users_ws.append(["Aung Aung", "aung.aung@example.com"])
    users_ws.append(["May May", "may.may@example.com"])
    users_ws.column_dimensions["A"].width = 24
    users_ws.column_dimensions["B"].width = 32

    kpis_ws = wb.create_sheet(SHEET_KPIS)
    kpis_ws.append(["email", "metric", "target", "weight"])
    kpis_ws.append(["aung.aung@example.com", "Monthly Sales", 100, 60])
    kpis_ws.append(["aung.aung@example.com", "Customer Visits", 40, 40])
    kpis_ws.append(["may.may@example.com", "Monthly Sales", 100, 100])
    for col, width in zip("ABCD", (28, 28, 12, 12)):
        kpis_ws.column_dimensions[col].width = width

    help_ws = wb.create_sheet("Read me")
    help_ws.column_dimensions["A"].width = 110
    for line in [
        "Bulk import instructions (for Department Admins)",
        "",
        "1. Users sheet: one row per employee (name, email). Everyone is added as an",
        "   Employee in YOUR department with the initial password: password",
        "   (ask them to change it on first login via the Change Password page).",
        "2. KPIs sheet: one row per KPI. The email must match a row in the Users sheet.",
        "   Each employee's weights must total exactly 100.",
        "3. Every KPI becomes a recurring monthly template starting this month, and a",
        "   draft submission is created right away so employees can start filling scores.",
        "4. If any row has an error, nothing is imported — fix the highlighted rows and",
        "   upload again. Use the Validate button to check before importing.",
        "5. Delete the example rows before uploading your real data.",
    ]:
        help_ws.append([line])

    out = BytesIO()
    wb.save(out)
    out.seek(0)
    return out
