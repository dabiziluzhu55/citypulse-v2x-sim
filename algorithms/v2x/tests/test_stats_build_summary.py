# algorithms/v2x/tests/test_stats_build_summary.py
from algorithms.v2x.hub import V2XHub
from algorithms.v2x.config import V2XConfig
from algorithms.v2x.messages import MessageDraft
from algorithms.v2x.stats import build_summary


def _draft(vid="v1", mtype="BSM", sim_time=5.0):
    if mtype == "BSM":
        return MessageDraft("BSM", vid, "cloud", sim_time,
                            {"vehicle_id": vid, "type_id": "t", "position": (0, 0),
                             "motion": {}, "location": {"lane_id": "L_0"},
                             "route_edges": ["a", "b"], "next_signal": None,
                             "front_gap_m": None, "rear_gap_m": None,
                             "gap_source": None})
    return MessageDraft("RSI", "cloud", vid, sim_time,
                        {"vehicle_id": vid, "target_speed_mps": 8.0,
                         "target_lane_index": None, "guidance_type": "speed"})


def test_summary_delivery_rate_null_and_latency():
    hub = V2XHub(config=V2XConfig())
    hub.ingest_initialize({"episode_id": "e", "vehicle_types": {},
                           "intersections": {}}, run_id="r", episode_id="e")
    hub.publish(_draft("v1", "BSM", 5.0), frame_id="e:step:000001")
    hub.finish_episode(5.0, drain_pending=False)
    s = build_summary(hub)
    # 默认 finish 丢弃 → delivered=0
    assert s["delivery"]["delivered"] == 0
    assert s["delivery"]["delivery_rate"] == 0.0


def test_summary_drain_true_rate_one():
    hub = V2XHub(config=V2XConfig())
    hub.ingest_initialize({"episode_id": "e", "vehicle_types": {},
                           "intersections": {}}, run_id="r", episode_id="e")
    hub.publish(_draft("v1", "BSM", 5.0), frame_id="e:step:000001")
    hub.finish_episode(5.0, drain_pending=True)
    s = build_summary(hub)
    assert s["delivery"]["delivered"] == 1
    assert s["delivery"]["delivery_rate"] == 1.0
    assert s["delivery"]["latency_ms"]["mean"] == 20.0


def test_summary_structured_fields_present():
    hub = V2XHub(config=V2XConfig())
    hub.ingest_initialize({"episode_id": "e", "vehicle_types": {},
                           "intersections": {}}, run_id="r", episode_id="e")
    hub.finish_episode(0.0, drain_pending=True)
    s = build_summary(hub)
    assert s["rsm_coverage"]["rate"] is None
    assert s["rsm_coverage"]["defined"] is False
    assert s["rsi_funnel"]["requested"] == 0
    assert s["signal_control"]["generated"] == 0
    assert s["penetration"]["rate"] is None
