# algorithms/v2x/tests/test_protocol.py
from algorithms.v2x.protocol import (
    build_bsm_draft, build_intent_draft, build_spat_draft,
    build_rsi_draft, build_signal_control_draft,
)

VEHICLE = {
    "type_id": "official_passenger",
    "position": {"x_m": 512.4, "y_m": 308.1},
    "motion": {"speed_mps": 6.2, "acceleration_mps2": -0.8,
               "angle_deg": 92.0, "allowed_speed_mps": 13.9},
    "location": {"road_id": "-56734", "lane_id": "-56734_0", "lane_index": 0,
                 "lane_position_m": 148.5},
    "route_edges": ["-56734", "-56736"],
    "next_signal": {"intersection_id": "i1", "distance_m": 68.9, "state": "G"},
    "leader_gap_m": 18.4, "follower_gap_m": 11.7,
}

INTERSECTION = {
    "current_phase": 1, "pending_phase": None, "stage": "GREEN",
    "stage_elapsed": 10.0,
    "lanes": {"-56734_0": {"connection_signal_states": [
        {"connection_id": "c0", "movement": "through",
         "downstream_lane_id": "-56736_0", "signal_state": "G"}]}},
}

PHASES_META = {"1": {"green_seconds": 25.0, "yellow_seconds": 3.0,
                     "clearance_seconds": 2.0}}


def test_bsm_draft_fields():
    d = build_bsm_draft("v1", VEHICLE)
    assert d.message_type == "BSM"
    assert d.payload["front_gap_m"] == 18.4
    assert d.payload["gap_source"] == "protocol"


def test_intent_draft_origin():
    d = build_intent_draft("v1", VEHICLE, turn="left", lane_change="-56734_1",
                           arrival=5.0, turn_conf=1.0, lane_change_conf=0.7, arrival_conf=1.0,
                           origin="derived")
    assert d.message_type == "INTENT"
    assert d.payload["intent_origin"] == "derived"
    assert d.payload["turn_intent"] == "left"


def test_spat_draft_schedule():
    d = build_spat_draft("i1", INTERSECTION, PHASES_META, sim_time=60.0)
    assert d.message_type == "SPaT"
    assert d.payload["remaining_time_s"] == 15.0
    assert d.payload["next_stage"] == "YELLOW"
    assert d.payload["schedule_status"] == "predicted"


def test_rsi_draft():
    d = build_rsi_draft("v1", {"target_speed_mps": 8.0, "target_lane_index": 1})
    assert d.message_type == "RSI"
    assert d.payload["target_speed_mps"] == 8.0


def test_signal_control_draft():
    d = build_signal_control_draft("i1", 2, sim_time=60.0, previous_action=1)
    assert d.message_type == "SIGNAL_CONTROL"
    assert d.payload["changed"] is True
    assert d.payload["previous_action"] == 1
    assert d.payload["requested_effective_time"] == 60.0
