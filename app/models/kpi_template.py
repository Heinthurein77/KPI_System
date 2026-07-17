from __future__ import annotations

from sqlalchemy import Float, ForeignKey, Integer, String
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.database import Base


class KPITemplate(Base):
    """A scorable metric definition.

    Two shapes:
    - Recurring (employee_id is None): applies every period to everyone in
      `department_id`, or company-wide if `department_id` is also None.
    - Custom one-off (employee_id is set): applies only to that one employee,
      only for the single period named by `locked_year` / `locked_period`.
    """

    __tablename__ = "kpi_templates"

    id: Mapped[int] = mapped_column(primary_key=True)
    metric_name: Mapped[str] = mapped_column(String(200), nullable=False)
    target: Mapped[float] = mapped_column(Float, nullable=False, default=100.0)
    weight: Mapped[float] = mapped_column(Float, nullable=False, default=1.0)

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

    @property
    def is_custom(self) -> bool:
        return self.employee_id is not None
