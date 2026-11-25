"""Pydantic schemas for the Attendance module (standalone dashboard — no KPI linkage)."""

from __future__ import annotations

import ipaddress
from datetime import date as date_type

from pydantic import BaseModel, field_validator

# M-24: an office IP must be an address a real check-in can ever match
# — the WiFi gate compares the request's PUBLIC IP. The old validator
# rejected only the three RFC-1918 ranges, so loopback, link-local, ULA,
# multicast and CGNAT addresses were all accepted and would silently
# lock the whole tenant out of check-in. The rule is now: globally
# routable, or one of the documentation ranges the test suite uses as a
# stand-in office IP (TEST-NET-1/2/3 — is_global is False for those, so
# a bare is_global check would reject every test office IP). Multicast
# is rejected explicitly: ipaddress reports it as "global".
_TEST_NETWORKS = (
    ipaddress.ip_network("192.0.2.0/24"),    # TEST-NET-1 (RFC 5737)
    ipaddress.ip_network("198.51.100.0/24"),  # TEST-NET-2 (RFC 5737)
    ipaddress.ip_network("203.0.113.0/24"),  # TEST-NET-3 (RFC 5737)
)

# M-25: size caps — an unbounded holidays/office_ips list is an
# unbounded write (and every holiday change notifies/emails the whole
# tenant). A year holds at most 366 days; 20 office IPs is far above
# any real office's public-IP count.
MAX_OFFICE_IPS = 20
MAX_HOLIDAYS = 366


def _is_matchable_office_ip(ip: ipaddress.IPv4Address | ipaddress.IPv6Address) -> bool:
    if ip.is_multicast or ip.is_loopback or ip.is_link_local or ip.is_unspecified:
        return False
    if ip.is_global:
        return True
    return any(ip in net for net in _TEST_NETWORKS)


class HolidayOut(BaseModel):
    date: str  # "YYYY-MM-DD"
    name: str = ""  # shown in the banner + notification; "" = unnamed


class AttendanceSettingsUpdate(BaseModel):
    work_days: list[int] | None = None  # datetime.weekday() numbers, 0=Mon
    start_time: str | None = None  # "HH:MM"
    end_time: str | None = None  # "HH:MM"
    office_ips: list[str] | None = None
    # "YYYY-MM-DD" strings (legacy) or {"date", "name"} objects — the
    # validator normalises both to sorted, deduped {"date", "name"} dicts.
    holidays: list | None = None

    @field_validator("work_days", mode="before")
    @classmethod
    def _check_work_days(cls, value):
        if value is None:
            return value
        # mode="before": raw JSON values. isinstance(True, int) is True,
        # so exclude booleans explicitly before Pydantic coerces True->1.
        if not isinstance(value, list) or not value or any(
            isinstance(d, bool) or not isinstance(d, int) or not 0 <= d <= 6
            for d in value
        ):
            raise ValueError("work_days must be weekday numbers 0-6 (0=Monday).")
        return sorted(set(value))

    @field_validator("start_time", "end_time")
    @classmethod
    def _check_time(cls, value):
        if value is None:
            return value
        from app.services.attendance_service import parse_time

        try:
            parse_time(value)
        except ValueError as exc:
            raise ValueError(str(exc)) from exc
        return value

    @field_validator("office_ips")
    @classmethod
    def _check_office_ips(cls, value):
        if value is None:
            return value
        if len(value) > MAX_OFFICE_IPS:
            raise ValueError(
                f"office_ips must have at most {MAX_OFFICE_IPS} entries."
            )
        cleaned = []
        for item in value:
            entry = item.strip() if isinstance(item, str) else item
            if not entry:
                continue
            try:
                ip = ipaddress.ip_address(entry)
            except ValueError:
                raise ValueError(f"office_ips must be valid IP addresses (got {entry!r}).") from None
            if not _is_matchable_office_ip(ip):
                raise ValueError(
                    f"office_ips must be public IPs — {entry} is a private "
                    "or non-routable address and can never match a "
                    "check-in, because the WiFi gate compares public IPs. "
                    "Use the office's public IP instead."
                )
            cleaned.append(str(ip))
        return cleaned

    @field_validator("holidays", mode="before")
    @classmethod
    def _check_holidays(cls, value):
        if value is None:
            return value
        if isinstance(value, list) and len(value) > MAX_HOLIDAYS:
            raise ValueError(
                f"holidays must have at most {MAX_HOLIDAYS} entries."
            )
        # Strict parse (bad date / long name -> 422), then dedupe by date
        # and sort. The model import is lazy, mirroring the parse_time
        # import above — schemas must not pull models in at import time.
        from app.models.attendance import normalize_holidays

        return normalize_holidays(value, strict=True)


class AttendanceSettingsOut(BaseModel):
    work_days: list[int]
    start_time: str
    end_time: str
    midday: str
    office_ips: list[str]
    holidays: list[HolidayOut]
    wifi_enforced: bool  # True once at least one office IP is configured

    model_config = {"from_attributes": True}


class AttendanceRecordOut(BaseModel):
    id: int
    employee_id: int
    employee_name: str | None = None
    record_date: date_type
    check_in: str | None = None
    check_out: str | None = None
    status: str
    # NOTE: late_minutes is recorded in the DB but deliberately NOT exposed
    # here (user decision — removed from UI + export too).

    model_config = {"from_attributes": True}


class AttendanceTodayOut(BaseModel):
    today: date_type
    is_working_day: bool
    start_time: str
    end_time: str
    midday: str
    wifi_enforced: bool
    ip_allowed: bool
    record: AttendanceRecordOut | None = None
    # M-30: the server closes yesterday's still-open record on check-out
    # (after-midnight fallback), but the UI used to disable Check-out
    # whenever there was no record for today — the fallback was
    # unreachable. The page enables the button when this is True.
    has_open_yesterday_record: bool = False
    # Holidays from today through today+7 days, sorted by date — the
    # Attendance page banner renders these.
    upcoming_holidays: list[HolidayOut] = []


class AttendanceSummaryOut(BaseModel):
    year: int
    month: int
    user_id: int
    employee_name: str | None = None
    department_id: int | None = None
    department_name: str | None = None
    role: str | None = None
    working_days: int
    present: int
    half: int
    leave: int
    absent: int
    # S-m8: records whose status is none of present/half/leave — counted
    # separately, never silently folded into absent. 0 on all healthy data.
    unknown: int = 0
    attendance_pct: float
    records: list[AttendanceRecordOut] = []


class LeaveCreate(BaseModel):
    # Admin-API-only: no UI caller marks leave for someone else (the UI
    # only ever marks the signed-in user's own leave). Tenant/dept admins
    # may use this via the API; visibility is enforced in mark_leave.
    user_id: int | None = None  # admin marking for someone else; None = self
    record_date: date_type


class LeaveUpdate(BaseModel):
    record_date: date_type
