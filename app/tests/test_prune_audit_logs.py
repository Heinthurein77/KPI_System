"""Tests for the audit-log retention job (app/jobs/prune_audit_logs.py)."""

from datetime import datetime, timedelta, timezone

from app.jobs.prune_audit_logs import prune_audit_logs
from app.models.audit_log import AuditLog
from app.models.user import UserRole
from app.tests.factories import make_tenant, make_user


def _make_log(db, tenant, actor, days_old: int) -> AuditLog:
    log = AuditLog(
        tenant_id=tenant.id,
        actor_id=actor.id,
        actor_name=actor.name,
        actor_role=actor.role.value,
        department_id=None,
        action="kpi_submitted",
        entity_type="kpi_submission",
        entity_id=1,
        entity_label="Test metric",
        summary="test",
        created_at=datetime.now(timezone.utc) - timedelta(days=days_old),
    )
    db.add(log)
    db.commit()
    return log


def test_prune_deletes_only_rows_older_than_retention(db):
    tenant = make_tenant(db)
    actor = make_user(db, tenant, UserRole.EMPLOYEE)
    old = _make_log(db, tenant, actor, days_old=200)
    recent = _make_log(db, tenant, actor, days_old=10)
    boundary = _make_log(db, tenant, actor, days_old=179)

    old_id, recent_id, boundary_id = old.id, recent.id, boundary.id

    deleted = prune_audit_logs(db, retention_days=180)

    assert deleted == 1
    db.expunge_all()  # bulk delete bypasses the session cache
    assert db.get(AuditLog, old_id) is None
    assert db.get(AuditLog, recent_id) is not None
    assert db.get(AuditLog, boundary_id) is not None


def test_prune_empty_table_returns_zero(db):
    assert prune_audit_logs(db, retention_days=180) == 0
