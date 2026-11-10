"""Submission grace period — entries stay open until N days after month-end.

Background: the dashboard only lets employees edit the exact current month
(``is_exact_current_month`` in ``app/routers/dashboard.py``), so in practice
each month's scores had to be entered within that month. This module adds
one configurable grace window on top of that rule: a past month stays
fillable until ``grace_days`` after its last day (default 7), then it closes.

Rules
-----
- Current month: always fillable (grace end is still in the future).
- Past month within grace: fillable — this is the "grace month".
- Past month after grace: closed for employee entry. The backend enforces
  this for DRAFT submissions; a REJECTED submission may still be corrected
  and resubmitted, because the delay there was the reviewer's, not the
  employee's. Dept/final approval and tenant-admin override are unaffected.
- Future month: never fillable (unchanged — locked until it begins).
- A period value that is not a month name (e.g. a quarterly label) is
  left alone: the grace check returns "no opinion" and callers skip it.

This module only does date math and raises nothing except the explicit
``assert_employee_window_open`` guard. It touches no score, weight, or
approval logic.
"""

from __future__ import annotations

import calendar
from datetime import date, timedelta

from fastapi import HTTPException, status

from app.core.months import MONTH_NAMES


def period_end(year: int, period: str) -> date | None:
    """Last calendar day of the given month, or None if not a month name."""
    if period not in MONTH_NAMES:
        return None
    month = MONTH_NAMES.index(period) + 1
    return date(year, month, calendar.monthrange(year, month)[1])


def grace_end(year: int, period: str, grace_days: int) -> date | None:
    """Last day employee entry is allowed, or None if not a month name."""
    end = period_end(year, period)
    if end is None:
        return None
    return end + timedelta(days=grace_days)


def is_current_month(year: int, period: str, today: date) -> bool:
    return year == today.year and period == MONTH_NAMES[today.month - 1]


def is_grace_month(year: int, period: str, today: date, grace_days: int) -> bool:
    """True when the period is a past month whose grace window is still open."""
    if period not in MONTH_NAMES or is_current_month(year, period, today):
        return False
    if (year, MONTH_NAMES.index(period)) >= (today.year, today.month - 1):
        return False  # current or future month, not a grace month
    end = grace_end(year, period, grace_days)
    return end is not None and today <= end


def is_fillable_period(year: int, period: str, today: date, grace_days: int) -> bool:
    """True when employees may enter scores: current month or grace month."""
    return is_current_month(year, period, today) or is_grace_month(
        year, period, today, grace_days
    )


def is_within_grace(year: int, period: str, today: date, grace_days: int) -> bool:
    """True when entry is still allowed (current month counts as within grace).

    Returns True for unparseable periods so callers never block on a label
    this module cannot interpret.
    """
    end = grace_end(year, period, grace_days)
    if end is None:
        return True
    return today <= end


def assert_not_future_month(year: int, period: str, today: date) -> None:
    """Raise 400 when the period is strictly after the current month.

    Employees may only ever enter the current month or a grace month;
    future months are admin pre-creation territory, not employee entry.
    Unparseable period labels are left alone.
    """
    if period not in MONTH_NAMES:
        return
    month = MONTH_NAMES.index(period) + 1
    if (year, month) > (today.year, today.month):
        raise HTTPException(
            status.HTTP_400_BAD_REQUEST,
            f"Score entry for {period} {year} is not open yet "
            f"(current month is {MONTH_NAMES[today.month - 1]} {today.year}).",
        )


def assert_employee_window_open(
    year: int, period: str, today: date, grace_days: int
) -> None:
    """Raise 400 when a DRAFT entry is attempted after the grace window."""
    end = grace_end(year, period, grace_days)
    if end is not None and today > end:
        raise HTTPException(
            status.HTTP_400_BAD_REQUEST,
            f"Score entry for {period} {year} closed on {end.isoformat()} "
            f"({grace_days} days after month-end).",
        )


def recurring_rollover_open(today: date, grace_days: int) -> bool:
    """True once the month's recurring KPIs may start: day grace_days + 1.

    With the default 7-day grace, recurring rollover opens on the 8th of
    each month. Days 1..grace_days belong to closing the previous month.
    """
    return today.day > grace_days


def recurring_rollover_date(today: date, grace_days: int) -> date | None:
    """The date this month's recurring KPIs start, or None if already open."""
    if recurring_rollover_open(today, grace_days):
        return None
    return date(today.year, today.month, grace_days + 1)


def assert_recurring_rollover_open(
    year: int, period: str, today: date, grace_days: int
) -> None:
    """Raise 400 when materialising the current month before rollover day.

    Past and future months are unaffected — an admin may always backfill
    or pre-create those explicitly. Only the current month waits for the
    8th (grace_days + 1), so the monthly rollover is automatic and uniform.
    """
    if period not in MONTH_NAMES:
        return
    if (
        year == today.year
        and period == MONTH_NAMES[today.month - 1]
        and not recurring_rollover_open(today, grace_days)
    ):
        rollover = date(today.year, today.month, grace_days + 1)
        raise HTTPException(
            status.HTTP_400_BAD_REQUEST,
            f"Recurring KPIs for {period} {year} open on {rollover.isoformat()} "
            f"(day {grace_days + 1} of the month). Rollover happens "
            "automatically from that day.",
        )


def _previous_month(year: int, month: int) -> tuple[int, int]:
    if month == 1:
        return year - 1, 12
    return year, month - 1


def resolve_reminder_target(
    today: date, days_before: int, grace_days: int
) -> tuple[int, str, bool, bool]:
    """Which period deadline reminders should target today.

    Returns ``(year, period, in_grace, window_open)``:
    - Inside the pre-month-end window (``days_before`` days or less to go):
      the current month, ``in_grace=False``.
    - Inside the grace week after a month ended: the previous month,
      ``in_grace=True`` — so employees with unfinished scores keep getting
      nudged while they can still act on them.
    - Otherwise: the current month with ``window_open=False`` (no reminders).
    """
    current_period = MONTH_NAMES[today.month - 1]
    last_day = calendar.monthrange(today.year, today.month)[1]
    if last_day - today.day <= days_before:
        return today.year, current_period, False, True
    prev_year, prev_month = _previous_month(today.year, today.month)
    prev_period = MONTH_NAMES[prev_month - 1]
    end = grace_end(prev_year, prev_period, grace_days)
    if end is not None and today <= end:
        return prev_year, prev_period, True, True
    return today.year, current_period, False, False


def assert_admin_creation_window_open(
    year: int, period: str, today: date, grace_days: int
) -> None:
    """Gate for ADMIN custom-KPI creation (tenant admins and dept admins).

    User rule: during days 1..grace_days of a month, admins may add the
    PREVIOUS month's KPI (the grace week exists to close the previous
    month), but NOT the current month's — its set materialises
    automatically on day grace_days + 1 — and NOT months already past
    their grace window. This is the intended admin override for the
    grace month, not a bypass of the day-8 rollover: employees' own
    automatic set still appears only on day 8.

    - Future months: allowed (admins pre-create those explicitly; the
      approved-month shift in create_custom_template also moves a
      fully-approved current month's new KPI to next month).
    - Unparseable period labels: left alone (schemas validate month names).
    """
    if period not in MONTH_NAMES:
        return
    month = MONTH_NAMES.index(period) + 1
    if (year, month) > (today.year, today.month):
        return  # future month: admin pre-creation
    if (year, month) == (today.year, today.month):
        # Current month: only once rollover opened (day grace_days + 1).
        if not recurring_rollover_open(today, grace_days):
            rollover = date(today.year, today.month, grace_days + 1)
            raise HTTPException(
                status.HTTP_400_BAD_REQUEST,
                f"KPIs for {period} {year} open on {rollover.isoformat()} "
                f"(day {grace_days + 1} of the month). During days 1–{grace_days} "
                f"you may only add the previous month's KPIs.",
            )
        return
    # Past month: allowed only while its grace window is still open.
    end = grace_end(year, period, grace_days)
    if end is not None and today > end:
        raise HTTPException(
            status.HTTP_400_BAD_REQUEST,
            f"KPIs for {period} {year} closed on {end.isoformat()} "
            f"({grace_days} days after month-end) and can no longer be added.",
        )
