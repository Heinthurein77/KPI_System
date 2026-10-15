from pydantic import BaseModel, ConfigDict, Field, field_validator

from app.schemas.user import DepartmentOut, UserSummaryOut


# Periods are calendar month names everywhere in the app (the routers also
# enforce this with manual 422 checks). Validate at the schema boundary so
# direct API callers cannot persist corrupt year/period rows.
VALID_PERIODS = (
    "January", "February", "March", "April", "May", "June",
    "July", "August", "September", "October", "November", "December",
)
MIN_YEAR, MAX_YEAR = 2000, 2100


class _PeriodYearValidated(BaseModel):
    """Mixin: year/period are validated at the schema boundary."""

    @field_validator("period", check_fields=False)
    @classmethod
    def _check_period(cls, v: str) -> str:
        if v not in VALID_PERIODS:
            raise ValueError(f"period must be one of: {', '.join(VALID_PERIODS)}")
        return v

    @field_validator("year", check_fields=False)
    @classmethod
    def _check_year(cls, v: int) -> int:
        if not (MIN_YEAR <= v <= MAX_YEAR):
            raise ValueError(f"year must be between {MIN_YEAR} and {MAX_YEAR}")
        return v


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


class CreateTemplateRequest(BaseModel):
    metric_name: str
    # A metric with a zero or negative target/weight would corrupt the weighted
    # combined-score math in dashboard.combined_final_score (divide-by-near-zero
    # attainment, or a negative contribution that pulls the whole weighted
    # average the wrong way) -- reject it at the door instead.
    target: float = Field(gt=0)
    weight: float = Field(gt=0)
    department_id: int | None = None


class CreateCustomTemplateRequest(_PeriodYearValidated):
    employee_id: int
    metric_name: str
    target: float = Field(gt=0)
    weight: float = Field(gt=0)
    year: int
    period: str
    # New custom assignments recur by default. Existing persisted templates
    # retain their migration default (False), and API callers can opt out.
    is_recurring: bool = True


class WeightAdjustmentItem(BaseModel):
    template_id: int
    weight: float = Field(gt=0, le=100)


class CreateCustomTemplateWithRebalanceRequest(_PeriodYearValidated):
    """Atomic custom-KPI create + weight rebalance (single transaction).

    Replaces the frontend's old N+1 flow (PATCH each changed weight, then
    POST the custom KPI), where a failed create left the weight changes
    silently persisted with no rollback and no user indication. Here either
    everything commits or nothing does.
    """

    employee_id: int
    metric_name: str
    target: float = Field(gt=0)
    weight: float = Field(gt=0)
    year: int
    period: str
    is_recurring: bool = True
    weight_adjustments: list[WeightAdjustmentItem] = []


class RunRecurringKpisRequest(BaseModel):
    """Target month for an idempotent tenant-wide recurring KPI run."""

    year: int = Field(ge=1)
    period: str


class UpdateTemplateWeightRequest(BaseModel):
    """Narrow weight-only update for a recurring custom KPI template.

    Only the KPITemplate.weight field is changed — no KPISubmission rows
    are touched.  Approved/historical records remain strictly immutable;
    the new weight takes effect from the next recurring period onward.
    """

    weight: float = Field(gt=0, le=100,
                          description="New weight percentage (0 < weight ≤ 100).")
