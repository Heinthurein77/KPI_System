from __future__ import annotations

import enum
from datetime import datetime, timezone

from sqlalchemy import DateTime, Enum, Float, ForeignKey, Integer, String, Text, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.database import Base


class KPIStatus(str, enum.Enum):
    DRAFT = "draft"
    PENDING_DEPT_APPROVAL = "pending_dept_approval"
    PENDING_FINAL_APPROVAL = "pending_final_approval"
    APPROVED = "approved"
    REJECTED = "rejected"


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


class KPISubmission(Base):
    """One scored metric instance for an employee in a given review period."""

    __tablename__ = "kpi_submissions"
    __table_args__ = (
        UniqueConstraint(
            "employee_id", "kpi_template_id", "year", "month_or_quarter",
            name="uq_submission_period_metric",
        ),
    )

    id: Mapped[int] = mapped_column(primary_key=True)

    employee_id: Mapped[int] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), nullable=False)
    department_id: Mapped[int | None] = mapped_column(
        ForeignKey("departments.id", ondelete="SET NULL"), nullable=True
    )
    kpi_template_id: Mapped[int] = mapped_column(
        ForeignKey("kpi_templates.id", ondelete="CASCADE"), nullable=False
    )

    year: Mapped[int] = mapped_column(Integer, nullable=False)
    month_or_quarter: Mapped[str] = mapped_column(String(20), nullable=False)  # e.g. "January"

    self_score: Mapped[float | None] = mapped_column(Float, nullable=True)
    dept_score: Mapped[float | None] = mapped_column(Float, nullable=True)
    final_score: Mapped[float | None] = mapped_column(Float, nullable=True)

    status: Mapped[KPIStatus] = mapped_column(
        Enum(KPIStatus), nullable=False, default=KPIStatus.DRAFT
    )
    remarks: Mapped[str | None] = mapped_column(Text, nullable=True)

    dept_reviewed_by_id: Mapped[int | None] = mapped_column(
        ForeignKey("users.id", ondelete="SET NULL"), nullable=True
    )
    final_reviewed_by_id: Mapped[int | None] = mapped_column(
        ForeignKey("users.id", ondelete="SET NULL"), nullable=True
    )

    submitted_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    dept_reviewed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    final_reviewed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)

    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_utcnow, nullable=False)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=_utcnow, onupdate=_utcnow, nullable=False
    )

    employee: Mapped["User"] = relationship(  # noqa: F821
        back_populates="submissions", foreign_keys=[employee_id]
    )
    department: Mapped["Department | None"] = relationship()  # noqa: F821
    kpi_template: Mapped["KPITemplate"] = relationship()  # noqa: F821
    dept_reviewer: Mapped["User | None"] = relationship(foreign_keys=[dept_reviewed_by_id])  # noqa: F821
    final_reviewer: Mapped["User | None"] = relationship(foreign_keys=[final_reviewed_by_id])  # noqa: F821

    @property
    def effective_score(self) -> float | None:
        """The most authoritative score available for this submission."""
        return self.final_score if self.final_score is not None else (
            self.dept_score if self.dept_score is not None else self.self_score
        )
