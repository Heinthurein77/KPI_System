from datetime import datetime

from pydantic import BaseModel, ConfigDict


class AuditLogOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: int
    actor_name: str
    actor_role: str
    action: str
    entity_type: str
    entity_id: int
    entity_label: str
    summary: str
    created_at: datetime
