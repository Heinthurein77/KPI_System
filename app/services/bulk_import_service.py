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
  department, with a unique random initial password per user (never a
  shared default, never returned by the API). At the user's explicit
  request, each user's welcome email carries their initial password and
  the login link (built from APP_BASE_URL; omitted when unset) — the
  account still forces a password change on first login via
  ``must_change_password``, so the emailed password is only usable until
  the real user first signs in. If a welcome email fails to send, the
  import still stands and the failure is reported per user in
  ``ImportResult.email_failures``.
- Every KPI row becomes a *recurring* template locked to the chosen
  period, plus a draft submission for that period — later months are
  carried forward by the existing recurrence mechanism. The period
  defaults to the current month; during the grace week (days 1..7) the
  caller may instead target the previous month. The target period is
  enforced by the same M6 admin-creation gate as single custom-KPI
  creation (``create_custom_employee_kpi``) — current month before day 8
  and past-grace months are rejected there, not here.

Validation is all-or-nothing: ``parse_import_file`` checks every row first and
collects per-row errors; ``execute_import`` only runs when there are none.

Public surface
--------------
parse_import_file(contents) -> ParsedImport
execute_import(db, dept_admin, parsed, year=None, period=None, today=None) -> ImportResult
build_import_template() -> BytesIO  (blank template with examples)
"""

from __future__ import annotations

import re
import secrets
from dataclasses import dataclass, field
from datetime import date
from io import BytesIO

from openpyxl import Workbook, load_workbook
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.months import MONTH_NAMES
from app.core.security import hash_password
from app.core.timezone import today as app_today
from app.models.tenant import Tenant
from app.models.user import User, UserRole
from app.services import audit_service, email_service, kpi_service

SHEET_IMPORT = "Import"

IMPORT_HEADERS = ("name", "email", "position", "metric", "target", "weight")

# DB column limits (users.name/position String(120), users.email
# String(255)). Postgres rejects longer values with
# StringDataRightTruncation at INSERT time — i.e. a generic 500 at
# Confirm Import, after Validate already passed. So length is checked
# here, per row, where the user can see and fix it. Metric names have
# no cap: kpi_templates.metric_name is TEXT since migration 0018, so
# long names (title plus description in one cell) import as written.
MAX_NAME_CHARS = 120
MAX_POSITION_CHARS = 120
MAX_EMAIL_CHARS = 255

#: Per-user random initial passwords (see execute_import). There is no
#: shared default anymore: anyone who knew an imported employee's email
#: could previously log in with the documented "password" and take the
#: account before its real owner ever logged in.
def generate_initial_password() -> str:
    return secrets.token_urlsafe(12)

EMAIL_RE = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+$")

# Zip-bomb / abuse guard: reject workbooks with more data rows than this.
MAX_IMPORT_ROWS = 5000


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
    # Welcome emails that did NOT go out (delivery failure, or SMTP
    # unconfigured). The users themselves are still created — email
    # failure never rolls back an import — but the admin must know who
    # never received their initial password.
    email_failures: list[str] = field(default_factory=list)


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

    # Stream rows instead of list()ing them: a small .xlsx can expand into
    # millions of rows (zip bomb), and the row cap must trip before worker
    # memory is exhausted, not after the whole sheet was materialised.
    row_iter = iter(ws.iter_rows(values_only=True))
    try:
        header_row = next(row_iter)
    except StopIteration:
        parsed.errors.append(ImportRowError(sheet=sheet, row=0, message="The sheet is empty."))
        return parsed
    header = [_norm_header(v) for v in header_row]
    if header[:6] != list(IMPORT_HEADERS):
        parsed.errors.append(
            ImportRowError(
                sheet=sheet, row=1,
                message=f'Header must be "name | email | position | metric | target | weight", got {list(header_row[:6])}.',
            )
        )
        return parsed

    existing_emails = {
        e.lower()
        for e in db.scalars(select(User.email).where(User.tenant_id == tenant_id)).all()
    }

    users_by_email: dict[str, ImportUser] = {}
    seen_metrics: set[tuple[str, str]] = set()
    row_count = 1  # header row already consumed
    for idx, row in enumerate(row_iter, start=2):
        row_count += 1
        if row_count > MAX_IMPORT_ROWS:
            # Zip-bomb guard: reject before validating so a crafted file
            # can't exhaust worker memory.
            return ParsedImport(errors=[
                ImportRowError(
                    sheet=sheet, row=0,
                    message=f"Too many rows (max {MAX_IMPORT_ROWS}). Split the file and import in parts.",
                )
            ])
        if _is_empty_row(row[:6]):
            continue
        name = str(row[0] or "").strip()
        email = str(row[1] or "").strip().lower()
        position = str(row[2] or "").strip() or None
        metric = str(row[3] or "").strip()

        if not name:
            parsed.errors.append(ImportRowError(sheet, idx, "Name is required."))
            continue
        if len(name) > MAX_NAME_CHARS:
            parsed.errors.append(ImportRowError(
                sheet, idx,
                f"Name is too long ({len(name)} characters, max {MAX_NAME_CHARS})."))
            continue
        if not email or not EMAIL_RE.match(email):
            parsed.errors.append(ImportRowError(sheet, idx, f'Invalid email "{row[1]}".'))
            continue
        if len(email) > MAX_EMAIL_CHARS:
            parsed.errors.append(ImportRowError(
                sheet, idx,
                f"Email is too long ({len(email)} characters, max {MAX_EMAIL_CHARS})."))
            continue
        if position and len(position) > MAX_POSITION_CHARS:
            parsed.errors.append(ImportRowError(
                sheet, idx,
                f"Position is too long ({len(position)} characters, max {MAX_POSITION_CHARS})."))
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


def execute_import(
    db: Session,
    dept_admin: User,
    parsed: ParsedImport,
    year: int | None = None,
    period: str | None = None,
    today: date | None = None,
) -> ImportResult:
    """Create users + recurring KPI templates + draft submissions.

    ``parsed`` must have no errors. The whole import runs in a single
    transaction: users, templates, submissions and the audit entry are
    committed together at the end, so a mid-import failure rolls everything
    back instead of leaving half-created users. Welcome emails go out only
    after the commit succeeds.

    ``year``/``period`` select the target period (``period`` is a month
    name, e.g. "October"). When omitted, the import targets the current
    month. When given, the period is enforced by the same M6 admin
    creation gate that ``create_custom_employee_kpi`` applies: the
    previous month is allowed during days 1..grace_days, the current
    month is blocked before day grace_days + 1, and past-grace months
    are blocked — the gate raises the user-facing error.
    """
    if not parsed.ok:
        raise ValueError("Cannot execute an import that has validation errors.")
    if dept_admin.department_id is None:
        raise ValueError("Importing admin has no department.")
    if (year is None) != (period is None):
        raise ValueError("year and period must be given together.")
    if period is not None and period not in MONTH_NAMES:
        raise ValueError(f"Unknown period: {period!r}.")

    today = today or app_today()
    if year is None:
        year, period = today.year, MONTH_NAMES[today.month - 1]

    try:
        created_users: dict[str, User] = {}
        # Plaintext initial passwords, kept in memory only for the welcome
        # emails below: hashed immediately, never stored, never returned.
        initial_passwords: dict[str, str] = {}
        for imp_user in parsed.users:
            initial_password = generate_initial_password()
            initial_passwords[imp_user.email] = initial_password
            user = User(
                tenant_id=dept_admin.tenant_id,
                name=imp_user.name,
                email=imp_user.email,
                # Unique random password per user — never a shared default,
                # and never returned to the caller (see module docstring).
                password_hash=hash_password(initial_password),
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
                # The target period (current month by default, previous
                # month when the caller selects it during grace week)
                # passes through the same M6 admin creation gate inside
                # create_custom_employee_kpi — no second gate here.
                today=today,
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

    # Welcome emails, sent synchronously AFTER the commit so a delivery
    # failure can be reported per user without ever rolling back the
    # import. Each email carries that user's initial password and the
    # login link (see email_service.send_welcome_email_sync).
    tenant = db.get(Tenant, dept_admin.tenant_id)
    org_name = tenant.name if tenant else None
    email_failures: list[str] = []
    for imp_user in parsed.users:
        sent = email_service.send_welcome_email_sync(
            imp_user.email,
            imp_user.name,
            initial_passwords[imp_user.email],
            org_name=org_name,
        )
        if not sent:
            email_failures.append(imp_user.email)

    return ImportResult(
        users_created=len(created_users),
        kpis_created=kpis_created,
        year=year,
        period=period,
        email_failures=email_failures,
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
        "2. Everyone is added as an Employee in YOUR department with a unique",
        "   random initial password, emailed to them together with the login",
        "   link. They must change it on first login.",
        "3. Each employee's weights must total exactly 100.",
        "4. Every KPI becomes a recurring monthly template for the period you",
        "   select (previous month during days 1–7, current month from day 8),",
        "   and a draft submission is created right away so employees can",
        "   start filling scores.",
        "5. If any row has an error, nothing is imported - fix the rows and upload",
        "   again. Use the Validate button to check before importing.",
        "6. Delete the example rows before uploading your real data.",
    ]:
        help_ws.append([line])

    out = BytesIO()
    wb.save(out)
    out.seek(0)
    return out
