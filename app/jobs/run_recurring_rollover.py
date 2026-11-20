"""Cron entrypoint: materialise the current month's recurring KPI set (S-M1).

The day-8 rollover was documented as "automatic" (recurring_service.py)
but had no automation behind it: no cron existed, no UI calls
POST /api/admin/recurring/run, and the only live path was lazy
per-employee materialisation when each employee opened their dashboard
after day 8. This job is the real automation.

Run daily from the server's cron (see docs/backup-and-restore.md), e.g.::

    docker compose exec -T web python -m app.jobs.run_recurring_rollover

The job materialises recurring custom templates for the CURRENT period
(all tenants) via ``materialise_recurring_for_period``, which is
idempotent via the submissions UniqueConstraint — running it daily is
safe, and re-running it on the same day creates nothing twice.

The day-8 rule itself is unchanged: before the rollover day
(SUBMISSION_GRACE_DAYS + 1, the 8th by default) the job does nothing
and exits 0 — the set still starts on day 8, exactly as the lazy path
and the admin endpoint already enforce. The lazy per-employee path
stays as a fallback, so a missed cron run never strands a month.
"""

import logging
import sys
from datetime import date

from app.core.config import settings
from app.core.months import MONTH_NAMES
from app.core.timezone import today as app_today
from app.database import SessionLocal
from app.services import recurring_service, submission_grace

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
logger = logging.getLogger("kpi_system.jobs")


def run_recurring_rollover(db, today: date | None = None) -> recurring_service.RecurrenceResult | None:
    """Materialise the current period's recurring set, or None when the
    rollover has not opened yet (days 1..grace_days — the set starts on
    day grace_days + 1). ``today`` is injectable for tests."""
    today = today or app_today()
    period = MONTH_NAMES[today.month - 1]
    if not submission_grace.recurring_rollover_open(today, settings.SUBMISSION_GRACE_DAYS):
        logger.info(
            "Recurring rollover not open yet: %s %s starts %s.",
            period,
            today.year,
            submission_grace.recurring_rollover_date(today, settings.SUBMISSION_GRACE_DAYS),
        )
        return None
    return recurring_service.materialise_recurring_for_period(db, today.year, period)


def main() -> int:
    # M20-style START / SUCCESS / FAILURE lines (same contract as the
    # other cron jobs) so a dead/failed run is visible in the cron log.
    logger.info("Recurring rollover job starting.")
    db = SessionLocal()
    try:
        result = run_recurring_rollover(db)
    except Exception:
        logger.exception("Recurring rollover job FAILED.")
        return 1
    finally:
        db.close()
    if result is None:
        logger.info("Recurring rollover job succeeded: rollover not open yet, nothing materialised.")
    else:
        logger.info(
            "Recurring rollover job succeeded: year=%s period=%s created=%s skipped=%s errors=%s",
            result.year,
            result.period,
            result.created_count,
            result.skipped_count,
            result.errors,
        )
    return 0


if __name__ == "__main__":
    sys.exit(main())
