from datetime import datetime

from pydantic import BaseModel, ConfigDict, Field, field_validator

from app.models.kpi_submission import KPIStatus
from app.schemas.kpi_template import KPITemplateOut
from app.schemas.user import DepartmentOut, UserSummaryOut


class KpiTrendPointOut(BaseModel):
    year: int
    month_or_quarter: str
    attainment: float | None = None
    status: str | None = None
    scored_count: int
    total_count: int


class KpiTrendOut(BaseModel):
    employee: UserSummaryOut
    year: int
    available_years: list[int]
    points: list[KpiTrendPointOut]
    peak: KpiTrendPointOut | None = None
    lowest: KpiTrendPointOut | None = None
    annual_average: float | None = None


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
    dept_reviewer: UserSummaryOut | None = None
    final_reviewer: UserSummaryOut | None = None


def _reject_negative_scores(scores: dict[str, float]) -> dict[str, float]:
    # A negative self_score isn't meaningful for any metric shape (percentage or
    # raw count) and would silently poison dept/final fallback chains and the
    # combined-score weighting downstream -- reject it here rather than trusting
    # the client.
    if any(v < 0 for v in scores.values()):
        raise ValueError("KPI scores cannot be negative.")
    return scores


class SaveScoresRequest(BaseModel):
    year: int
    period: str
    scores: dict[str, float] = {}

    _validate_scores = field_validator("scores")(_reject_negative_scores)


class SubmitRequest(BaseModel):
    year: int
    period: str
    scores: dict[str, float] = {}

    _validate_scores = field_validator("scores")(_reject_negative_scores)


class DeptSaveRequest(BaseModel):
    year: int
    period: str
    dept_score: float = Field(ge=0)
    remarks: str | None = None


class DeptApproveRequest(BaseModel):
    year: int
    period: str
    dept_score: float | None = Field(default=None, ge=0)
    remarks: str | None = None


class FinalApproveRequest(BaseModel):
    year: int
    period: str
    final_score: float | None = Field(default=None, ge=0)
    remarks: str | None = None


class OverrideRequest(BaseModel):
    year: int
    period: str
    final_score: float = Field(ge=0)
    remarks: str | None = None


class RejectRequest(BaseModel):
    year: int
    period: str
    remarks: str | None = None
