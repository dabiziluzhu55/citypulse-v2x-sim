"""Protocol 2.0 joint-mode controller tests (train + collect + update)."""

import importlib.util
from pathlib import Path
import sys

import numpy as np
import pytest

torch = pytest.importorskip("torch")

from algorithms.cov2x import controller as cov2x
from algorithms.cov2x.collab.joint_rollout import JointRollout, to_signal_joint_arrays


def _load_lane_state():
    path = Path(__file__).resolve().parents[1] / "road" / "lane_state.py"
    spec = importlib.util.spec_from_file_location(
        "cov2x_joint_controller_test_lane_state", path
    )
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


LANE_STATE = _load_lane_state()


def _metadata(phase_order=(10, 20)):
    order = list(phase_order)
    phases = {
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
    }
    for phase in order[2:]:
        phases[str(phase)] = {
            "movement": "through",
            "green_seconds": 25.0,
            "connection_priorities": {f"c{int(phase)}": "protected"},
        }
    return {
        "protocol_version": "2.0",
        "episode_id": "ep-joint-1",
        "period": "off_peak",
        "seed": 7,
        "decision_interval": 5.0,
        "minimum_green": 5.0,
        "intersections": {
            "a": {
                "intersection_id": "a",
                "phase_order": order,
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
                "phases": phases,
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


def _payload(step_id, sim_time, arrived=0, hard_braking=0, current_phase=10):
    return {
        "protocol_version": "2.0",
        "episode_id": "ep-joint-1",
        "step_id": step_id,
        "simulation_time": sim_time,
        "intersections": {
            "a": {
                "current_phase": current_phase,
                "stage": "GREEN",
                "stage_elapsed": 8.0,
                "lanes": {
                    f"a_in_{i}": {
                        "vehicle_count": 2,
                        "halting_count": 1,
                        "occupancy": 0.2,
                        "queue_length_m": 6.0,
                        "mean_speed": 5.0,
                        "waiting_time": 12.0,
                    }
                    for i in range(3)
                },
            }
        },
        "traffic": {
            "active_vehicles": 3,
            "departed_vehicles": 2,
            "arrived_vehicles": arrived,
            "min_expected_vehicles": 10,
            "hard_braking_events": hard_braking,
        },
        "vehicles": {
            "v": {
                "type_id": "passenger",
                "motion": {
                    "speed_mps": 5.0,
                    "acceleration_mps2": 0.0,
                    "allowed_speed_mps": 13.9,
                },
                "location": {
                    "road_id": "a_in",
                    "lane_id": "a_in_1",
                    "lane_index": 1,
                    "lane_position_m": 40.0,
                    "route_edges": ["a_in", "a_out"],
                },
                "next_signal": {
                    "intersection_id": "a",
                    "distance_m": 60.0,
                    "state": "G",
                },
                "leader_gap_m": 18.4,
                "time_since_last_lane_change_s": 6.5,
            }
        },
        "previous_action_results": {
            "step_id": max(step_id - 1, 0),
            "vehicles": {},
        },
    }


def _set_joint_env(monkeypatch):
    monkeypatch.setenv("COV2X_MODE", "train")
    monkeypatch.setenv("COV2X_SIGNAL_MODE", "learned")
    monkeypatch.setenv("COV2X_CLOUD_MODE", "learned")
    monkeypatch.setenv("COV2X_VEHICLE_MODE", "learned")
    monkeypatch.setenv("COV2X_SIGNAL_DECISION_INTERVAL", "15.0")
    monkeypatch.setenv("COV2X_CLOUD_DECISION_INTERVAL", "30.0")
    monkeypatch.delenv("COV2X_MODEL_PATH", raising=False)
    monkeypatch.setenv("COV2X_CHECKPOINT_DIR", str(Path("/tmp/cov2x_joint_ckpt")))


def test_joint_train_mode_collects_all_three_families(monkeypatch):
    _set_joint_env(monkeypatch)
    cov2x._lane_state = LANE_STATE
    cov2x.initialize(_metadata())
    assert cov2x._joint_policy is not None

    for step_id, sim_time, arrived in [
        (0, 0.0, 0),
        (1, 5.0, 1),
        (2, 10.0, 1),
        (3, 15.0, 2),
        (4, 20.0, 2),
        (5, 25.0, 3),
        (6, 30.0, 4),
    ]:
        response = cov2x.step(
            _payload(step_id, sim_time, arrived=arrived)
        )
        assert set(response["actions"]) == {"signals", "vehicles"}
        assert set(response["actions"]["signals"]) == {"a"}

    cov2x.finish(_payload(7, 35.0, arrived=5))
    rollout = cov2x.take_collected_rollout()
    assert isinstance(rollout, JointRollout)
    assert len(rollout.vehicle_steps) > 0
    assert len(rollout.signal_steps) >= 2
    assert len(rollout.cloud_steps) >= 2

    diagnostics = cov2x.train_on_rollout(rollout)
    assert diagnostics is not None
    for family in ("vehicle", "signal", "cloud"):
        assert diagnostics[family][f"{family}_parameter_delta_l2"] > 0.0
    assert "critic_loss" in diagnostics["critic"]


def test_signal_rollout_action_is_keep_advance_not_phase_index(monkeypatch):
    """Rollout must store the 0/1 policy action, not the target phase index."""
    torch = pytest.importorskip("torch")
    _set_joint_env(monkeypatch)
    cov2x._lane_state = LANE_STATE
    cov2x._joint_policy = None
    cov2x._policy = None
    cov2x.initialize(_metadata(phase_order=(10, 20, 30, 40)))

    class _AdvanceBatch:
        action = np.asarray([1], dtype=np.int64)
        logprob = np.asarray([-0.1], dtype=np.float32)
        entropy = np.asarray([0.1], dtype=np.float32)

    monkeypatch.setattr(
        cov2x._joint_policy,
        "act_signal_batch",
        lambda observations, *, deterministic=False: _AdvanceBatch(),
    )

    # Advance from phase 30 (index 2): fixed code records action=1 and
    # target phase 40; the old bug recorded action=3 (phase index).
    cov2x.step(_payload(0, 0.0, current_phase=30))
    assert len(cov2x._signal_pending) == 1
    step = cov2x._signal_pending[0]
    assert int(step.action) == 1
    assert int(step.requested_phase) == 40

    cov2x.finish(_payload(1, 5.0, current_phase=30))
    rollout = cov2x.take_collected_rollout()
    arrays = to_signal_joint_arrays(rollout)
    assert set(np.unique(arrays["actions"]).tolist()).issubset({0, 1})


def test_joint_eval_mode_returns_deterministic_actions(tmp_path, monkeypatch):
    torch = pytest.importorskip("torch")
    _set_joint_env(monkeypatch)
    monkeypatch.setenv("COV2X_MODE", "eval")
    cov2x._lane_state = LANE_STATE
    cov2x.initialize(_metadata())
    checkpoint = tmp_path / "joint.pt"
    cov2x.save_checkpoint(checkpoint)
    cov2x.finish({})
    cov2x._initialized = False

    monkeypatch.setenv("COV2X_MODEL_PATH", str(checkpoint))
    cov2x.initialize(_metadata())
    response = cov2x.step(_payload(0, 0.0))
    assert "target_phase" in response["actions"]["signals"]["a"]
    assert "v" in response["actions"]["vehicles"]
    cov2x.finish({})


def test_joint_policy_persists_across_episodes(monkeypatch):
    """initialize() must not reset the joint policy between episodes."""
    _set_joint_env(monkeypatch)
    cov2x._lane_state = LANE_STATE
    cov2x._joint_policy = None
    cov2x._policy = None

    def _run_episode():
        cov2x.initialize(_metadata())
        for step_id in range(7):
            cov2x.step(_payload(step_id, float(step_id) * 5.0, arrived=step_id))
        cov2x.finish(_payload(7, 35.0, arrived=7))
        return cov2x.take_collected_rollout()

    first = cov2x.train_on_rollout(_run_episode())
    second = cov2x.train_on_rollout(_run_episode())
    assert first["policy_generation"] == 1
    assert second["policy_generation"] == 2
    assert second["episode_count"] == 2


def test_joint_resume_loads_once_across_episodes(tmp_path, monkeypatch):
    """--resume must load the checkpoint once, not overwrite learned updates."""
    _set_joint_env(monkeypatch)
    cov2x._lane_state = LANE_STATE
    cov2x._joint_policy = None
    cov2x._policy = None
    cov2x._resume_loaded_path = None

    checkpoint = tmp_path / "joint_resume.pt"
    cov2x.initialize(_metadata())
    assert cov2x._joint_policy is not None
    cov2x.save_checkpoint(checkpoint)
    cov2x.finish({})
    cov2x._initialized = False

    monkeypatch.setenv("COV2X_MODEL_PATH", str(checkpoint))
    cov2x.initialize(_metadata())
    assert cov2x._joint_policy.policy_generation == 0
    for step_id in range(7):
        cov2x.step(_payload(step_id, float(step_id) * 5.0, arrived=step_id))
    cov2x.finish(_payload(7, 35.0, arrived=7))
    diagnostics = cov2x.train_on_rollout(cov2x.take_collected_rollout())
    assert diagnostics["policy_generation"] == 1

    # The second episode must continue from gen 1, not reload the checkpoint.
    cov2x._initialized = False
    cov2x.initialize(_metadata())
    assert cov2x._joint_policy.policy_generation == 1
    cov2x.finish({})


def test_joint_eval_allows_vehicle_off_ablation(tmp_path, monkeypatch):
    """Eval may disable vehicle guidance while signal/cloud stay learned."""
    _set_joint_env(monkeypatch)
    monkeypatch.setenv("COV2X_MODE", "eval")
    monkeypatch.setenv("COV2X_VEHICLE_MODE", "off")
    cov2x._lane_state = LANE_STATE
    cov2x._joint_policy = None
    cov2x._policy = None
    cov2x._resume_loaded_path = None

    cov2x.initialize(_metadata())
    checkpoint = tmp_path / "joint_ablation.pt"
    cov2x.save_checkpoint(checkpoint)
    cov2x.finish({})
    cov2x._initialized = False

    monkeypatch.setenv("COV2X_MODEL_PATH", str(checkpoint))
    cov2x.initialize(_metadata())
    response = cov2x.step(_payload(0, 0.0))
    assert response["actions"]["vehicles"] == {}
    assert "target_phase" in response["actions"]["signals"]["a"]
    cov2x.finish({})


def test_joint_train_requires_vehicle_learned(monkeypatch):
    """Joint training must keep all three ends learning."""
    _set_joint_env(monkeypatch)
    monkeypatch.setenv("COV2X_VEHICLE_MODE", "off")
    cov2x._lane_state = LANE_STATE
    cov2x._joint_policy = None
    cov2x._policy = None
    with pytest.raises(ValueError, match="joint training requires"):
        cov2x.initialize(_metadata())
