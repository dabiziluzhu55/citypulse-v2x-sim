"""Unit tests for rollout GAE/segmentation and training-array conversion."""

import numpy as np

from algorithms.cov2x.vehicle.agent import VehicleObservation
from algorithms.cov2x.vehicle.rollout import (
    Rollout,
    RolloutStep,
    compute_gae,
    episode_summary,
    split_segments,
    to_training_arrays,
)


def _obs(slot=0, vid="v1"):
    return VehicleObservation(
        vehicle_id=vid,
        tls_id="a",
        speed_mps=8.0,
        accel_mps2=0.0,
        allowed_speed_mps=13.9,
        dist_to_stopline_m=80.0,
        phase_is_green=True,
        signal_remaining_s=20.0,
        time_to_next_green_s=None,
        lane_index=1,
        road_lane_indices=(0, 1, 2),
        state_41=np.zeros(41, dtype=np.float32),
        action_mask=np.array([True, True, True], dtype=bool),
        edge_id="a_in",
        slot_index=slot,
    )


def _step(slot=0, step_id=1, value=1.0, reward=None, next_value=None):
    return RolloutStep(
        slot_index=slot,
        vehicle_id="v1",
        edge_id="a_in",
        tls_id="a",
        obs=_obs(slot=slot),
        lane_action=0,
        speed_bin=4,
        logprob=-0.5,
        value=value,
        sim_time=float(step_id * 5),
        step_id=step_id,
        reward=reward,
        next_value=next_value,
        reward_basis="fresh" if reward is not None else "terminal",
        requested={"target_speed_mps": 13.9},
        executed={"lane_change_status": "not_completed"},
    )


def test_compute_gae_single_segment():
    steps = [
        _step(step_id=1, value=1.0, reward=0.5, next_value=2.0),
        _step(step_id=2, value=2.0, reward=0.7, next_value=3.0),
        _step(step_id=3, value=3.0),
    ]
    compute_gae(steps, gamma=0.99, lam=0.95)
    assert all(step.advantage is not None for step in steps)
    assert all(step.return_ is not None for step in steps)
    # Manual GAE on the third (terminal) step: delta = 0 + 0.99*0 - 3 = -3.
    assert steps[2].advantage == -3.0
    assert steps[2].return_ == 0.0
    # Second step: delta = 0.7 + 0.99*3 - 2 = 1.67
    # adv2 = 1.67 + 0.99*0.95*(-3)
    expected_adv2 = 1.67 + 0.99 * 0.95 * (-3.0)
    assert steps[1].advantage == expected_adv2


def test_compute_gae_includes_stale_final_reward():
    # A segment ending with a settled (stale) reward must include that reward
    # even though there is no bootstrap value (next_value is None).
    steps = [
        _step(
            step_id=1,
            value=1.0,
            reward=0.5,
            next_value=None,
            # _step's default reward_basis is "terminal"; override to stale
        ),
    ]
    steps[0].reward_basis = "stale"
    compute_gae(steps, gamma=0.99, lam=0.95)
    assert steps[0].advantage == 0.5 + 0.99 * 0.0 - 1.0
    assert steps[0].return_ == 0.5


def test_split_segments_on_missing_next_value():
    steps = [
        _step(slot=0, step_id=1, reward=0.1, next_value=1.0),
        _step(slot=0, step_id=2, reward=0.2, next_value=2.0),
        _step(slot=0, step_id=3),  # terminal: next_value None
        _step(slot=0, step_id=5, reward=0.3, next_value=3.0),
        _step(slot=1, step_id=1),
    ]
    segments = split_segments(steps)
    assert len(segments) == 3
    assert [len(segment) for segment in segments] == [3, 1, 1]


def test_to_training_arrays_shapes():
    steps = [
        _step(slot=0, step_id=1, reward=0.1, next_value=1.0),
        _step(slot=0, step_id=2),
    ]
    rollout = Rollout(
        episode_id="e1",
        period="off_peak",
        seed=42,
        duration_s=300.0,
        generation=0,
        signal_mode="max_pressure",
        steps=steps,
    )
    compute_gae(steps)
    arrays = to_training_arrays(rollout)
    assert arrays["states"].shape == (2, 41)
    assert arrays["masks"].shape == (2, 3)
    assert arrays["lane_actions"].shape == (2,)
    assert arrays["speed_bins"].shape == (2,)
    assert arrays["advantages"].shape == (2,)
    assert arrays["returns"].shape == (2,)
    assert np.isfinite(arrays["advantages"]).all()


def test_episode_summary_counts_commands_and_execution():
    steps = [
        _step(
            step_id=1,
            reward=0.5,
            next_value=1.0,
        ),
        _step(step_id=2),
    ]
    rollout = Rollout(
        episode_id="e1",
        period="off_peak",
        seed=42,
        duration_s=300.0,
        generation=0,
        signal_mode="max_pressure",
        steps=steps,
    )
    summary = episode_summary(rollout)
    assert summary["command_count"] == 2
    assert summary["speed_command_count"] == 2
    assert summary["lane_change_requested"] == 0
    assert summary["reward_mean"] == 0.5
    assert summary["reward_fresh_count"] == 1
    assert summary["reward_terminal_count"] == 1
