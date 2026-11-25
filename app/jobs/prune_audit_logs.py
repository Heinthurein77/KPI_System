"""Cron entrypoint: prune audit logs older than the retention window, plus
expired revoked-token rows, plus old notifications.

Run monthly from the server's cron (or a scheduler), e.g.::

    0 3 1 * * cd ~/kpi-app && docker compose exec -T web python -m app.jobs.prune_audit_logs >> ~/kpi-app/prune-audit.log 2>&1

The retention window defaults to 180 days and can be overridden with the
AUDIT_LOG_RETENTION_DAYS environment variable. Deletion is batched so a
large backlog never holds a long lock. Safe to run any time — only rows
older than the cutoff are touched.

Revoked tokens used to be pruned only lazily on logout (m8): a row is
only needed until its token's expiry, so this job also deletes
revoked_tokens rows whose expires_at has passed.

Notifications (S-m9): the table used to grow unboundedly — every KPI
submit/approve/reject/holiday change writes one row per recipient.
This job now also deletes notifications older than
NOTIFICATION_RETENTION_DAYS (default 90), EXCEPT ``deadline_reminder``
rows: _already_reminded keys reminder idempotency on the continued
existence of a deadline_reminder Notification row, so pruning those
would make old reminders re-fire. Reminder rows therefore accumulate
at ~1 per user per period — see docs/neon-usage-check.md.
"""

import json
import logging
import os
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

from sqlalchemy import delete, select

from app.database import SessionLocal
from app.models.audit_log import AuditLog
from app.models.notification import Notification
from app.models.revoked_token import RevokedToken
from app.services.deadline_reminder_service import REMINDER_TYPE

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
logger = logging.getLogger("kpi_system.jobs")

DEFAULT_RETENTION_DAYS = 180
DEFAULT_NOTIFICATION_RETENTION_DAYS = 90
BATCH_SIZE = 5000
# S-10: pruned audit rows are archived to JSONL first — a prune must
# never be the thing that destroys audit evidence. Path from
# AUDIT_LOG_ARCHIVE_PATH, else audit-log-archive.jsonl in the working
# directory (the server cron runs from ~/kpi-app). Callers (tests) may
# pass archive_path explicitly. NOTE: the file is append-only and is
# NOT git-tracked — add it to .gitignore / set the env var on the
# server so a local run does not leave a stray file in the repo.
DEFAULT_ARCHIVE_PATH = "audit-log-archive.jsonl"


def _archive_rows(rows: list[AuditLog], archive_path: str | Path) -> None:
    """Append one JSON object per audit row to the archive file.

    The archived row carries the hash-chain fields too, so an archived
    row can still be verified against its neighbours.
    """
    path = Path(archive_path)
    if path.parent != Path(".") and str(path.parent):
        path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "a", encoding="utf-8") as fh:
        for row in rows:
            fh.write(
                json.dumps(
                    {
                        "id": row.id,
                        "tenant_id": row.tenant_id,
                        "actor_id": row.actor_id,
                        "actor_name": row.actor_name,
                        "actor_role": row.actor_role,
                        "department_id": row.department_id,
                        "action": row.action,
                        "entity_type": row.entity_type,
                        "entity_id": row.entity_id,
                        "entity_label": row.entity_label,
                        "summary": row.summary,
                        "created_at": row.created_at.isoformat() if row.created_at else None,
                        "previous_hash": row.previous_hash,
                        "entry_hash": row.entry_hash,
                        "ip_address": row.ip_address,
                    },
                    ensure_ascii=False,
                )
                + "\n"
            )


def _resolve_archive_path(archive_path: str | Path | None) -> str:
    if archive_path is not None:
        return str(archive_path)
    return os.environ.get("AUDIT_LOG_ARCHIVE_PATH", DEFAULT_ARCHIVE_PATH)


def prune_audit_logs(
    db, retention_days: int, archive_path: str | Path | None = None
) -> int:
    """Delete audit_logs older than retention_days — archiving each
    batch to JSONL first (S-10). Returns the deleted count."""
    cutoff = datetime.now(timezone.utc) - timedelta(days=retention_days)
    archive = _resolve_archive_path(archive_path)
    total = 0
    while True:
        rows = db.scalars(
            select(AuditLog).where(AuditLog.created_at < cutoff).limit(BATCH_SIZE)
        ).all()
        if not rows:
            break
        _archive_rows(rows, archive)
        db.execute(delete(AuditLog).where(AuditLog.id.in_([r.id for r in rows])))
        db.commit()
        total += len(rows)
    if total:
        logger.info("Archived %s pruned audit log row(s) to %s.", total, archive)
    return total


def prune_revoked_tokens(db) -> int:
    """Delete revoked_tokens rows whose token has expired (m8).

    A revoked row is only needed until its token's expiry — after that
    the token is dead by expiry anyway. Returns the deleted count.
    """
    now = datetime.now(timezone.utc)
    total = 0
    while True:
        ids = db.scalars(
            select(RevokedToken.jti).where(RevokedToken.expires_at < now).limit(BATCH_SIZE)
        ).all()
        if not ids:
            break
        db.execute(delete(RevokedToken).where(RevokedToken.jti.in_(ids)))
        db.commit()
        total += len(ids)
    return total


def prune_notifications(db, retention_days: int) -> int:
    """Delete notifications older than retention_days — EXCEPT
    ``deadline_reminder`` rows (S-m9).

    The deadline-reminder idempotency marker
    (``_already_reminded``) keys on the continued existence of a
    ``deadline_reminder`` Notification row, so those rows must survive
    pruning or old reminders would re-fire. Everything else — KPI
    workflow and holiday notifications — is inbox history once read,
    and is pruned like the audit logs. Returns the deleted count.
    """
    cutoff = datetime.now(timezone.utc) - timedelta(days=retention_days)
    total = 0
    while True:
        ids = db.scalars(
            select(Notification.id)
            .where(Notification.created_at < cutoff)
            .where(Notification.type != REMINDER_TYPE)
            .limit(BATCH_SIZE)
        ).all()
        if not ids:
            break
        db.execute(delete(Notification).where(Notification.id.in_(ids)))
        db.commit()
        total += len(ids)
    return total


def main() -> int:
    # M20: distinct timestamped START / SUCCESS / FAILURE lines (with a
    # non-zero exit code on failure) so a dead or failing prune is
    # visible in the cron log instead of silently not running.
    logger.info("Audit-log prune job starting.")
    try:
        retention_days = int(os.environ.get("AUDIT_LOG_RETENTION_DAYS", str(DEFAULT_RETENTION_DAYS)))
    except ValueError:
        logger.error("Invalid AUDIT_LOG_RETENTION_DAYS; falling back to %s days.", DEFAULT_RETENTION_DAYS)
        retention_days = DEFAULT_RETENTION_DAYS
    if retention_days < 30:
        logger.error("Refusing to prune with retention < 30 days (got %s).", retention_days)
        return 1
    try:
        notification_retention_days = int(
            os.environ.get(
                "NOTIFICATION_RETENTION_DAYS",
                str(DEFAULT_NOTIFICATION_RETENTION_DAYS),
            )
        )
    except ValueError:
        logger.error(
            "Invalid NOTIFICATION_RETENTION_DAYS; falling back to %s days.",
            DEFAULT_NOTIFICATION_RETENTION_DAYS,
        )
        notification_retention_days = DEFAULT_NOTIFICATION_RETENTION_DAYS
    if notification_retention_days < 30:
        logger.error(
            "Refusing to prune notifications with retention < 30 days (got %s).",
            notification_retention_days,
        )
        return 1

    db = SessionLocal()
    try:
        deleted = prune_audit_logs(db, retention_days)
        revoked_deleted = prune_revoked_tokens(db)
        notifications_deleted = prune_notifications(db, notification_retention_days)
    except Exception:
        logger.exception("Audit-log prune job FAILED.")
        return 1
    finally:
        db.close()
    logger.info(
        "Audit-log prune job succeeded: pruned %s audit log rows older than %s days "
        "(archived to %s), "
        "%s notifications older than %s days "
        "(deadline_reminder rows kept), "
        "and %s expired revoked-token rows.",
        deleted, retention_days,
        _resolve_archive_path(None),
        notifications_deleted, notification_retention_days,
        revoked_deleted,
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
