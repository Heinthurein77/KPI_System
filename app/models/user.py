from __future__ import annotations

import enum

from sqlalchemy import Enum, ForeignKey, String, Boolean
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.database import Base


class UserRole(str, enum.Enum):
    SUPER_ADMIN = "super_admin"
    DEPT_ADMIN = "dept_admin"
    EMPLOYEE = "employee"


class User(Base):
    __tablename__ = "users"

    id: Mapped[int] = mapped_column(primary_key=True)
    name: Mapped[str] = mapped_column(String(120), nullable=False)
    email: Mapped[str] = mapped_column(String(255), unique=True, nullable=False, index=True)
    password_hash: Mapped[str] = mapped_column(String(255), nullable=False)
    role: Mapped[UserRole] = mapped_column(Enum(UserRole), nullable=False, default=UserRole.EMPLOYEE)
    is_active: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)

    department_id: Mapped[int | None] = mapped_column(
        ForeignKey("departments.id", ondelete="SET NULL"), nullable=True
    )
    department: Mapped["Department"] = relationship(back_populates="users")  # noqa: F821

    submissions: Mapped[list["KPISubmission"]] = relationship(  # noqa: F821
        back_populates="employee",
        foreign_keys="KPISubmission.employee_id",
        cascade="all, delete-orphan",
    )

    @property
    def is_super_admin(self) -> bool:
        return self.role == UserRole.SUPER_ADMIN

    @property
    def is_dept_admin(self) -> bool:
        return self.role == UserRole.DEPT_ADMIN

    @property
    def is_employee(self) -> bool:
        return self.role == UserRole.EMPLOYEE
