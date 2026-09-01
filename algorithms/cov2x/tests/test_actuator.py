from __future__ import annotations

import pytest

from algorithms.cov2x.vehicle.actuator import (
    AdapterMode,
    ConstraintState,
    VehicleLimits,
    classify_constraint,
    outcome_reason,
    speed_reference,
)


LIMITS = VehicleLimits(accel_mps2=2.6, decel_mps2=4.5, min_gap_m=2.5, max_speed_mps=20.0)


def _vehicle(*, speed=10.0, allowed=20.0, gap=None, signal=None):
    return {
        "type_id": "car",
        "motion": {"speed_mps": speed, "allowed_speed_mps": allowed},
        "leader_gap_m": gap,
        "next_signal": signal,
        "location": {"road_id": "edge_1"},
        "traffic": {"waiting_time_s": 0.0},
    }


def test_one_shot_freezes_five_second_target() -> None:
    first = speed_reference(
        mode=AdapterMode.ONE_SHOT,
        realized_speed_mps=10.0,
        policy_start_speed_mps=8.0,
        acceleration_mps2=1.0,
        step_length_s=0.05,
        policy_cadence_s=5.0,
        max_speed_mps=20.0,
    )
    later = speed_reference(
        mode=AdapterMode.ONE_SHOT,
        realized_speed_mps=12.0,
        policy_start_speed_mps=8.0,
        acceleration_mps2=1.0,
        step_length_s=0.05,
        policy_cadence_s=5.0,
        max_speed_mps=20.0,
    )
    assert first.target_speed_mps == later.target_speed_mps == 13.0


def test_micro_step_rebuilds_from_latest_realized_speed() -> None:
    reference = speed_reference(
        mode=AdapterMode.MICRO_STEP,
        realized_speed_mps=12.0,
        policy_start_speed_mps=8.0,
        acceleration_mps2=1.0,
        step_length_s=0.05,
        policy_cadence_s=5.0,
        max_speed_mps=20.0,
    )
    assert reference.target_speed_mps == pytest.approx(12.05)


def test_speed_reference_clips_to_current_vmax() -> None:
    reference = speed_reference(
        mode=AdapterMode.MICRO_STEP,
        realized_speed_mps=19.99,
        policy_start_speed_mps=19.99,
        acceleration_mps2=1.0,
        step_length_s=0.05,
        policy_cadence_s=5.0,
        max_speed_mps=20.0,
    )
    assert reference.target_speed_mps == 20.0
    assert reference.clipped is True


def test_constraint_classifier_distinguishes_three_requested_states() -> None:
    assert classify_constraint(_vehicle(gap=None), LIMITS).state == ConstraintState.FREE_FLOW
    assert classify_constraint(_vehicle(gap=5.0), LIMITS).state == ConstraintState.LEADER_LIMITED
    assert classify_constraint(
        _vehicle(gap=5.0, signal={"state": "r", "distance_m": 8.0}), LIMITS
    ).state == ConstraintState.SIGNAL_LIMITED


def test_free_flow_rejects_internal_waiting_and_near_stationary_contexts() -> None:
    internal = _vehicle()
    internal["location"]["road_id"] = ":junction_0"
    assert classify_constraint(internal, LIMITS).state == ConstraintState.UNCLASSIFIED
    waiting = _vehicle()
    waiting["traffic"]["waiting_time_s"] = 2.0
    assert classify_constraint(waiting, LIMITS).state == ConstraintState.UNCLASSIFIED
    assert classify_constraint(_vehicle(speed=0.1), LIMITS).state == ConstraintState.UNCLASSIFIED


def test_miss_reasons_keep_native_clamp_and_unconstrained_error_separate() -> None:
    native, outcome = outcome_reason(
        accepted=True,
        status="applied",
        realized=False,
        state=ConstraintState.LEADER_LIMITED,
        reference_clipped=False,
        requested_mps2=1.0,
        realized_mps2=0.0,
        limits=LIMITS,
    )
    assert (native, outcome) == ("leader_safety", "native_car_following_clamp")
    native, outcome = outcome_reason(
        accepted=True,
        status="applied",
        realized=False,
        state=ConstraintState.FREE_FLOW,
        reference_clipped=False,
        requested_mps2=1.0,
        realized_mps2=0.0,
        limits=LIMITS,
    )
    assert (native, outcome) == (None, "unconstrained_tracking_error")
