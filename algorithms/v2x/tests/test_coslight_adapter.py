# algorithms/v2x/tests/test_coslight_adapter.py
import json
import os
from pathlib import Path
import pytest
from algorithms.v2x.adapters.coslight import (
    bridge_initialize, bridge_step, bridge_finish, reset_bridge,
)

# 确定性 fixture：2 个 RSU、1 网联机动车、1 非网联机动车、1 非机动车
INIT = {
    "episode_id": "ep_fix",
    "protocol_version": "2.0",
    "vehicle_types": {
        "official_passenger": {"type_id": "official_passenger",
                               "profile_id": "passenger", "vehicle_class": "passenger"},
        "official_electric_bicycle": {"type_id": "official_electric_bicycle",
                                      "profile_id": "electric_bicycle",
                                      "vehicle_class": "bicycle"},
    },
    "intersections": {
        "i1": {"intersection_id": "i1",
               "phases": {"1": {"green_seconds": 25.0, "yellow_seconds": 3.0,
                                "clearance_seconds": 2.0}},
               "lanes": {"A_0": {"lane_id": "A_0", "edge_id": "A",
                                 "connection_signal_states": []}},
               "connections": [{"from_lane": "A_0", "to_lane": "B_0",
                                "movement": "through"}],
               "direct_neighbors": ["i2"]},
        "i2": {"intersection_id": "i2", "phases": {},
               "lanes": {}, "connections": [], "direct_neighbors": ["i1"]},
    },
}

STEP = {
    "episode_id": "ep_fix", "step_id": 1, "simulation_time": 5.0,
    "intersections": {
        "i1": {"current_phase": 1, "stage": "GREEN", "stage_elapsed": 5.0,
               "lanes": {"A_0": {"connection_signal_states": [
                   {"connection_id": "c0", "movement": "through",
                    "downstream_lane_id": "B_0", "signal_state": "G"}]}}},
        "i2": {"current_phase": None, "stage": "GREEN", "stage_elapsed": 0.0,
               "lanes": {}},
    },
    "vehicles": {
        "car1": {"type_id": "official_passenger",
                 "position": {"x_m": 10.0, "y_m": 10.0},
                 "motion": {"speed_mps": 6.0},
                 "location": {"road_id": "A", "lane_id": "A_0", "lane_index": 0,
                              "lane_position_m": 50.0},
                 "route_edges": ["A", "B"],
                 "next_signal": {"intersection_id": "i1", "distance_m": 60.0,
                                 "state": "G"},
                 "leader_gap_m": None, "follower_gap_m": None},
        "truck1": {"type_id": "official_truck",
                   "position": {"x_m": 20.0, "y_m": 20.0},
                   "motion": {"speed_mps": 5.0},
                   "location": {"road_id": "A", "lane_id": "A_0", "lane_index": 0,
                                "lane_position_m": 40.0},
                   "route_edges": ["A", "B"],
                   "next_signal": {"intersection_id": "i1", "distance_m": 50.0,
                                   "state": "G"},
                   "leader_gap_m": None, "follower_gap_m": None},
        "bike1": {"type_id": "official_electric_bicycle",
                  "position": {"x_m": 30.0, "y_m": 30.0},
                  "motion": {"speed_mps": 3.0},
                  "location": {"road_id": "A", "lane_id": "A_0", "lane_index": 0,
                               "lane_position_m": 30.0},
                  "route_edges": ["A", "B"],
                  "next_signal": {"intersection_id": "i1", "distance_m": 40.0,
                                  "state": "G"},
                  "leader_gap_m": None, "follower_gap_m": None},
    },
}

ACTIONS = {
    "signals": {"i1": {"target_phase": 1}},
    "vehicles": {"car1": {"target_speed_mps": 8.0, "target_lane_index": 0},
                 "bike1": {"target_speed_mps": 3.0, "target_lane_index": 0}},
}


@pytest.fixture()
def log_path(tmp_path: Path, monkeypatch):
    path = tmp_path / "v2x.jsonl"
    monkeypatch.setenv("COSLIGHT_V2X_LOG", str(path))
    monkeypatch.setenv("COSLIGHT_V2X_RUN_ID", "run_fix")
    reset_bridge()
    yield path
    reset_bridge()


def _run_bridge():
    bridge_initialize(INIT)
    bridge_step(STEP, ACTIONS)
    bridge_finish(STEP["simulation_time"])


def test_all_seven_message_types(log_path: Path):
    _run_bridge()
    lines = [json.loads(x) for x in log_path.read_text(encoding="utf-8").splitlines()
             if x.strip()]
    kinds = {rec.get("message", {}).get("message_type")
             for rec in lines if rec.get("record_type") == "message"}
    assert {"BSM", "INTENT", "SPaT", "MAP", "RSM", "RSI",
            "SIGNAL_CONTROL"} <= kinds
    maps = [rec for rec in lines
            if rec.get("message", {}).get("message_type") == "MAP"]
    assert len(maps) == 2  # MAP 数 == RSU 数


def test_rsi_only_to_connected_and_actions_untouched(log_path: Path):
    reset_bridge()
    _run_bridge()
    lines = [json.loads(x) for x in log_path.read_text(encoding="utf-8").splitlines()
             if x.strip()]
    rsi_targets = {rec["message"]["payload"]["vehicle_id"] for rec in lines
                   if rec.get("message", {}).get("message_type") == "RSI"}
    assert rsi_targets == {"car1"}   # truck1(非网联机动车) 与 bike1(非机动车) 无 RSI
    # 原 actions 不被过滤：bridge 不修改 actions（由测试直接断言字典不可变即可）
    assert ACTIONS["vehicles"]["bike1"]["target_speed_mps"] == 3.0


def test_rsm_covers_non_connected(log_path: Path):
    _run_bridge()
    lines = [json.loads(x) for x in log_path.read_text(encoding="utf-8").splitlines()
             if x.strip()]
    rsm = [rec for rec in lines if rec.get("message", {}).get("message_type") == "RSM"]
    assert len(rsm) >= 1
    objects = {obj["object_id"] for rec in rsm
               for obj in rec["message"]["payload"]["objects"]}
    assert "bike1" in objects
    # truck1 是机动车且不在 connected_classes（truck 默认非网联）→ 也进 RSM
    assert "truck1" in objects


def test_summary_delivery_rate_one(log_path: Path):
    _run_bridge()
    lines = [json.loads(x) for x in log_path.read_text(encoding="utf-8").splitlines()
             if x.strip()]
    end = next(rec for rec in lines if rec.get("record_type") == "episode_end")
    assert end["summary"]["delivery"]["delivery_rate"] == 1.0
    assert end["summary"]["delivery"]["pending"] == 0
