"""Tests for the joint vehicle-road-cloud policy stack."""

import numpy as np
import pytest

torch = pytest.importorskip("torch")

from algorithms.cov2x.vehicle.agent import VehicleObservation
from algorithms.cov2x.cloud.observations import CloudObservation
from algorithms.cov2x.collab.joint_policy import JointPPOAgent, JointPolicyConfig
from algorithms.cov2x.collab.joint_rollout import (
    CloudRolloutStep,
    JointRollout,
    SignalRolloutStep,
    compute_gae,
)
from algorithms.cov2x.vehicle.rollout import RolloutStep
from algorithms.cov2x.road.signal_observations import SignalObservation


def _vehicle_step(index: int) -> RolloutStep:
    return RolloutStep(
        slot_index=index,
        vehicle_id=f"v{index}",
        edge_id="e",
        tls_id="a",
        obs=VehicleObservation(
            vehicle_id=f"v{index}",
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
        value=1.0,
        sim_time=float(index),
        step_id=index,
        reward=0.5,
        next_value=1.5,
        global_state=np.zeros(146, dtype=np.float32),
    )


def _signal_step(index: int) -> SignalRolloutStep:
    return SignalRolloutStep(
        tls_id="a",
        state=np.zeros(37, dtype=np.float32),
        mask=np.ones(2, dtype=bool),
        action=0,
        logprob=-0.3,
        value=1.0,
        sim_time=float(index),
        step_id=index,
        source_phase=10,
        requested_phase=10,
        phase_order=(10, 20),
        reward=0.5,
        next_value=1.5,
        global_state=np.zeros(146, dtype=np.float32),
    )


def _cloud_step(index: int) -> CloudRolloutStep:
    return CloudRolloutStep(
        state=np.zeros(86, dtype=np.float32),
        action=np.zeros(2, dtype=np.int64),
        logprob=-0.3,
        value=1.0,
        sim_time=float(index),
        step_id=index,
        intersection_ids=("a", "b"),
        reward=0.5,
        next_value=1.5,
        global_state=np.zeros(146, dtype=np.float32),
    )


def _rollout() -> JointRollout:
    rollout = JointRollout(
        episode_id="ep1",
        period="off_peak",
        seed=1,
        duration_s=300.0,
        generation=0,
        signal_mode="learned",
    )
    for index in range(8):
        rollout.vehicle_steps.append(_vehicle_step(index))
        rollout.signal_steps.append(_signal_step(index))
    for index in range(3):
        rollout.cloud_steps.append(_cloud_step(index))
    compute_gae(rollout.vehicle_steps, key_fn=lambda s: s.slot_index)
    compute_gae(rollout.signal_steps, key_fn=lambda s: s.tls_id)
    compute_gae(rollout.cloud_steps, key_fn=lambda s: "cloud")
    return rollout


def test_act_batches_have_expected_shapes():
    agent = JointPPOAgent(JointPolicyConfig())
    vehicle_obs = [
        VehicleObservation(
            vehicle_id="v1",
            tls_id="a",
            speed_mps=10.0,
            accel_mps2=0.0,
            allowed_speed_mps=13.9,
            dist_to_stopline_m=50.0,
            phase_is_green=True,
            signal_remaining_s=10.0,
            state_41=np.zeros(41, dtype=np.float32),
            action_mask=np.ones(3, dtype=bool),
            cloud_context=np.asarray([1.0, 0.0, 0.0], dtype=np.float32),
        )
    ]
    signal_obs = [
        SignalObservation(
            tls_id="a",
            state=np.zeros(37, dtype=np.float32),
            action_mask=np.ones(2, dtype=bool),
            phase_order=(10, 20),
            current_phase=10,
            stage="GREEN",
            stage_elapsed=8.0,
            min_green_s=5.0,
            cloud_priority=np.asarray([0.0, 1.0, 0.0], dtype=np.float32),
        )
    ]
    cloud_obs = CloudObservation(
        state=np.zeros(86, dtype=np.float32),
        intersection_ids=("a", "b"),
    )

    vehicle = agent.act_vehicle_batch(vehicle_obs)
    signal = agent.act_signal_batch(signal_obs)
    cloud = agent.act_cloud(cloud_obs)
    assert vehicle.lane_action.shape == (1,)
    assert vehicle.speed_bin.shape == (1,)
    assert signal.action.shape == (1,)
    assert cloud.action.shape == (2,)


def test_update_touches_all_three_actors_and_critic():
    agent = JointPPOAgent(JointPolicyConfig(ppo_epochs=2, minibatch_size=4))
    rollout = _rollout()
    diagnostics = agent.update(rollout)
    assert diagnostics["steps"] == {
        "vehicle": 8,
        "signal": 8,
        "cloud": 3,
    }
    for family in ("vehicle", "signal", "cloud"):
        assert diagnostics[family][f"{family}_parameter_delta_l2"] > 0.0
        assert "critic_loss" in diagnostics["critic"]
    assert diagnostics["policy_generation"] == 1
    assert diagnostics["episode_count"] == 1


def test_state_dict_roundtrip(tmp_path):
    agent = JointPPOAgent(JointPolicyConfig())
    agent.update(_rollout())
    state = agent.state_dict()
    clone = JointPPOAgent(JointPolicyConfig())
    clone.load_state_dict(state)
    assert clone.policy_generation == agent.policy_generation
    assert clone.episode_count == agent.episode_count
