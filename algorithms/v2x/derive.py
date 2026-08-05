# algorithms/v2x/derive.py
"""意图推导纯函数：转向/换道/到达/相位时间表（确定性规则 + 置信度证据表）。"""
from __future__ import annotations

from typing import Any, Mapping, Optional

SPEED_EPSILON = 0.1


def _edge_of(lane_id: Optional[str]) -> Optional[str]:
    if not lane_id:
        return None
    return lane_id.rsplit("_", 1)[0]


def derive_turn_intent(
    vehicle: Mapping[str, Any],
    intersections: Mapping[str, Mapping[str, Any]],
) -> tuple[str, float]:
    """返回 (turn_intent, confidence)。"""
    location = vehicle.get("location") or {}
    lane_id = location.get("lane_id")
    route = list(vehicle.get("route_edges") or [])
    ns = vehicle.get("next_signal") or {}
    inter_id = ns.get("intersection_id")
    if not inter_id or inter_id not in intersections or len(route) < 2:
        return "unknown", 0.0
    connections = intersections[inter_id].get("connections") or []
    next_edge = route[1]
    # 1) 完整匹配：from_lane 精确 + to_lane 的 edge 是 route[1]
    for conn in connections:
        if conn.get("from_lane") == lane_id:
            to_edge = _edge_of(conn.get("to_lane"))
            if to_edge == next_edge:
                return conn.get("movement", "unknown"), 1.0
    # 2) edge 级匹配
    edge = _edge_of(lane_id)
    if edge is None:
        return "unknown", 0.0
    for conn in connections:
        if _edge_of(conn.get("from_lane")) == edge:
            to_edge = _edge_of(conn.get("to_lane"))
            if to_edge == next_edge:
                return conn.get("movement", "unknown"), 0.7
    return "unknown", 0.0


def derive_lane_change_intent(
    vehicle: Mapping[str, Any],
    intersections: Mapping[str, Mapping[str, Any]],
) -> tuple[Optional[str], float]:
    """返回 (目标车道 or None, confidence)。

    期望动作取 edge 级（同 edge 首条匹配连接）；若车辆当前车道的 lane 级
    动作与期望不符，则建议同 edge 上动作匹配的最近车道。
    """
    location = vehicle.get("location") or {}
    lane_id = location.get("lane_id")
    ns = vehicle.get("next_signal") or {}
    inter_id = ns.get("intersection_id")
    route = list(vehicle.get("route_edges") or [])
    if not inter_id or inter_id not in intersections or lane_id is None:
        return None, 0.0
    if len(route) < 2:
        return None, 0.0
    edge = _edge_of(lane_id)
    next_edge = route[1]
    connections = intersections[inter_id].get("connections") or []
    expected: Optional[str] = None
    for conn in connections:
        if (_edge_of(conn.get("from_lane")) == edge
                and _edge_of(conn.get("to_lane")) == next_edge):
            expected = conn.get("movement")
            break
    if expected is None:
        return None, 0.0
    current_movement: Optional[str] = None
    for conn in connections:
        if (conn.get("from_lane") == lane_id
                and _edge_of(conn.get("to_lane")) == next_edge):
            current_movement = conn.get("movement")
            break
    if current_movement == expected:
        return None, 0.0
    candidates = {
        conn.get("from_lane")
        for conn in connections
        if conn.get("movement") == expected and _edge_of(conn.get("from_lane")) == edge
    }
    candidates.discard(None)
    if not candidates:
        return None, 0.0
    current_index = int(location.get("lane_index") or 0)

    def lat_index(lane: str) -> int:
        try:
            return int(lane.rsplit("_", 1)[1])
        except (IndexError, ValueError):
            return current_index

    best = min(candidates, key=lambda lane: (abs(lat_index(lane) - current_index), lane))
    if best == lane_id:
        return None, 0.0
    return best, 0.7


def derive_estimated_arrival_s(
    vehicle: Mapping[str, Any],
    speed_epsilon: float = SPEED_EPSILON,
) -> tuple[Optional[float], float]:
    ns = vehicle.get("next_signal") or {}
    distance_m = ns.get("distance_m")
    speed = (vehicle.get("motion") or {}).get("speed_mps")
    if distance_m is None or speed is None or speed <= speed_epsilon:
        return None, 0.0
    return float(distance_m) / float(speed), 1.0


def derive_phase_schedule(
    intersection_state: Mapping[str, Any],
    phases_meta: Mapping[str, Mapping[str, Any]],
    sim_time: float,
) -> tuple[Optional[float], Optional[str], Optional[float], str]:
    """返回 (remaining_time_s, next_stage, next_stage_start_time, schedule_status)。"""
    stage = intersection_state.get("stage")
    elapsed = float(intersection_state.get("stage_elapsed") or 0.0)
    current_phase = intersection_state.get("current_phase")
    meta = phases_meta.get(str(current_phase)) if current_phase is not None else None
    if meta is None:
        return None, None, None, "predicted"
    if stage == "GREEN":
        total = float(meta.get("green_seconds") or 0.0)
        nxt: Optional[str] = "YELLOW"
    elif stage == "YELLOW":
        total = float(meta.get("yellow_seconds") or 0.0)
        nxt = "CLEARANCE"
    elif stage == "CLEARANCE":
        total = float(meta.get("clearance_seconds") or 0.0)
        nxt = "GREEN"
    else:
        return None, None, None, "predicted"
    remaining = max(total - elapsed, 0.0)
    return remaining, nxt, sim_time + remaining, "predicted"
