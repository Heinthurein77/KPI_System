"""Coverage for app/routers/dashboard.py's combined_final_score weighting/status
logic and the live current-month rule (submission_grace).

combined_final_score takes `submissions: List[Any]` and only touches
`.final_score`, `.effective_target` and `.effective_weight` (which on real ORM
rows resolve to the frozen snapshot with fallback to `.kpi_template`) -- so
plain SimpleNamespace stubs are used here instead of real ORM rows/DB
fixtures, which is the most direct way to exercise this pure function's
branches.
"""

from datetime import date
from types import SimpleNamespace

from app.routers.dashboard import combined_final_score
from app.services.submission_grace import is_current_month, is_fillable_period


def _template(target=100.0, weight=1.0, is_lower_better=None):
    kwargs = {"target": target, "weight": weight}
    if is_lower_better is not None:
        kwargs["is_lower_better"] = is_lower_better
    return SimpleNamespace(**kwargs)


def _submission(final_score, template):
    return SimpleNamespace(
        final_score=final_score,
        kpi_template=template,
        # Mirrors KPISubmission.effective_target/effective_weight: the frozen
        # snapshot when present, else the live template value.
        effective_target=template.target,
        effective_weight=float(template.weight or 0.0),
    )


# ---------------------------------------------------------------------------
# combined_final_score
# ---------------------------------------------------------------------------


def test_weighted_average_across_mixed_weights():
    subs = [
        _submission(100.0, _template(target=100.0, weight=1.0)),  # attainment 100
        _submission(100.0, _template(target=200.0, weight=3.0)),  # attainment 50
    ]
    result = combined_final_score(subs)

    # (100*1 + 50*3) / 4 = 62.5
    assert result["attainment"] == 62.5
    assert result["status"] == "critical"
    assert result["scored_count"] == 2
    assert result["total_count"] == 2


def test_target_zero_or_none_is_skipped_but_counts_toward_total_count():
    subs = [
        _submission(100.0, _template(target=100.0, weight=1.0)),  # counted
        _submission(50.0, _template(target=0.0, weight=1.0)),     # skipped: target falsy
        _submission(50.0, _template(target=None, weight=1.0)),    # skipped: target falsy
    ]
    result = combined_final_score(subs)

    assert result["scored_count"] == 1
    # total_count is len(submissions) -- it includes the skipped ones too.
    assert result["total_count"] == 3
    assert result["attainment"] == 100.0


def test_final_score_none_is_skipped():
    subs = [
        _submission(100.0, _template()),
        _submission(None, _template()),
    ]
    result = combined_final_score(subs)

    assert result["scored_count"] == 1
    assert result["total_count"] == 2


def test_attainment_capped_at_default_max_cap_120():
    subs = [_submission(200.0, _template(target=50.0, weight=1.0))]  # raw attainment 400
    result = combined_final_score(subs)

    assert result["attainment"] == 120.0
    assert result["status"] == "good"


def test_attainment_uncapped_when_max_cap_is_none():
    subs = [_submission(200.0, _template(target=50.0, weight=1.0))]  # raw attainment 400
    result = combined_final_score(subs, max_cap=None)

    assert result["attainment"] == 400.0


def test_is_lower_better_inverts_formula():
    # target=50, actual=100 -> attainment = target/actual*100 = 50.0
    subs = [_submission(100.0, _template(target=50.0, weight=1.0, is_lower_better=True))]
    result = combined_final_score(subs)

    assert result["attainment"] == 50.0
    assert result["status"] == "critical"


def test_is_lower_better_actual_zero_or_negative_is_special_cased_to_100():
    subs = [_submission(0.0, _template(target=50.0, weight=1.0, is_lower_better=True))]
    result = combined_final_score(subs)

    assert result["attainment"] == 100.0
    assert result["status"] == "good"


def test_status_good_at_exactly_100():
    subs = [_submission(100.0, _template(target=100.0, weight=1.0))]
    result = combined_final_score(subs)

    assert result["attainment"] == 100.0
    assert result["status"] == "good"


def test_status_warning_at_exactly_85():
    subs = [_submission(85.0, _template(target=100.0, weight=1.0))]
    result = combined_final_score(subs)

    assert result["attainment"] == 85.0
    assert result["status"] == "warning"


def test_status_critical_just_under_85():
    subs = [_submission(84.9, _template(target=100.0, weight=1.0))]
    result = combined_final_score(subs)

    assert result["attainment"] == 84.9
    assert result["status"] == "critical"


def test_returns_none_when_all_final_scores_are_none():
    subs = [
        _submission(None, _template()),
        _submission(None, _template()),
    ]
    assert combined_final_score(subs) is None


def test_returns_none_when_all_submissions_are_targetless():
    subs = [
        _submission(100.0, _template(target=0.0)),
        _submission(100.0, _template(target=None)),
    ]
    assert combined_final_score(subs) is None


def test_returns_none_for_empty_submissions_list():
    assert combined_final_score([]) is None


# ---------------------------------------------------------------------------
# Live current-month rule (B-m7: is_current_or_future_period /
# is_exact_current_month in dashboard.py were dead code — the live rule
# is submission_grace.is_current_month, with is_fillable_period adding
# the grace window on top. These tests pin the live rule, not the dead
# helpers.)
# ---------------------------------------------------------------------------


def test_is_current_month_true_for_current_month():
    today = date(2026, 3, 15)
    assert is_current_month(2026, "March", today) is True


def test_is_current_month_false_for_future_month_same_year():
    today = date(2026, 3, 15)
    assert is_current_month(2026, "April", today) is False


def test_is_current_month_false_for_past_month_same_year():
    today = date(2026, 3, 15)
    assert is_current_month(2026, "February", today) is False


def test_is_current_month_false_for_future_year():
    today = date(2026, 3, 15)
    assert is_current_month(2027, "January", today) is False


def test_is_current_month_false_for_past_year():
    today = date(2026, 3, 15)
    assert is_current_month(2025, "December", today) is False


def test_fillable_period_covers_current_and_grace_month_only():
    # 2026-03-05: March is current, February is still in grace
    # (Feb 28 + 7 days = Mar 7), January's grace ended Feb 7, and
    # April is a future month.
    today = date(2026, 3, 5)
    assert is_fillable_period(2026, "March", today, 7) is True
    assert is_fillable_period(2026, "February", today, 7) is True
    assert is_fillable_period(2026, "January", today, 7) is False
    assert is_fillable_period(2026, "April", today, 7) is False


def test_over_target_row_is_excluded_and_flagged_not_dropped():
    """B-m8: one over-target row is excluded from the employee's
    combined score and flagged — the employee no longer vanishes from
    combined-score displays (combined_final_score used to return None
    for the whole group). calculate_kpi is untouched: the bad row
    simply never reaches it."""
    from app.routers.dashboard import per_employee_combined_scores

    over = _submission(150.0, _template(target=100.0, weight=1.0))
    over.employee_id = 1
    over.employee = SimpleNamespace(name="Over")
    ok = _submission(80.0, _template(target=100.0, weight=1.0))
    ok.employee_id = 2
    ok.employee = SimpleNamespace(name="Ok")
    # All-bad group: no number to show, but still present and flagged.
    flagged = combined_final_score([over])
    assert flagged is not None
    assert flagged["attainment"] is None
    assert flagged["status"] is None
    assert flagged["scored_count"] == 0
    assert flagged["total_count"] == 1
    assert flagged["has_over_target_row"] is True
    # Mixed group: the good row scores on its own, flagged. (The
    # combined score is the weight-weighted sum, so 80/100 at weight 1
    # is 0.8 — the percentage-vs-sum question is the parked B-M1
    # decision, not this finding.)
    mixed = combined_final_score([over, ok])
    assert mixed["attainment"] == 0.8
    assert mixed["scored_count"] == 1
    assert mixed["total_count"] == 2
    assert mixed["has_over_target_row"] is True
    # Per-employee map: neither employee vanishes.
    result = per_employee_combined_scores([over, ok])
    assert result[1] is not None
    assert result[1]["has_over_target_row"] is True
    assert result[2] is not None
    assert result[2]["has_over_target_row"] is False
