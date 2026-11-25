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
    CheckConstraint,
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


# ---------------------------------------------------------------- holidays
#
# Holidays are stored as a JSON list of {"date": "YYYY-MM-DD", "name": ""}
# objects in holidays_json (the name is what the announcement banner and
# the bell notification show). Older rows hold plain "YYYY-MM-DD" strings
# — every read below is tolerant of both shapes, and a corrupt entry is
# skipped rather than breaking settings reads. No migration: same column.

def parse_holiday_entry(entry) -> dict:
    """Strict parse of one holiday entry -> {"date", "name"}.

    Accepts a legacy plain string or a {"date", "name"} dict. Raises
    ValueError on a missing/bad date or a name longer than 80 chars —
    the write path (schema validator) surfaces these as 422.
    """
    if isinstance(entry, str):
        raw_date, name = entry.strip(), ""
    elif isinstance(entry, dict):
        raw_date = entry.get("date")
        name = entry.get("name") or ""
        if not isinstance(raw_date, str):
            raise ValueError("each holiday needs a date (YYYY-MM-DD).")
        raw_date = raw_date.strip()
        if not isinstance(name, str):
            raise ValueError("holiday name must be text.")
        name = name.strip()
    else:
        raise ValueError("holidays must be dates (YYYY-MM-DD) or {date, name} objects.")
    try:
        date_type.fromisoformat(raw_date)
    except ValueError:
        raise ValueError(f"holidays must be real dates (YYYY-MM-DD), got {raw_date!r}.") from None
    if len(name) > 80:
        raise ValueError("holiday name must be at most 80 characters.")
    return {"date": raw_date, "name": name}


def _dedupe_holidays(items: list[dict]) -> list[dict]:
    """Dedupe by date (keep the entry that has a name, else the first),
    sorted by date."""
    by_date: dict[str, dict] = {}
    for item in items:
        prev = by_date.get(item["date"])
        if prev is None or (not prev["name"] and item["name"]):
            by_date[item["date"]] = item
    return [by_date[d] for d in sorted(by_date)]


def normalize_holiday(entry) -> dict | None:
    """Tolerant parse of one stored entry; None when it has no real date."""
    try:
        return parse_holiday_entry(entry)
    except (ValueError, TypeError, AttributeError):
        return None


def normalize_holidays(raw, *, strict: bool = False) -> list[dict]:
    """A stored holidays list -> sorted, deduped [{"date", "name"}].

    Tolerant by default (bad entries are skipped — a corrupt row must
    never break a settings read). With strict=True a bad entry raises
    ValueError instead (write path).
    """
    if not isinstance(raw, list):
        if strict:
            raise ValueError("holidays must be a list of dates or {date, name} objects.")
        return []
    if strict:
        return _dedupe_holidays([parse_holiday_entry(entry) for entry in raw])
    items = []
    for entry in raw:
        item = normalize_holiday(entry)
        if item is not None:
            items.append(item)
    return _dedupe_holidays(items)


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
    # Tenant holidays, JSON list of {"date": "YYYY-MM-DD", "name": ""}
    # objects (legacy rows hold plain "YYYY-MM-DD" strings — reads
    # normalise both shapes; see normalize_holidays above).
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
    def holidays(self) -> list[dict]:
        try:
            return normalize_holidays(json.loads(self.holidays_json or "[]"))
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
        # S-m8: DB-level backstop for the status vocabulary. The summary
        # used to count any unrecognised status as absent; a corrupt or
        # mistyped status can now never be written in the first place.
        # Verified safe before adding: both local dev DBs hold zero
        # attendance_records rows, so no existing row violates it.
        CheckConstraint(
            "status IN ('present', 'half', 'leave')",
            name="ck_attendance_records_status",
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
    # S-15 (security review 2026-11-25): leave approval. NULL for
    # non-leave records and for legacy leave rows (created before approval
    # existed — treated as approved). "pending" for employee self-marked
    # leave until a dept/tenant admin approves; "approved"/"rejected"
    # after review. Pending/rejected leave counts as absent in summaries
    # and exports — only approved (or legacy) leave counts as leave.
    leave_approval: Mapped[str | None] = mapped_column(String(20), nullable=True)
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
