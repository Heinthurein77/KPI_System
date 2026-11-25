import hashlib

from sqlalchemy import select

from app.models.audit_log import AuditLog
from app.models.user import User


def _entry_hash(
    previous_hash: str,
    tenant_id: int | None,
    actor_id: int | None,
    actor_name: str,
    actor_role: str,
    department_id: int | None,
    action: str,
    entity_type: str,
    entity_id: int,
    entity_label: str,
    summary: str,
    ip_address: str | None,
) -> str:
    """S-10 hash-chain link: sha256 over the previous row's hash plus
    this row's fields. Any edit to a stored row changes its recomputed
    hash, so verify_audit_chain detects tampering. created_at is
    deliberately NOT hashed: SQLite does not round-trip a timezone-aware
    datetime exactly (tz is dropped on storage), so the pre-store and
    post-store values would hash differently and verification would
    always fail."""
    payload = "|".join(
        [
            previous_hash or "",
            str(tenant_id),
            str(actor_id),
            actor_name,
            actor_role,
            str(department_id),
            action,
            entity_type,
            str(entity_id),
            entity_label,
            summary,
            ip_address or "",
        ]
    )
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def log(
    db,
    actor: User,
    action: str,
    entity_type: str,
    entity_id: int,
    entity_label: str,
    summary: str,
    department_id: int | None = None,
    ip_address: str | None = None,
) -> None:
    """Records one audit entry. Added to the session but NOT committed here —
    callers commit it together with the change it describes, so the two never
    diverge (no audit entry for a change that rolled back, or vice versa).

    S-10: the entry is hash-chained to the previous row (latest by id).
    The session runs with autoflush=False, so log() flushes first —
    otherwise rows logged earlier in the same session would be
    invisible to the lookup and every row in a batch would chain to
    the same (stale) previous hash. ``ip_address`` is the client IP
    where the call site had a request; None elsewhere (never faked).
    """
    from app.models.audit_log import _utcnow

    created_at = _utcnow()
    db.flush()
    previous_hash = db.scalar(
        select(AuditLog.entry_hash)
        .where(AuditLog.entry_hash.is_not(None))
        .order_by(AuditLog.id.desc())
        .limit(1)
    ) or ""
    entry_hash = _entry_hash(
        previous_hash,
        actor.tenant_id,
        actor.id,
        actor.name,
        actor.role.value,
        department_id,
        action,
        entity_type,
        entity_id,
        entity_label,
        summary,
        ip_address,
    )
    db.add(
        AuditLog(
            tenant_id=actor.tenant_id,
            actor_id=actor.id,
            actor_name=actor.name,
            actor_role=actor.role.value,
            department_id=department_id,
            action=action,
            entity_type=entity_type,
            entity_id=entity_id,
            entity_label=entity_label,
            summary=summary,
            created_at=created_at,
            previous_hash=previous_hash,
            entry_hash=entry_hash,
            ip_address=ip_address,
        )
    )


def verify_audit_chain(db) -> tuple[bool, int | None]:
    """Recompute every hash-chained row in id order.

    Returns (True, None) when the chain is intact, else (False, id of
    the first row whose stored hash or previous-link does not match).
    Rows with no entry_hash (created outside log(), e.g. legacy rows)
    are skipped, not treated as tampering.
    """
    rows = db.scalars(select(AuditLog).order_by(AuditLog.id)).all()
    expected_previous = ""
    for row in rows:
        if row.entry_hash is None:
            continue
        if (row.previous_hash or "") != expected_previous:
            return False, row.id
        recomputed = _entry_hash(
            row.previous_hash or "",
            row.tenant_id,
            row.actor_id,
            row.actor_name,
            row.actor_role,
            row.department_id,
            row.action,
            row.entity_type,
            row.entity_id,
            row.entity_label,
            row.summary,
            row.ip_address,
        )
        if recomputed != row.entry_hash:
            return False, row.id
        expected_previous = row.entry_hash
    return True, None


def fmt_score(value: float | None) -> str:
    return "—" if value is None else f"{value:g}"
