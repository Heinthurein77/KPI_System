"""Schemas for the Daily Task diary (additive-only module)."""

from datetime import date as date_type

from pydantic import BaseModel, ConfigDict, Field

from app.models.daily_task import TASK_STATUSES


class DailyTaskEntryCreate(BaseModel):
    task_date: date_type
    plan_linkage: str | None = None
    work_done: str = Field(min_length=1)
    status: str = "in_progress"
    figure_output: str | None = None
    evidence_link: str | None = None
    difficulties: str | None = None
    support_needed: str | None = None
    self_reflection: str | None = None
    tomorrow_plan_1: str | None = None
    tomorrow_plan_2: str | None = None
    tomorrow_plan_3: str | None = None


class DailyTaskEntryUpdate(BaseModel):
    """Owner-editable fields. supervisor_feedback is handled separately
    (only dept admin of the author's department / tenant admin)."""

    task_date: date_type | None = None
    plan_linkage: str | None = None
    work_done: str | None = None
    status: str | None = None
    figure_output: str | None = None
    evidence_link: str | None = None
    difficulties: str | None = None
    support_needed: str | None = None
    self_reflection: str | None = None
    tomorrow_plan_1: str | None = None
    tomorrow_plan_2: str | None = None
    tomorrow_plan_3: str | None = None


class DailyTaskFeedbackUpdate(BaseModel):
    supervisor_feedback: str


class DailyTaskEntryOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    user_id: int
    author_name: str = ""
    department_name: str | None = None
    position: str | None = None
    task_date: date_type
    plan_linkage: str | None = None
    work_done: str
    status: str
    figure_output: str | None = None
    evidence_link: str | None = None
    evidence_file: str | None = None
    has_evidence_file: bool = False
    difficulties: str | None = None
    support_needed: str | None = None
    self_reflection: str | None = None
    tomorrow_plan_1: str | None = None
    tomorrow_plan_2: str | None = None
    tomorrow_plan_3: str | None = None
    supervisor_feedback: str | None = None


class MonthlyUserSummary(BaseModel):
    user_id: int
    author_name: str = ""
    working_days: int
    completed: int
    in_progress_or_pending: int
    not_started: int
    total: int


class MonthlySummaryOut(BaseModel):
    year: int
    month: int
    users: list[MonthlyUserSummary]


class MonthlyReviewOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    user_id: int
    year: int
    month: int
    achievements: str | None = None
    challenges_solutions: str | None = None
    improvements_next_focus: str | None = None
    hod_evaluation: str | None = None


class MonthlyReviewUpdate(BaseModel):
    """Owner writes the first three; HOD evaluation is supervisor-only
    and is only applied when the caller is allowed to give it."""

    achievements: str | None = None
    challenges_solutions: str | None = None
    improvements_next_focus: str | None = None
    hod_evaluation: str | None = None


def validate_status(status: str) -> str:
    if status not in TASK_STATUSES:
        raise ValueError(f"status must be one of {', '.join(TASK_STATUSES)}")
    return status
