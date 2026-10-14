from __future__ import annotations
from datetime import datetime, timezone
from sqlalchemy import Boolean, DateTime, ForeignKey, Integer, String, Text
from sqlalchemy.orm import Mapped, mapped_column, relationship
from app.database import Base


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


class Notification(Base):
    """In-app notification for a single user.

    Created by workflow transitions in kpi_service (submit / dept approve /
    reject / final approve / override). Recipient name is snapshotted at write
    time so the inbox stays readable even if the user is later renamed.
    """

    __tablename__ = "notifications"

    id: Mapped[int] = mapped_column(primary_key=True)

    tenant_id: Mapped[int] = mapped_column(
        ForeignKey("tenants.id", ondelete="CASCADE"), nullable=False, index=True
    )
    tenant: Mapped["Tenant"] = relationship()  # noqa: F821

    user_id: Mapped[int] = mapped_column(
        ForeignKey("users.id", ondelete="CASCADE"), nullable=False, index=True
    )
    user: Mapped["User"] = relationship()  # noqa: F821

    # Machine-readable kind: kpi_submitted, kpi_dept_approved,
    # kpi_rejected, kpi_final_approved, kpi_overridden.
    type: Mapped[str] = mapped_column(String(50), nullable=False)
    title: Mapped[str] = mapped_column(String(200), nullable=False)
    message: Mapped[str] = mapped_column(Text, nullable=False)
    # Frontend route the notification links to, e.g. "/dashboard?year=2026&period=October".
    link: Mapped[str | None] = mapped_column(String(500), nullable=True)

    is_read: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)

    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_utcnow, nullable=False)
