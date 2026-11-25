import re

from pydantic import BaseModel, ConfigDict, Field, field_validator

from app.core.validation import reject_control_chars
from app.models.user import UserRole

# Same shape as the bulk-import validator (app/services/bulk_import_service.py):
# plain str fields can't use EmailStr without adding the email-validator
# dependency, so both paths share this regex.
_EMAIL_RE = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+$")

# Column limits (app/models/user.py, app/models/department.py,
# app/models/tenant.py): Postgres rejects longer values with
# StringDataRightTruncation at INSERT time — a generic 500 after the
# request already passed validation. SQLite does not enforce varchar
# length, so the suite cannot catch it; cap here (M-33) so a too-long
# value is a 422 at the boundary instead.
MAX_NAME_CHARS = 120
MAX_POSITION_CHARS = 120
MAX_EMAIL_CHARS = 255
MAX_DEPARTMENT_NAME_CHARS = 120


def _normalize_email(v: str) -> str:
    v = v.strip().lower()
    if len(v) > MAX_EMAIL_CHARS:
        raise ValueError("Email address is too long (max 255 characters).")
    if not _EMAIL_RE.match(v):
        raise ValueError("Invalid email address.")
    return v


def _validate_display_name(v: str, *, field: str = "name") -> str:
    """A non-blank display name: stripped, within the column limit, and
    with no control characters (M-39 — a newline in a name becomes the
    notification email's Subject and kills that email). Shared by the
    create/update user schemas, tenants, and departments."""
    v = v.strip()
    if not v:
        raise ValueError(f"{field.capitalize()} cannot be empty.")
    if len(v) > MAX_NAME_CHARS:
        raise ValueError(f"{field.capitalize()} is too long (max {MAX_NAME_CHARS} characters).")
    return reject_control_chars(v, field)


def _normalize_position(v: str | None) -> str | None:
    """Blank/whitespace-only position normalises to None (M-34) — it
    must never be stored as an empty string."""
    if v is None:
        return None
    v = v.strip()
    if not v:
        return None
    if len(v) > MAX_POSITION_CHARS:
        raise ValueError(f"Position is too long (max {MAX_POSITION_CHARS} characters).")
    return reject_control_chars(v, "position")


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
    name: str = Field(max_length=MAX_NAME_CHARS)
    email: str = Field(max_length=MAX_EMAIL_CHARS)
    password: str = Field(min_length=8, max_length=128)
    role: UserRole
    department_id: int | None = None
    position: str | None = Field(default=None, max_length=MAX_POSITION_CHARS)

    @field_validator("name")
    @classmethod
    def _name_not_blank(cls, v: str) -> str:
        # B-m2: an empty/whitespace-only user name must 422 here, like
        # the rename/edit paths already reject it — never persisted.
        return _validate_display_name(v)

    @field_validator("email")
    @classmethod
    def _email_valid(cls, v: str) -> str:
        return _normalize_email(v)

    @field_validator("position")
    @classmethod
    def _position_normalised(cls, v: str | None) -> str | None:
        return _normalize_position(v)


class UpdateUserRequest(BaseModel):
    name: str = Field(max_length=MAX_NAME_CHARS)
    email: str = Field(max_length=MAX_EMAIL_CHARS)
    role: UserRole
    department_id: int | None = None
    position: str | None = Field(default=None, max_length=MAX_POSITION_CHARS)
    password: str | None = Field(default=None, min_length=8, max_length=128)
    # M-01: required only when an admin changes their OWN password via
    # PUT /users/{id} — proves the caller knows the current password
    # (same 400 as POST /api/auth/change-password on a wrong one).
    # Changing another user's password never needs it.
    current_password: str | None = None

    @field_validator("name")
    @classmethod
    def _name_not_blank(cls, v: str) -> str:
        # M-34: the update path reused no name validator at all, so a
        # blank/whitespace-only name was stored — same rule as create.
        return _validate_display_name(v)

    @field_validator("email")
    @classmethod
    def _email_valid(cls, v: str) -> str:
        return _normalize_email(v)

    @field_validator("position")
    @classmethod
    def _position_normalised(cls, v: str | None) -> str | None:
        return _normalize_position(v)


class ResetPasswordRequest(BaseModel):
    password: str = Field(min_length=8, max_length=128)


class CreateDepartmentRequest(BaseModel):
    name: str = Field(max_length=MAX_DEPARTMENT_NAME_CHARS)

    @field_validator("name")
    @classmethod
    def _name_not_blank(cls, v: str) -> str:
        return _validate_display_name(v, field="department name")
