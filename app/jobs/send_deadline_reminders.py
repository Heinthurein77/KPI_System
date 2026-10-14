"""Cron entrypoint: send KPI deadline reminders for all tenants.

Run daily from the server's cron (or a scheduler), e.g.::

    docker compose exec -T backend python -m app.jobs.send_deadline_reminders

The job is idempotent — each user gets at most one reminder per period — and
does nothing outside the reminder window, so running it daily is safe.
"""

import logging
import sys

from app.database import SessionLocal
from app.services import deadline_reminder_service

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
logger = logging.getLogger("kpi_system.jobs")


def main() -> int:
    db = SessionLocal()
    try:
        result = deadline_reminder_service.send_deadline_reminders(db)
    finally:
        db.close()
    logger.info(
        "Deadline reminders: window_open=%s year=%s period=%s reminded=%s already_reminded=%s per_tenant=%s",
        result.window_open,
        result.year,
        result.period,
        result.reminded,
        result.already_reminded,
        result.per_tenant,
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
