from __future__ import annotations
import enum
from datetime import datetime, timezone
from sqlalchemy import DateTime, Enum, String
from sqlalchemy.orm import Mapped, mapped_column, relationship
from app.database import Base


class TenantStatus(str, enum.Enum):
    ACTIVE = "active"
    SUSPENDED = "suspended"


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


class Tenant(Base):
    """An organization using the platform. Every department/user/KPI record
    belongs to exactly one tenant; platform Super Admins belong to none."""

    __tablename__ = "tenants"

    id: Mapped[int] = mapped_column(primary_key=True)
    name: Mapped[str] = mapped_column(String(200), nullable=False)
    slug: Mapped[str] = mapped_column(String(100), unique=True, nullable=False, index=True)
    domain: Mapped[str | None] = mapped_column(String(255), unique=True, nullable=True)
    status: Mapped[TenantStatus] = mapped_column(
        Enum(TenantStatus), nullable=False, default=TenantStatus.ACTIVE
    )
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_utcnow, nullable=False)

    # passive_deletes=True: User.tenant_id is nullable (platform admins have
    # none), so without this SQLAlchemy's default cascade would UPDATE users
    # SET tenant_id = NULL on tenant delete instead of deleting them. This
    # tells the ORM to leave it to the DB's ON DELETE CASCADE instead.
    users: Mapped[list["User"]] = relationship(back_populates="tenant", passive_deletes=True)  # noqa: F821

    @property
    def is_active(self) -> bool:
        return self.status == TenantStatus.ACTIVE
