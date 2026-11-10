"""Single source of "today" for the whole app.

All period/month/grace/reminder math must use this, never ``date.today()``
or ``datetime.now(timezone.utc).date()`` directly: the business operates
on Asia/Yangon time, and server-local/UTC dates shift month boundaries by
hours (e.g. a bulk import run in the first hours of a Yangon month would
otherwise land in the previous month, and a reminder run near a UTC/Yangon
midnight boundary would target the wrong day).
"""

from __future__ import annotations

from datetime import date, datetime
from zoneinfo import ZoneInfo

from app.core.config import settings


def today() -> date:
    """Current calendar date in the app's configured timezone."""
    return datetime.now(ZoneInfo(settings.TIMEZONE)).date()


def now() -> datetime:
    """Current date-time in the app's configured timezone (time-of-day
    variant of today())."""
    return datetime.now(ZoneInfo(settings.TIMEZONE))
