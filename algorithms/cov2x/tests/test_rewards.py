"""Unit tests for the CCTV-MAC-adapted vehicle reward (Eq. 9-10)."""

import pytest

from algorithms.cov2x.vehicle.rewards import (
    VehicleRewardInputs,
    VehicleRewardWeights,
    braking_penalty,
    vehicle_reward,
    vehicle_reward_components,
)

MAX_SPEED = 13.9
MAX_ACCEL = 5.0
DIST_MAX = 150.0


def _inputs(
    speed=5.0,
    accel=0.0,
    dist=50.0,
    green=False,
    remaining=0.0,
    waiting=0.0,
    max_speed=MAX_SPEED,
    max_accel=MAX_ACCEL,
    dist_max=DIST_MAX,
    waiting_max=300.0,
    weights=None,
):
    return VehicleRewardInputs(
        speed_mps=speed,
        accel_mps2=accel,
        dist_to_stopline_m=dist,
        phase_is_green=green,
        signal_remaining_s=remaining,
        max_speed_mps=max_speed,
        max_accel_mps2=max_accel,
        dist_to_stopline_max_m=dist_max,
        waiting_time_s=waiting,
        waiting_time_max_s=waiting_max,
        weights=weights or VehicleRewardWeights(),
    )


def test_components_sum_to_total():
    inputs = _inputs(speed=10.0, accel=-1.0, dist=60.0, green=True, remaining=4.0)
    comps = vehicle_reward_components(inputs)
    assert comps["total"] == pytest.approx(
        comps["speed"] + comps["accel"] + comps["proximity"] + comps["braking"]
    )
    assert vehicle_reward(inputs) == pytest.approx(comps["total"])


def test_speed_term_is_normalized_and_clipped():
    assert vehicle_reward_components(_inputs(speed=MAX_SPEED))["speed"] == pytest.approx(1.0)
    assert vehicle_reward_components(_inputs(speed=0.0))["speed"] == pytest.approx(0.0)
    assert vehicle_reward_components(_inputs(speed=2.0 * MAX_SPEED))["speed"] == pytest.approx(1.0)


def test_accel_penalty_is_negative_and_clipped():
    assert vehicle_reward_components(_inputs(accel=-MAX_ACCEL))["accel"] == pytest.approx(-1.0)
    assert vehicle_reward_components(_inputs(accel=2.5))["accel"] == pytest.approx(-0.5)
    assert vehicle_reward_components(_inputs(accel=-3.0 * MAX_ACCEL))["accel"] == pytest.approx(-1.0)


def test_proximity_penalty_uses_distance_ratio():
    assert vehicle_reward_components(_inputs(dist=DIST_MAX))["proximity"] == pytest.approx(-1.0)
    assert vehicle_reward_components(_inputs(dist=75.0))["proximity"] == pytest.approx(-0.5)


def test_braking_penalty_when_stopped():
    penalty = braking_penalty(
        0.0,
        -2.0,
        phase_is_green=False,
        signal_remaining_s=0.0,
        dist_to_stopline_m=10.0,
    )
    assert penalty == pytest.approx(6.0 * -2.0 / MAX_ACCEL)


def test_braking_penalty_when_green_but_can_cross():
    penalty = braking_penalty(
        10.0,
        -1.0,
        phase_is_green=True,
        signal_remaining_s=3.0,
        dist_to_stopline_m=20.0,
    )
    assert penalty == pytest.approx(6.0 * -1.0 / MAX_ACCEL)


def test_no_braking_penalty_when_green_cannot_cross():
    penalty = braking_penalty(
        10.0,
        -1.0,
        phase_is_green=True,
        signal_remaining_s=1.0,
        dist_to_stopline_m=20.0,
    )
    assert penalty == 0.0


def test_no_braking_penalty_on_positive_accel():
    penalty = braking_penalty(
        5.0,
        1.0,
        phase_is_green=True,
        signal_remaining_s=3.0,
        dist_to_stopline_m=20.0,
    )
    assert penalty == 0.0


def test_custom_weights_scale_terms():
    inputs = _inputs(
        speed=6.95,
        accel=-2.5,
        dist=75.0,
        weights=VehicleRewardWeights(speed=2.0, accel=3.0, proximity=4.0, braking=5.0),
    )
    comps = vehicle_reward_components(inputs)
    assert comps["speed"] == pytest.approx(2.0 * 0.5)
    assert comps["accel"] == pytest.approx(-3.0 * 0.5)
    assert comps["proximity"] == pytest.approx(-4.0 * 0.5)


def test_waiting_penalty_is_normalized_and_negative():
    assert vehicle_reward_components(_inputs(waiting=0.0))["waiting"] == 0.0
    assert vehicle_reward_components(_inputs(waiting=150.0))["waiting"] == pytest.approx(-0.25)
    assert vehicle_reward_components(_inputs(waiting=600.0))["waiting"] == pytest.approx(-0.5)


def test_waiting_penalty_scales_with_weight():
    inputs = _inputs(waiting=150.0, weights=VehicleRewardWeights(waiting=2.0))
    assert vehicle_reward_components(inputs)["waiting"] == pytest.approx(-1.0)
