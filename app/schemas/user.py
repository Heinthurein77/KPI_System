from pydantic import BaseModel, ConfigDict

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
    department_id: int | None
    department: DepartmentOut | None = None


class UserSummaryOut(BaseModel):
    """Lightweight nested representation (avoids re-serializing department for every row)."""
    model_config = ConfigDict(from_attributes=True)
    id: int
    name: str
    email: str
    role: UserRole


class CreateUserRequest(BaseModel):
    name: str
    email: str
    password: str
    role: UserRole
    department_id: int | None = None


class UpdateUserRequest(BaseModel):
    name: str
    email: str
    role: UserRole
    department_id: int | None = None
    password: str | None = None


class CreateDepartmentRequest(BaseModel):
    name: str
