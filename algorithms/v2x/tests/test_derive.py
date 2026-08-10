# algorithms/v2x/tests/test_derive.py
from algorithms.v2x.derive import (
    derive_turn_intent, derive_lane_change_intent,
    derive_estimated_arrival_s, derive_phase_schedule,
)

# 两条边 e0 -> e1，路口 i1 有连接 -56734_0 -> -56736_0 (through)
INTERSECTIONS = {
    "i1": {
        "connections": [
            {"from_lane": "-56734_0", "to_lane": "-56736_0", "movement": "through"},
            {"from_lane": "-56734_1", "to_lane": "-56736_0", "movement": "left"},
        ],
        "lanes": {"-56734_0": {}, "-56734_1": {}},
    }
}

def _vehicle(lane="-56734_0", lane_index=0, speed=6.0, dist=68.9):
    return {
        "location": {"lane_id": lane, "lane_index": lane_index},
        "motion": {"speed_mps": speed},
        "route_edges": ["-56734", "-56736"],
        "next_signal": {"intersection_id": "i1", "distance_m": dist},
    }


def test_turn_full_match():
    intent, conf = derive_turn_intent(_vehicle(), INTERSECTIONS)
    assert intent == "through"
    assert conf == 1.0


def test_turn_edge_level_match():
    v = _vehicle(lane="-56734_2")  # 连接表里没有该 from_lane
    intent, conf = derive_turn_intent(v, INTERSECTIONS)
    # 同 edge 的 through 连接，edge 级匹配
    assert intent == "through"
    assert conf == 0.7


def test_turn_unknown():
    v = _vehicle(lane="other_0")
    v["next_signal"] = {"intersection_id": "i1", "distance_m": 10.0}
    intent, conf = derive_turn_intent(v, INTERSECTIONS)
    assert intent == "unknown"
    assert conf == 0.0


def test_lane_change_suggests_allowed_lane():
    v = _vehicle(lane="-56734_1", lane_index=1)  # left 车道但路线是 through
    target, conf = derive_lane_change_intent(v, INTERSECTIONS)
    assert target == "-56734_0"   # 最近的可直行车道
    assert conf > 0.0


def test_lane_change_none_when_already_correct():
    v = _vehicle(lane="-56734_0", lane_index=0)
    target, conf = derive_lane_change_intent(v, INTERSECTIONS)
    assert target is None


def test_arrival():
    assert derive_estimated_arrival_s(_vehicle(speed=6.0, dist=68.9)) == (68.9 / 6.0, 1.0)
    assert derive_estimated_arrival_s(_vehicle(speed=0.0))[0] is None
    assert derive_estimated_arrival_s(_vehicle(dist=None))[0] is None


def test_phase_schedule_green():
    meta = {"1": {"green_seconds": 25.0, "yellow_seconds": 3.0, "clearance_seconds": 2.0}}
    state = {"current_phase": 1, "stage": "GREEN", "stage_elapsed": 10.0}
    remaining, nxt, start, status = derive_phase_schedule(state, meta, sim_time=60.0)
    assert remaining == 15.0
    assert nxt == "YELLOW"
    assert start == 75.0
    assert status == "predicted"


def test_phase_schedule_unknown_phase():
    meta = {"1": {"green_seconds": 25.0}}
    state = {"current_phase": 99, "stage": "GREEN", "stage_elapsed": 1.0}
    remaining, nxt, start, status = derive_phase_schedule(state, meta, sim_time=60.0)
    assert remaining is None and nxt is None and start is None
