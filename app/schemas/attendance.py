"""Pydantic schemas for the Attendance module (standalone dashboard — no KPI linkage)."""

from __future__ import annotations

from datetime import date as date_type

from pydantic import BaseModel, field_validator


class AttendanceSettingsUpdate(BaseModel):
    work_days: list[int] | None = None  # datetime.weekday() numbers, 0=Mon
    start_time: str | None = None  # "HH:MM"
    end_time: str | None = None  # "HH:MM"
    office_ips: list[str] | None = None
    holidays: list[str] | None = None  # "YYYY-MM-DD"

    @field_validator("work_days")
    @classmethod
    def _check_work_days(cls, value):
        if value is None:
            return value
        if not value or any(not isinstance(d, int) or not 0 <= d <= 6 for d in value):
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
    late_minutes: int

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
    working_days: int
    present: int
    half: int
    leave: int
    absent: int
    attendance_pct: float
    total_late_minutes: int
    late_days: int
    records: list[AttendanceRecordOut] = []


class LeaveCreate(BaseModel):
    user_id: int | None = None  # admin marking for someone else; None = self
    record_date: date_type
