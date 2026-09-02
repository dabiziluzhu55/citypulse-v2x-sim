import os

import pytest
import torch

from algorithms.cov2x import controller, mvp_runtime


def _payload(vehicle_ids=("v1",), time_loss=1.0):
    vehicles = {
        vehicle_id: {
            "type_id": "passenger",
            "motion": {"speed_mps": 5.0, "allowed_speed_mps": 13.9},
            "location": {"road_id": "in", "lane_id": "in_0", "route_edges": ["in", "out"], "route_index": 0},
            "next_signal": {"intersection_id": "i", "state": "G", "movement": "through", "distance_m": 40.0},
            "traffic": {"time_loss_s": time_loss},
        }
        for vehicle_id in vehicle_ids
    }
    return {
        "episode_id": "runtime-test",
        "period": "off_peak",
        "seed": 24501,
        "duration_seconds": 30,
        "decision_interval": 5,
        "intersections": {
            "i": {
                "phase_order": [0, 1],
                "current_phase": 0,
                "stage": "GREEN",
                "lanes": {"in_0": {"queue_length_m": 10, "vehicle_count": 1, "halting_count": 1, "current_allowed_speed_mps": 13.9}},
                "connections": [{"connection_id": "i:0", "movement": "through", "from_lane": "in_0", "to_lane": "out_0"}],
            }
        },
        "edge_lanes": {
            "in": [{"lane_id": "in_0", "edge_id": "in"}],
            "out": [{"lane_id": "out_0", "edge_id": "out"}],
        },
        "vehicles": vehicles,
        "vehicle_types": {"passenger": {"max_speed_mps": 13.9, "min_gap_m": 2.5}},
    }


def test_mvp_protocol_lifecycle_and_joint_update():
    previous_runtime = os.environ.get("COV2X_RUNTIME")
    previous_mode = os.environ.get("COV2X_MODE")
    os.environ["COV2X_RUNTIME"] = "mvp"; os.environ["COV2X_MODE"] = "train"
    try:
        payload = _payload()
        assert controller.initialize(payload)["candidate_id"] == "cov2x_movement_approach_corridor_v1"
        with torch.no_grad():
            mvp_runtime._vehicle_actor.mean.weight.zero_()
            mvp_runtime._vehicle_actor.mean.bias.fill_(-1.0)
        first = controller.step({**payload, "step_id": 0, "simulation_time": 0})
        assert "target_speed_mps" in first["actions"]["vehicles"]["v1"]
        target = first["actions"]["vehicles"]["v1"]["target_speed_mps"]
        controller.step({
            **payload,
            "step_id": 1,
            "simulation_time": 5,
            "previous_action_results": {"vehicles": {"v1": {"speed_status": "applied", "actual_speed_mps": target}}},
        })
        controller.finish({"simulation_time": 10, "vehicles": {}, "intersections": payload["intersections"]})
        rollout = controller.take_collected_rollout()
        assert rollout.metrics["ledger"]["retired_last_observed"]["v1"] == 1.0
        assert rollout.metrics["advantages_finite"]
        assert rollout.metrics["role_steps"] == {"cloud": 1, "road": 2, "vehicle": 2}
        assert rollout.metrics["vehicle_authority"]["commands_accepted"] == 1
        for transition in rollout.transitions:
            if transition.role == "vehicle":
                assert transition.action == pytest.approx(transition.executed_action)
        update = controller.train_on_rollouts([rollout])
        assert update["updates"] == 1 and update["episodes"] == 1
        assert update["roles"]["cloud"]["updated"] is False
        assert update["roles"]["road"]["updated"] is False
        assert update["roles"]["vehicle"]["samples"] > 0
        assert update["roles"]["critic"]["samples"] == len(rollout.transitions)
    finally:
        if previous_runtime is None:
            os.environ.pop("COV2X_RUNTIME", None)
        else:
            os.environ["COV2X_RUNTIME"] = previous_runtime
        if previous_mode is None:
            os.environ.pop("COV2X_MODE", None)
        else:
            os.environ["COV2X_MODE"] = previous_mode


def test_three_episode_rollouts_queue_without_generation_drift():
    previous_runtime = os.environ.get("COV2X_RUNTIME")
    previous_mode = os.environ.get("COV2X_MODE")
    os.environ["COV2X_RUNTIME"] = "mvp"; os.environ["COV2X_MODE"] = "train"
    try:
        for episode in range(3):
            payload = {**_payload(), "episode_id": f"queue-{episode}"}
            controller.initialize(payload)
            controller.step({**payload, "step_id": 0, "simulation_time": 0})
            controller.finish({"simulation_time": 5, "vehicles": {}, "intersections": payload["intersections"]})
        rollouts = [controller.take_collected_rollout() for _ in range(3)]
        assert [item.episode_id for item in rollouts] == ["queue-0", "queue-1", "queue-2"]
        assert len({item.policy_generation for item in rollouts}) == 1
        assert controller.take_collected_rollout() is None
    finally:
        if previous_runtime is None:
            os.environ.pop("COV2X_RUNTIME", None)
        else:
            os.environ["COV2X_RUNTIME"] = previous_runtime
        if previous_mode is None:
            os.environ.pop("COV2X_MODE", None)
        else:
            os.environ["COV2X_MODE"] = previous_mode
