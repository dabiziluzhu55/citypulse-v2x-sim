"""Unit tests for the network-level team reward and traffic snapshot."""

import pytest

from algorithms.cov2x.collab.joint_rewards import (
    TrafficSnapshot,
    team_reward,
)


def _snapshot(
    *,
    arrived=0,
    hard_braking=0,
    halting=0,
    waiting=0.0,
    mean_speed=0.0,
):
    return TrafficSnapshot(
        active_vehicles=0,
        departed_vehicles=0,
        arrived_vehicles=arrived,
        min_expected_vehicles=0,
        hard_braking_events=hard_braking,
        total_halting=halting,
        total_waiting_time=waiting,
        mean_speed=mean_speed,
    )


def test_team_reward_rewards_arrivals():
    before = _snapshot()
    after = _snapshot(arrived=2)
    assert team_reward(before, after) > 0.0


def test_team_reward_penalizes_waiting_growth():
    before = _snapshot(waiting=100.0, halting=10)
    after = _snapshot(waiting=600.0, halting=10)
    assert team_reward(before, after) < team_reward(after, before)


def test_team_reward_rewards_speed_improvement():
    before = _snapshot(mean_speed=6.0)
    after = _snapshot(mean_speed=9.0)
    assert team_reward(before, after) > 0.0


def test_team_reward_penalizes_halting_growth():
    before = _snapshot(halting=10)
    after = _snapshot(halting=60)
    assert team_reward(before, after) < 0.0


def test_snapshot_from_payload_aggregates_lanes():
    payload = {
        "traffic": {
            "active_vehicles": 10,
            "departed_vehicles": 20,
            "arrived_vehicles": 3,
            "min_expected_vehicles": 30,
            "hard_braking_events": 4,
        },
        "intersections": {
            "a": {
                "lanes": {
                    "a0": {
                        "halting_count": 2,
                        "waiting_time": 40.0,
                        "vehicle_count": 4,
                        "mean_speed": 5.0,
                    },
                    "a1": {
                        "halting_count": 3,
                        "waiting_time": 60.0,
                        "vehicle_count": 1,
                        "mean_speed": 1.0,
                    },
                }
            }
        },
    }
    snap = TrafficSnapshot.from_payload(payload)
    assert snap.total_halting == 5
    assert snap.total_waiting_time == pytest.approx(100.0)
    assert snap.mean_speed == pytest.approx((4 * 5.0 + 1 * 1.0) / 5.0)
    assert snap.arrived_vehicles == 3
