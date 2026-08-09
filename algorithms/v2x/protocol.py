# algorithms/v2x/protocol.py
"""协议 2.0 适配器：step 载荷 → 上行草稿；actions → RSI/事件草稿。"""
from __future__ import annotations

from typing import Any, Mapping, Optional

from .derive import derive_phase_schedule
from .messages import MessageDraft


def build_bsm_draft(vehicle_id: str, raw: Mapping[str, Any]) -> MessageDraft:
    location = raw.get("location") or {}
    motion = raw.get("motion") or {}
    position = raw.get("position") or {}
    ns = raw.get("next_signal")
    return MessageDraft(
        "BSM", vehicle_id, "cloud", float(raw.get("_sim_time", 0.0)),
        {"vehicle_id": vehicle_id,
         "type_id": raw.get("type_id"),
         "position": (position.get("x_m"), position.get("y_m")),
         "motion": {"speed_mps": motion.get("speed_mps"),
                    "acceleration_mps2": motion.get("acceleration_mps2"),
                    "angle_deg": motion.get("angle_deg")},
         "location": {"road_id": location.get("road_id"),
                      "lane_id": location.get("lane_id"),
                      "lane_index": location.get("lane_index"),
                      "lane_position_m": location.get("lane_position_m")},
         "route_edges": list(raw.get("route_edges") or []),
         "next_signal": ns,
         "front_gap_m": raw.get("leader_gap_m"),
         "rear_gap_m": raw.get("follower_gap_m"),
         "gap_source": "protocol" if raw.get("leader_gap_m") is not None or
                       raw.get("follower_gap_m") is not None else None},
    )


def build_intent_draft(
    vehicle_id: str, raw: Mapping[str, Any], *,
    sim_time: float = 0.0,
    turn: str, lane_change: Optional[str], arrival: Optional[float],
    turn_conf: float, lane_change_conf: float, arrival_conf: float,
    origin: str,
) -> MessageDraft:
    return MessageDraft(
        "INTENT", vehicle_id, "cloud", sim_time,
        {"vehicle_id": vehicle_id, "turn_intent": turn,
         "lane_change_intent": lane_change, "estimated_arrival_s": arrival,
         "turn_confidence": turn_conf, "lane_change_confidence": lane_change_conf,
         "arrival_confidence": arrival_conf, "intent_origin": origin},
    )


def build_spat_draft(
    intersection_id: str, state: Mapping[str, Any],
    phases_meta: Mapping[str, Mapping[str, Any]], *, sim_time: float,
) -> MessageDraft:
    remaining, nxt, start, status = derive_phase_schedule(state, phases_meta, sim_time)
    lanes = state.get("lanes") or {}
    conn_states = []
    for lane in lanes.values():
        conn_states.extend(lane.get("connection_signal_states") or [])
    return MessageDraft(
        "SPaT", intersection_id, "cloud", sim_time,
        {"intersection_id": intersection_id,
         "current_phase": state.get("current_phase"),
         "stage": state.get("stage"),
         "stage_elapsed": state.get("stage_elapsed"),
         "connection_signal_states": conn_states,
         "remaining_time_s": remaining, "next_stage": nxt,
         "next_stage_start_time": start, "schedule_status": status},
    )


def build_rsm_draft(
    rsu_id: str, objects: list[dict], *, sim_time: float,
) -> MessageDraft:
    return MessageDraft("RSM", rsu_id, "cloud", sim_time,
                        {"rsu_id": rsu_id, "objects": objects})


def build_rsi_draft(
    vehicle_id: str, action: Mapping[str, Any], *,
    sim_time: float = 0.0,
) -> MessageDraft:
    return MessageDraft(
        "RSI", "cloud", vehicle_id, sim_time,
        {"vehicle_id": vehicle_id,
         "target_speed_mps": action.get("target_speed_mps"),
         "target_lane_index": action.get("target_lane_index"),
         "guidance_type": "speed" if "target_speed_mps" in action else "lane"},
    )


def build_signal_control_draft(
    intersection_id: str, action: Any, *, sim_time: float,
    previous_action: Optional[int],
) -> MessageDraft:
    changed = previous_action is None or action != previous_action
    return MessageDraft(
        "SIGNAL_CONTROL", "cloud", intersection_id, sim_time,
        {"intersection_id": intersection_id, "action": action,
         "requested_effective_time": sim_time, "changed": changed,
         "previous_action": previous_action, "reason": None},
    )
