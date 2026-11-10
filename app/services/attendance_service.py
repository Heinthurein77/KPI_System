"""Attendance calculation helpers (standalone dashboard module).

Pure functions — no database, no KPI code of any kind. The month-end
percentage is display-only: it is shown in the dashboard and never
written to any KPI submission or template.

Rules (decided by the user 2026-11-10):
- Working days Mon-Fri by default (per-tenant setting).
- Office hours 09:00-17:00 by default; midday is the midpoint of office
  hours (13:00 for 09:00-17:00), derived per tenant.
- Present = 1, half-day = 0.5, absent = 0, approved leave = absent (0).
- Half-day trigger: check-in at/after midday OR check-out before midday.
- Late does NOT deduct: late minutes are recorded and shown for info only.
- Month-end % = (full days + 0.5 * half days) / working days in the month.
"""

from __future__ import annotations

import calendar
from datetime import date as date_type, time as time_type

MONTH_NAMES = [
    "January", "February", "March", "April", "May", "June",
    "July", "August", "September", "October", "November", "December",
]

DEFAULT_WORK_DAYS = [0, 1, 2, 3, 4]  # Mon-Fri (datetime.weekday())
DEFAULT_START = "09:00"
DEFAULT_END = "17:00"


def parse_time(value: str) -> time_type:
    """Parse "HH:MM" (24-hour) into a time; raises ValueError on bad input."""
    parts = value.strip().split(":")
    if len(parts) != 2:
        raise ValueError(f"Time must be HH:MM, got {value!r}.")
    hour, minute = int(parts[0]), int(parts[1])
    if not 0 <= hour <= 23 or not 0 <= minute <= 59:
        raise ValueError(f"Time must be HH:MM, got {value!r}.")
    return time_type(hour, minute)


def _minutes(t: time_type) -> int:
    return t.hour * 60 + t.minute


def midday(start: time_type, end: time_type) -> time_type:
    """Midpoint of the office day (e.g. 09:00-17:00 -> 13:00)."""
    mid = (_minutes(start) + _minutes(end)) // 2
    return time_type(mid // 60, mid % 60)


def late_minutes(check_in: time_type, start: time_type) -> int:
    """Minutes after the office start time; 0 when on time or early."""
    return max(0, _minutes(check_in) - _minutes(start))


def day_status(
    check_in: time_type | None,
    check_out: time_type | None,
    start: time_type,
    end: time_type,
) -> str:
    """'present' or 'half' for a day with a check-in.

    Half-day when the employee arrived at/after midday, or left before
    midday. Anything else with a check-in is a full present day. Late
    arrival alone (before midday) never reduces the day.
    """
    mid = midday(start, end)
    if check_in is not None and _minutes(check_in) >= _minutes(mid):
        return "half"
    if check_out is not None and _minutes(check_out) < _minutes(mid):
        return "half"
    return "present"


def working_days_in_month(
    year: int,
    month: int,
    work_days: list[int],
    holidays: set[date_type] | None = None,
) -> list[date_type]:
    """All working days of a month: weekdays in `work_days`, minus holidays."""
    holidays = holidays or set()
    _, last_day = calendar.monthrange(year, month)
    return [
        date_type(year, month, day)
        for day in range(1, last_day + 1)
        if date_type(year, month, day).weekday() in work_days
        and date_type(year, month, day) not in holidays
    ]


def monthly_summary(
    records: dict[date_type, str],
    working_days: list[date_type],
) -> dict:
    """Summarise a month from {date: status} over the working days.

    `records` maps a date to 'present', 'half' or 'leave'. A working day
    with no record is an unexplained absence. Leave counts as absent
    (0 credit) in the percentage, but is reported separately.
    """
    present = half = leave = absent = 0
    for day in working_days:
        status = records.get(day)
        if status == "present":
            present += 1
        elif status == "half":
            half += 1
        elif status == "leave":
            leave += 1
        else:
            absent += 1
    total = len(working_days)
    credited = present + 0.5 * half
    pct = round(credited / total * 100, 1) if total else 0.0
    return {
        "working_days": total,
        "present": present,
        "half": half,
        "leave": leave,
        "absent": absent,
        "attendance_pct": pct,
    }
