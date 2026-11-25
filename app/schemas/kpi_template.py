from pydantic import BaseModel, ConfigDict, Field, field_validator

from app.core.months import MONTH_NAMES
from app.core.validation import PeriodYearValidated
from app.schemas.user import DepartmentOut, UserSummaryOut


# Periods are calendar month names everywhere in the app (the routers also
# enforce this with manual 422 checks). Validate at the schema boundary so
# direct API callers cannot persist corrupt year/period rows. One shared
# copy (app.core.months) — never a second local list.
VALID_PERIODS = tuple(MONTH_NAMES)


# Backwards-compatible alias: the shared mixin lives in
# app.core.validation now (B-m1); this name stays so existing imports
# keep working.
_PeriodYearValidated = PeriodYearValidated


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
    # average the wrong way) -- reject it at the door instead. A single
    # metric above 100% is likewise meaningless (weights are percentages
    # of one period's total), so it is capped here too.
    target: float = Field(gt=0)
    weight: float = Field(gt=0, le=100)
    department_id: int | None = None

    @field_validator("metric_name")
    @classmethod
    def _name_not_blank(cls, v: str) -> str:
        # B-m2: an empty/whitespace-only metric name must 422 here, like
        # the rename path already rejects it — it must never be persisted.
        # M-39 (metric names EXEMPT): control characters are deliberately
        # NOT rejected here. Live production metric names contain
        # newlines (e.g. the ~245-char Audit Cycle metric) and the user
        # rule is that metric names are entered as-is; rejecting them
        # would break real data (test_long_metric_custom_create_keeps_
        # full_audit_label pins this). The newline-in-email-Subject risk
        # remains as a residual: it must be fixed at the email layer
        # (sanitize the Subject in email_service), not by rejecting
        # metric names.
        v = v.strip()
        if not v:
            raise ValueError("Metric name cannot be empty.")
        return v


class CreateCustomTemplateRequest(_PeriodYearValidated):
    employee_id: int
    metric_name: str
    target: float = Field(gt=0)
    weight: float = Field(gt=0, le=100)  # single-metric cap; the period total guard runs in the service
    year: int
    period: str
    # New custom assignments recur by default. Existing persisted templates
    # retain their migration default (False), and API callers can opt out.
    is_recurring: bool = True

    @field_validator("metric_name")
    @classmethod
    def _name_not_blank(cls, v: str) -> str:
        # B-m2: same rule as CreateTemplateRequest (M-39 metric-name
        # exemption applies here too — see above).
        v = v.strip()
        if not v:
            raise ValueError("Metric name cannot be empty.")
        return v


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
    weight: float = Field(gt=0, le=100)
    year: int
    period: str
    is_recurring: bool = True
    weight_adjustments: list[WeightAdjustmentItem] = []

    @field_validator("metric_name")
    @classmethod
    def _name_not_blank(cls, v: str) -> str:
        # B-m2: same rule as CreateTemplateRequest (M-39 metric-name
        # exemption applies here too — see above).
        v = v.strip()
        if not v:
            raise ValueError("Metric name cannot be empty.")
        return v


class RunRecurringKpisRequest(_PeriodYearValidated):
    """Target month for an idempotent tenant-wide recurring KPI run.

    Year/period go through the shared validator now (B-m1): previously
    year was only ge=1 and period was unchecked here, with the router
    doing its own 422 period check.
    """

    year: int
    period: str


class PeriodWeightSubmissionOut(BaseModel):
    """One submission filling the target period, with its frozen weight."""

    submission_id: int
    template_id: int
    metric_name: str
    weight: float
    status: str
    is_custom: bool


class PeriodWeightApplicableOut(BaseModel):
    """One template that will fill the target period: a recurring custom
    template active from its locked month, a one-off locked to this
    period, or one of the employee's department/company templates."""

    template_id: int
    metric_name: str
    weight: float
    is_custom: bool
    is_recurring: bool


class PeriodWeightOut(BaseModel):
    """The true weight budget for adding a KPI in one period.

    ``snapshot_total`` is the number the period-total guard enforces: the
    sum of the period's submissions' frozen weights. It is deliberately
    NOT the sum of the employee's template weights — templates locked to
    other periods, templates rebalanced after materialisation, and
    department-template submissions the form never counted all make those
    two numbers disagree. The Add Custom KPI form budgets against this.

    ``future_template_total`` / ``future_custom_total`` describe the NEXT
    month (template weights as saved): everything that will carry forward,
    and the recurring-custom part of it that the rebalancer can scale.
    The rebalancer only ever changes future months, so its one-tap
    auto-fit budgets against ``future_template_total``, not this period.

    ``applicable_template_total`` / ``applicable_templates`` describe
    the TARGET period itself as templates: everything that will
    materialise into it. For a future period there are no submissions
    yet, so the guard's number is this total — the form budgets a
    future period against it, and the rebalance prompt's "add from
    next month" path needs it to know whether next month even has room.
    """

    employee_id: int
    year: int
    period: str
    snapshot_total: float
    submission_count: int
    submissions: list[PeriodWeightSubmissionOut]
    future_template_total: float
    future_custom_total: float
    applicable_template_total: float
    applicable_templates: list[PeriodWeightApplicableOut]
    is_future_period: bool


class UpdateTemplateWeightRequest(BaseModel):
    """Narrow weight-only update for a recurring custom KPI template.

    Only the KPITemplate.weight field is changed — no KPISubmission rows
    are touched.  Approved/historical records remain strictly immutable;
    the new weight takes effect from the next recurring period onward.
    """

    weight: float = Field(gt=0, le=100,
                          description="New weight percentage (0 < weight ≤ 100).")


class UpdateTemplateNameRequest(BaseModel):
    """Rename-only update for an existing KPI template.

    Only KPITemplate.metric_name changes — target, weight, and every
    KPISubmission row are untouched, so scores and approval history are
    unchanged. Dashboards read the name live from the template, so the
    new name shows on past and future periods alike.
    """

    metric_name: str

    @field_validator("metric_name")
    @classmethod
    def _name_not_blank(cls, v: str) -> str:
        # M-39: the rename path had no validator at all — a blank name
        # was stored unchecked. Control characters stay allowed (metric
        # names are entered as-is; see the exemption note above).
        v = v.strip()
        if not v:
            raise ValueError("Metric name cannot be empty.")
        return v
