"""Attendance API (additive-only module; no KPI route or formula touched).

Office-WiFi-only check-in/out: the server checks the request's client IP
against the tenant's whitelisted office public IPs. A browser cannot read
the WiFi SSID, so the IP is the gate. Until a tenant configures any office
IP, check-in is allowed with a warning (so the feature is testable); once
IPs are set, off-WiFi check-in/out is rejected with 403.

Month-end auto-calc writes ONLY the Attendance metric's self_score, and
only on DRAFT submissions. dept_score / final_score and every other KPI
are never written here — all other KPIs stay manual (self -> dept ->
final) exactly as before. If the tenant has no company-wide template
named "Attendance", the percentage is shown in the UI but nothing is
written.

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
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.core.config import settings
from app.core.deps import (
    RoleChecker,
    get_current_user,
    require_tenant_admin,
    require_tenant_member,
)
from app.core.timezone import today as app_today
from app.database import get_db
from app.models.attendance import AttendanceRecord, AttendanceSettings
from app.models.kpi_submission import KPISubmission, KPIStatus
from app.models.kpi_template import KPITemplate
from app.models.user import User, UserRole
from app.schemas.attendance import (
    AttendanceCalcOut,
    AttendanceCalcResult,
    AttendanceRecordOut,
    AttendanceSettingsOut,
    AttendanceSettingsUpdate,
    AttendanceSummaryOut,
    AttendanceTodayOut,
    LeaveCreate,
)
from app.services.attendance_service import (
    DEFAULT_END,
    DEFAULT_START,
    DEFAULT_WORK_DAYS,
    MONTH_NAMES,
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
# fill their own attendance record).
require_attendance_author = RoleChecker([UserRole.EMPLOYEE, UserRole.DEPT_ADMIN])


def _client_ip(request: Request) -> str:
    """Real client IP.

    Caddy (the only public front door) overwrites X-Forwarded-For with the
    real client IP, so the rightmost entry is the one Caddy appended —
    the same convention the app's rate limiter uses. Direct clients
    cannot spoof it through Caddy. Falls back to the socket peer.
    """
    forwarded = request.headers.get("x-forwarded-for")
    if forwarded:
        entries = [part.strip() for part in forwarded.split(",") if part.strip()]
        if entries:
            return entries[-1]
    return request.client.host if request.client else ""


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
    author = db.get(User, record.employee_id)
    return AttendanceRecordOut(
        id=record.id,
        employee_id=record.employee_id,
        employee_name=author.name if author else None,
        record_date=record.record_date,
        check_in=record.check_in.strftime("%H:%M") if record.check_in else None,
        check_out=record.check_out.strftime("%H:%M") if record.check_out else None,
        status=record.status,
        late_minutes=record.late_minutes,
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
        working_days=result["working_days"],
        present=result["present"],
        half=result["half"],
        leave=result["leave"],
        absent=result["absent"],
        attendance_pct=result["attendance_pct"],
        total_late_minutes=sum(r.late_minutes for r in records),
        late_days=sum(1 for r in records if r.late_minutes > 0),
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


# ------------------------------------------------------- month-end calc


def _find_attendance_template(db: Session, tenant_id: int) -> KPITemplate | None:
    """The company-wide template named 'Attendance' (any capitalisation)."""
    return db.scalar(
        select(KPITemplate).where(
            KPITemplate.tenant_id == tenant_id,
            func.lower(KPITemplate.metric_name) == "attendance",
            KPITemplate.department_id.is_(None),
            KPITemplate.employee_id.is_(None),
        )
    )


@router.post("/calc", response_model=AttendanceCalcOut)
def month_end_calc(
    year: int = Query(...),
    month: int = Query(...),
    db: Session = Depends(get_db),
    user: User = Depends(require_tenant_member),
):
    """Month-end auto-calc: write each employee's attendance % as the
    self_score of their Attendance submission for the period.

    ADDITIVE and DRAFT-only: a submission is only written when it is in
    DRAFT status (or created fresh as DRAFT when none exists). A
    pending/approved/rejected submission is left untouched and reported
    as skipped. dept_score and final_score are never written, and no
    other KPI is touched. Re-running is idempotent: a second run writes
    the same value again to the same DRAFT rows.
    """
    if user.role not in (UserRole.TENANT_ADMIN, UserRole.DEPT_ADMIN):
        raise HTTPException(403, "Only admins can run the month-end calculation.")
    _month_bounds(year, month)
    now = app_today()
    if (year, month) > (now.year, now.month):
        raise HTTPException(400, "Cannot calculate a future month.")
    period = MONTH_NAMES[month - 1]
    visible = _visible_user_ids(db, user)
    users = db.scalars(
        select(User)
        .where(
            User.id.in_(visible),
            User.role.in_([UserRole.EMPLOYEE, UserRole.DEPT_ADMIN]),
        )
        .order_by(User.name)
    ).all()
    template = _find_attendance_template(db, user.tenant_id)
    results: list[AttendanceCalcResult] = []
    for employee in users:
        summary = _summary_for(db, user.tenant_id, employee, year, month)
        pct = summary.attendance_pct
        if template is None:
            results.append(AttendanceCalcResult(
                user_id=employee.id, employee_name=employee.name,
                attendance_pct=pct, written=False,
                reason="No company-wide 'Attendance' template — percentage shown only.",
            ))
            continue
        submission = db.scalar(
            select(KPISubmission).where(
                KPISubmission.tenant_id == user.tenant_id,
                KPISubmission.employee_id == employee.id,
                KPISubmission.kpi_template_id == template.id,
                KPISubmission.year == year,
                KPISubmission.month_or_quarter == period,
            )
        )
        if submission is None:
            submission = KPISubmission(
                tenant_id=user.tenant_id,
                employee_id=employee.id,
                department_id=employee.department_id,
                kpi_template_id=template.id,
                year=year,
                month_or_quarter=period,
                status=KPIStatus.DRAFT,
                self_score=pct,
                weight_snapshot=template.weight,
                target_snapshot=template.target,
            )
            db.add(submission)
            results.append(AttendanceCalcResult(
                user_id=employee.id, employee_name=employee.name,
                attendance_pct=pct, written=True,
                reason="DRAFT submission created with attendance self_score.",
            ))
        elif submission.status == KPIStatus.DRAFT:
            submission.self_score = pct
            results.append(AttendanceCalcResult(
                user_id=employee.id, employee_name=employee.name,
                attendance_pct=pct, written=True,
                reason="DRAFT submission self_score updated.",
            ))
        else:
            results.append(AttendanceCalcResult(
                user_id=employee.id, employee_name=employee.name,
                attendance_pct=pct, written=False,
                reason=f"Submission is {submission.status.value}; left untouched.",
            ))
    db.commit()
    return AttendanceCalcOut(year=year, month=month, results=results)
