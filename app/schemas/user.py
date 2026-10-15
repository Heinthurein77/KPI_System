import re

from pydantic import BaseModel, ConfigDict, Field, field_validator

from app.models.user import UserRole

# Same shape as the bulk-import validator (app/services/bulk_import_service.py):
# plain str fields can't use EmailStr without adding the email-validator
# dependency, so both paths share this regex.
_EMAIL_RE = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+$")


def _normalize_email(v: str) -> str:
    v = v.strip().lower()
    if not _EMAIL_RE.match(v):
        raise ValueError("Invalid email address.")
    return v


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
    must_change_password: bool = False
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
    password: str = Field(min_length=8, max_length=128)
    role: UserRole
    department_id: int | None = None
    position: str | None = None

    @field_validator("email")
    @classmethod
    def _email_valid(cls, v: str) -> str:
        return _normalize_email(v)


class UpdateUserRequest(BaseModel):
    name: str
    email: str
    role: UserRole
    department_id: int | None = None
    position: str | None = None
    password: str | None = Field(default=None, min_length=8, max_length=128)

    @field_validator("email")
    @classmethod
    def _email_valid(cls, v: str) -> str:
        return _normalize_email(v)


class ResetPasswordRequest(BaseModel):
    password: str = Field(min_length=8, max_length=128)


class CreateDepartmentRequest(BaseModel):
    name: str
