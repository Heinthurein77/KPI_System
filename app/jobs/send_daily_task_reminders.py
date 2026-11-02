"""Cron entrypoint: weekday 16:00 Daily Task diary reminders.

Run Monday–Friday at 16:00 from the server's cron::

    0 16 * * 1-5 docker compose exec -T web python -m app.jobs.send_daily_task_reminders
"""

import logging
import sys

from app.database import SessionLocal
from app.services import daily_task_reminder_service

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
logger = logging.getLogger("kpi_system.jobs")


def main() -> int:
    db = SessionLocal()
    try:
        result = daily_task_reminder_service.send_daily_task_reminders(db)
    finally:
        db.close()
    logger.info(
        "Daily task reminders: date=%s reminded=%d skipped=%d",
        result["date"],
        len(result["reminded"]),
        len(result["skipped"]),
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
