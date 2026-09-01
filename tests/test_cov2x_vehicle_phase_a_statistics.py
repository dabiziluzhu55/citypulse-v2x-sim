from __future__ import annotations

import pytest

from algorithms.cov2x.vehicle.phase_a_statistics import (
    PhaseAThresholds,
    evaluate_phase_a,
    period_stratified_cluster_bootstrap_mean_deltas,
    snapshot_delta,
    wilson_interval,
)


def _thresholds(**overrides) -> PhaseAThresholds:
    values = {
        "useful_delta_min_s": 1.0,
        "periods": ("morning", "off_peak", "evening"),
    }
    values.update(overrides)
    return PhaseAThresholds(**values)


def _row(
    period: str,
    seed: int,
    index: int,
    delta: float,
    *,
    valid: bool = True,
    safety_pass: bool = True,
    causal_pass: bool = True,
    ledger_pass: bool = True,
) -> dict:
    release = 10.0
    best_cap = release - delta
    return {
        "snapshot_id": f"{period}-{seed}-{index}",
        "period": period,
        "seed": seed,
        "episode_id": f"episode-{seed}",
        "release_movement_time_loss": release,
        "negative_cap_movement_time_loss": {
            "-0.25": best_cap + 0.5,
            "-0.50": best_cap,
            "-0.75": best_cap + 0.25,
        },
        "valid": valid,
        "safety_pass": safety_pass,
        "causal_pass": causal_pass,
        "ledger_pass": ledger_pass,
    }


def _rows_with_useful_count(useful_count: int) -> list[dict]:
    rows = []
    periods = ("morning", "off_peak", "evening")
    base, remainder = divmod(useful_count, len(periods))
    useful_by_period = {
        period: base + int(index < remainder)
        for index, period in enumerate(periods)
    }
    for period in periods:
        period_useful = useful_by_period[period]
        seed_base, seed_remainder = divmod(period_useful, 3)
        useful_by_seed = {
            seed: seed_base + int(seed_index < seed_remainder)
            for seed_index, seed in enumerate((1, 2, 3))
        }
        for seed in (1, 2, 3):
            for index in range(10):
                rows.append(
                    _row(
                        period,
                        seed,
                        index,
                        2.0 if index < useful_by_seed[seed] else 0.0,
                    )
                )
    return rows


def test_snapshot_delta_uses_best_cap_and_explicit_useful_threshold() -> None:
    row = _row("morning", 1, 0, delta=2.0)
    assert snapshot_delta(row) == pytest.approx(2.0)

    assert evaluate_phase_a(
        [_row("morning", 1, 0, 1.5)],
        _thresholds(useful_delta_min_s=1.5),
        bootstrap_reps=20,
        bootstrap_seed=7,
    ).useful_count == 0
    assert evaluate_phase_a(
        [_row("morning", 1, 0, 1.5001)],
        _thresholds(useful_delta_min_s=1.5),
        bootstrap_reps=20,
        bootstrap_seed=7,
    ).useful_count == 1


def test_cluster_bootstrap_is_period_stratified_and_resamples_whole_clusters() -> None:
    rows = [
        _row("morning", 1, 0, 0.0),
        _row("morning", 1, 1, 0.0),
        _row("morning", 2, 0, 5.0),
        _row("morning", 2, 1, 5.0),
        _row("evening", 3, 0, 10.0),
        _row("evening", 3, 1, 10.0),
        _row("evening", 4, 0, 10.0),
        _row("evening", 4, 1, 10.0),
    ]
    samples = period_stratified_cluster_bootstrap_mean_deltas(
        rows,
        repetitions=80,
        seed=11,
        periods=("morning", "evening"),
    )

    # Morning's two-row episode cluster is selected as a unit; evening is
    # fixed at 20.  A snapshot bootstrap could produce 11.25/13.75, which is
    # impossible for this cluster bootstrap.
    assert set(samples) <= {5.0, 6.25, 7.5}
    assert set(samples) & {5.0, 6.25, 7.5}

    interval = wilson_interval(successes=3, trials=90)
    assert 0.0 < interval.lower < interval.rate < interval.upper < 0.2


def test_phase_b_gate_requires_total_period_and_both_intervals() -> None:
    result = evaluate_phase_a(
        _rows_with_useful_count(18),
        _thresholds(),
        bootstrap_reps=500,
        bootstrap_seed=20260828,
    )
    assert result.status == "ENTER_PHASE_B_LOCAL_CREDIT"
    assert result.useful_count == 18
    assert result.period_useful_counts == {
        "morning": 6,
        "off_peak": 6,
        "evening": 6,
    }
    assert result.useful_rate_interval.lower > 0.05
    assert result.mean_delta_bootstrap.lower > 0.0


def test_phase_c_gate_accepts_only_very_rare_useful_snapshots() -> None:
    result = evaluate_phase_a(
        _rows_with_useful_count(3),
        _thresholds(),
        bootstrap_reps=250,
        bootstrap_seed=9,
    )
    assert result.status == "ENTER_PHASE_C_GLOSA_RESIDUAL"
    assert result.useful_count == 3
    assert result.useful_rate_interval.upper < 0.10


def test_intermediate_useful_rate_is_inconclusive() -> None:
    result = evaluate_phase_a(
        _rows_with_useful_count(10),
        _thresholds(),
        bootstrap_reps=250,
        bootstrap_seed=9,
    )
    assert result.status == "INCONCLUSIVE"
    assert result.useful_count == 10


@pytest.mark.parametrize("flag", ["safety_pass", "causal_pass", "ledger_pass"])
def test_hard_safety_causal_or_ledger_failure_precedes_statistical_gate(flag: str) -> None:
    rows = _rows_with_useful_count(18)
    rows[0][flag] = False
    result = evaluate_phase_a(
        rows,
        _thresholds(),
        bootstrap_reps=100,
        bootstrap_seed=3,
    )
    assert result.status == "INVALID_HARD_STOP"
    assert flag.removesuffix("_pass") in result.hard_failure_reasons
    assert result.mean_delta_bootstrap is None


def test_invalid_snapshot_cannot_be_silently_used_for_phase_c() -> None:
    rows = _rows_with_useful_count(3)
    rows[0]["valid"] = False
    result = evaluate_phase_a(
        rows,
        _thresholds(),
        bootstrap_reps=100,
        bootstrap_seed=3,
    )
    assert result.status == "INCONCLUSIVE"
    assert result.valid_count == 89
    assert result.invalid_snapshot_count == 1
