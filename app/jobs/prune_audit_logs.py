"""Cron entrypoint: prune audit logs older than the retention window.

Run monthly from the server's cron (or a scheduler), e.g.::

    0 3 1 * * cd ~/kpi-app && docker compose exec -T web python -m app.jobs.prune_audit_logs >> ~/kpi-app/prune-audit.log 2>&1

The retention window defaults to 180 days and can be overridden with the
AUDIT_LOG_RETENTION_DAYS environment variable. Deletion is batched so a
large backlog never holds a long lock. Safe to run any time — only rows
older than the cutoff are touched.
"""

import logging
import os
import sys
from datetime import datetime, timedelta, timezone

from sqlalchemy import delete, select

from app.database import SessionLocal
from app.models.audit_log import AuditLog

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
logger = logging.getLogger("kpi_system.jobs")

DEFAULT_RETENTION_DAYS = 180
BATCH_SIZE = 5000


def prune_audit_logs(db, retention_days: int) -> int:
    """Delete audit_logs older than retention_days. Returns the deleted count."""
    cutoff = datetime.now(timezone.utc) - timedelta(days=retention_days)
    total = 0
    while True:
        ids = db.scalars(select(AuditLog.id).where(AuditLog.created_at < cutoff).limit(BATCH_SIZE)).all()
        if not ids:
            break
        db.execute(delete(AuditLog).where(AuditLog.id.in_(ids)))
        db.commit()
        total += len(ids)
    return total


def main() -> int:
    try:
        retention_days = int(os.environ.get("AUDIT_LOG_RETENTION_DAYS", str(DEFAULT_RETENTION_DAYS)))
    except ValueError:
        logger.error("Invalid AUDIT_LOG_RETENTION_DAYS; falling back to %s days.", DEFAULT_RETENTION_DAYS)
        retention_days = DEFAULT_RETENTION_DAYS
    if retention_days < 30:
        logger.error("Refusing to prune with retention < 30 days (got %s).", retention_days)
        return 1

    db = SessionLocal()
    try:
        deleted = prune_audit_logs(db, retention_days)
    finally:
        db.close()
    logger.info("Pruned %s audit log rows older than %s days.", deleted, retention_days)
    return 0


if __name__ == "__main__":
    sys.exit(main())
