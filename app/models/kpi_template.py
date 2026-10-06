from __future__ import annotations
from sqlalchemy import Boolean, Float, ForeignKey, Integer, String
from sqlalchemy.orm import Mapped, mapped_column, relationship
from app.database import Base


class KPITemplate(Base):
    """A scorable metric definition.

    Two shapes:
    - Recurring department/company-wide (employee_id is None): applies every
      period to everyone in `department_id`, or company-wide if
      `department_id` is also None.
    - Custom one-off (employee_id is set): applies only to that one employee,
      for the single period named by `locked_year` / `locked_period`.
      When `is_recurring` is True the recurrence service will automatically
      carry the template forward into every subsequent month, creating a fresh
      draft submission so the admin doesn't have to recreate it manually.
    """

    __tablename__ = "kpi_templates"

    id: Mapped[int] = mapped_column(primary_key=True)
    metric_name: Mapped[str] = mapped_column(String(200), nullable=False)
    target: Mapped[float] = mapped_column(Float, nullable=False, default=100.0)
    weight: Mapped[float] = mapped_column(Float, nullable=False, default=1.0)

    tenant_id: Mapped[int] = mapped_column(
        ForeignKey("tenants.id", ondelete="CASCADE"), nullable=False, index=True
    )
    tenant: Mapped["Tenant"] = relationship()  # noqa: F821

    department_id: Mapped[int | None] = mapped_column(
        ForeignKey("departments.id", ondelete="CASCADE"), nullable=True
    )
    department: Mapped["Department | None"] = relationship()  # noqa: F821

    employee_id: Mapped[int | None] = mapped_column(
        ForeignKey("users.id", ondelete="CASCADE"), nullable=True
    )
    employee: Mapped["User | None"] = relationship()  # noqa: F821
    locked_year: Mapped[int | None] = mapped_column(Integer, nullable=True)
    locked_period: Mapped[str | None] = mapped_column(String(20), nullable=True)

    # When True (on a custom/employee-scoped template), the recurrence service
    # will carry this template into each new month automatically.  The
    # migration defaults existing rows to False; newly assigned custom KPIs
    # default to recurring.
    is_recurring: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)

    @property
    def is_custom(self) -> bool:
        return self.employee_id is not None
