"""Protocol 2.0 controller tests (rule mode is torch-free)."""

import importlib.util
from pathlib import Path
import sys

import pytest

from algorithms.cov2x import controller as cov2x
from simulation.sumo.policy_transport import validate_step_response


def _load_lane_state():
    path = (
        Path(__file__).resolve().parents[1] / "road" / "lane_state.py"
    )
    spec = importlib.util.spec_from_file_location(
        "cov2x_controller_test_lane_state", path
    )
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


LANE_STATE = _load_lane_state()


def _metadata():
    return {
        "protocol_version": "2.0",
        "episode_id": "ep-rule-1",
        "period": "off_peak",
        "seed": 42,
        "decision_interval": 5.0,
        "minimum_green": 5.0,
        "intersections": {
            "a": {
                "intersection_id": "a",
                "phase_order": [10, 20],
                "incoming_lanes": ["a_in_0", "a_in_1", "a_in_2"],
                "outgoing_lanes": ["a_out_0"],
                "lanes": {
                    f"a_in_{i}": {
                        "lane_id": f"a_in_{i}",
                        "edge_id": "a_in",
                        "lane_index": i,
                        "role": "incoming",
                        "length_m": 100.0,
                        "allowed_vehicle_type_ids": ["passenger"],
                        "downstream_lane_ids": ["a_out_0"],
                    }
                    for i in range(3)
                },
                "phases": {
                    "10": {
                        "movement": "through",
                        "green_seconds": 25.0,
                        "connection_priorities": {"c1": "protected"},
                    },
                    "20": {
                        "movement": "left",
                        "green_seconds": 20.0,
                        "connection_priorities": {"c2": "protected"},
                    },
                },
                "connections": [
                    {
                        "connection_id": f"c{i + 1}",
                        "from_lane": f"a_in_{i}",
                        "to_lane": "a_out_0",
                        "movement": "through",
                    }
                    for i in range(3)
                ],
            }
        },
        "edge_lanes": {
            "a_in": [
                {
                    "lane_id": f"a_in_{i}",
                    "lane_index": i,
                    "length_m": 100.0,
                    "allowed_vehicle_type_ids": ["passenger"],
                }
                for i in range(3)
            ],
            "a_out": [
                {
                    "lane_id": "a_out_0",
                    "lane_index": 0,
                    "length_m": 80.0,
                    "allowed_vehicle_type_ids": ["passenger"],
                }
            ],
        },
        "vehicle_types": {"passenger": {"length_m": 5.0}},
    }


def _step_payload(green=True, speed=5.0, lane_position=40.0):
    return {
        "protocol_version": "2.0",
        "episode_id": "ep-rule-1",
        "step_id": 1,
        "simulation_time": 10.0,
        "intersections": {
            "a": {
                "current_phase": 10,
                "stage": "GREEN" if green else "RED",
                "stage_elapsed": 5.0,
                "lanes": {
                    f"a_in_{i}": {
                        "vehicle_count": 0,
                        "halting_count": 0,
                        "occupancy": 0.0,
                        "queue_length_m": 0.0,
                        "mean_speed": 5.0,
                        "waiting_time": 0.0,
                    }
                    for i in range(3)
                },
            }
        },
        "traffic": {
            "active_vehicles": 1,
            "departed_vehicles": 0,
            "arrived_vehicles": 0,
            "min_expected_vehicles": 10,
        },
        "vehicles": {
            "v": {
                "type_id": "passenger",
                "motion": {
                    "speed_mps": speed,
                    "acceleration_mps2": -0.8,
                    "allowed_speed_mps": 13.9,
                },
                "location": {
                    "road_id": "a_in",
                    "lane_id": "a_in_1",
                    "lane_index": 1,
                    "lane_position_m": lane_position,
                    "route_edges": ["a_in", "a_out"],
                },
                "next_signal": {
                    "intersection_id": "a",
                    "distance_m": 60.0,
                    "state": "G" if green else "r",
                },
                "leader_gap_m": 18.4,
                "time_since_last_lane_change_s": 6.5,
            }
        },
        "previous_action_results": {
            "step_id": 0,
            "vehicles": {},
        },
    }


def _set_rule_env(monkeypatch):
    monkeypatch.setenv("COV2X_MODE", "rule")
    monkeypatch.setenv("COV2X_SIGNAL_MODE", "max_pressure")
    monkeypatch.setenv("COV2X_VEHICLE_MODE", "rule")
    monkeypatch.delenv("COV2X_MODEL_PATH", raising=False)


def test_rule_mode_step_response_is_protocol_valid(monkeypatch):
    _set_rule_env(monkeypatch)
    cov2x._lane_state = LANE_STATE
    response = cov2x.initialize(_metadata())
    assert response["ready"] is True

    decision = validate_step_response(
        cov2x.step(_step_payload()),
        episode_id="ep-rule-1",
        step_id=1,
        source="test-rule",
    )
    assert set(decision.signal_actions) == {"a"}
    assert "v" in decision.vehicle_actions
    vehicle_action = decision.vehicle_actions["v"]
    assert set(vehicle_action) <= {"target_speed_mps", "target_lane_index"}
    assert vehicle_action

    cov2x.finish({})
    assert cov2x.take_collected_rollout() is None


def test_rule_mode_skips_inactive_slots(monkeypatch):
    _set_rule_env(monkeypatch)
    cov2x._lane_state = LANE_STATE
    cov2x.initialize(_metadata())
    # Speed 0 disqualifies the only candidate -> no vehicle command.
    decision = validate_step_response(
        cov2x.step(_step_payload(speed=0.0)),
        episode_id="ep-rule-1",
        step_id=1,
        source="test-rule",
    )
    assert decision.vehicle_actions == {}
    cov2x.finish({})


def test_off_eval_mode_returns_no_vehicle_commands(monkeypatch):
    monkeypatch.setenv("COV2X_MODE", "eval")
    monkeypatch.setenv("COV2X_SIGNAL_MODE", "max_pressure")
    monkeypatch.setenv("COV2X_VEHICLE_MODE", "off")
    monkeypatch.delenv("COV2X_MODEL_PATH", raising=False)
    cov2x._lane_state = LANE_STATE
    cov2x.initialize(_metadata())

    decision = validate_step_response(
        cov2x.step(_step_payload()),
        episode_id="ep-rule-1",
        step_id=1,
        source="test-off",
    )
    assert set(decision.signal_actions) == {"a"}
    assert decision.vehicle_actions == {}
    cov2x.finish({})


def test_learned_eval_mode_returns_vehicle_commands(tmp_path, monkeypatch):
    torch = pytest.importorskip("torch")
    monkeypatch.setenv("COV2X_MODE", "eval")
    monkeypatch.setenv("COV2X_SIGNAL_MODE", "max_pressure")
    monkeypatch.setenv("COV2X_VEHICLE_MODE", "learned")
    monkeypatch.delenv("COV2X_MODEL_PATH", raising=False)
    cov2x._lane_state = LANE_STATE

    checkpoint = tmp_path / "model.pt"
    cov2x.initialize(_metadata())
    cov2x.save_checkpoint(checkpoint)
    cov2x._initialized = False

    monkeypatch.setenv("COV2X_MODEL_PATH", str(checkpoint))
    cov2x.initialize(_metadata())
    response = cov2x.step(_step_payload())
    decision = validate_step_response(
        response,
        episode_id="ep-rule-1",
        step_id=1,
        source="test-learned",
    )
    assert "v" in decision.vehicle_actions
    vehicle_action = decision.vehicle_actions["v"]
    assert "target_speed_mps" in vehicle_action
    cov2x.finish({})
