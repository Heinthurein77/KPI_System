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


class CreateTemplateRequest(BaseModel):
    metric_name: str
    # A metric with a zero or negative target/weight would corrupt the weighted
    # combined-score math in dashboard.combined_final_score (divide-by-near-zero
    # attainment, or a negative contribution that pulls the whole weighted
    # average the wrong way) -- reject it at the door instead.
    target: float = Field(gt=0)
    weight: float = Field(gt=0)
    department_id: int | None = None


class CreateCustomTemplateRequest(BaseModel):
    employee_id: int
    metric_name: str
    target: float = Field(gt=0)
    weight: float = Field(gt=0)
    year: int
    period: str
