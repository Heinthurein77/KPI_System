from pydantic import BaseModel, ConfigDict, Field

from app.schemas.user import DepartmentOut, UserSummaryOut


class KPITemplateOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: int
    metric_name: str
    target: float
    weight: float
    department_id: int | None
    department: DepartmentOut | None = None
    employee_id: int | None
    employee: UserSummaryOut | None = None
    locked_year: int | None
    locked_period: str | None
    is_custom: bool
    # True when the recurrence service should carry this custom template
    # forward into every new month automatically.
    is_recurring: bool = False


class CreateCustomTemplateRequest(BaseModel):
    employee_id: int
    metric_name: str
    target: float = Field(gt=0)
    weight: float = Field(gt=0)
    year: int
    period: str
    # New custom assignments recur by default. Existing persisted templates
    # retain their migration default (False), and API callers can opt out.
    is_recurring: bool = True


class RunRecurringKpisRequest(BaseModel):
    """Target month for an idempotent tenant-wide recurring KPI run."""

    year: int = Field(ge=1)
    period: str
