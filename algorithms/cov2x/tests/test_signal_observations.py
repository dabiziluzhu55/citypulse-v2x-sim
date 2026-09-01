"""Tests for the road-agent observation builder and phase legality mask."""

import numpy as np

import pytest

from algorithms.cov2x.road.signal_observations import (
    MAX_APPROACHES,
    SIGNAL_ACTION_DIM,
    SIGNAL_OBS_DIM,
    build_signal_observations,
    phase_legal_mask,
)


def _payload(phase_order=(10, 20), stage="GREEN", stage_elapsed=10.0):
    return {
        "minimum_green": 5.0,
        "intersections": {
            "a": {
                "current_phase": 10,
                "stage": stage,
                "stage_elapsed": stage_elapsed,
                "lanes": {
                    "a_in_0": {
                        "halting_count": 2,
                        "vehicle_count": 4,
                        "waiting_time": 30.0,
                    },
                    "a_in_1": {
                        "halting_count": 0,
                        "vehicle_count": 1,
                        "waiting_time": 5.0,
                    },
                },
            }
        },
    }


def test_signal_observation_shape_and_fields():
    obs = build_signal_observations(
        _payload(), phase_orders={"a": (10, 20)}
    )
    assert len(obs) == 1
    signal = obs[0]
    assert signal.tls_id == "a"
    assert signal.state.shape == (SIGNAL_OBS_DIM,)
    assert signal.action_mask.shape == (SIGNAL_ACTION_DIM,)
    assert signal.phase_order == (10, 20)
    assert signal.current_phase == 10
    # Default cloud priority is neutral.
    assert np.allclose(signal.cloud_priority, [0.0, 1.0, 0.0])


def test_approach_features_are_padded_to_fixed_dimension():
    payload = _payload()
    payload["intersections"]["a"]["lanes"] = {
        "a_in_0": {
            "halting_count": 3,
            "vehicle_count": 5,
            "waiting_time": 45.0,
        }
    }
    signal = build_signal_observations(
        payload, phase_orders={"a": (10, 20)}
    )[0]
    approach_block = signal.state[: MAX_APPROACHES * 3]
    # One populated approach, seven zero-padded.
    assert approach_block[0] == pytest.approx(3 / 10.0)
    assert approach_block[1] == pytest.approx(5 / 10.0)
    assert approach_block[2] == pytest.approx(45 / 300.0)
    assert np.allclose(approach_block[3:], 0.0)


def test_phase_mask_allows_current_phase_during_min_green_hold():
    mask = phase_legal_mask(
        phase_order=(10, 20),
        current_phase=10,
        stage="GREEN",
        stage_elapsed=2.0,
        min_green_s=5.0,
    )
    assert bool(mask[0]) is True  # keep
    assert bool(mask[1]) is False  # advance blocked by minimum green
    assert mask.sum() == 1


def test_phase_mask_allows_advance_after_min_green():
    mask = phase_legal_mask(
        phase_order=(10, 20),
        current_phase=10,
        stage="GREEN",
        stage_elapsed=8.0,
        min_green_s=5.0,
    )
    assert mask[0]  # keep
    assert mask[1]  # advance
    assert mask.sum() == 2


def test_phase_mask_blocks_advance_for_single_phase_order():
    mask = phase_legal_mask(
        phase_order=(10,),
        current_phase=10,
        stage="GREEN",
        stage_elapsed=30.0,
        min_green_s=5.0,
    )
    assert mask[0]
    assert not mask[1]


def test_phase_mask_blocks_advance_in_transition():
    mask = phase_legal_mask(
        phase_order=(10, 20),
        current_phase=10,
        stage="YELLOW",
        stage_elapsed=2.0,
        min_green_s=5.0,
    )
    assert mask[0]
    assert not mask[1]


def test_cloud_priority_flows_into_signal_observation():
    priorities = {
        "a": np.asarray([1.0, 0.0, 0.0], dtype=np.float32)  # relax
    }
    signal = build_signal_observations(
        _payload(),
        phase_orders={"a": (10, 20)},
        cloud_priorities=priorities,
    )[0]
    assert np.allclose(signal.cloud_priority, [1.0, 0.0, 0.0])
    assert np.allclose(signal.state[-3:], [1.0, 0.0, 0.0])
