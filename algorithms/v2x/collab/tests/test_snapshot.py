import pytest
from dataclasses import replace

from algorithms.v2x.collab.snapshot import build_static_context
from algorithms.v2x.messages import V2XMessage


def _map_message(intersection_id="i1"):
    return V2XMessage(
        message_type="MAP",
        message_id=f"map-{intersection_id}",
        schema_version="1.0",
        run_id="run1", episode_id="ep1",
        frame_id="ep1:init", sequence_no=1,
        sim_time=0.0, source_id=intersection_id, destination="cloud",
        correlation_id=None,
        payload={
            "intersection_id": intersection_id,
            "phase_order": [1, 2],
            "phases": {
                "1": {"phase_id": 1, "movement": "through",
                      "connection_priorities": {"c0": "protected"}},
                "2": {"phase_id": 2, "movement": "left",
                      "connection_priorities": {"c1": "protected"}},
            },
            "lanes": {
                "A_0": {"lane_id": "A_0", "edge_id": "A", "lane_index": 0,
                        "approach_id": "west", "movements": ("through",),
                        "speed_limit_mps": 13.9},
                "A_1": {"lane_id": "A_1", "edge_id": "A", "lane_index": 1,
                        "approach_id": "west", "movements": ("left",),
                        "speed_limit_mps": 11.1},
                "B_0": {"lane_id": "B_0", "edge_id": "B", "lane_index": 0,
                        "approach_id": "east", "movements": ("through",),
                        "speed_limit_mps": 13.9},
            },
            "connections": [
                {"connection_id": "c0", "from_lane": "A_0", "to_lane": "B_0",
                 "movement": "through"},
                {"connection_id": "c1", "from_lane": "A_1", "to_lane": "B_0",
                 "movement": "left"},
            ],
            "direct_neighbors": [],
        },
    )


def test_static_context_fields():
    ctx = build_static_context(_map_message())
    assert ctx.intersection_id == "i1"
    assert ctx.phase_order == (1, 2)
    assert ctx.valid_actions == (1, 2)
    assert ctx.phase_to_action == {1: 1, 2: 2}
    assert ctx.action_to_movements[1] == ("through",)
    assert ctx.action_to_movements[2] == ("left",)
    assert ctx.movement_to_lanes["through"] == ("A_0",)
    assert ctx.movement_to_lanes["left"] == ("A_1",)
    assert ctx.lane_to_edge["A_0"] == "A"
    assert ctx.lane_to_index["A_0"] == 0
    assert ctx.lane_to_approach["A_0"] == "west"
    assert ctx.lane_to_movements["A_1"] == ("left",)
    assert ctx.lane_speed_limit_mps["A_1"] == 11.1
    assert ctx.transition_phases == frozenset()
    assert ctx.map_source_message_id == "map-i1"


def test_static_context_missing_phase_order_raises():
    msg = _map_message()
    msg = replace(msg, payload=dict(msg.payload, phase_order=[]))
    with pytest.raises(ValueError):
        build_static_context(msg)


def test_static_context_unknown_priority_connection_ignored():
    msg = _map_message()
    payload = dict(msg.payload)
    payload["phases"] = dict(payload["phases"])
    payload["phases"]["1"] = dict(payload["phases"]["1"],
                                  connection_priorities={"c0": "protected",
                                                         "ghost": "protected"})
    ctx = build_static_context(replace(msg, payload=payload))
    assert ctx.action_to_movements[1] == ("through",)
