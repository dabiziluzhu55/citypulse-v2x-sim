# algorithms/v2x/tests/test_hub.py
import pytest
from algorithms.v2x.config import V2XConfig
from algorithms.v2x.hub import V2XHub, FrameContext
from algorithms.v2x.messages import MessageDraft


def _hub(**kw):
    return V2XHub(config=V2XConfig(**kw))


def _init(hub, sim_time=0.0):
    payload = {
        "episode_id": "ep1",
        "vehicle_types": {},
        "intersections": {},
    }
    return hub.ingest_initialize(payload, run_id="run1", episode_id="ep1",
                                 initial_sim_time=sim_time)


def test_lifecycle_errors():
    hub = _hub()
    with pytest.raises(ValueError):
        hub.ingest_step({"simulation_time": 5.0})
    _init(hub)
    with pytest.raises(ValueError):
        hub.ingest_initialize({}, run_id="r", episode_id="ep2")
    hub.finish_episode(60.0)
    with pytest.raises(ValueError):
        hub.ingest_step({"simulation_time": 65.0})
    with pytest.raises(ValueError):
        hub.ingest_actions({"signals": {}, "vehicles": {}},
                           frame=FrameContext("ep1", "ep1:step:000001", 5.0, ()))
    # 新 episode 合法
    hub.ingest_initialize({}, run_id="run1", episode_id="ep2")


def test_frame_ids_and_sequence_per_stream():
    hub = _hub()
    _init(hub)
    hub.publish(MessageDraft("BSM", "v1", "cloud", 5.0,
                             {"vehicle_id": "v1", "type_id": "t", "position": (0, 0),
                              "motion": {}, "location": {"lane_id": "L_0"},
                              "route_edges": ["a", "b"], "next_signal": None,
                              "front_gap_m": None, "rear_gap_m": None,
                              "gap_source": None}), frame_id="ep1:step:000001")
    hub.publish(MessageDraft("INTENT", "v1", "cloud", 5.0,
                             {"vehicle_id": "v1", "turn_intent": "through",
                              "lane_change_intent": None, "estimated_arrival_s": 3.0,
                              "turn_confidence": 1.0, "lane_change_confidence": 0.0,
                              "arrival_confidence": 1.0, "intent_origin": "derived"}),
                frame_id="ep1:step:000001")
    assert hub.sequence_no("ep1", "v1", "BSM") == 1
    assert hub.sequence_no("ep1", "v1", "INTENT") == 1
    hub.publish(MessageDraft("BSM", "v1", "cloud", 10.0,
                             {"vehicle_id": "v1", "type_id": "t", "position": (0, 0),
                              "motion": {}, "location": {"lane_id": "L_0"},
                              "route_edges": ["a", "b"], "next_signal": None,
                              "front_gap_m": None, "rear_gap_m": None,
                              "gap_source": None}), frame_id="ep1:step:000002")
    assert hub.sequence_no("ep1", "v1", "BSM") == 2


def test_advance_delivers_with_latency():
    hub = _hub()
    _init(hub)
    delivered = []
    hub.subscribe("BSM", lambda msg: delivered.append(msg))
    hub.publish(MessageDraft("BSM", "v1", "cloud", 10.0,
                             {"vehicle_id": "v1", "type_id": "t", "position": (0, 0),
                              "motion": {}, "location": {"lane_id": "L_0"},
                              "route_edges": ["a", "b"], "next_signal": None,
                              "front_gap_m": None, "rear_gap_m": None,
                              "gap_source": None}), frame_id="ep1:step:000001")
    assert delivered == []          # 未到期
    hub.advance(10.02 + 1e-9)
    assert len(delivered) == 1
    assert delivered[0].sequence_no == 1
    assert delivered[0].message_id.startswith("ep1|") is False  # uuid 字符串


def test_scheduler_first_send_then_interval():
    hub = _hub(bsm_interval_s=10.0)
    _init(hub)
    drafts = _bsm_draft("v1", 5.0)
    assert hub.should_send("ep1", "v1", "BSM", 5.0) is True
    hub.mark_sent("ep1", "v1", "BSM", 5.0)
    assert hub.should_send("ep1", "v1", "BSM", 9.0) is False
    assert hub.should_send("ep1", "v1", "BSM", 15.0) is True


def test_time_regression_raises():
    hub = _hub()
    _init(hub)
    hub.advance(10.0)
    with pytest.raises(ValueError, match="regression"):
        hub.advance(9.0)


def test_ingest_actions_frame_consumed_once():
    hub = _hub()
    _init(hub)
    frame = hub.ingest_step({"simulation_time": 5.0, "episode_id": "ep1",
                             "intersections": {}, "vehicles": {}})
    hub.ingest_actions({"signals": {}, "vehicles": {}}, frame=frame)
    with pytest.raises(ValueError, match="consumed"):
        hub.ingest_actions({"signals": {}, "vehicles": {}}, frame=frame)


def _bsm_draft(vid, sim_time):
    return MessageDraft("BSM", vid, "cloud", sim_time,
                        {"vehicle_id": vid, "type_id": "t", "position": (0, 0),
                         "motion": {}, "location": {"lane_id": "L_0"},
                         "route_edges": ["a", "b"], "next_signal": None,
                         "front_gap_m": None, "rear_gap_m": None,
                         "gap_source": None})


def test_finish_drain_pending_false_drops_episode_ended():
    hub = _hub()
    _init(hub)
    hub.publish(_bsm_draft("v1", 10.0), frame_id="ep1:step:000001")
    hub.finish_episode(10.0, drain_pending=False)
    drops = [r for r in hub.delivery_records if r["status"] == "dropped"]
    assert len(drops) == 1
    assert drops[0]["drop_reason"] == "episode_ended"


def test_finish_drain_pending_true_delivers_all():
    hub = _hub()
    _init(hub)
    hub.publish(_bsm_draft("v1", 10.0), frame_id="ep1:step:000001")
    hub.finish_episode(10.0, drain_pending=True)
    assert sum(1 for r in hub.delivery_records if r["status"] == "delivered") == 1
    assert sum(1 for r in hub.delivery_records if r["status"] == "dropped") == 0


def test_map_payload_carries_phase_order():
    hub = _hub()
    payload = {
        "episode_id": "ep1",
        "vehicle_types": {},
        "intersections": {
            "i1": {"phase_order": [1, 2, 3],
                   "phases": {}, "lanes": {}, "connections": [], "direct_neighbors": []},
        },
    }
    frame = hub.ingest_initialize(
        payload, run_id="run1", episode_id="ep1", initial_sim_time=0.0)
    # MAP 走延迟队列，手动 advance 到投递时刻
    hub.advance(1.0)
    maps = [rec for rec in hub.sent_records if rec["message_type"] == "MAP"]
    assert len(maps) == 1
    # sent_records 只有元数据；通过订阅回调读取 payload
    seen = []

    def _on_map(message):
        seen.append(message)

    hub2 = _hub()
    hub2.subscribe("MAP", _on_map)
    frame2 = hub2.ingest_initialize(
        payload, run_id="run1", episode_id="ep1", initial_sim_time=0.0)
    hub2.advance(1.0)
    assert len(seen) == 1
    assert seen[0].payload["phase_order"] == [1, 2, 3]
