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

import json
import logging
from datetime import date as date_type, datetime, time as time_type
from zoneinfo import ZoneInfo

from fastapi import APIRouter, Depends, HTTPException, Query, Request
from fastapi.responses import StreamingResponse
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.config import settings
from app.core.deps import (
    RoleChecker,
    get_current_user,
    require_tenant_admin,
    require_tenant_member,
)
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
    working_days_in_month,
)

router = APIRouter(prefix="/api/attendance", tags=["attendance"])

logger = logging.getLogger("kpi_system")

# Check-in/out authors: employees and dept admins (tenant admins never
# fill their own attendance record — same boundary as the daily diary).
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


def _now_local() -> datetime:
    return datetime.now(ZoneInfo(settings.TIMEZONE))


def _visible_user_ids(db: Session, user: User) -> list[int]:
    if user.role == UserRole.TENANT_ADMIN:
        stmt = select(User.id).where(
            User.tenant_id == user.tenant_id, User.is_active == True  # noqa: E712
        )
    elif user.role == UserRole.DEPT_ADMIN:
        stmt = select(User.id).where(
            User.tenant_id == user.tenant_id,
            User.department_id == user.department_id,
            User.is_active == True,  # noqa: E712
        )
    else:
        return [user.id]
    return list(db.scalars(stmt))


def _month_bounds(year: int, month: int) -> tuple[date_type, date_type]:
    if not 1 <= month <= 12:
        raise HTTPException(400, "month must be 1-12")
    start = date_type(year, month, 1)
    end = date_type(year + 1, 1, 1) if month == 12 else date_type(year, month + 1, 1)
    return start, end


def _serialize(db: Session, record: AttendanceRecord) -> AttendanceRecordOut:
    # late_minutes is recorded in the DB but deliberately never exposed
    # via the API (user decision: removed from UI + export as well).
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


def _working_days(db: Session, tenant_id: int, year: int, month: int) -> list[date_type]:
    cfg = _settings_dict(db, tenant_id)
    holidays = set()
    for raw in cfg["holidays"]:
        try:
            holidays.add(date_type.fromisoformat(raw))
        except ValueError:
            continue
    return working_days_in_month(year, month, cfg["work_days"], holidays)


def _summary_for(
    db: Session, tenant_id: int, employee: User, year: int, month: int
) -> AttendanceSummaryOut:
    start, end = _month_bounds(year, month)
    records = db.scalars(
        select(AttendanceRecord).where(
            AttendanceRecord.tenant_id == tenant_id,
            AttendanceRecord.employee_id == employee.id,
            AttendanceRecord.record_date >= start,
            AttendanceRecord.record_date < end,
        )
    ).all()
    by_date = {r.record_date: r.status for r in records}
    working = _working_days(db, tenant_id, year, month)
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
        records=[_serialize(db, r) for r in sorted(records, key=lambda r: r.record_date)],
    )


# ---------------------------------------------------------------- settings


@router.get("/settings", response_model=AttendanceSettingsOut)
def get_settings(
    db: Session = Depends(get_db),
    user: User = Depends(require_tenant_admin),
):
    return _settings_out(_settings_dict(db, user.tenant_id))


@router.patch("/settings", response_model=AttendanceSettingsOut)
def update_settings(
    payload: AttendanceSettingsUpdate,
    db: Session = Depends(get_db),
    user: User = Depends(require_tenant_admin),
):
    data = payload.model_dump(exclude_unset=True)
    if "start_time" in data and "end_time" in data:
        if parse_time(data["start_time"]) >= parse_time(data["end_time"]):
            raise HTTPException(400, "start_time must be before end_time.")
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
    return _settings_out(_settings_dict(db, user.tenant_id))


# ---------------------------------------------------------------- today


@router.get("/today", response_model=AttendanceTodayOut)
def get_today(
    request: Request,
    db: Session = Depends(get_db),
    user: User = Depends(require_tenant_member),
):
    cfg = _settings_dict(db, user.tenant_id)
    now = _now_local()
    today = now.date()
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
        is_working_day=today.weekday() in cfg["work_days"],
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
    if today.weekday() not in cfg["work_days"]:
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
    check_in_time = time_type(now.hour, now.minute)
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
    db.commit()
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
    if record is None or record.check_in is None:
        raise HTTPException(409, "You have not checked in today.")
    if record.status == "leave":
        raise HTTPException(409, "This day is recorded as leave.")
    if record.check_out is not None:
        raise HTTPException(409, "You have already checked out today.")
    start = parse_time(cfg["start_time"])
    end = parse_time(cfg["end_time"])
    check_out_time = time_type(now.hour, now.minute)
    record.check_out = check_out_time
    record.check_out_ip = ip
    record.status = day_status(record.check_in, check_out_time, start, end)
    db.commit()
    db.refresh(record)
    logger.info(
        "Attendance check-out user=%s date=%s ip=%s",
        user.id, today.isoformat(), ip,
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
    db.commit()
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
    or a dept/tenant admin for a user in their visible set. No working-day
    validation, same as mark_leave."""
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
    record.record_date = payload.record_date
    db.commit()
    db.refresh(record)
    return _serialize(db, record)


@router.delete("/records/{record_id}", status_code=204)
def delete_record(
    record_id: int,
    db: Session = Depends(get_db),
    user: User = Depends(require_tenant_admin),
):
    """Correction tool for tenant admins (wrong leave day, bad record)."""
    record = db.get(AttendanceRecord, record_id)
    if record is None or record.tenant_id != user.tenant_id:
        raise HTTPException(404, "Attendance record not found.")
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
def get_report(
    year: int = Query(...),
    month: int = Query(...),
    db: Session = Depends(get_db),
    user: User = Depends(require_tenant_member),
):
    visible = _visible_user_ids(db, user)
    users = db.scalars(
        select(User)
        .where(
            User.id.in_(visible),
            User.role.in_([UserRole.EMPLOYEE, UserRole.DEPT_ADMIN]),
        )
        .order_by(User.name)
    ).all()
    return [_summary_for(db, user.tenant_id, u, year, month) for u in users]


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


def _safe_file_part(value: str) -> str:
    slug = "".join(c if c.isalnum() or c in "-_" else "-" for c in value.lower().replace(" ", "-"))
    return slug.strip("-") or "department"


@router.get("/report/export")
def export_report(
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
    Late minutes are recorded in the database but deliberately never
    exported."""
    _month_bounds(year, month)  # 400 on an invalid month
    start, end = _month_bounds(year, month)
    visible = _visible_user_ids(db, user)
    users = db.scalars(
        select(User)
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
            users = []
        else:
            dept_label = dept.name
            users = [u for u in users if u.department_id == department_id]
    person_label = None
    if user_id is not None:
        target = db.get(User, user_id)
        if (
            target is None
            or target.id not in visible
            or target.role not in (UserRole.EMPLOYEE, UserRole.DEPT_ADMIN)
        ):
            users = []
        else:
            users = [target]
            person_label = target.name
    working = _working_days(db, user.tenant_id, year, month)
    entries = []
    for u in users:
        records = {
            r.record_date: r
            for r in db.scalars(
                select(AttendanceRecord).where(
                    AttendanceRecord.tenant_id == user.tenant_id,
                    AttendanceRecord.employee_id == u.id,
                    AttendanceRecord.record_date >= start,
                    AttendanceRecord.record_date < end,
                )
            ).all()
        }
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
    output = build_attendance_detail_report(entries, year, month, dept_label, person_label)
    filename = f"attendance-{year}-{month:02d}"
    if person_label:
        filename += f"-{_safe_file_part(person_label)}"
    elif dept_label:
        filename += f"-{_safe_file_part(dept_label)}"
    filename += ".xlsx"
    return StreamingResponse(
        output,
        media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        headers={"Content-Disposition": f'attachment; filename="{filename}"'},
    )
