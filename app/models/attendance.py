"""Attendance models (additive-only; no existing model touched).

One AttendanceRecord is one employee's one day: check-in/out times, the
derived status (present / half / leave) and the late minutes (info only —
late never deducts). A working day with no record is an unexplained
absence, derived at summary time rather than stored.

AttendanceSettings is one row per tenant (tenant_id unique): the tenant
admin sets work days, office hours, office WiFi public IPs and holidays
for his own organization only. No department-level settings exist, per
the user's decision.
"""

from __future__ import annotations

import json
from datetime import date as date_type, datetime, time as time_type, timezone

from sqlalchemy import (
    Date,
    DateTime,
    ForeignKey,
    Integer,
    String,
    Text,
    Time,
    UniqueConstraint,
)
from sqlalchemy.orm import Mapped, mapped_column

from app.database import Base
from app.services.attendance_service import (
    DEFAULT_END,
    DEFAULT_START,
    DEFAULT_WORK_DAYS,
)


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


class AttendanceSettings(Base):
    __tablename__ = "attendance_settings"
    __table_args__ = (
        UniqueConstraint("tenant_id", name="uq_attendance_settings_tenant"),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    tenant_id: Mapped[int] = mapped_column(
        ForeignKey("tenants.id", ondelete="CASCADE"), nullable=False, index=True
    )

    # Weekdays that count as working days, datetime.weekday() numbers
    # (0=Monday). JSON list, default Mon-Fri.
    work_days_json: Mapped[str] = mapped_column(
        Text, nullable=False, default=lambda: json.dumps(DEFAULT_WORK_DAYS)
    )
    start_time: Mapped[str] = mapped_column(String(5), nullable=False, default=DEFAULT_START)
    end_time: Mapped[str] = mapped_column(String(5), nullable=False, default=DEFAULT_END)
    # Office WiFi public IPs (whitelist). JSON list of strings; empty means
    # no enforcement yet (allowed, with a warning) so the feature is testable
    # before the office IP is known.
    office_ips_json: Mapped[str] = mapped_column(Text, nullable=False, default="[]")
    # Tenant holidays, JSON list of "YYYY-MM-DD" strings.
    holidays_json: Mapped[str] = mapped_column(Text, nullable=False, default="[]")

    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=_utcnow, onupdate=_utcnow, nullable=False
    )

    @property
    def work_days(self) -> list[int]:
        try:
            return [int(d) for d in json.loads(self.work_days_json or "[]")]
        except (ValueError, TypeError):
            return list(DEFAULT_WORK_DAYS)

    @property
    def office_ips(self) -> list[str]:
        try:
            return [str(ip).strip() for ip in json.loads(self.office_ips_json or "[]") if str(ip).strip()]
        except (ValueError, TypeError):
            return []

    @property
    def holidays(self) -> list[str]:
        try:
            return [str(d) for d in json.loads(self.holidays_json or "[]")]
        except (ValueError, TypeError):
            return []


class AttendanceRecord(Base):
    """One employee's one day. Status is 'present', 'half' or 'leave'."""

    __tablename__ = "attendance_records"
    __table_args__ = (
        UniqueConstraint(
            "tenant_id", "employee_id", "record_date",
            name="uq_attendance_tenant_employee_day",
        ),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    tenant_id: Mapped[int] = mapped_column(
        ForeignKey("tenants.id", ondelete="CASCADE"), nullable=False, index=True
    )
    employee_id: Mapped[int] = mapped_column(
        ForeignKey("users.id", ondelete="CASCADE"), nullable=False, index=True
    )
    # Denormalised from the employee at creation, for dept-admin scoping.
    department_id: Mapped[int | None] = mapped_column(
        ForeignKey("departments.id", ondelete="SET NULL"), nullable=True, index=True
    )

    record_date: Mapped[date_type] = mapped_column(Date, nullable=False, index=True)
    check_in: Mapped[time_type | None] = mapped_column(Time, nullable=True)
    check_out: Mapped[time_type | None] = mapped_column(Time, nullable=True)
    status: Mapped[str] = mapped_column(String(10), nullable=False, default="present")
    # Minutes after office start on check-in. Recorded for info only —
    # late never reduces the attendance percentage.
    late_minutes: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    check_in_ip: Mapped[str | None] = mapped_column(String(45), nullable=True)
    check_out_ip: Mapped[str | None] = mapped_column(String(45), nullable=True)

    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=_utcnow, nullable=False
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=_utcnow, onupdate=_utcnow, nullable=False
    )
