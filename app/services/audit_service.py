from app.models.audit_log import AuditLog
from app.models.user import User


def log(
    db,
    actor: User,
    action: str,
    entity_type: str,
    entity_id: int,
    entity_label: str,
    summary: str,
    department_id: int | None = None,
) -> None:
    """Records one audit entry. Added to the session but NOT committed here —
    callers commit it together with the change it describes, so the two never
    diverge (no audit entry for a change that rolled back, or vice versa)."""
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
        )
    )


def fmt_score(value: float | None) -> str:
    return "—" if value is None else f"{value:g}"
