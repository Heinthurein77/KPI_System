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
    self_score_comment: str | None = None
    weight_snapshot: float | None = None
    target_snapshot: float | None = None
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


def _reject_non_numeric_keys(scores: dict[str, float]) -> dict[str, float]:
    # Score-dict keys are submission IDs; the router converts them with int(),
    # so non-numeric keys must be rejected here with a 422 instead of a 500.
    for key in scores:
        if not key.isdigit():
            raise ValueError(f"Invalid submission id: {key!r}.")
    return scores


def _validate_score_keys_and_values(scores: dict[str, float]) -> dict[str, float]:
    scores = _reject_non_numeric_keys(scores)
    return _reject_negative_scores(scores)


MAX_COMMENT_LENGTH = 2000


def _validate_comment_keys_and_values(comments: dict[str, str]) -> dict[str, str]:
    # Comment-dict keys are submission IDs, like scores. over-long values are
    # rejected with a 422 instead of being silently truncated.
    for key in comments:
        if not key.isdigit():
            raise ValueError(f"Invalid submission id: {key!r}.")
    for key, value in comments.items():
        if not isinstance(value, str):
            raise ValueError(f"Invalid comment for submission {key!r}.")
        if len(value) > MAX_COMMENT_LENGTH:
            raise ValueError(
                f"Comment for submission {key!r} exceeds {MAX_COMMENT_LENGTH} characters."
            )
    return comments


class SaveScoresRequest(BaseModel):
    year: int
    period: str
    scores: dict[str, float] = {}
    comments: dict[str, str] = {}

    _validate_scores = field_validator("scores")(_validate_score_keys_and_values)
    _validate_comments = field_validator("comments")(_validate_comment_keys_and_values)


class SubmitRequest(BaseModel):
    year: int
    period: str
    scores: dict[str, float] = {}
    comments: dict[str, str] = {}

    _validate_scores = field_validator("scores")(_validate_score_keys_and_values)
    _validate_comments = field_validator("comments")(_validate_comment_keys_and_values)


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


class BulkApproveItem(BaseModel):
    submission_id: int
    score: float | None = Field(default=None, ge=0)
    remarks: str | None = None


class BulkApproveRequest(BaseModel):
    year: int
    period: str
    items: list[BulkApproveItem] = Field(min_length=1, max_length=500)


class RejectRequest(BaseModel):
    year: int
    period: str
    remarks: str | None = None
