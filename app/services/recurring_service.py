"""recurring_service.py — Monthly carry-over logic for recurring custom KPIs.

This module is additive-only: it never modifies any existing submission,
template, approval flow, formula, or routing logic.  It only ever creates
new draft KPISubmission rows for the target period, guarded by the database's
existing UniqueConstraint so duplicate creation is always idempotent.

Public surface
--------------
materialise_recurring_for_period(db, year, period, tenant_id=None)
    The core "roll-forward" function.  Call it from:
      - the POST /api/admin/recurring/run admin endpoint  (on demand)
      - a scheduled task / cron job                       (automatic)

RecurrenceResult
    A small dataclass returned by materialise_recurring_for_period
    describing what was created and what was skipped (already existed).
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Optional

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models.kpi_submission import KPIStatus, KPISubmission
from app.models.kpi_template import KPITemplate
from app.models.user import User, UserRole


MONTH_NAMES = (
    "January", "February", "March", "April", "May", "June",
    "July", "August", "September", "October", "November", "December",
)


def is_template_active_for_period(template: KPITemplate, year: int, period: str) -> bool:
    """Return whether a recurring custom template has reached its start month.

    A template created for May 2026 must not be backfilled into April 2026;
    it starts in its locked month and carries forward from there.
    """
    if period not in MONTH_NAMES:
        raise ValueError(f"Unsupported monthly period: {period!r}")
    if template.locked_year is None or template.locked_period not in MONTH_NAMES:
        # Legacy/incomplete custom templates have no reliable start month, so
        # leave them alone rather than unexpectedly backfilling them.
        return False
    return (year, MONTH_NAMES.index(period)) >= (
        template.locked_year,
        MONTH_NAMES.index(template.locked_period),
    )


# ---------------------------------------------------------------------------
# Result type
# ---------------------------------------------------------------------------

@dataclass
class RecurrenceResult:
    """Summary of what materialise_recurring_for_period did."""

    year: int
    period: str
    created: list[int] = field(default_factory=list)   # template IDs materialised
    skipped: list[int] = field(default_factory=list)   # template IDs already covered
    errors: list[str] = field(default_factory=list)    # non-fatal warnings

    @property
    def created_count(self) -> int:
        return len(self.created)

    @property
    def skipped_count(self) -> int:
        return len(self.skipped)


# ---------------------------------------------------------------------------
# Core service function
# ---------------------------------------------------------------------------

def materialise_recurring_for_period(
    db: Session,
    year: int,
    period: str,
    tenant_id: Optional[int] = None,
) -> RecurrenceResult:
    """Create draft KPI submissions for every active recurring custom template
    that does not already have a submission in (year, period).

    A "recurring custom template" is any KPITemplate where:
      - employee_id IS NOT NULL  (it is a custom/one-off template)
      - is_recurring IS TRUE     (new custom assignments use this by default)

    Idempotency guarantee
    ---------------------
    The existing UniqueConstraint on kpi_submissions
    (employee_id, kpi_template_id, year, month_or_quarter) makes double-
    creation impossible at the DB level.  This function additionally
    checks for existing rows in Python before attempting INSERT, so it
    never even tries to violate the constraint and is safe to call
    multiple times for the same period.

    Scope
    -----
    Pass tenant_id to restrict to one tenant (normal admin-triggered use).
    Omit / pass None to process all tenants (scheduled cron use).

    Nothing existing is touched
    ---------------------------
    This function only ever does SELECT + INSERT (draft KPISubmission).
    It never modifies existing submissions, templates, approval statuses,
    scores, or any other data.
    """
    result = RecurrenceResult(year=year, period=period)

    # 1. Find all recurring custom templates in scope.
    template_query = select(KPITemplate).where(
        KPITemplate.employee_id.isnot(None),
        KPITemplate.is_recurring.is_(True),
    )
    if tenant_id is not None:
        template_query = template_query.where(KPITemplate.tenant_id == tenant_id)

    templates = db.scalars(template_query).all()

    if not templates:
        return result

    # 2. For each active template, check whether a submission already exists for this
    #    period and create one if not.
    for template in templates:
        if not is_template_active_for_period(template, year, period):
            continue

        employee = db.get(User, template.employee_id)

        # Guard: employee may have been deleted between template creation and now.
        if employee is None:
            result.errors.append(
                f"Template {template.id} ({template.metric_name!r}): "
                "linked employee no longer exists — skipped."
            )
            continue

        # Guard: only active employees participate.
        if not employee.is_active:
            result.errors.append(
                f"Template {template.id} ({template.metric_name!r}): "
                f"employee {employee.name!r} is inactive — skipped."
            )
            continue

        # Guard: only Employee / Dept Admin roles have the KPI workflow.
        if employee.role not in (UserRole.EMPLOYEE, UserRole.DEPT_ADMIN):
            result.errors.append(
                f"Template {template.id} ({template.metric_name!r}): "
                f"employee {employee.name!r} has role {employee.role.value!r} "
                "which does not participate in the KPI workflow — skipped."
            )
            continue

        # Check for an existing submission for this exact template + period.
        existing = db.scalar(
            select(KPISubmission).where(
                KPISubmission.employee_id == template.employee_id,
                KPISubmission.kpi_template_id == template.id,
                KPISubmission.year == year,
                KPISubmission.month_or_quarter == period,
                KPISubmission.tenant_id == template.tenant_id,
            )
        )
        if existing is not None:
            result.skipped.append(template.id)
            continue

        # Create a fresh draft submission for the new period.
        new_submission = KPISubmission(
            tenant_id=template.tenant_id,
            employee_id=template.employee_id,
            department_id=employee.department_id,
            kpi_template_id=template.id,
            year=year,
            month_or_quarter=period,
            status=KPIStatus.DRAFT,
            # All score fields default to None — employee fills them in.
        )
        db.add(new_submission)
        result.created.append(template.id)

    db.commit()
    return result
