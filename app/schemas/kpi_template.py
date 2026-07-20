from pydantic import BaseModel, ConfigDict

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
    target: float
    weight: float
    department_id: int | None = None


class CreateCustomTemplateRequest(BaseModel):
    employee_id: int
    metric_name: str
    target: float
    weight: float
    year: int
    period: str
