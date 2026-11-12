# Weight Budget — Single Source of Truth per Period Type

**Status:** decision record, 2026-11-12 (HEAD `a2acb06`). Review finding M8.
**Scope:** the "how much weight room is left" question only. The scoring and
weight formulas themselves are unchanged and out of scope (closed 2026-10-21).

## The three numbers (and why there are three)

| # | Number | Where it lives | What it means |
|---|---|---|---|
| 1 | Live template weight | `KPITemplate.weight` | The weight as saved on the template right now. Edits here affect **future** months only — never a period that already materialised. |
| 2 | Frozen submission snapshot | `KPISubmission.weight_snapshot` / `effective_weight` | The weight frozen into each submission when the period materialised. A later template edit does **not** change this period. This is the number the backend guard enforces. |
| 3 | Applicable-template total | `applicable_templates()` (`app/services/kpi_service.py:227`) | The templates that *would* materialise into a period — what will fill a month that has no submissions yet. |

These three disagree whenever a template was locked to another period, was
rebalanced after a period materialised, or when department-template
submissions fill the period. That disagreement caused three consecutive bug
reports in two days (`445a4fd` → `309cc98` → `a2acb06`). The rule below is
the fix for the confusion; it changes no behaviour.

## The rule

- **Current period (a period that already has submissions): the snapshot
  total is the single source of truth.** Sum `effective_weight` over the
  period's submissions — `period_submission_snapshots()`
  (`app/services/kpi_service.py:138`) — and nothing else. This is exactly
  what the guard `validate_period_total_weight()`
  (`app/services/kpi_service.py:174`) sums, and what the endpoint returns as
  `snapshot_total`. The Add Custom KPI form budgets the current period
  against it.
- **Future period (no submissions yet): the applicable-template total is the
  single source of truth.** Sum `KPITemplate.weight` over
  `applicable_templates()` for that period. This is the number the guard
  *will* enforce there once the period materialises, so the form budgets a
  future period against `applicable_total`, not the (empty) snapshot total.
- **The rebalancer only ever changes future months.** Its one-tap auto-fit
  budgets against the *next* month's applicable totals (`future_template_total`
  / `future_custom_total` from the endpoint), never against the current
  period. Approved submissions are provably untouched by a rebalance
  (snapshots are frozen at materialisation).

## The endpoint

`GET /api/admin/employees/{employee_id}/period-weight?year=YYYY&period=Month`
(`app/routers/admin.py:663`, dept admin or tenant admin; dept admins only
their own department's employees). Read-only. It returns both views so the
form never has to guess:

- `snapshot_total` + `submissions[]` — the current period's frozen weights
  (the guard's number; per-submission breakdown names what fills the period).
- `applicable_total` + `applicable_templates[]` — the *target* period as
  templates (what will materialise there).
- `future_template_total` / `future_custom_total` — the *next* month's
  template totals, for the rebalancer (recurring custom templates only in
  the custom subtotal; one-offs are excluded — they have no future months).

## Resolved edge case — user decision 2026-11-12: keep the conservative form behaviour

The `a2acb06` builder deliberately chose to budget a future month against
applicable templates **even when that month already has submissions**. In
that case the snapshot total is the number the backend guard actually
enforces, so the form can **block a create the backend would allow**
(a false "no room" when the snapshots total less than the applicable
templates do).

Options considered:

1. **Keep the conservative form behaviour** (current): the form may say no
   when the server would say yes. Safe, occasionally annoying.
2. **Prefer snapshots whenever submissions exist**: if a future month already
   has submissions, budget against `snapshot_total` (the guard's number) and
   only fall back to `applicable_total` when the month is empty. The form
   then agrees with the backend in every case, but the number it shows can
   change as submissions appear.

**Decision (user, 2026-11-12): option 1 — keep the conservative form
behaviour.** No code change was needed; this record now states the decided
behaviour plainly so the next person working in this area does not
re-discover it as a bug.

## Rules for future work in this area

- Never sum live template weights to answer "what fills this period" —
  use the endpoint's `snapshot_total` (current) or `applicable_total`
  (future).
- Never add a fourth source of truth. Any new weight feature reads one of
  the two numbers above, through the endpoint or the two helpers
  (`period_submission_snapshots`, `applicable_templates`).
- The guard allows under-allocation (total < 100 is valid); only > 100 is
  blocked. Do not "fix" under-allocation.
