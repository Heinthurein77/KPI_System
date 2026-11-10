"""Pydantic schemas for the Attendance module (standalone dashboard — no KPI linkage)."""

from __future__ import annotations

import ipaddress
from datetime import date as date_type

from pydantic import BaseModel, field_validator

# RFC 1918 private ranges — a private IP can never match a check-in, because
# the WiFi gate compares the request's PUBLIC IP. Saving one would silently
# lock the whole tenant out of check-in, so settings reject them outright.
_RFC1918_NETWORKS = (
    ipaddress.ip_network("10.0.0.0/8"),
    ipaddress.ip_network("172.16.0.0/12"),
    ipaddress.ip_network("192.168.0.0/16"),
)


class AttendanceSettingsUpdate(BaseModel):
    work_days: list[int] | None = None  # datetime.weekday() numbers, 0=Mon
    start_time: str | None = None  # "HH:MM"
    end_time: str | None = None  # "HH:MM"
    office_ips: list[str] | None = None
    holidays: list[str] | None = None  # "YYYY-MM-DD"

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
        cleaned = []
        for item in value:
            entry = item.strip() if isinstance(item, str) else item
            if not entry:
                continue
            try:
                ip = ipaddress.ip_address(entry)
            except ValueError:
                raise ValueError(f"office_ips must be valid IP addresses (got {entry!r}).") from None
            if any(ip in network for network in _RFC1918_NETWORKS):
                raise ValueError(
                    f"office_ips must be public IPs — {entry} is a private (RFC 1918) "
                    "address and can never match a check-in, because the WiFi gate "
                    "compares public IPs. Use the office's public IP instead."
                )
            cleaned.append(str(ip))
        return cleaned

    @field_validator("holidays")
    @classmethod
    def _check_holidays(cls, value):
        if value is None:
            return value
        for item in value:
            date_type.fromisoformat(item)  # raises ValueError on bad dates
        return sorted(set(value))


class AttendanceSettingsOut(BaseModel):
    work_days: list[int]
    start_time: str
    end_time: str
    midday: str
    office_ips: list[str]
    holidays: list[str]
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
