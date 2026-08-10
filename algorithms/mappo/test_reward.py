from __future__ import annotations

import pytest

from algorithms.mappo.reward import (
    V5ARewardAccumulator,
    normalize_reward,
    spillback_penalty,
)


def _intersection(
    *,
    incoming_halting: float = 5.0,
    incoming_waiting: float = 100.0,
    outgoing_occupancy: float = 90.0,
) -> dict:
    return {
        "lanes": {
            "in": {
                "halting_count": incoming_halting,
                "waiting_time": incoming_waiting,
            },
            "out": {"occupancy": outgoing_occupancy},
        }
    }


def test_reward_clipping_matches_ippo_v5a() -> None:
    assert normalize_reward(0.1) == pytest.approx(0.1)
    assert normalize_reward(5.0) == pytest.approx(1.0)
    assert normalize_reward(-5.0) == pytest.approx(-3.0)


def test_spillback_penalty_matches_sumo_percent_boundaries() -> None:
    assert spillback_penalty(70.0) == pytest.approx(0.0)
    assert spillback_penalty(80.0) == pytest.approx(0.25)
    assert spillback_penalty(90.0) == pytest.approx(1.0)


def test_accumulator_matches_hand_derived_congestion_and_spillback() -> None:
    accumulator = V5ARewardAccumulator(
        incoming_lanes=("in",),
        outgoing_lanes=("out",),
        lane_capacities={"in": 20.0},
        incoming_capacity=20.0,
        flow_reference_rate=0.5,
        waiting_start=100.0,
    )
    for _ in range(3):
        accumulator.observe(
            _intersection(), elapsed_seconds=5.0, delay_increment=0.0, crossings=0
        )

    result = accumulator.finalize()

    assert result.components["L"] == pytest.approx(0.0)
    assert result.components["S"] == pytest.approx(0.25)
    assert result.components["Qmax"] == pytest.approx(0.25)
    assert result.components["D"] == pytest.approx(0.1375)
    assert result.components["B"] == pytest.approx(0.7)
    assert result.components["F_safe"] == pytest.approx(0.0)
    assert result.components["H"] == pytest.approx(0.0)
    assert result.raw_reward == pytest.approx(-0.1875)
    assert result.reward == pytest.approx(-0.1875)


def test_safe_crossing_and_waiting_gain_use_same_physical_scale() -> None:
    accumulator = V5ARewardAccumulator(
        incoming_lanes=("in",),
        outgoing_lanes=("out",),
        lane_capacities={"in": 20.0},
        incoming_capacity=20.0,
        flow_reference_rate=0.5,
        waiting_start=100.0,
    )
    accumulator.observe(
        _intersection(
            incoming_halting=0.0,
            incoming_waiting=80.0,
            outgoing_occupancy=0.0,
        ),
        elapsed_seconds=5.0,
        delay_increment=0.0,
        crossings=2,
    )

    result = accumulator.finalize()

    assert result.components["F_safe"] == pytest.approx(0.8)
    assert result.components["H"] == pytest.approx(0.005)
    assert result.components["B"] == pytest.approx(0.0)
    assert result.raw_reward == pytest.approx(0.16025)


def test_reward_cannot_finalize_without_followup_observation() -> None:
    accumulator = V5ARewardAccumulator(
        incoming_lanes=("in",),
        outgoing_lanes=("out",),
        lane_capacities={"in": 20.0},
        incoming_capacity=20.0,
        flow_reference_rate=0.5,
        waiting_start=100.0,
    )

    with pytest.raises(RuntimeError, match="follow-up observation"):
        accumulator.finalize()
