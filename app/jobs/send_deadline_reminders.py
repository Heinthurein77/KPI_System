"""Cron entrypoint: send KPI deadline reminders for all tenants.

Run daily from the server's cron (or a scheduler), e.g.::

    docker compose exec -T web python -m app.jobs.send_deadline_reminders

The job is idempotent — each user gets at most one reminder per period — and
does nothing outside the reminder window, so running it daily is safe.
"""

import logging
import sys

from app.database import SessionLocal
from app.services import deadline_reminder_service

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
logger = logging.getLogger("kpi_system.jobs")


logger = logging.getLogger("kpi_system.jobs")


def main() -> int:
    db = SessionLocal()
    try:
        # Sync email delivery: the cron process exits right after this
        # call, so a background thread could be killed mid-send and the
        # failure would never be retried (the notification row already
        # marks the user reminded). In sync mode a failed email's row is
        # deleted, so the next run retries it.
        result = deadline_reminder_service.send_deadline_reminders(db, sync_email=True)
    finally:
        db.close()
    logger.info(
        "Deadline reminders: window_open=%s year=%s period=%s reminded=%s already_reminded=%s per_tenant=%s errors=%s",
        result.window_open,
        result.year,
        result.period,
        result.reminded,
        result.already_reminded,
        result.per_tenant,
        result.errors,
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
