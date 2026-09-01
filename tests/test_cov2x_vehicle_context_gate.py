from __future__ import annotations

import pytest

from algorithms.cov2x.vehicle.context_gate import (
    ADVICE_ELIGIBLE,
    NO_FEASIBLE_ADVICE,
    PASS_CURRENT_GREEN,
    GateConfig,
    classify_gate,
    select_period_treatments,
)


def _snapshot(**overrides):
    value = {
        "movement_id": "through",
        "distance_m": 70.5,
        "speed_mps": 12.0,
        "base_speed_mps": 13.9,
        "leader_gap_m": None,
        "minimum_gap_m": 2.5,
        "max_decel_mps2": 4.5,
        "queue_length_m": 0.0,
        "current_phase": 1,
        "pending_phase": None,
        "stage": "GREEN",
        "stage_elapsed_s": 5.0,
        "target_phase": 2,
        "minimum_green_s": 5.0,
        "phase_movements": {1: ["left"], 2: ["through"]},
        "phase_timings": {
            1: {"green_s": 25.0, "yellow_s": 3.0, "clearance_s": 1.0},
            2: {"green_s": 25.0, "yellow_s": 3.0, "clearance_s": 1.0},
        },
    }
    value.update(overrides)
    return value


def test_target_arrival_is_usable_window_start_plus_exactly_one_sumo_step():
    decision = classify_gate(_snapshot(), GateConfig(sumo_step_s=0.05))

    assert decision.category == ADVICE_ELIGIBLE
    assert decision.nominal_window_start_s == pytest.approx(4.0)
    assert decision.queue_clearance_delay_s == pytest.approx(3.0)
    assert decision.usable_window_start_s == pytest.approx(7.0)
    assert decision.target_arrival_time_s == pytest.approx(7.05)
    assert decision.reference_speed_cap_mps == pytest.approx(10.0)


def test_native_arrival_inside_current_usable_green_passes_without_advice():
    decision = classify_gate(
        _snapshot(
            current_phase=2,
            target_phase=2,
            stage_elapsed_s=5.0,
            distance_m=54.0,
            speed_mps=12.0,
        )
    )

    assert decision.category == PASS_CURRENT_GREEN
    assert decision.native_arrival_time_s == pytest.approx(4.5)
    assert decision.reference_speed_cap_mps is None


def test_current_green_does_not_claim_time_beyond_current_road_action():
    decision = classify_gate(
        _snapshot(
            current_phase=2,
            target_phase=2,
            stage_elapsed_s=5.0,
            distance_m=66.0,
            speed_mps=12.0,
        )
    )

    assert decision.category == NO_FEASIBLE_ADVICE
    assert decision.reason == "cap_only_advice_cannot_reach_usable_window"


def test_native_arrival_already_inside_next_window_defaults_to_release():
    decision = classify_gate(
        _snapshot(distance_m=105.6, speed_mps=12.0)
    )

    assert decision.category == NO_FEASIBLE_ADVICE
    assert decision.reason == "native_release_reaches_usable_window"
    assert decision.nominal_window_end_s == pytest.approx(9.0)


def test_next_green_does_not_claim_static_schedule_duration():
    decision = classify_gate(_snapshot(distance_m=109.2, speed_mps=12.0))

    assert decision.category == NO_FEASIBLE_ADVICE
    assert decision.reason == "cap_only_advice_cannot_reach_usable_window"


def test_leader_headway_failure_is_no_feasible_advice():
    decision = classify_gate(_snapshot(leader_gap_m=10.0))

    assert decision.category == NO_FEASIBLE_ADVICE
    assert decision.reason == "leader_gap_not_feasible"


def test_no_committed_target_window_does_not_use_future_action_tape():
    decision = classify_gate(_snapshot(target_phase=1))

    assert decision.category == NO_FEASIBLE_ADVICE
    assert decision.reason == "no_causally_available_usable_window"


def _candidate(seed: int, category: str, time_s: float, ordinal: int):
    return {
        "seed": seed,
        "simulation_time": time_s,
        "step_id": int(time_s / 5.0),
        "opportunity": {
            "vehicle_id": f"v-{seed}-{ordinal}",
            "movement_id": "through",
            "intersection_id": "tls",
            "previous_advice_mps": None,
            "transition_kind": "native_release",
            "gate_decision": {
                "category": category,
                "reference_speed_cap_mps": 10.0 if category == ADVICE_ELIGIBLE else None,
            },
        },
    }


def test_period_selection_freezes_category_quotas_and_spacing():
    by_seed = {}
    categories = (ADVICE_ELIGIBLE, PASS_CURRENT_GREEN, NO_FEASIBLE_ADVICE)
    for seed in (1, 2, 3, 4):
        rows = []
        ordinal = 0
        for category in categories:
            for offset in range(8):
                ordinal += 1
                rows.append(
                    _candidate(seed, category, 10.0 + 10.0 * ordinal, ordinal)
                )
        by_seed[seed] = rows

    selected = select_period_treatments(
        by_seed,
        quotas={
            ADVICE_ELIGIBLE: 12,
            PASS_CURRENT_GREEN: 6,
            NO_FEASIBLE_ADVICE: 6,
        },
        minimum_spacing_s=20.0,
        episode_duration_s=300.0,
        horizon_s=20.0,
        max_per_seed=8,
    )

    assert len(selected) == 24
    assert {
        category: sum(
            row["opportunity"]["gate_decision"]["category"] == category
            for row in selected
        )
        for category in categories
    } == {
        ADVICE_ELIGIBLE: 12,
        PASS_CURRENT_GREEN: 6,
        NO_FEASIBLE_ADVICE: 6,
    }
    for seed in by_seed:
        times = sorted(
            row["simulation_time"] for row in selected if row["seed"] == seed
        )
        assert all(right - left >= 20.0 for left, right in zip(times, times[1:]))
        assert len(times) <= 8
