from datetime import datetime

from pydantic import BaseModel, ConfigDict

from app.models.kpi_submission import KPIStatus
from app.schemas.kpi_template import KPITemplateOut
from app.schemas.user import DepartmentOut, UserSummaryOut


class KPISubmissionOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: int
    employee: UserSummaryOut
    department: DepartmentOut | None = None
    kpi_template: KPITemplateOut
    year: int
    month_or_quarter: str
    self_score: float | None
    dept_score: float | None
    final_score: float | None
    status: KPIStatus
    remarks: str | None
    submitted_at: datetime | None
    dept_reviewed_at: datetime | None
    final_reviewed_at: datetime | None


class SaveScoresRequest(BaseModel):
    year: int
    period: str
    scores: dict[str, float] = {}


class SubmitRequest(BaseModel):
    year: int
    period: str
    scores: dict[str, float] = {}


class DeptSaveRequest(BaseModel):
    year: int
    period: str
    dept_score: float
    remarks: str | None = None


class DeptApproveRequest(BaseModel):
    year: int
    period: str
    dept_score: float | None = None
    remarks: str | None = None


class FinalApproveRequest(BaseModel):
    year: int
    period: str
    final_score: float | None = None
    remarks: str | None = None


class OverrideRequest(BaseModel):
    year: int
    period: str
    final_score: float
    remarks: str | None = None


class RejectRequest(BaseModel):
    year: int
    period: str
    remarks: str | None = None
