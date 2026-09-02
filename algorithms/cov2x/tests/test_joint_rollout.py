"""Tests for multi-timescale joint rollout storage and GAE."""

import numpy as np

from algorithms.cov2x.collab.joint_rollout import (
    CloudRolloutStep,
    JointRollout,
    SignalRolloutStep,
    compute_gae,
    to_cloud_joint_arrays,
    to_signal_joint_arrays,
    to_vehicle_joint_arrays,
)
from algorithms.cov2x.vehicle.agent import VehicleObservation
from algorithms.cov2x.vehicle.rollout import RolloutStep


def _vehicle_step(slot=0, value=1.0, reward=0.5, next_value=2.0):
    return RolloutStep(
        slot_index=slot,
        vehicle_id=f"v{slot}",
        edge_id="e",
        tls_id="a",
        obs=VehicleObservation(
            vehicle_id=f"v{slot}",
            tls_id="a",
            speed_mps=10.0,
            accel_mps2=0.0,
            allowed_speed_mps=13.9,
            dist_to_stopline_m=50.0,
            phase_is_green=True,
            signal_remaining_s=10.0,
            state_41=np.zeros(41, dtype=np.float32),
            action_mask=np.ones(3, dtype=bool),
            cloud_context=np.asarray([0.0, 1.0, 0.0], dtype=np.float32),
        ),
        lane_action=0,
        speed_bin=4,
        logprob=-0.3,
        value=value,
        sim_time=0.0,
        step_id=0,
        reward=reward,
        next_value=next_value,
    )


def _signal_step(tls_id="a", value=1.0, reward=0.5, next_value=2.0):
    return SignalRolloutStep(
        tls_id=tls_id,
        state=np.zeros(37, dtype=np.float32),
        mask=np.ones(8, dtype=bool),
        action=0,
        logprob=-0.3,
        value=value,
        sim_time=0.0,
        step_id=0,
        source_phase=10,
        requested_phase=10,
        phase_order=(10, 20),
        reward=reward,
        next_value=next_value,
    )


def _cloud_step(value=1.0, reward=0.5, next_value=2.0):
    return CloudRolloutStep(
        state=np.zeros(86, dtype=np.float32),
        action=np.zeros(2, dtype=np.int64),
        logprob=-0.3,
        value=value,
        sim_time=0.0,
        step_id=0,
        intersection_ids=("a", "b"),
        reward=reward,
        next_value=next_value,
    )


def test_compute_gae_splits_by_identity():
    steps = [
        _vehicle_step(slot=0, value=1.0, reward=0.5, next_value=2.0),
        _vehicle_step(slot=1, value=3.0, reward=1.0, next_value=None),
        _vehicle_step(slot=0, value=2.0, reward=0.0, next_value=None),
    ]
    compute_gae(steps, key_fn=lambda step: step.slot_index, gamma=0.99, lam=0.95)
    assert steps[0].return_ is not None
    assert steps[1].return_ is not None
    assert steps[2].return_ is not None
    # Slot 0 has two steps: GAE propagates the terminal step's delta back.
    delta0 = 0.5 + 0.99 * 2.0 - 1.0
    delta1 = 0.0 + 0.0 - 2.0
    assert np.isclose(steps[0].advantage, delta0 + 0.99 * 0.95 * delta1)
    assert np.isclose(steps[0].return_, steps[0].advantage + 1.0)
    # Slot 1 terminates: no next value.
    assert np.isclose(steps[1].advantage, 1.0 + 0.0 - 3.0)


def test_signal_and_cloud_gae_respect_physical_time_gamma():
    signal = _signal_step()
    cloud = _cloud_step()
    compute_gae([signal], key_fn=lambda s: s.tls_id, gamma=0.99 ** 3, lam=0.95)
    compute_gae([cloud], key_fn=lambda s: "cloud", gamma=0.99 ** 6, lam=0.95)
    assert np.isclose(signal.advantage, 0.5 + (0.99 ** 3) * 2.0 - 1.0)
    assert np.isclose(cloud.advantage, 0.5 + (0.99 ** 6) * 2.0 - 1.0)


def test_training_arrays_stack_all_families():
    rollout = JointRollout(
        episode_id="ep1",
        period="off_peak",
        seed=1,
        duration_s=300.0,
        generation=0,
        signal_mode="learned",
    )
    rollout.vehicle_steps.append(_vehicle_step())
    rollout.signal_steps.append(_signal_step())
    rollout.cloud_steps.append(_cloud_step())

    vehicle = to_vehicle_joint_arrays(rollout)
    signal = to_signal_joint_arrays(rollout)
    cloud = to_cloud_joint_arrays(rollout)
    assert vehicle["states"].shape == (1, 44)
    assert signal["states"].shape == (1, 37)
    assert signal["masks"].shape == (1, 8)
    assert cloud["states"].shape == (1, 86)
    assert cloud["actions"].shape == (1, 2)
