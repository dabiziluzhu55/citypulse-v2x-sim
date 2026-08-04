# algorithms/v2x/collab/snapshot.py
"""不可变模型与 MAP→静态上下文构建（spec §1.4）。"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Mapping

from algorithms.v2x.messages import V2XMessage


@dataclass(frozen=True, slots=True)
class LaneState:
    lane_id: str
    connected_count: int
    observed_count: int
    stopped_count: int
    queue_estimate: float
    arrivals_since_last_snapshot: int


@dataclass(frozen=True, slots=True)
class ApproachState:
    approach_id: str
    incoming_lane_ids: tuple[str, ...]
    lane_states: Mapping[str, LaneState]
    downstream_vehicle_count: int | None
    downstream_queue_estimate: float | None
    turn_intent_counts: Mapping[str, int]
    arrival_etas_s: tuple[float, ...]


@dataclass(frozen=True, slots=True)
class ConnectedVehicleState:
    vehicle_id: str
    lane_id: str | None
    approach_id: str | None
    speed_mps: float
    acceleration_mps2: float | None
    next_signal_intersection_id: str | None
    distance_to_signal_m: float | None
    turn_intent: str | None
    turn_confidence: float
    lane_change_intent: int | None
    estimated_arrival_s: float | None
    bsm_delivered_at: float
    intent_delivered_at: float | None
    source_message_ids: tuple[str, ...]


@dataclass(frozen=True, slots=True)
class EdgeSnapshot:
    intersection_id: str
    sim_time: float
    phase: int | None
    stage: str | None
    stage_elapsed_s: float | None
    remaining_time_s: float | None
    approaches: Mapping[str, ApproachState]
    connected_vehicles: Mapping[str, ConnectedVehicleState]
    last_delivery_at: Mapping[str, float | None]
    source_message_ids: tuple[str, ...]
    source_frame_ids: tuple[str, ...]


@dataclass(frozen=True, slots=True)
class IntersectionStaticContext:
    intersection_id: str
    phase_order: tuple[int, ...]
    lane_to_approach: Mapping[str, str]
    lane_to_edge: Mapping[str, str]
    lane_to_index: Mapping[str, int]
    lane_to_movements: Mapping[str, tuple[str, ...]]
    lane_speed_limit_mps: Mapping[str, float]
    valid_actions: tuple[int, ...]
    phase_to_action: Mapping[int, int | None]
    action_to_movements: Mapping[int, tuple[str, ...]]
    movement_to_lanes: Mapping[str, tuple[str, ...]]
    transition_phases: frozenset[int]
    map_source_message_id: str


def build_static_context(map_message: V2XMessage) -> IntersectionStaticContext:
    """仅由已投递 MAP 构建静态上下文（spec §7.2：不得回读 initialize payload）。"""
    payload = map_message.payload
    intersection_id = str(payload["intersection_id"])
    phase_order = tuple(int(v) for v in payload.get("phase_order") or ())
    if not phase_order:
        raise ValueError(f"MAP {map_message.message_id} has empty phase_order")
    lanes = payload.get("lanes") or {}
    lane_to_approach: dict[str, str] = {}
    lane_to_edge: dict[str, str] = {}
    lane_to_index: dict[str, int] = {}
    lane_to_movements: dict[str, tuple[str, ...]] = {}
    lane_speed_limit_mps: dict[str, float] = {}
    for lane_id, meta in lanes.items():
        lane_id = str(lane_id)
        lane_to_approach[lane_id] = str(
            meta.get("approach_id") or meta.get("edge_id") or lane_id)
        lane_to_edge[lane_id] = str(meta.get("edge_id") or lane_id.rsplit("_", 1)[0])
        lane_to_index[lane_id] = int(meta.get("lane_index", 0))
        movements = tuple(
            str(m) for m in (meta.get("movements") or ()))
        lane_to_movements[lane_id] = movements
        lane_speed_limit_mps[lane_id] = float(
            meta.get("speed_limit_mps", meta.get("max_speed", 13.9)))
    connections_by_id = {
        str(conn.get("connection_id")): conn
        for conn in (payload.get("connections") or [])
        if conn.get("connection_id")
    }
    phases_meta = payload.get("phases") or {}
    action_to_movements: dict[int, tuple[str, ...]] = {}
    movement_to_lanes: dict[str, set[str]] = {}
    for phase_id in phase_order:
        phase = phases_meta.get(str(phase_id), phases_meta.get(phase_id, {}))
        served: set[str] = set()
        for raw_connection_id, _priority in (phase.get("connection_priorities") or {}).items():
            conn = connections_by_id.get(str(raw_connection_id))
            if conn and conn.get("movement"):
                served.add(str(conn["movement"]))
        action_to_movements[phase_id] = tuple(sorted(served))
    for conn in (payload.get("connections") or []):
        movement = conn.get("movement")
        from_lane = conn.get("from_lane")
        if movement and from_lane:
            movement_to_lanes.setdefault(str(movement), set()).add(str(from_lane))
    return IntersectionStaticContext(
        intersection_id=intersection_id,
        phase_order=phase_order,
        lane_to_approach=lane_to_approach,
        lane_to_edge=lane_to_edge,
        lane_to_index=lane_to_index,
        lane_to_movements=lane_to_movements,
        lane_speed_limit_mps=lane_speed_limit_mps,
        valid_actions=phase_order,
        phase_to_action={p: p for p in phase_order},
        action_to_movements=action_to_movements,
        movement_to_lanes={m: tuple(sorted(ls)) for m, ls in movement_to_lanes.items()},
        transition_phases=frozenset(),
        map_source_message_id=map_message.message_id,
    )
