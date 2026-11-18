from __future__ import annotations

import pytest

from cnpj_pipeline.agent.eval_stats import (
    bootstrap_interval,
    exact_mcnemar_p,
    paired_difference_interval,
    wilson_interval,
)


def test_wilson_matches_known_values():
    lo, hi = wilson_interval(22, 25)
    assert lo == pytest.approx(0.7005, abs=1e-3) and hi == pytest.approx(0.9583, abs=1e-3)
    lo, hi = wilson_interval(5, 10)
    assert lo == pytest.approx(0.2366, abs=1e-3) and hi == pytest.approx(0.7634, abs=1e-3)


def test_wilson_stays_inside_zero_and_one_at_the_extremes():
    lo, hi = wilson_interval(25, 25)
    assert hi == 1.0 and 0.8 < lo < 1.0
    lo, hi = wilson_interval(0, 25)
    assert lo == 0.0 and 0.0 < hi < 0.2


def test_wilson_narrows_with_more_questions_and_handles_no_questions():
    small = wilson_interval(9, 10)
    large = wilson_interval(90, 100)
    assert (large[1] - large[0]) < (small[1] - small[0])
    assert wilson_interval(0, 0) == (0.0, 1.0)
    with pytest.raises(ValueError):
        wilson_interval(11, 10)


def test_bootstrap_is_deterministic_and_brackets_the_mean():
    rates = [1.0] * 20 + [0.0] * 4 + [0.5]
    first = bootstrap_interval(rates)
    assert first == bootstrap_interval(rates)
    mean = sum(rates) / len(rates)
    assert first[0] < mean < first[1]
    assert 0.0 <= first[0] and first[1] <= 1.0


def test_bootstrap_of_identical_rates_has_no_width_and_empty_input_is_unbounded():
    assert bootstrap_interval([1.0] * 10) == (1.0, 1.0)
    assert bootstrap_interval([]) == (0.0, 1.0)


@pytest.mark.parametrize(
    ("only_a", "only_b", "expected"),
    [(0, 0, 1.0), (0, 5, 0.0625), (5, 0, 0.0625), (1, 5, 0.21875), (5, 5, 1.0), (0, 10, 2 / 1024)],
)
def test_exact_mcnemar_known_values(only_a, only_b, expected):
    assert exact_mcnemar_p(only_a, only_b) == pytest.approx(expected)


def test_paired_difference_interval_excludes_zero_for_a_consistent_gain():
    a = [0.0] * 10 + [1.0] * 10
    b = [1.0] * 10 + [1.0] * 10
    lo, hi = paired_difference_interval(a, b)
    assert lo > 0 and hi <= 1.0
    with pytest.raises(ValueError):
        paired_difference_interval([1.0], [1.0, 0.0])
