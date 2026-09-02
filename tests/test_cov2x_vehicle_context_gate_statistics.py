from __future__ import annotations

from algorithms.cov2x.vehicle.context_gate import (
    ADVICE_ELIGIBLE,
    NO_FEASIBLE_ADVICE,
    PASS_CURRENT_GREEN,
)
from algorithms.cov2x.vehicle.context_gate_statistics import (
    CONTEXT_GATE_SUPPORTED,
    CONTEXT_GATE_UNSUPPORTED,
    INCONCLUSIVE_2,
    INVALID_HARD_STOP,
    ContextGateThresholds,
    evaluate_context_gate,
)


PERIODS = ("morning_peak", "off_peak", "evening_peak")


def _row(period, seed, category, benefits, *, hard=()):
    release = 10.0
    arms = {
        "NATIVE_RELEASE": {"movement_local_time_loss_s": release},
        "NATIVE_RELEASE_PLACEBO": {"movement_local_time_loss_s": release},
    }
    for arm, benefit in benefits.items():
        arms[arm] = {"movement_local_time_loss_s": release - benefit}
    return {
        "period": period,
        "seed": seed,
        "episode_cluster": f"{period}:{seed}",
        "category": category,
        "valid": not hard,
        "hard_validity_failures": list(hard),
        "arms": arms,
    }


def _complete_rows(eligible_benefits, pass_benefits=None):
    pass_benefits = pass_benefits or {
        "u=-0.25": -1.0,
        "u=-0.50": -1.0,
        "u=-0.75": -1.0,
    }
    rows = []
    for p_index, period in enumerate(PERIODS):
        seeds = tuple(100 * (p_index + 1) + index for index in range(4))
        for index in range(12):
            rows.append(
                _row(period, seeds[index % 4], ADVICE_ELIGIBLE, eligible_benefits)
            )
        for index in range(6):
            rows.append(
                _row(period, seeds[index % 4], PASS_CURRENT_GREEN, pass_benefits)
            )
        for index in range(6):
            rows.append(
                _row(period, seeds[index % 4], NO_FEASIBLE_ADVICE, {})
            )
    return rows


THRESHOLDS = ContextGateThresholds(bootstrap_repetitions=500)


def test_supported_requires_positive_glosa_ci_and_current_green_negative_control():
    rows = _complete_rows(
        {
            "u=-0.25": 0.2,
            "u=-0.50": 0.1,
            "u=-0.75": -0.1,
            "QUEUE_AWARE_GLOSA": 1.0,
        }
    )

    result = evaluate_context_gate(rows, thresholds=THRESHOLDS)

    assert result.status == CONTEXT_GATE_SUPPORTED
    assert result.eligible_glosa_ci.lower > 0.0
    assert result.pass_current_green_max_advice_ci.upper <= 0.0


def test_unsupported_requires_joint_upper_bound_for_every_eligible_advice_arm():
    rows = _complete_rows(
        {
            "u=-0.25": -0.2,
            "u=-0.50": -0.3,
            "u=-0.75": -0.4,
            "QUEUE_AWARE_GLOSA": -0.1,
        }
    )

    result = evaluate_context_gate(rows, thresholds=THRESHOLDS)

    assert result.status == CONTEXT_GATE_UNSUPPORTED
    assert result.eligible_max_advice_ci.upper <= 0.0


def test_fixed_cap_signal_does_not_posthoc_replace_glosa_primary():
    rows = _complete_rows(
        {
            "u=-0.25": 1.0,
            "u=-0.50": 0.0,
            "u=-0.75": -0.2,
            "QUEUE_AWARE_GLOSA": -0.2,
        }
    )

    result = evaluate_context_gate(rows, thresholds=THRESHOLDS)

    assert result.status == INCONCLUSIVE_2
    assert result.eligible_glosa_ci.upper <= 0.0
    assert result.eligible_max_advice_ci.lower > 0.0


def test_any_hard_failure_is_invalid_not_a_scientific_branch_verdict():
    rows = _complete_rows(
        {
            "u=-0.25": 0.2,
            "u=-0.50": 0.1,
            "u=-0.75": -0.1,
            "QUEUE_AWARE_GLOSA": 1.0,
        }
    )
    rows[0] = {**rows[0], "valid": False, "hard_validity_failures": ["TRUE_RED_ENTRY"]}

    result = evaluate_context_gate(rows, thresholds=THRESHOLDS)

    assert result.status == INVALID_HARD_STOP
    assert "TRUE_RED_ENTRY" in result.hard_failure_reasons


def test_quota_shortfall_is_the_only_non_hard_inconclusive_retry_terminal():
    rows = _complete_rows(
        {
            "u=-0.25": 0.2,
            "u=-0.50": 0.1,
            "u=-0.75": -0.1,
            "QUEUE_AWARE_GLOSA": 1.0,
        }
    )[:-1]

    result = evaluate_context_gate(rows, thresholds=THRESHOLDS)

    assert result.status == INCONCLUSIVE_2
    assert "category_quota_mismatch" in result.reason


def test_unregistered_extra_row_cannot_be_silently_ignored():
    rows = _complete_rows(
        {
            "u=-0.25": 0.2,
            "u=-0.50": 0.1,
            "u=-0.75": -0.1,
            "QUEUE_AWARE_GLOSA": 1.0,
        }
    )
    rows.append(_row("morning_peak", 100, "UNKNOWN_CATEGORY", {}))

    result = evaluate_context_gate(rows, thresholds=THRESHOLDS)

    assert result.status == INCONCLUSIVE_2
    assert result.reason == "snapshot_count_mismatch"
