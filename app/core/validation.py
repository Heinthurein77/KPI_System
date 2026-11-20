"""One shared year/period validator (B-m1, senior review 2026-11-20).

Year/period validation used to live in five different places with
different ranges and error codes (schema mixin 2000-2100/422, importer
2000-2100/400, attendance 1-9999/400, dashboard + analytics copies that
never validated the year at all). Every caller below goes through here,
so the same input gets the same verdict on every endpoint.

Two shapes:
- ``validate_year`` / ``validate_period`` raise ValueError — for pydantic
  field validators, which turn ValueError into a 422.
- ``validate_year_period`` / ``resolve_year_period`` raise HTTPException
  (422 by default) — for routers and query params.
- ``validate_month_bounds`` is the attendance variant: a numeric month
  (1-12) and a 1-9999 year range with a 400, unchanged from the old
  ``_month_bounds`` behaviour (attendance is additive-only; its frontend
  depends on the current codes and ranges).
"""

from __future__ import annotations

import calendar
from datetime import date as date_type

from fastapi import HTTPException
from pydantic import BaseModel, field_validator

from app.core.months import MONTH_NAMES

MIN_YEAR, MAX_YEAR = 2000, 2100
VALID_PERIODS = tuple(MONTH_NAMES)


def validate_year(value: int, *, min_year: int = MIN_YEAR, max_year: int = MAX_YEAR) -> int:
    """A calendar year in range; ValueError (→ 422 in pydantic) otherwise."""
    if isinstance(value, bool) or not isinstance(value, int) or not (min_year <= value <= max_year):
        raise ValueError(f"year must be between {min_year} and {max_year}")
    return value


def validate_period(value: str) -> str:
    """A full calendar month name; ValueError (→ 422 in pydantic) otherwise."""
    if value not in VALID_PERIODS:
        raise ValueError(f"period must be one of: {', '.join(VALID_PERIODS)}")
    return value


def validate_year_period(
    year: int,
    period: str,
    *,
    min_year: int = MIN_YEAR,
    max_year: int = MAX_YEAR,
    status_code: int = 422,
) -> tuple[int, str]:
    """Validate an explicit (year, period) pair; HTTPException otherwise."""
    try:
        validate_year(year, min_year=min_year, max_year=max_year)
        validate_period(period)
    except ValueError as exc:
        raise HTTPException(status_code, str(exc)) from exc
    return year, period


def resolve_year_period(
    year: int | None,
    period: str | None,
    *,
    status_code: int = 422,
) -> tuple[int, str]:
    """Apply the current-month defaults, then validate (dashboard/analytics).

    Replaces the duplicated ``_validated_year_period`` copies that used to
    live in dashboard.py and analytics.py — same defaults, same 422, and
    the year is now validated too (the old copies never checked it).
    """
    from app.core.timezone import today as app_today

    today = app_today()
    year = year if year is not None else today.year
    period = period if period is not None else MONTH_NAMES[today.month - 1]
    return validate_year_period(year, period, status_code=status_code)


def validate_month_bounds(
    year: int,
    month: int,
    *,
    min_year: int = 1,
    max_year: int = 9999,
    status_code: int = 400,
) -> tuple[date_type, date_type]:
    """First and last day of a numeric (year, month); HTTPException otherwise.

    The attendance variant of the rule: years 1-9999 with a 400, exactly
    as the old ``_month_bounds`` behaved. The bound is inclusive (not
    first-of-next-month) so December 9999 is representable.
    """
    if not min_year <= year <= max_year:
        raise HTTPException(status_code, f"year must be {min_year}-{max_year}")
    if not 1 <= month <= 12:
        raise HTTPException(status_code, "month must be 1-12")
    _, last_day = calendar.monthrange(year, month)
    return date_type(year, month, 1), date_type(year, month, last_day)


class PeriodYearValidated(BaseModel):
    """Pydantic mixin: year/period are validated at the schema boundary."""

    @field_validator("period", check_fields=False)
    @classmethod
    def _check_period(cls, v: str) -> str:
        return validate_period(v)

    @field_validator("year", check_fields=False)
    @classmethod
    def _check_year(cls, v: int) -> int:
        return validate_year(v)
