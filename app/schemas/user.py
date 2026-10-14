from pydantic import BaseModel, ConfigDict, Field

from app.models.user import UserRole


class DepartmentOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: int
    name: str


class DepartmentWithCountOut(DepartmentOut):
    employee_count: int = 0


class UserOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: int
    name: str
    email: str
    role: UserRole
    is_active: bool
    position: str | None = None
    tenant_id: int | None
    department_id: int | None
    department: DepartmentOut | None = None


class UserSummaryOut(BaseModel):
    """Lightweight nested representation (avoids re-serializing department for every row)."""
    model_config = ConfigDict(from_attributes=True)
    id: int
    name: str
    email: str
    role: UserRole
    position: str | None = None


class CreateUserRequest(BaseModel):
    name: str
    email: str
    password: str
    role: UserRole
    department_id: int | None = None
    position: str | None = None


class UpdateUserRequest(BaseModel):
    name: str
    email: str
    role: UserRole
    department_id: int | None = None
    position: str | None = None
    password: str | None = None


class ResetPasswordRequest(BaseModel):
    password: str = Field(min_length=8, max_length=128)


class CreateDepartmentRequest(BaseModel):
    name: str
