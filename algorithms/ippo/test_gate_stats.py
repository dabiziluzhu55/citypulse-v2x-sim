# algorithms/ippo/test_gate_stats.py
"""Tests for the pre-registered zero-shot gate statistics."""

from __future__ import annotations

import pytest

from algorithms.ippo.gate_stats import (
    aggregate_rate,
    bootstrap_ci,
    evaluate_primary_gate,
    paired_relative_degradation,
    wilcoxon_one_sided_less,
    win_rate,
)


def test_wilcoxon_exact_small_sample():
    # 7 of 8 wins with one LARGE loss must NOT be significant; tied |diffs|
    # follow the pre-registered sign-flip permutation policy.
    diffs = [-1.0, -1.0, -1.0, -1.0, -1.0, -1.0, -1.0, 10.0]
    p, method = wilcoxon_one_sided_less(diffs)
    assert method == "permutation"
    assert p > 0.05


def test_wilcoxon_permutation_on_ties():
    diffs = [-2.0, -2.0, -2.0, -2.0, -2.0, 0.0, 1.0, 1.0, 1.0, 1.0]
    p, method = wilcoxon_one_sided_less(diffs)
    assert method == "permutation"
    assert 0.0 <= p <= 1.0


def test_win_rate_ties_not_wins():
    wins, ties, losses = win_rate([1.0, 2.0, 2.0], [1.0, 3.0, 1.0])
    assert (wins, ties, losses) == (1, 1, 1)


def test_primary_gate_verdicts():
    strong = evaluate_primary_gate(
        "east_dense",
        model_waiting=[10.0, 11.0, 12.0, 9.0, 10.0, 11.0, 10.0, 11.0, 12.0, 10.0],
        fixed_waiting=[12.0, 13.0, 13.0, 12.0, 12.0, 14.0, 12.0, 13.0, 13.0, 12.0],
    )
    assert strong.status == "strong"
    weak = evaluate_primary_gate(
        "east_dense",
        model_waiting=[11.9, 12.8, 12.9, 11.8, 11.9, 13.8, 11.9, 12.8, 12.9, 11.9],
        fixed_waiting=[12.0, 13.0, 13.0, 12.0, 12.0, 14.0, 12.0, 13.0, 13.0, 12.0],
    )
    assert weak.status == "weak"
    fail = evaluate_primary_gate(
        "east_dense",
        model_waiting=[15.0, 15.0, 15.0, 15.0, 15.0, 15.0, 15.0, 15.0, 15.0, 15.0],
        fixed_waiting=[12.0, 13.0, 13.0, 12.0, 12.0, 14.0, 12.0, 13.0, 13.0, 12.0],
    )
    assert fail.status == "fail"


def test_primary_gate_insufficient_pairs():
    result = evaluate_primary_gate(
        "east_dense",
        model_waiting=[10.0, 11.0, 12.0, 9.0, 10.0, 11.0, 10.0],
        fixed_waiting=[12.0, 13.0, 13.0, 12.0, 12.0, 14.0, 12.0],
    )
    assert result.status == "insufficient"


def test_aggregate_rate_and_bootstrap():
    events = [0, 1, 2]
    exposures = [1000, 2000, 1000]
    assert aggregate_rate(events, exposures, k=10000) == pytest.approx(7.5)
    lo, hi = bootstrap_ci(events, exposures, k=10000)
    assert 0.0 <= lo <= hi


def test_paired_relative_degradation_lower_is_better():
    values = paired_relative_degradation(
        [11.0, 13.0], [10.0, 10.0], lower_is_better=True
    )
    assert values == pytest.approx([0.10, 0.30])
