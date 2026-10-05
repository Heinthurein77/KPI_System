from __future__ import annotations
import enum
from sqlalchemy import Enum, ForeignKey, Index, String, Boolean, UniqueConstraint, text
from sqlalchemy.orm import Mapped, mapped_column, relationship
from app.database import Base


class UserRole(str, enum.Enum):
    SUPER_ADMIN = "super_admin"    # platform-level only; tenant_id IS NULL
    TENANT_ADMIN = "tenant_admin"  # tenant-wide admin; takes over what SUPER_ADMIN did pre-multi-tenancy
    DEPT_ADMIN = "dept_admin"
    EMPLOYEE = "employee"


class User(Base):
    __tablename__ = "users"
    __table_args__ = (
        UniqueConstraint("tenant_id", "email", name="uq_users_tenant_email"),
        # A plain composite unique above doesn't stop duplicate emails among
        # platform admins, since SQL treats NULL as distinct from itself.
        Index(
            "uq_users_platform_email",
            "email",
            unique=True,
            postgresql_where=text("tenant_id IS NULL"),
            sqlite_where=text("tenant_id IS NULL"),
        ),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    name: Mapped[str] = mapped_column(String(120), nullable=False)
    email: Mapped[str] = mapped_column(String(255), nullable=False, index=True)
    password_hash: Mapped[str] = mapped_column(String(255), nullable=False)
    role: Mapped[UserRole] = mapped_column(Enum(UserRole), nullable=False, default=UserRole.EMPLOYEE)
    is_active: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)

    tenant_id: Mapped[int | None] = mapped_column(
        ForeignKey("tenants.id", ondelete="CASCADE"), nullable=True, index=True
    )
    tenant: Mapped["Tenant | None"] = relationship(back_populates="users")  # noqa: F821

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
    def is_platform_super_admin(self) -> bool:
        return self.role == UserRole.SUPER_ADMIN

    @property
    def is_tenant_admin(self) -> bool:
        return self.role == UserRole.TENANT_ADMIN

    @property
    def is_dept_admin(self) -> bool:
        return self.role == UserRole.DEPT_ADMIN

    @property
    def is_employee(self) -> bool:
        return self.role == UserRole.EMPLOYEE
