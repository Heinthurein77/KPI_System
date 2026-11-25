from __future__ import annotations
from datetime import datetime, timezone
from sqlalchemy import DateTime, ForeignKey, Integer, String, Text
from sqlalchemy.orm import Mapped, mapped_column, relationship
from app.database import Base
def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


class AuditLog(Base):
    """An immutable record of who changed what KPI/target data, and when.

    Actor name/role are snapshotted at write time (not just a live FK) so the
    trail stays readable even after the acting user is later deleted or
    changes role — the same reasoning as KPISubmission.remarks staying put
    regardless of what happens to the reviewer account afterward.
    """

    __tablename__ = "audit_logs"

    id: Mapped[int] = mapped_column(primary_key=True)

    # NULL = platform scope: actions by the platform super-admin (tenant
    # delete, platform login) have no tenant. S-10 (security review
    # 2026-11-25): the FK is ON DELETE SET NULL, not CASCADE — deleting
    # a tenant must NOT erase that tenant's audit history. The rows
    # survive with tenant_id nulled (they stay queryable by actor /
    # action / date); only the "tenant deleted" row itself is logged
    # platform-scoped (tenant_id NULL from the start).
    tenant_id: Mapped[int | None] = mapped_column(
        ForeignKey("tenants.id", ondelete="SET NULL"), nullable=True, index=True
    )
    tenant: Mapped["Tenant"] = relationship()  # noqa: F821

    actor_id: Mapped[int | None] = mapped_column(
        ForeignKey("users.id", ondelete="SET NULL"), nullable=True
    )
    actor: Mapped["User | None"] = relationship()  # noqa: F821
    actor_name: Mapped[str] = mapped_column(String(200), nullable=False)
    actor_role: Mapped[str] = mapped_column(String(50), nullable=False)

    department_id: Mapped[int | None] = mapped_column(
        ForeignKey("departments.id", ondelete="SET NULL"), nullable=True, index=True
    )

    action: Mapped[str] = mapped_column(String(50), nullable=False)
    entity_type: Mapped[str] = mapped_column(String(50), nullable=False)
    entity_id: Mapped[int] = mapped_column(Integer, nullable=False)
    entity_label: Mapped[str] = mapped_column(Text, nullable=False)
    summary: Mapped[str] = mapped_column(Text, nullable=False)

    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=_utcnow, nullable=False, index=True
    )

    # S-10: tamper-evidence. entry_hash is sha256(previous_hash + this
    # row's fields), computed in audit_service.log; previous_hash is the
    # previous row's entry_hash ("" for the first row). Any edit, delete
    # or reorder breaks verify_audit_chain. Rows created outside
    # audit_service.log (e.g. old direct inserts in tests) have NULL
    # hashes and are skipped by verification, not treated as tamper.
    previous_hash: Mapped[str | None] = mapped_column(String(64), nullable=True)
    entry_hash: Mapped[str | None] = mapped_column(String(64), nullable=True)

    # S-10: client IP where the call site had a request (populated by
    # audit_service.log's ip_address argument). NULL where the call site
    # has no request (service-layer calls, cron jobs) — absence is
    # recorded as NULL, never faked.
    ip_address: Mapped[str | None] = mapped_column(String(45), nullable=True)
