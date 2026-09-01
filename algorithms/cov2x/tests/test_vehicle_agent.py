"""Unit tests for the vehicle/approach agent contract."""

import pytest

from algorithms.cov2x.vehicle.agent import (
    VehicleAction,
    VehicleActionError,
    VehicleAgent,
    VehicleAgentConfig,
    VehicleObservation,
    validate_vehicle_action,
)


def _obs(**overrides):
    base = dict(
        vehicle_id="v1",
        tls_id="demo_1",
        speed_mps=8.0,
        accel_mps2=0.0,
        allowed_speed_mps=13.9,
        dist_to_stopline_m=80.0,
        phase_is_green=True,
        signal_remaining_s=20.0,
        time_to_next_green_s=None,
        lane_index=1,
        road_lane_indices=(0, 1, 2),
    )
    base.update(overrides)
    return VehicleObservation(**base)


def test_protocol_dict_omits_none_fields():
    assert VehicleAction(target_speed_mps=8.0).to_protocol_dict() == {"target_speed_mps": 8.0}
    assert VehicleAction(target_lane_index=1).to_protocol_dict() == {"target_lane_index": 1}
    assert VehicleAction(target_speed_mps=8.0, target_lane_index=1).to_protocol_dict() == {
        "target_speed_mps": 8.0,
        "target_lane_index": 1,
    }


def test_validate_accepts_speed_only_and_lane_only():
    action = validate_vehicle_action(
        VehicleAction(target_speed_mps=8.0),
        allowed_speed_mps=13.9,
        road_lane_indices=(0, 1, 2),
    )
    assert action == {"target_speed_mps": 8.0}

    action = validate_vehicle_action(
        VehicleAction(target_lane_index=2),
        allowed_speed_mps=13.9,
        road_lane_indices=(0, 1, 2),
    )
    assert action == {"target_lane_index": 2}


def test_validate_rejects_empty_action():
    with pytest.raises(VehicleActionError):
        validate_vehicle_action(
            VehicleAction(),
            allowed_speed_mps=13.9,
            road_lane_indices=(0, 1, 2),
        )


def test_validate_rejects_speed_out_of_range():
    with pytest.raises(VehicleActionError):
        validate_vehicle_action(
            VehicleAction(target_speed_mps=-1.0),
            allowed_speed_mps=13.9,
            road_lane_indices=(0, 1, 2),
        )
    with pytest.raises(VehicleActionError):
        validate_vehicle_action(
            VehicleAction(target_speed_mps=14.0),
            allowed_speed_mps=13.9,
            road_lane_indices=(0, 1, 2),
        )


def test_validate_rejects_lane_outside_road():
    with pytest.raises(VehicleActionError):
        validate_vehicle_action(
            VehicleAction(target_lane_index=3),
            allowed_speed_mps=13.9,
            road_lane_indices=(0, 1, 2),
        )


def test_observation_to_reward_inputs():
    obs = _obs(
        speed_mps=10.0,
        accel_mps2=-1.0,
        dist_to_stopline_m=60.0,
        phase_is_green=True,
        signal_remaining_s=4.0,
    )
    reward_inputs = obs.to_reward_inputs()
    assert reward_inputs.speed_mps == 10.0
    assert reward_inputs.accel_mps2 == -1.0
    assert reward_inputs.dist_to_stopline_m == 60.0
    assert reward_inputs.phase_is_green is True
    assert reward_inputs.signal_remaining_s == 4.0
    assert reward_inputs.max_speed_mps == 13.9


def test_agent_contract_is_abstract():
    with pytest.raises(TypeError):
        VehicleAgent(VehicleAgentConfig())


def test_minimal_concrete_agent():
    class KeepSpeedAgent(VehicleAgent):
        def decide(self, obs):
            return VehicleAction(target_speed_mps=obs.allowed_speed_mps, source="rule")

        def reset(self, episode_id):
            self.episode_id = episode_id

    agent = KeepSpeedAgent(VehicleAgentConfig(decision_interval_s=5.0))
    assert agent.decision_interval_s == 5.0
    action = agent.decide(_obs())
    assert action.target_speed_mps == 13.9
    agent.reset("ep-1")
    assert agent.episode_id == "ep-1"
