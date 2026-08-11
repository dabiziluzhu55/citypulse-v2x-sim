"""Deployment tests for the default (EP12) CoV2X algorithm.

Covers:
- checkpoint alias/contract resolution (SHA256, format_version=2, config dims);
- Protocol 2.0 initialize/step/finish through the ``traffic_control.cov2x``
  package with the CoV2X controller;
- subset-scenario safety: signal actions must only contain payload-controlled
  intersections (not every intersection in the checkpoint phase_orders).
"""

from __future__ import annotations

import os

import pytest

torch = pytest.importorskip("torch")

from traffic_control.cov2x.aliases import (
    default_model_alias_for,
    resolve_model_path,
    validate_alias_combo,
)
from traffic_control.cov2x.contract import (
    EXPECTED_JOINT_MODEL_CONFIG,
    load_joint_contract,
)

EXPECTED_EP12_SHA256 = (
    "0a9cb67dd75c09156de2aedc82caad723eb795790b37db95ea0a47d43dea22f4"
)


def _set_joint_env(monkeypatch) -> None:
    monkeypatch.setenv("COV2X_MODEL_ALIAS", "cov2x_joint_ep12")


def _metadata() -> dict:
    order = [1, 2, 3, 4]
    phases = {
        str(phase): {
            "movement": "through",
            "green_seconds": 25.0,
            "connection_priorities": {f"c{phase}": "protected"},
        }
        for phase in order
    }
    return {
        "protocol_version": "2.0",
        "episode_id": "deploy-test",
        "period": "off_peak",
        "seed": 7,
        "decision_interval": 5.0,
        "minimum_green": 5.0,
        "intersections": {
            "demo_1": {
                "intersection_id": "demo_1",
                "phase_order": order,
                "incoming_lanes": ["demo_1_in_0", "demo_1_in_1", "demo_1_in_2"],
                "outgoing_lanes": ["demo_1_out_0"],
                "lanes": {
                    f"demo_1_in_{i}": {
                        "lane_id": f"demo_1_in_{i}",
                        "edge_id": "demo_1_in",
                        "lane_index": i,
                        "role": "incoming",
                        "length_m": 100.0,
                        "allowed_vehicle_type_ids": ["passenger"],
                        "downstream_lane_ids": ["demo_1_out_0"],
                    }
                    for i in range(3)
                },
                "phases": phases,
                "connections": [
                    {
                        "connection_id": f"c{i + 1}",
                        "from_lane": f"demo_1_in_{i}",
                        "to_lane": "demo_1_out_0",
                        "movement": "through",
                    }
                    for i in range(3)
                ],
            }
        },
        "edge_lanes": {
            "demo_1_in": [
                {
                    "lane_id": f"demo_1_in_{i}",
                    "lane_index": i,
                    "length_m": 100.0,
                    "allowed_vehicle_type_ids": ["passenger"],
                }
                for i in range(3)
            ],
            "demo_1_out": [
                {
                    "lane_id": "demo_1_out_0",
                    "lane_index": 0,
                    "length_m": 80.0,
                    "allowed_vehicle_type_ids": ["passenger"],
                }
            ],
        },
        "vehicle_types": {"passenger": {"length_m": 5.0}},
    }


def _payload(step_id: int, sim_time: float) -> dict:
    return {
        "protocol_version": "2.0",
        "episode_id": "deploy-test",
        "step_id": step_id,
        "simulation_time": sim_time,
        "intersections": {
            "demo_1": {
                "current_phase": 1,
                "stage": "GREEN",
                "stage_elapsed": 8.0,
                "lanes": {
                    f"demo_1_in_{i}": {
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
            "arrived_vehicles": 1,
            "min_expected_vehicles": 10,
            "hard_braking_events": 0,
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
                    "road_id": "demo_1_in",
                    "lane_id": "demo_1_in_1",
                    "lane_index": 1,
                    "lane_position_m": 40.0,
                    "route_edges": ["demo_1_in", "demo_1_out"],
                },
                "next_signal": {
                    "intersection_id": "demo_1",
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


def test_ep12_alias_and_contract() -> None:
    path = resolve_model_path("cov2x_joint_ep12")
    assert path.name == "cov2x_joint_ep12.pt"
    assert default_model_alias_for("xiongan_20") == "cov2x_joint_ep12"
    alias, resolved = validate_alias_combo(
        ["demo_3", "demo_5", "demo_6", "demo_9"], "cov2x_joint_ep12"
    )
    assert alias == "cov2x_joint_ep12"
    assert resolved == path
    checkpoint = torch.load(path, map_location="cpu")
    version, view = load_joint_contract(path, checkpoint)
    assert version == 2
    assert view["model_family"] == "joint"
    assert view["format_version"] == 2
    assert view["episode_count"] == 12
    assert view["sha256"] == EXPECTED_EP12_SHA256
    for key, value in EXPECTED_JOINT_MODEL_CONFIG.items():
        assert int(view["config"][key]) == value


def test_protocol_smoke_through_package_dispatch(monkeypatch) -> None:
    _set_joint_env(monkeypatch)
    import traffic_control.cov2x as cov2x_pkg

    response = cov2x_pkg.initialize(_metadata())
    assert response["ready"] is True
    assert response["episode_id"] == "deploy-test"
    for step_id, sim_time in [(0, 0.0), (1, 5.0), (2, 10.0), (3, 15.0)]:
        decision = cov2x_pkg.step(_payload(step_id, sim_time))
        assert decision["protocol_version"] == "2.0"
        assert set(decision["actions"]) == {"signals", "vehicles"}
        assert set(decision["actions"]["signals"]) == {"demo_1"}
    cov2x_pkg.finish(
        {
            "protocol_version": "2.0",
            "episode_id": "deploy-test",
            "intersections": _metadata()["intersections"],
        }
    )
    assert os.environ["COV2X_MODEL_PATH"].endswith("cov2x_joint_ep12.pt")


def test_demo4_offpeak_phase_order_never_emits_phase4(monkeypatch) -> None:
    """demo_4 off_peak 程序只有 (1,2,3)，任何 step 都不得输出相位 4。"""
    _set_joint_env(monkeypatch)
    import traffic_control.cov2x as cov2x_pkg

    meta = _metadata()
    meta["episode_id"] = "deploy-demo4-offpeak"
    meta["intersections"] = {"demo_4": meta["intersections"].pop("demo_1")}
    meta["intersections"]["demo_4"]["intersection_id"] = "demo_4"
    meta["intersections"]["demo_4"]["phase_order"] = [1, 2, 3]
    meta["edge_lanes"] = {
        "demo_1_in": meta["edge_lanes"]["demo_1_in"],
        "demo_1_out": meta["edge_lanes"]["demo_1_out"],
    }
    assert cov2x_pkg.initialize(meta)["ready"] is True

    def demo4_payload(step_id: int, sim_time: float) -> dict:
        raw = _payload(step_id, sim_time)
        raw["intersections"] = {
            "demo_4": raw["intersections"].pop("demo_1")
        }
        return raw

    for step_id, sim_time in [(0, 0.0), (3, 15.0)]:
        decision = cov2x_pkg.step(demo4_payload(step_id, sim_time))
        target = decision["actions"]["signals"]["demo_4"]["target_phase"]
        assert target in (1, 2, 3), f"demo_4 off_peak emitted illegal phase {target}"
    cov2x_pkg.finish(
        {
            "protocol_version": "2.0",
            "episode_id": "deploy-demo4-offpeak",
            "intersections": meta["intersections"],
        }
    )
