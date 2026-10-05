from datetime import datetime
from pydantic import BaseModel, ConfigDict

from app.models.tenant import TenantStatus


class TenantOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: int
    name: str
    slug: str
    domain: str | None
    status: TenantStatus
    created_at: datetime


class CreateTenantRequest(BaseModel):
    name: str
    slug: str
    admin_name: str
    admin_email: str
    admin_password: str
