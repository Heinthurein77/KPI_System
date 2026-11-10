"""Attendance API — standalone dashboard, fully decoupled from KPI.

Office-WiFi-only check-in/out: the server checks the request's client IP
against the tenant's whitelisted office public IPs. A browser cannot read
the WiFi SSID, so the IP is the gate. Until a tenant configures any office
IP, check-in is allowed with a warning (so the feature is testable); once
IPs are set, off-WiFi check-in/out is rejected with 403.

STANDALONE: the monthly attendance % is display-only. This module never
reads or writes KPI submissions or templates — there is no KPI linkage
of any kind, and no auto-calculation into KPI. Attendance data lives
only in the attendance tables.

Visibility mirrors the KPI role model:
- employee: own records only
- dept_admin: own department's records (+ own check-in)
- tenant_admin: whole tenant
- super_admin: platform only, no tenant attendance access
"""

from __future__ import annotations

import calendar
import json
import logging
from datetime import date as date_type, datetime, time as time_type, timedelta
from zoneinfo import ZoneInfo

from fastapi import APIRouter, Depends, HTTPException, Query, Request
from fastapi.responses import StreamingResponse
from sqlalchemy import select, update
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session, selectinload

from app.core.config import settings
from app.core.deps import (
    RoleChecker,
    get_current_user,
    require_tenant_admin,
    require_tenant_member,
)
from app.core.timezone import now as _now_local, today as _today_local
from app.core.proxy import trusted_client_ip
from app.core.rate_limit import (
    ATTENDANCE_RATE_LIMIT,
    EXPORT_RATE_LIMIT,
    REPORT_RATE_LIMIT,
    _authenticated_user_key,
    limiter,
)
from app.database import get_db
from app.models.attendance import AttendanceRecord, AttendanceSettings
from app.models.department import Department
from app.models.user import User, UserRole
from app.schemas.attendance import (
    AttendanceRecordOut,
    AttendanceSettingsOut,
    AttendanceSettingsUpdate,
    AttendanceSummaryOut,
    AttendanceTodayOut,
    LeaveCreate,
    LeaveUpdate,
)
from app.services import audit_service
from app.services.attendance_export_service import build_attendance_detail_report
from app.services.attendance_service import (
    DEFAULT_END,
    DEFAULT_START,
    DEFAULT_WORK_DAYS,
    day_status,
    late_minutes,
    midday,
    monthly_summary,
    parse_time,
)

router = APIRouter(prefix="/api/attendance", tags=["attendance"])

logger = logging.getLogger("kpi_system")

# Check-in/out authors: employees and dept admins (tenant admins never
# fill their own attendance record).
require_attendance_author = RoleChecker([UserRole.EMPLOYEE, UserRole.DEPT_ADMIN])


def _client_ip(request: Request) -> str:
    """Real client IP — X-Forwarded-For only when the socket peer is a
    trusted proxy (Caddy on the private Docker network); otherwise the
    socket peer. See app/core/proxy.py: a direct client forging the header
    gets its real IP, so the WiFi gate cannot be bypassed off-network."""
    return trusted_client_ip(request)


def _settings_dict(db: Session, tenant_id: int) -> dict:
    """Effective settings: stored row, or the defaults when not configured."""
    row = db.scalar(
        select(AttendanceSettings).where(AttendanceSettings.tenant_id == tenant_id)
    )
    if row is None:
        return {
            "work_days": list(DEFAULT_WORK_DAYS),
            "start_time": DEFAULT_START,
            "end_time": DEFAULT_END,
            "office_ips": [],
            "holidays": [],
        }
    return {
        "work_days": row.work_days,
        "start_time": row.start_time,
        "end_time": row.end_time,
        "office_ips": row.office_ips,
        "holidays": row.holidays,
    }


def _settings_out(cfg: dict) -> AttendanceSettingsOut:
    start = parse_time(cfg["start_time"])
    end = parse_time(cfg["end_time"])
    mid = midday(start, end)
    return AttendanceSettingsOut(
        work_days=cfg["work_days"],
        start_time=cfg["start_time"],
        end_time=cfg["end_time"],
        midday=f"{mid.hour:02d}:{mid.minute:02d}",
        office_ips=cfg["office_ips"],
        holidays=cfg["holidays"],
        wifi_enforced=bool(cfg["office_ips"]),
    )


def _ip_allowed(cfg: dict, ip: str) -> tuple[bool, bool]:
    """(allowed, enforced). No IPs configured yet -> allowed, not enforced."""
    ips = cfg["office_ips"]
    if not ips:
        return True, False
    return ip in ips, True


def _visible_user_ids(db: Session, user: User) -> list[int]:
    if user.role == UserRole.TENANT_ADMIN:
        stmt = select(User.id).where(
            User.tenant_id == user.tenant_id, User.is_active == True  # noqa: E712
        )
    elif user.role == UserRole.DEPT_ADMIN:
        if user.department_id is None:
            # A dept admin with no department sees only themself:
            # department_id == None would render as IS NULL and leak
            # every user with no department.
            return [user.id]
        stmt = select(User.id).where(
            User.tenant_id == user.tenant_id,
            User.department_id == user.department_id,
            User.is_active == True,  # noqa: E712
        )
    else:
        return [user.id]
    return list(db.scalars(stmt))


def _month_bounds(year: int, month: int) -> tuple[date_type, date_type]:
    """First and last day of the month (inclusive).

    Years outside 1-9999 are rejected with 400: date(0, …) / date(10000, …)
    raise ValueError, which the generic handler would otherwise turn into
    a 500. The bound is inclusive (not first-of-next-month) so December
    9999 is representable — there is no 10000-01-01 date object.
    """
    if not 1 <= year <= 9999:
        raise HTTPException(400, "year must be 1-9999")
    if not 1 <= month <= 12:
        raise HTTPException(400, "month must be 1-12")
    _, last_day = calendar.monthrange(year, month)
    return date_type(year, month, 1), date_type(year, month, last_day)


def _serialize(
    db: Session, record: AttendanceRecord, author: User | None = None
) -> AttendanceRecordOut:
    # late_minutes is recorded in the DB but deliberately never exposed
    # via the API (user decision: removed from UI + export as well).
    # M13: callers that already loaded the user pass it in, so a report
    # over N records does not do N extra db.get calls.
    if author is None:
        author = db.get(User, record.employee_id)
    return AttendanceRecordOut(
        id=record.id,
        employee_id=record.employee_id,
        employee_name=author.name if author else None,
        record_date=record.record_date,
        check_in=record.check_in.strftime("%H:%M") if record.check_in else None,
        check_out=record.check_out.strftime("%H:%M") if record.check_out else None,
        status=record.status,
    )


def _holidays_set(cfg: dict) -> set[date_type]:
    holidays = set()
    for raw in cfg["holidays"]:
        try:
            holidays.add(date_type.fromisoformat(raw))
        except ValueError:
            continue
    return holidays


def is_working_day(cfg: dict, day: date_type) -> bool:
    """The ONE working-day rule: weekday in work_days AND not a holiday.

    Shared by /today, check-in, check-out, leave marking/editing and the
    monthly working-days list, so a holiday can never be a working day in
    one place and a non-working day in another.
    """
    return day.weekday() in cfg["work_days"] and day not in _holidays_set(cfg)


def _working_days(
    db: Session, tenant_id: int, year: int, month: int, cfg: dict | None = None
) -> list[date_type]:
    # M13: pass cfg when the caller already loaded settings once.
    if cfg is None:
        cfg = _settings_dict(db, tenant_id)
    _, last_day = calendar.monthrange(year, month)
    return [
        date_type(year, month, day)
        for day in range(1, last_day + 1)
        if is_working_day(cfg, date_type(year, month, day))
    ]


def _summary_for(
    db: Session,
    tenant_id: int,
    employee: User,
    year: int,
    month: int,
    cfg: dict | None = None,
    records: list[AttendanceRecord] | None = None,
) -> AttendanceSummaryOut:
    # M13: cfg/records are optional pre-loaded inputs — get_report passes
    # one settings dict and one batched records query for all users, so a
    # report no longer costs a settings + records query per user.
    start, end = _month_bounds(year, month)
    if records is None:
        records = db.scalars(
            select(AttendanceRecord).where(
                AttendanceRecord.tenant_id == tenant_id,
                AttendanceRecord.employee_id == employee.id,
                AttendanceRecord.record_date >= start,
                AttendanceRecord.record_date <= end,
            )
        ).all()
    by_date = {r.record_date: r.status for r in records}
    working = _working_days(db, tenant_id, year, month, cfg=cfg)
    result = monthly_summary(by_date, working)
    return AttendanceSummaryOut(
        year=year,
        month=month,
        user_id=employee.id,
        employee_name=employee.name,
        department_id=employee.department_id,
        department_name=employee.department.name if employee.department else None,
        role=employee.role.value,
        working_days=result["working_days"],
        present=result["present"],
        half=result["half"],
        leave=result["leave"],
        absent=result["absent"],
        attendance_pct=result["attendance_pct"],
        records=[_serialize(db, r, author=employee) for r in sorted(records, key=lambda r: r.record_date)],
    )


# ---------------------------------------------------------------- settings


@router.get("/settings", response_model=AttendanceSettingsOut)
def get_settings(
    db: Session = Depends(get_db),
    user: User = Depends(require_tenant_admin),
):
    return _settings_out(_settings_dict(db, user.tenant_id))


def _save_settings(db: Session, user: User, data: dict) -> AttendanceSettings:
    row = db.scalar(
        select(AttendanceSettings).where(AttendanceSettings.tenant_id == user.tenant_id)
    )
    if row is None:
        row = AttendanceSettings(tenant_id=user.tenant_id)
        db.add(row)
    if "work_days" in data:
        row.work_days_json = json.dumps(data["work_days"])
    if "start_time" in data:
        row.start_time = data["start_time"]
    if "end_time" in data:
        row.end_time = data["end_time"]
    if "office_ips" in data:
        row.office_ips_json = json.dumps([ip.strip() for ip in data["office_ips"] if ip.strip()])
    if "holidays" in data:
        row.holidays_json = json.dumps(data["holidays"])
    # Audit: a tenant admin can silently clear office_ips (disabling the
    # WiFi gate for everyone) — that change must be attributable.
    changed = sorted(data.keys())
    audit_service.log(
        db, user, "attendance_settings_updated", "attendance_settings",
        user.tenant_id, "Attendance settings",
        f"Attendance settings updated by {user.name}: {', '.join(changed)}."
        + (f" office_ips now: {row.office_ips_json}." if "office_ips" in data else ""),
    )
    db.commit()
    db.refresh(row)
    return row


@router.patch("/settings", response_model=AttendanceSettingsOut)
def update_settings(
    payload: AttendanceSettingsUpdate,
    db: Session = Depends(get_db),
    user: User = Depends(require_tenant_admin),
):
    data = payload.model_dump(exclude_unset=True)
    # M10: validate the EFFECTIVE (merged stored + patched) start/end
    # pair, not just the pair in the payload — a PATCH with only
    # start_time="18:00" against a stored end_time="17:00" must fail.
    cfg = _settings_dict(db, user.tenant_id)
    effective_start = data.get("start_time", cfg["start_time"])
    effective_end = data.get("end_time", cfg["end_time"])
    if parse_time(effective_start) >= parse_time(effective_end):
        raise HTTPException(400, "start_time must be before end_time.")
    # M1: two admins creating settings at once race the INSERT against
    # uq_attendance_settings_tenant — the loser re-reads the winner's
    # row and applies the update to it instead of 500ing.
    try:
        _save_settings(db, user, data)
    except IntegrityError:
        db.rollback()
        _save_settings(db, user, data)
    return _settings_out(_settings_dict(db, user.tenant_id))


# ---------------------------------------------------------------- today


@router.get("/today", response_model=AttendanceTodayOut)
def get_today(
    request: Request,
    db: Session = Depends(get_db),
    user: User = Depends(require_tenant_member),
):
    cfg = _settings_dict(db, user.tenant_id)
    today = _today_local()
    record = db.scalar(
        select(AttendanceRecord).where(
            AttendanceRecord.tenant_id == user.tenant_id,
            AttendanceRecord.employee_id == user.id,
            AttendanceRecord.record_date == today,
        )
    )
    allowed, enforced = _ip_allowed(cfg, _client_ip(request))
    out = _settings_out(cfg)
    return AttendanceTodayOut(
        today=today,
        is_working_day=is_working_day(cfg, today),
        start_time=out.start_time,
        end_time=out.end_time,
        midday=out.midday,
        wifi_enforced=enforced,
        ip_allowed=allowed,
        record=_serialize(db, record) if record else None,
    )


# ------------------------------------------------------------ check-in/out


@router.post("/check-in", response_model=AttendanceRecordOut, status_code=201)
@limiter.limit(ATTENDANCE_RATE_LIMIT, key_func=_authenticated_user_key)
def check_in(
    request: Request,
    db: Session = Depends(get_db),
    user: User = Depends(require_attendance_author),
):
    cfg = _settings_dict(db, user.tenant_id)
    now = _now_local()
    today = now.date()
    if not is_working_day(cfg, today):
        raise HTTPException(400, "Today is not a working day for your organization.")
    ip = _client_ip(request)
    allowed, enforced = _ip_allowed(cfg, ip)
    if not allowed:
        raise HTTPException(
            403,
            "Check-in is only allowed on office WiFi. "
            "Please connect to the office WiFi and try again.",
        )
    existing = db.scalar(
        select(AttendanceRecord).where(
            AttendanceRecord.tenant_id == user.tenant_id,
            AttendanceRecord.employee_id == user.id,
            AttendanceRecord.record_date == today,
        )
    )
    if existing is not None:
        if existing.status == "leave":
            raise HTTPException(409, "This day is recorded as leave. Ask an admin to remove the leave record first.")
        raise HTTPException(409, "You have already checked in today.")
    start = parse_time(cfg["start_time"])
    end = parse_time(cfg["end_time"])
    check_in_time = time_type(now.hour, now.minute, now.second)
    record = AttendanceRecord(
        tenant_id=user.tenant_id,
        employee_id=user.id,
        department_id=user.department_id,
        record_date=today,
        check_in=check_in_time,
        status=day_status(check_in_time, None, start, end),
        late_minutes=late_minutes(check_in_time, start),
        check_in_ip=ip,
    )
    db.add(record)
    # M1: a double-tap / two tabs racing the INSERT hits the unique
    # constraint (uq_attendance_tenant_employee_day) — roll back and
    # answer 409 with the winner's record instead of a 500.
    try:
        db.commit()
    except IntegrityError:
        db.rollback()
        existing = db.scalar(
            select(AttendanceRecord).where(
                AttendanceRecord.tenant_id == user.tenant_id,
                AttendanceRecord.employee_id == user.id,
                AttendanceRecord.record_date == today,
            )
        )
        if existing is not None:
            if existing.status == "leave":
                raise HTTPException(409, "This day is recorded as leave. Ask an admin to remove the leave record first.")
            raise HTTPException(409, "You have already checked in today.")
        raise
    db.refresh(record)
    logger.info(
        "Attendance check-in user=%s date=%s ip=%s enforced=%s",
        user.id, today.isoformat(), ip, enforced,
    )
    return _serialize(db, record)


@router.post("/check-out", response_model=AttendanceRecordOut)
@limiter.limit(ATTENDANCE_RATE_LIMIT, key_func=_authenticated_user_key)
def check_out(
    request: Request,
    db: Session = Depends(get_db),
    user: User = Depends(require_attendance_author),
):
    cfg = _settings_dict(db, user.tenant_id)
    now = _now_local()
    today = now.date()
    ip = _client_ip(request)
    allowed, _enforced = _ip_allowed(cfg, ip)
    if not allowed:
        raise HTTPException(
            403,
            "Check-out is only allowed on office WiFi. "
            "Please connect to the office WiFi and try again.",
        )
    record = db.scalar(
        select(AttendanceRecord).where(
            AttendanceRecord.tenant_id == user.tenant_id,
            AttendanceRecord.employee_id == user.id,
            AttendanceRecord.record_date == today,
        )
    )
    if record is not None and record.status == "leave":
        raise HTTPException(409, "This day is recorded as leave.")
    if record is None or record.check_in is None:
        # M18: check-out after midnight — no record for today, so fall
        # back to the most recent OPEN record from yesterday only and
        # close that one. Anything older stays open (an admin corrects
        # it); with no open record, keep the existing 409.
        yesterday = today - timedelta(days=1)
        record = db.scalar(
            select(AttendanceRecord).where(
                AttendanceRecord.tenant_id == user.tenant_id,
                AttendanceRecord.employee_id == user.id,
                AttendanceRecord.record_date == yesterday,
                AttendanceRecord.check_in.is_not(None),
                AttendanceRecord.check_out.is_(None),
            )
        )
        if record is None:
            raise HTTPException(409, "You have not checked in today.")
    # M11: the day being closed must be a working day (a holiday/weekend
    # record — e.g. created before the day became a holiday — cannot be
    # checked out). Checked against the record's date, not today, so the
    # after-midnight fallback (M18) still works when today is a weekend.
    if not is_working_day(cfg, record.record_date):
        raise HTTPException(400, "That day is not a working day for your organization.")
    if record.check_out is not None:
        raise HTTPException(409, "You have already checked out today.")
    start = parse_time(cfg["start_time"])
    end = parse_time(cfg["end_time"])
    check_out_time = time_type(now.hour, now.minute, now.second)
    # Compute the new status in Python first (check-in is known, the
    # check-out time is now), then close the record with one atomic
    # conditional UPDATE — M3: only the first concurrent check-out
    # closes it; the rest see rowcount 0 and get 409.
    new_status = day_status(record.check_in, check_out_time, start, end)
    result = db.execute(
        update(AttendanceRecord)
        .where(
            AttendanceRecord.id == record.id,
            AttendanceRecord.check_out.is_(None),
        )
        .values(check_out=check_out_time, check_out_ip=ip, status=new_status)
    )
    if result.rowcount == 0:
        db.rollback()
        raise HTTPException(409, "You have already checked out today.")
    db.commit()
    db.refresh(record)
    logger.info(
        "Attendance check-out user=%s date=%s ip=%s",
        user.id, record.record_date.isoformat(), ip,
    )
    return _serialize(db, record)


# ------------------------------------------------------------------ leave


@router.post("/leave", response_model=AttendanceRecordOut, status_code=201)
def mark_leave(
    payload: LeaveCreate,
    db: Session = Depends(get_db),
    user: User = Depends(require_tenant_member),
):
    """Record an approved-leave day. Counts as absent (0) in the month-end
    percentage, but is shown separately from unexplained absence."""
    target_id = payload.user_id or user.id
    if target_id != user.id:
        if target_id not in _visible_user_ids(db, user):
            raise HTTPException(403, "Not allowed to mark leave for this user.")
        if user.role == UserRole.EMPLOYEE:
            raise HTTPException(403, "Employees can only mark their own leave.")
    target = db.get(User, target_id)
    if target is None or target.tenant_id != user.tenant_id:
        raise HTTPException(404, "User not found.")
    # m11: leave only on a working day — weekends and holidays are not
    # working days, so there is nothing to take leave from.
    cfg = _settings_dict(db, user.tenant_id)
    if not is_working_day(cfg, payload.record_date):
        raise HTTPException(
            400, "Leave can only be marked on a working day (not a weekend or holiday)."
        )
    existing = db.scalar(
        select(AttendanceRecord).where(
            AttendanceRecord.tenant_id == user.tenant_id,
            AttendanceRecord.employee_id == target_id,
            AttendanceRecord.record_date == payload.record_date,
        )
    )
    if existing is not None:
        raise HTTPException(409, "An attendance record already exists for this day.")
    record = AttendanceRecord(
        tenant_id=user.tenant_id,
        employee_id=target_id,
        department_id=target.department_id,
        record_date=payload.record_date,
        status="leave",
    )
    db.add(record)
    # M1: the pre-check above can race a concurrent check-in/leave for
    # the same day — the unique constraint is the arbiter. The flush
    # raises IntegrityError for the loser; roll back and answer 409.
    try:
        db.flush()  # assigns record.id for the audit entry below
        audit_service.log(
            db, user, "attendance_leave_created", "attendance_record",
            record.id, f"{target.name} — {payload.record_date.isoformat()}",
            f"Leave marked for {target.name} on {payload.record_date.isoformat()} by {user.name}.",
            department_id=target.department_id,
        )
        db.commit()
    except IntegrityError:
        db.rollback()
        existing = db.scalar(
            select(AttendanceRecord).where(
                AttendanceRecord.tenant_id == user.tenant_id,
                AttendanceRecord.employee_id == target_id,
                AttendanceRecord.record_date == payload.record_date,
            )
        )
        if existing is not None:
            raise HTTPException(409, "An attendance record already exists for this day.")
        raise
    db.refresh(record)
    return _serialize(db, record)


@router.patch("/records/{record_id}", response_model=AttendanceRecordOut)
def update_record(
    record_id: int,
    payload: LeaveUpdate,
    db: Session = Depends(get_db),
    user: User = Depends(require_tenant_member),
):
    """Move a leave day to another date. Leave records only — check-in/out
    records are never editable. Permissions mirror mark_leave: the owner,
    or a dept/tenant admin for a user in their visible set. The new date
    must be a working day in the SAME month as the original record —
    moving across months is not allowed (delete + re-mark instead)."""
    record = db.get(AttendanceRecord, record_id)
    if record is None or record.tenant_id != user.tenant_id:
        raise HTTPException(404, "Attendance record not found.")
    if record.status != "leave":
        raise HTTPException(400, "Only leave records can be edited.")
    if record.employee_id != user.id:
        if record.employee_id not in _visible_user_ids(db, user):
            raise HTTPException(403, "Not allowed to edit this user's leave.")
        if user.role == UserRole.EMPLOYEE:
            raise HTTPException(403, "Employees can only edit their own leave.")
    # m11: the new date must be a working day, in the same month.
    cfg = _settings_dict(db, user.tenant_id)
    if not is_working_day(cfg, payload.record_date):
        raise HTTPException(
            400, "Leave can only be moved to a working day (not a weekend or holiday)."
        )
    if (payload.record_date.year, payload.record_date.month) != (
        record.record_date.year,
        record.record_date.month,
    ):
        raise HTTPException(
            400,
            "A leave day can only be moved within the same month. "
            "Delete it and mark a new leave day instead.",
        )
    existing = db.scalar(
        select(AttendanceRecord).where(
            AttendanceRecord.tenant_id == user.tenant_id,
            AttendanceRecord.employee_id == record.employee_id,
            AttendanceRecord.record_date == payload.record_date,
            AttendanceRecord.id != record.id,
        )
    )
    if existing is not None:
        raise HTTPException(409, "An attendance record already exists for this day.")
    old_date = record.record_date
    record.record_date = payload.record_date
    audit_service.log(
        db, user, "attendance_leave_updated", "attendance_record",
        record.id, f"Leave — {payload.record_date.isoformat()}",
        f"Leave moved for user {record.employee_id}: "
        f"{old_date.isoformat()} → {payload.record_date.isoformat()} by {user.name}.",
        department_id=record.department_id,
    )
    # M1: same race as mark_leave — two moves landing on the same day.
    try:
        db.commit()
    except IntegrityError:
        db.rollback()
        raise HTTPException(409, "An attendance record already exists for this day.")
    db.refresh(record)
    return _serialize(db, record)


@router.delete("/records/{record_id}", status_code=204)
def delete_record(
    record_id: int,
    db: Session = Depends(get_db),
    user: User = Depends(require_tenant_admin),
):
    """Correction tool for tenant admins (wrong leave day, bad record).
    Leave records only — check-in/out records are never deletable via
    this endpoint (the UI only exposes Delete on leave rows)."""
    record = db.get(AttendanceRecord, record_id)
    if record is None or record.tenant_id != user.tenant_id:
        raise HTTPException(404, "Attendance record not found.")
    if record.status != "leave":
        raise HTTPException(400, "Only leave records can be deleted.")
    audit_service.log(
        db, user, "attendance_record_deleted", "attendance_record",
        record.id, f"Leave — {record.record_date.isoformat()}",
        f"Leave record for user {record.employee_id} on "
        f"{record.record_date.isoformat()} deleted by {user.name}.",
        department_id=record.department_id,
    )
    db.delete(record)
    db.commit()
    return None


# -------------------------------------------------------------- summaries


@router.get("/summary", response_model=AttendanceSummaryOut)
def get_summary(
    year: int = Query(...),
    month: int = Query(...),
    user_id: int | None = Query(None),
    db: Session = Depends(get_db),
    user: User = Depends(require_tenant_member),
):
    target_id = user_id or user.id
    if target_id not in _visible_user_ids(db, user):
        raise HTTPException(403, "Not allowed to view this user's attendance.")
    target = db.get(User, target_id)
    if target is None or target.tenant_id != user.tenant_id:
        raise HTTPException(404, "User not found.")
    return _summary_for(db, user.tenant_id, target, year, month)


@router.get("/report", response_model=list[AttendanceSummaryOut])
@limiter.limit(REPORT_RATE_LIMIT, key_func=_authenticated_user_key)
def get_report(
    request: Request,
    year: int = Query(...),
    month: int = Query(...),
    limit: int | None = Query(None, ge=1, le=1000),
    offset: int = Query(0, ge=0),
    db: Session = Depends(get_db),
    user: User = Depends(require_tenant_member),
):
    # M13: settings loaded once, all of the month's records fetched in
    # ONE query and grouped in memory, and _serialize takes the already-
    # loaded user — a report no longer costs ~2 queries per user plus
    # one db.get per record. limit/offset paginate the user list; the
    # default (no limit) returns every visible row, as the frontend
    # expects.
    start, end = _month_bounds(year, month)
    cfg = _settings_dict(db, user.tenant_id)
    visible = _visible_user_ids(db, user)
    users_stmt = (
        select(User)
        .options(selectinload(User.department))
        .where(
            User.id.in_(visible),
            User.role.in_([UserRole.EMPLOYEE, UserRole.DEPT_ADMIN]),
        )
        .order_by(User.name)
        .offset(offset)
    )
    if limit is not None:
        users_stmt = users_stmt.limit(limit)
    users = db.scalars(users_stmt).all()
    by_user: dict[int, list[AttendanceRecord]] = {}
    if users:
        for r in db.scalars(
            select(AttendanceRecord).where(
                AttendanceRecord.tenant_id == user.tenant_id,
                AttendanceRecord.employee_id.in_([u.id for u in users]),
                AttendanceRecord.record_date >= start,
                AttendanceRecord.record_date <= end,
            )
        ).all():
            by_user.setdefault(r.employee_id, []).append(r)
    return [
        _summary_for(db, user.tenant_id, u, year, month, cfg=cfg, records=by_user.get(u.id, []))
        for u in users
    ]


_ROLE_LABELS = {
    UserRole.EMPLOYEE: "Employee",
    UserRole.DEPT_ADMIN: "Dept Admin",
    UserRole.TENANT_ADMIN: "Tenant Admin",
    UserRole.SUPER_ADMIN: "Super Admin",
}

_STATUS_LABELS = {
    "present": "Present",
    "half": "Half day",
    "leave": "Leave",
    "absent": "Absent",
}


def _safe_file_part(value: str, fallback: str = "department") -> str:
    """ASCII slug for a filename part. Non-Latin names (e.g. Burmese) have
    no ASCII letters, so they fall back to `fallback` instead of producing
    an empty slug — "department" for a department file, "employee" for a
    per-person file."""
    slug = "".join(
        c if c.isascii() and (c.isalnum() or c in "-_") else "-"
        for c in value.lower().replace(" ", "-")
    )
    return slug.strip("-") or fallback


@router.get("/report/export")
@limiter.limit(EXPORT_RATE_LIMIT, key_func=_authenticated_user_key)
def export_report(
    request: Request,
    year: int = Query(...),
    month: int = Query(...),
    department_id: int | None = Query(None),
    user_id: int | None = Query(None),
    db: Session = Depends(get_db),
    user: User = Depends(require_tenant_member),
):
    """Download the monthly attendance detail as .xlsx — one row per working
    day per person. Same visibility as GET /report: a dept admin only ever
    gets their own department, even when no department filter is given.
    Working days without a record appear as Absent with blank times.
    With user_id, only that person is exported (per-person download); a
    user outside the caller's visible set yields a headers-only file.
    An unknown department_id/user_id (no such row, or another tenant's)
    returns 404 instead of a silently empty file; headers-only is only
    for a valid-but-empty scope. Late minutes are recorded in the
    database but deliberately never exported."""
    start, end = _month_bounds(year, month)
    visible = _visible_user_ids(db, user)
    users = db.scalars(
        select(User)
        .options(selectinload(User.department))
        .where(
            User.id.in_(visible),
            User.role.in_([UserRole.EMPLOYEE, UserRole.DEPT_ADMIN]),
        )
        .order_by(User.name)
    ).all()
    dept_label = None
    if department_id is not None:
        dept = db.get(Department, department_id)
        if dept is None or dept.tenant_id != user.tenant_id:
            raise HTTPException(404, "Department not found.")
        # Only put the department name in the filename when the caller
        # may see that department (tenant admin: any; anyone else:
        # only their own). Otherwise a headers-only file for an
        # out-of-scope department would still leak its name.
        if user.role == UserRole.TENANT_ADMIN or user.department_id == department_id:
            dept_label = dept.name
        users = [u for u in users if u.department_id == department_id]
    person_label = None
    if user_id is not None:
        target = db.get(User, user_id)
        if target is None or target.tenant_id != user.tenant_id:
            raise HTTPException(404, "User not found.")
        if (
            target.id not in visible
            or target.role not in (UserRole.EMPLOYEE, UserRole.DEPT_ADMIN)
        ):
            users = []
        else:
            users = [target]
            person_label = target.name
    # M13: one settings load and one records query for the whole export,
    # grouped in memory — not a settings + records query per user, and
    # no lazy department load per user (eager-loaded above).
    cfg = _settings_dict(db, user.tenant_id)
    working = _working_days(db, user.tenant_id, year, month, cfg=cfg)
    records_by_user: dict[int, dict] = {}
    if users:
        for r in db.scalars(
            select(AttendanceRecord).where(
                AttendanceRecord.tenant_id == user.tenant_id,
                AttendanceRecord.employee_id.in_([u.id for u in users]),
                AttendanceRecord.record_date >= start,
                AttendanceRecord.record_date <= end,
            )
        ).all():
            records_by_user.setdefault(r.employee_id, {})[r.record_date] = r
    entries = []
    for u in users:
        records = records_by_user.get(u.id, {})
        dept_name = u.department.name if u.department else "—"
        for day in working:
            record = records.get(day)
            if record is None:
                entries.append(
                    {
                        "date": day.isoformat(),
                        "employee_name": u.name,
                        "department_name": dept_name,
                        "role": _ROLE_LABELS.get(u.role, u.role.value),
                        "check_in": "",
                        "check_out": "",
                        "status": "Absent",
                    }
                )
            else:
                entries.append(
                    {
                        "date": day.isoformat(),
                        "employee_name": u.name,
                        "department_name": dept_name,
                        "role": _ROLE_LABELS.get(u.role, u.role.value),
                        "check_in": record.check_in.strftime("%H:%M") if record.check_in else "",
                        "check_out": record.check_out.strftime("%H:%M") if record.check_out else "",
                        "status": _STATUS_LABELS.get(record.status, record.status),
                    }
                )
    audit_service.log(
        db, user, "attendance_export", "attendance_export",
        user.tenant_id,
        f"Attendance export {year}-{month:02d}",
        f"Attendance export for {year}-{month:02d} by {user.name} "
        f"({len(users)} user(s){', ' + dept_label if dept_label else ''}).",
    )
    db.commit()
    output = build_attendance_detail_report(entries, year, month, dept_label, person_label)
    filename = f"attendance-{year}-{month:02d}"
    if person_label:
        filename += f"-{_safe_file_part(person_label, 'employee')}"
    elif dept_label:
        filename += f"-{_safe_file_part(dept_label, 'department')}"
    filename += ".xlsx"
    return StreamingResponse(
        output,
        media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        headers={"Content-Disposition": f'attachment; filename="{filename}"'},
    )
