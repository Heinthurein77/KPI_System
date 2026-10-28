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

MONTH_NAMES = (
    "January", "February", "March", "April", "May", "June",
    "July", "August", "September", "October", "November", "December",
)


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
