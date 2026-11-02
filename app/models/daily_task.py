"""Daily Task diary models (additive-only; no KPI calculation code touched).

One DailyTaskEntry is one diary row: an employee's work for one day.
Header fields (employee name, department, position) are NOT stored per row
— they come from the author's User record at read time, exactly like the
Excel header block. The month-end review lives in DailyTaskMonthlyReview.
"""

from __future__ import annotations

from datetime import date as date_type, datetime, timezone

from sqlalchemy import (
    Date,
    DateTime,
    ForeignKey,
    Integer,
    String,
    Text,
    UniqueConstraint,
)
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.database import Base


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


# Status values shown in the Status dropdown. "pending" covers the Excel
# Monthly Dashboard's "In Progress / Pending" bucket together with
# "in_progress" when the monthly counts are computed.
TASK_STATUSES = ("not_started", "in_progress", "completed", "pending")


class DailyTaskEntry(Base):
    __tablename__ = "daily_task_entries"

    id: Mapped[int] = mapped_column(primary_key=True)

    tenant_id: Mapped[int] = mapped_column(
        ForeignKey("tenants.id", ondelete="CASCADE"), nullable=False, index=True
    )
    user_id: Mapped[int] = mapped_column(
        ForeignKey("users.id", ondelete="CASCADE"), nullable=False, index=True
    )
    user: Mapped["User"] = relationship(foreign_keys=[user_id])  # noqa: F821
    department_id: Mapped[int | None] = mapped_column(
        ForeignKey("departments.id", ondelete="SET NULL"), nullable=True, index=True
    )

    task_date: Mapped[date_type] = mapped_column(Date, nullable=False, index=True)
    plan_linkage: Mapped[str | None] = mapped_column(String(300), nullable=True)
    work_done: Mapped[str] = mapped_column(Text, nullable=False)
    status: Mapped[str] = mapped_column(
        String(20), nullable=False, default="in_progress"
    )
    figure_output: Mapped[str | None] = mapped_column(String(300), nullable=True)
    # Free-text evidence (Drive link / report name) as in the Excel column.
    evidence_link: Mapped[str | None] = mapped_column(String(500), nullable=True)
    # Server-side uploaded evidence file (stored filename only; the file
    # itself lives under uploads/daily_tasks/). Null when no file attached.
    evidence_file: Mapped[str | None] = mapped_column(String(300), nullable=True)
    difficulties: Mapped[str | None] = mapped_column(Text, nullable=True)
    support_needed: Mapped[str | None] = mapped_column(Text, nullable=True)
    self_reflection: Mapped[str | None] = mapped_column(Text, nullable=True)
    tomorrow_plan_1: Mapped[str | None] = mapped_column(String(300), nullable=True)
    tomorrow_plan_2: Mapped[str | None] = mapped_column(String(300), nullable=True)
    tomorrow_plan_3: Mapped[str | None] = mapped_column(String(300), nullable=True)

    # Supervisor feedback: only a dept admin of the author's department
    # (or a tenant admin) may write this; the author sees it read-only.
    supervisor_feedback: Mapped[str | None] = mapped_column(Text, nullable=True)
    feedback_by_id: Mapped[int | None] = mapped_column(
        ForeignKey("users.id", ondelete="SET NULL"), nullable=True
    )

    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=_utcnow, nullable=False
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=_utcnow, onupdate=_utcnow, nullable=False
    )


class DailyTaskMonthlyReview(Base):
    """Month-end review text, one row per (tenant, user, year, month)."""

    __tablename__ = "daily_task_monthly_reviews"
    __table_args__ = (
        UniqueConstraint(
            "tenant_id", "user_id", "year", "month",
            name="uq_daily_review_tenant_user_month",
        ),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    tenant_id: Mapped[int] = mapped_column(
        ForeignKey("tenants.id", ondelete="CASCADE"), nullable=False, index=True
    )
    user_id: Mapped[int] = mapped_column(
        ForeignKey("users.id", ondelete="CASCADE"), nullable=False, index=True
    )
    year: Mapped[int] = mapped_column(Integer, nullable=False)
    month: Mapped[int] = mapped_column(Integer, nullable=False)
    achievements: Mapped[str | None] = mapped_column(Text, nullable=True)
    challenges_solutions: Mapped[str | None] = mapped_column(Text, nullable=True)
    improvements_next_focus: Mapped[str | None] = mapped_column(Text, nullable=True)
    # HOD evaluation: dept admin of the author's department / tenant admin.
    hod_evaluation: Mapped[str | None] = mapped_column(Text, nullable=True)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=_utcnow, onupdate=_utcnow, nullable=False
    )
