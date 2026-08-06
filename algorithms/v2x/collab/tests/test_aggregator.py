from algorithms.v2x.collab.aggregator import EdgeAggregator
from algorithms.v2x.collab.snapshot import build_static_context
from algorithms.v2x.messages import V2XMessage


def _msg(message_type, source_id, payload, frame_id="ep1:step:000001",
         sim_time=5.0, message_id=None):
    return V2XMessage(
        message_type=message_type,
        message_id=message_id or f"{message_type}-{source_id}-{sim_time}",
        schema_version="1.0", run_id="run1", episode_id="ep1",
        frame_id=frame_id, sequence_no=1, sim_time=sim_time,
        source_id=source_id, destination="cloud", correlation_id=None,
        payload=payload,
    )


MAP = _msg("MAP", "i1", {
    "intersection_id": "i1", "phase_order": [1],
    "phases": {"1": {"phase_id": 1, "connection_priorities": {"c0": "protected"}}},
    "lanes": {
        "A_0": {"lane_id": "A_0", "edge_id": "A", "lane_index": 0,
                "approach_id": "west", "movements": ("through",),
                "speed_limit_mps": 13.9},
        "B_0": {"lane_id": "B_0", "edge_id": "B", "lane_index": 0,
                "approach_id": "east", "movements": ("through",),
                "speed_limit_mps": 13.9},
    },
    "connections": [{"connection_id": "c0", "from_lane": "A_0", "to_lane": "B_0",
                     "movement": "through"}],
    "direct_neighbors": [],
}, frame_id="ep1:init", sim_time=0.0, message_id="map-i1")


def _spat(stage_elapsed=2.0, remaining=20.0, message_id="spat-i1-5"):
    return _msg("SPaT", "i1", {
        "intersection_id": "i1", "current_phase": 1, "stage": "GREEN",
        "stage_elapsed": stage_elapsed, "connection_signal_states": [],
        "remaining_time_s": remaining, "next_stage": "YELLOW",
        "next_stage_start_time": 25.0, "schedule_status": "predicted",
    }, message_id=message_id)


def _bsm(vid, lane="A_0", speed=6.0, ns="i1", distance=50.0, sim_time=5.0,
         message_id=None):
    return _msg("BSM", vid, {
        "vehicle_id": vid, "type_id": "passenger", "position": (0.0, 0.0),
        "motion": {"speed_mps": speed, "acceleration_mps2": 0.0},
        "location": {"road_id": "A", "lane_id": lane, "lane_index": 0,
                     "lane_position_m": 10.0},
        "route_edges": ["A", "B"],
        "next_signal": {"intersection_id": ns, "distance_m": distance,
                        "state": "G"},
        "front_gap_m": None, "rear_gap_m": None, "gap_source": None,
    }, sim_time=sim_time, message_id=message_id or f"bsm-{vid}-{sim_time}")


def _intent(vid, turn="through", eta=8.0, sim_time=5.0, message_id=None):
    return _msg("INTENT", vid, {
        "vehicle_id": vid, "turn_intent": turn, "lane_change_intent": None,
        "estimated_arrival_s": eta, "turn_confidence": 1.0,
        "lane_change_confidence": 0.0, "arrival_confidence": 1.0,
        "intent_origin": "derived",
    }, sim_time=sim_time, message_id=message_id or f"intent-{vid}-{sim_time}")


def _rsm(objects, message_id="rsm-i1-5"):
    return _msg("RSM", "i1", {"rsu_id": "i1", "objects": objects},
                message_id=message_id)


def _make_aggregator():
    agg = EdgeAggregator(managed_ids=("i1",))
    agg.on_message(MAP)
    return agg


def test_bsm_migrates_between_intersections():
    agg = EdgeAggregator(managed_ids=("i1", "i2"))
    agg.on_message(MAP)
    agg.on_message(_bsm("car1", ns="i1"))
    snap = agg.snapshot("i1", now=5.0)
    assert "car1" in snap.connected_vehicles
    # next_signal 迁移到 i2（i2 无 MAP，不建快照，但车辆从 i1 移除）
    agg.on_message(_bsm("car1", ns="i2", message_id="bsm-car1-6"))
    snap2 = agg.snapshot("i1", now=6.0)
    assert "car1" not in snap2.connected_vehicles


def test_snapshot_aggregates_lane_state_and_arrivals():
    agg = _make_aggregator()
    agg.on_message(_bsm("car1", speed=0.1))      # 停车网联车
    agg.on_message(_bsm("car2", speed=6.0))
    agg.on_message(_intent("car1", eta=5.0))
    agg.on_message(_spat())
    agg.on_message(_rsm([{"object_id": "truck1", "object_class": "truck",
                          "position": (1.0, 1.0), "speed_mps": 0.0,
                          "lane_id": "A_0", "confidence": 1.0}]))
    snap = agg.snapshot("i1", now=5.0)
    lane = snap.approaches["west"].lane_states["A_0"]
    assert lane.connected_count == 2
    assert lane.observed_count == 1
    assert lane.stopped_count == 2      # car1 + truck1
    assert lane.queue_estimate == 2.0
    assert snap.phase == 1
    assert snap.stage == "GREEN"
    assert snap.stage_elapsed_s == 2.0
    assert snap.remaining_time_s == 20.0
    car1 = snap.connected_vehicles["car1"]
    assert car1.turn_intent == "through"
    assert car1.turn_confidence == 1.0
    assert car1.estimated_arrival_s == 5.0
    assert car1.bsm_delivered_at == 5.0
    assert car1.intent_delivered_at == 5.0
    assert snap.last_delivery_at["SPaT"] == 5.0
    agg.after_snapshot("i1")
    # 第二帧：car1/car2 已存在 → arrivals=0；新增 car3 → arrivals=1
    agg.on_message(_bsm("car3", speed=6.0))
    snap2 = agg.snapshot("i1", now=10.0)
    assert snap2.approaches["west"].lane_states["A_0"].arrivals_since_last_snapshot == 1


def test_non_managed_message_ignored():
    agg = EdgeAggregator(managed_ids=("i1",))
    agg.on_message(_msg("MAP", "i2", {
        "intersection_id": "i2", "phase_order": [1], "phases": {},
        "lanes": {}, "connections": [], "direct_neighbors": []},
        frame_id="ep1:init", sim_time=0.0, message_id="map-i2"))
    agg.on_message(_bsm("car1", ns="i2", message_id="bsm-car1-7"))
    assert agg.snapshot("i2", now=5.0) is None
    assert agg.snapshot("i1", now=5.0) is None  # i1 无 MAP 也未收到
