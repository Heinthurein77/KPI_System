import re
from datetime import datetime
from pydantic import BaseModel, ConfigDict, Field, field_validator

from app.core.validation import reject_control_chars
from app.models.tenant import TenantStatus
from app.schemas.user import (
    MAX_NAME_CHARS,
    _normalize_email,
    _validate_display_name,
)

# Column limits (app/models/tenant.py): name String(200), slug
# String(100). A slug is also used in URLs/subdomains, so it is
# restricted to the safe shape below (M-35).
MAX_TENANT_NAME_CHARS = 200
MAX_TENANT_SLUG_CHARS = 100
_SLUG_RE = re.compile(r"^[a-z0-9][a-z0-9-]*$")


class TenantOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: int
    name: str
    slug: str
    domain: str | None
    status: TenantStatus
    created_at: datetime


class CreateTenantRequest(BaseModel):
    name: str = Field(max_length=MAX_TENANT_NAME_CHARS)
    slug: str = Field(max_length=MAX_TENANT_SLUG_CHARS)
    admin_name: str = Field(max_length=MAX_NAME_CHARS)
    admin_email: str
    admin_password: str = Field(min_length=8, max_length=128)

    @field_validator("name")
    @classmethod
    def _name_not_blank(cls, v: str) -> str:
        v = v.strip()
        if not v:
            raise ValueError("Name cannot be empty.")
        if len(v) > MAX_TENANT_NAME_CHARS:
            raise ValueError(
                f"Name is too long (max {MAX_TENANT_NAME_CHARS} characters)."
            )
        return reject_control_chars(v, "name")

    @field_validator("slug")
    @classmethod
    def _slug_valid(cls, v: str) -> str:
        # M-35: slugs used to be stored unchecked ("a b", "a/b", "") —
        # they end up in URLs/subdomains, so enforce the safe shape.
        v = v.strip().lower()
        if not v:
            raise ValueError("Slug cannot be empty.")
        if len(v) > MAX_TENANT_SLUG_CHARS:
            raise ValueError(
                f"Slug is too long (max {MAX_TENANT_SLUG_CHARS} characters)."
            )
        if not _SLUG_RE.match(v):
            raise ValueError(
                "Slug must start with a letter or digit and contain only "
                "lowercase letters, digits, and hyphens."
            )
        return v

    @field_validator("admin_name")
    @classmethod
    def _admin_name_not_blank(cls, v: str) -> str:
        return _validate_display_name(v, field="admin name")

    @field_validator("admin_email")
    @classmethod
    def _admin_email_valid(cls, v: str) -> str:
        # M-35: the admin email goes through the same shared regex as
        # every other user email instead of being stored unchecked.
        return _normalize_email(v)
