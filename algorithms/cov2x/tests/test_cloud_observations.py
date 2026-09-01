"""Tests for the cloud coordinator observation and global critic state."""

import numpy as np

from algorithms.cov2x.cloud.observations import (
    CLOUD_OBS_DIM,
    GLOBAL_STATE_DIM,
    MAX_INTERSECTIONS,
    build_cloud_observation,
    build_global_state,
    neutral_priority,
    priority_for_action,
)


def _payload():
    return {
        "simulation_time": 60.0,
        "traffic": {
            "active_vehicles": 80,
            "departed_vehicles": 120,
            "arrived_vehicles": 40,
            "min_expected_vehicles": 200,
            "hard_braking_events": 15,
        },
        "intersections": {
            "a": {
                "current_phase": 10,
                "lanes": {
                    "a_in_0": {
                        "halting_count": 4,
                        "vehicle_count": 6,
                        "waiting_time": 90.0,
                    },
                    "a_in_1": {
                        "halting_count": 1,
                        "vehicle_count": 2,
                        "waiting_time": 20.0,
                    },
                },
            }
        },
    }


def test_cloud_observation_shape_and_intersection_ids():
    cloud = build_cloud_observation(
        _payload(), phase_orders={"a": (10, 20)}
    )
    assert cloud.state.shape == (CLOUD_OBS_DIM,)
    assert cloud.intersection_ids == ("a",)
    # First intersection: halting 5/20, vehicles 8/20, waiting 110/600,
    # phase 10/8 clamped to 1.0.
    assert np.isclose(cloud.state[0], 5 / 20.0)
    assert np.isclose(cloud.state[1], 8 / 20.0)
    assert np.isclose(cloud.state[2], 110 / 600.0)
    assert np.isclose(cloud.state[3], 1.0)


def test_global_state_appends_priority_one_hot():
    priorities = {"a": priority_for_action(0)}
    state = build_global_state(
        _payload(),
        phase_orders={"a": (10, 20)},
        cloud_priorities=priorities,
    )
    assert state.shape == (GLOBAL_STATE_DIM,)
    offset = CLOUD_OBS_DIM
    assert np.allclose(state[offset : offset + 3], [1.0, 0.0, 0.0])
    # Remaining intersections default to neutral.
    for index in range(1, MAX_INTERSECTIONS):
        assert np.allclose(
            state[offset + index * 3 : offset + (index + 1) * 3],
            [0.0, 1.0, 0.0],
        )


def test_neutral_priority_and_priority_for_action_are_one_hot():
    assert np.allclose(neutral_priority(), [0.0, 1.0, 0.0])
    assert np.allclose(priority_for_action(2), [0.0, 0.0, 1.0])
    assert np.allclose(priority_for_action(99), [0.0, 1.0, 0.0])
