"""Coverage for app/routers/dashboard.py's combined_final_score weighting/status
logic and is_current_or_future_period.

combined_final_score takes `submissions: List[Any]` and only touches
`.final_score` and `.kpi_template` (itself only needing `.target`, `.weight`,
and optionally `.is_lower_better`) -- so plain SimpleNamespace stubs are used
here instead of real ORM rows/DB fixtures, which is the most direct way to
exercise this pure function's branches.
"""

from datetime import date
from types import SimpleNamespace

from app.routers.dashboard import combined_final_score, is_current_or_future_period


def _template(target=100.0, weight=1.0, is_lower_better=None):
    kwargs = {"target": target, "weight": weight}
    if is_lower_better is not None:
        kwargs["is_lower_better"] = is_lower_better
    return SimpleNamespace(**kwargs)


def _submission(final_score, template):
    return SimpleNamespace(final_score=final_score, kpi_template=template)


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
# is_current_or_future_period
# ---------------------------------------------------------------------------


def test_is_current_or_future_period_true_for_current_month():
    today = date(2026, 3, 15)
    assert is_current_or_future_period(2026, "March", today) is True


def test_is_current_or_future_period_true_for_future_month_same_year():
    today = date(2026, 3, 15)
    assert is_current_or_future_period(2026, "April", today) is True


def test_is_current_or_future_period_false_for_past_month_same_year():
    today = date(2026, 3, 15)
    assert is_current_or_future_period(2026, "February", today) is False


def test_is_current_or_future_period_true_for_future_year():
    today = date(2026, 3, 15)
    assert is_current_or_future_period(2027, "January", today) is True


def test_is_current_or_future_period_false_for_past_year():
    today = date(2026, 3, 15)
    assert is_current_or_future_period(2025, "December", today) is False
