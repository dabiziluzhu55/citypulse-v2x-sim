"""
Movement-based Max Pressure信号控制

对每个connection (movement) 估计排队x(l,m)，计算
    w(l,m) = x(l,m) - downstream_queue(m)
    phase_pressure = Σ service_weight(l,m) × w(l,m)

-movement级上游队列x(l,m)与下游反压downstream_queue(m)的差作为压力；
-相位压力为各service movement压力之和；
-保留负压力、选择总压力最大的相位，平局保持当前相位
"""

from __future__ import annotations

import logging
from typing import Any, Optional

logger = logging.getLogger(__name__)

DEFAULT_PERMISSIVE_WEIGHT = 0.5
DEFAULT_PROTECTED_WEIGHT = 1.0
HALTING_SPEED_MPS = 0.1
_TRANSITION_STAGES = frozenset({"YELLOW", "CLEARANCE"})


class MaxPressureController:

    def __init__(self, metadata: dict[str, Any]) -> None:
        self._metadata = metadata
        self._episode_id: str = str(metadata.get("episode_id", ""))
        self._decision_interval: float = float(metadata.get("decision_interval", 5.0))
        self._permissive_weight = float(
            metadata.get("max_pressure_permissive_weight", DEFAULT_PERMISSIVE_WEIGHT)
        )
        self._protected_weight = float(
            metadata.get("max_pressure_protected_weight", DEFAULT_PROTECTED_WEIGHT)
        )
        self._ix: dict[str, _IntersectionIndex] = {}
        for iid, i_meta in metadata.get("intersections", {}).items():
            self._ix[iid] = _build_intersection_index(
                i_meta,
                protected_weight=self._protected_weight,
                permissive_weight=self._permissive_weight,
            )
        logger.info(
            "MaxPressureController 初始化完成: episode=%s 路口数=%d 决策间隔=%.1fs "
            "protected_weight=%.2f permissive_weight=%.2f",
            self._episode_id,
            len(self._ix),
            self._decision_interval,
            self._protected_weight,
            self._permissive_weight,
        )

    def compute_actions(
        self,
        observation: dict[str, Any],
        *,
        tie_keep_current: bool = True,
    ) -> dict[str, Optional[int]]:
        actions: dict[str, Optional[int]] = {}
        obs_intersections: dict[str, Any] = observation.get("intersections", {})
        vehicles: dict[str, Any] = observation.get("vehicles", {})

        for iid, ix in self._ix.items():
            i_obs = obs_intersections.get(iid)
            if i_obs is None:
                logger.warning("路口 %s 未出现在 /step 观测中，跳过", iid)
                continue
            actions[iid] = _decide_phase(
                ix,
                i_obs,
                vehicles,
                tie_keep_current=tie_keep_current,
            )
        return actions


class _MovementIndex:
    __slots__ = (
        "connection_id",
        "from_lane",
        "to_lane",
        "from_edge",
        "to_edge",
        "movement",
        "priority",
        "service_weight",
    )

    def __init__(
        self,
        *,
        connection_id: str,
        from_lane: str,
        to_lane: str,
        from_edge: str | None,
        to_edge: str | None,
        movement: str | None,
        priority: str,
        service_weight: float,
    ) -> None:
        self.connection_id = connection_id
        self.from_lane = from_lane
        self.to_lane = to_lane
        self.from_edge = from_edge
        self.to_edge = to_edge
        self.movement = movement
        self.priority = priority
        self.service_weight = service_weight


class _IntersectionIndex:
    __slots__ = (
        "intersection_id",
        "phase_order",
        "movements",
        "phase_movements",
        "lane_movements",
        "downstream_by_to_lane",
        "lane_edges",
        "incoming_lanes",
        "outgoing_lanes",
        "all_lanes",
    )

    def __init__(self) -> None:
        self.intersection_id: str = ""
        self.phase_order: list[int] = []
        self.movements: dict[str, _MovementIndex] = {}
        self.phase_movements: dict[int, list[str]] = {}
        self.lane_movements: dict[str, list[str]] = {}
        self.downstream_by_to_lane: dict[str, list[str]] = {}
        self.lane_edges: dict[str, str] = {}
        self.incoming_lanes: list[str] = []
        self.outgoing_lanes: list[str] = []
        self.all_lanes: set[str] = set()


def _build_intersection_index(
    i_meta: dict[str, Any],
    *,
    protected_weight: float,
    permissive_weight: float,
) -> _IntersectionIndex:
    ix = _IntersectionIndex()
    ix.intersection_id = str(i_meta["intersection_id"])
    ix.phase_order = [int(p) for p in i_meta.get("phase_order", [])]
    ix.incoming_lanes = [str(lane) for lane in i_meta.get("incoming_lanes", [])]
    ix.outgoing_lanes = [str(lane) for lane in i_meta.get("outgoing_lanes", [])]

    lane_meta = i_meta.get("lanes", {})
    if isinstance(lane_meta, dict):
        for lane_id, info in lane_meta.items():
            lane_key = str(lane_id)
            ix.all_lanes.add(lane_key)
            if isinstance(info, dict):
                edge_id = info.get("edge_id")
                if edge_id is not None:
                    ix.lane_edges[lane_key] = str(edge_id)

    connection_map: dict[str, dict[str, Any]] = {}
    for conn in i_meta.get("connections", []):
        if not isinstance(conn, dict):
            continue
        conn_id = str(conn["connection_id"])
        connection_map[conn_id] = conn
        from_lane = str(conn["from_lane"])
        to_lane = str(conn["to_lane"])
        ix.all_lanes.update({from_lane, to_lane})

        from_edge = _lane_edge_id(from_lane, ix.lane_edges)
        to_edge = _lane_edge_id(to_lane, ix.lane_edges)
        if conn.get("from_edge") is not None:
            from_edge = str(conn["from_edge"])
        if conn.get("to_edge") is not None:
            to_edge = str(conn["to_edge"])

        ix.movements[conn_id] = _MovementIndex(
            connection_id=conn_id,
            from_lane=from_lane,
            to_lane=to_lane,
            from_edge=from_edge,
            to_edge=to_edge,
            movement=str(conn["movement"]) if conn.get("movement") is not None else None,
            priority="protected",
            service_weight=protected_weight,
        )
        ix.lane_movements.setdefault(from_lane, []).append(conn_id)
        ix.downstream_by_to_lane.setdefault(to_lane, [])

    for to_lane, downstream_ids in list(ix.downstream_by_to_lane.items()):
        for conn_id, movement in ix.movements.items():
            if movement.from_lane == to_lane:
                downstream_ids.append(conn_id)

    phases = i_meta.get("phases", {})
    for pid in ix.phase_order:
        phase_info = phases.get(str(pid)) or phases.get(pid)
        conn_ids: list[str] = []
        if isinstance(phase_info, dict):
            priorities = phase_info.get("connection_priorities", {})
            for conn_id, priority in priorities.items():
                conn_key = str(conn_id)
                movement = ix.movements.get(conn_key)
                if movement is None:
                    logger.warning(
                        "路口 %s 相位 %d 引用了不存在的 connection %s",
                        ix.intersection_id,
                        pid,
                        conn_key,
                    )
                    continue
                normalized = str(priority).lower()
                movement.priority = normalized
                if normalized == "permissive":
                    movement.service_weight = permissive_weight
                else:
                    # 等饱和流率：protected及未知类型均使用protected权重
                    movement.service_weight = protected_weight
                conn_ids.append(conn_key)
        ix.phase_movements[pid] = conn_ids

    return ix


def _decide_phase(
    ix: _IntersectionIndex,
    i_obs: dict[str, Any],
    vehicles: dict[str, Any],
    *,
    tie_keep_current: bool,
) -> Optional[int]:
    stage = str(i_obs.get("stage", "GREEN"))
    pending_phase = i_obs.get("pending_phase")
    current_phase = int(
        i_obs.get("current_phase", ix.phase_order[0] if ix.phase_order else 0)
    )

    if stage in _TRANSITION_STAGES or pending_phase is not None:
        if pending_phase is not None:
            return int(pending_phase)
        return current_phase

    if not ix.phase_order:
        logger.warning("路口 %s 缺少 phase_order，保持当前相位 %d", ix.intersection_id, current_phase)
        return current_phase

    lanes_obs: dict[str, Any] = i_obs.get("lanes", {})
    if not lanes_obs:
        logger.warning(
            "路口 %s 缺少 lanes 观测，保持当前相位 %d",
            ix.intersection_id,
            current_phase,
        )
        return current_phase
    # 估计各connection的排队
    relevant_vehicles = _intersection_vehicles(ix, vehicles)
    movement_queues = _estimate_movement_queues(ix, lanes_obs, relevant_vehicles)

    best_phase: Optional[int] = None
    best_pressure = -float("inf")
    has_valid_phase = False
    # 计算各相位的压力，选择压力最大的相位
    for pid in ix.phase_order:
        conn_ids = ix.phase_movements.get(pid, [])
        if not conn_ids:
            continue
        pressure = _compute_phase_pressure(ix, conn_ids, movement_queues, lanes_obs)
        has_valid_phase = True
        if pressure > best_pressure + 1e-9:
            best_pressure = pressure
            best_phase = pid
        elif tie_keep_current and abs(pressure - best_pressure) < 1e-9 and pid == current_phase:
            best_phase = pid

    if not has_valid_phase:
        logger.warning(
            "路口 %s 无有效 connection/相位映射，保持当前相位 %d",
            ix.intersection_id,
            current_phase,
        )
        return current_phase

    if best_phase is None:
        logger.warning(
            "路口 %s 无法计算有效压力，保持当前相位 %d",
            ix.intersection_id,
            current_phase,
        )
        return current_phase

    return best_phase


def _compute_phase_pressure(
    ix: _IntersectionIndex,
    conn_ids: list[str],
    movement_queues: dict[str, float],
    lanes_obs: dict[str, Any],
) -> float:
    total = 0.0
    for conn_id in conn_ids:
        movement = ix.movements.get(conn_id)
        if movement is None:
            continue
        upstream = movement_queues.get(conn_id, 0.0)
        downstream = _downstream_queue(ix, movement, movement_queues, lanes_obs)
        weight = movement.service_weight
        total += weight * (upstream - downstream)
    return total


def _downstream_queue(
    ix: _IntersectionIndex,
    movement: _MovementIndex,
    movement_queues: dict[str, float],
    lanes_obs: dict[str, Any],
) -> float:
    downstream_ids = ix.downstream_by_to_lane.get(movement.to_lane, [])
    if downstream_ids:
        weights = _movement_turn_weights(
            ix,
            movement.to_lane,
            downstream_ids,
            lanes_obs,
            movement_queues,
        )
        weighted_sum = 0.0
        weight_total = 0.0
        for conn_id in downstream_ids:
            weight = weights.get(conn_id, 0.0)
            if weight <= 0.0:
                continue
            weighted_sum += weight * movement_queues.get(conn_id, 0.0)
            weight_total += weight
        if weight_total > 0.0:
            return weighted_sum / weight_total

    # 无下游受控movement时，用出口车道halting_count作为反压
    to_obs = lanes_obs.get(movement.to_lane)
    if to_obs is None:
        return 0.0
    return float(to_obs.get("halting_count", 0))


def _estimate_movement_queues(
    ix: _IntersectionIndex,
    lanes_obs: dict[str, Any],
    vehicles: dict[str, Any],
) -> dict[str, float]:
    movement_queues: dict[str, float] = {conn_id: 0.0 for conn_id in ix.movements}
    lane_halting_assigned: dict[str, float] = {}
    # 将halting车辆分配给各connection
    for vehicle in vehicles.values():
        if not isinstance(vehicle, dict):
            continue
        lane_id, speed = _vehicle_lane_speed(vehicle)
        if lane_id is None or lane_id not in ix.lane_movements:
            continue
        if speed > HALTING_SPEED_MPS:
            continue
        conn_id = _match_vehicle_movement(vehicle, lane_id, ix)
        if conn_id is None:
            continue
        movement_queues[conn_id] += 1.0
        lane_halting_assigned[lane_id] = lane_halting_assigned.get(lane_id, 0.0) + 1.0
    # 将未分配给connection的halting车辆按车道转向比例分配给各connection
    for from_lane, conn_ids in ix.lane_movements.items():
        if not conn_ids:
            continue
        lane_obs = lanes_obs.get(from_lane)
        lane_halting = float(lane_obs.get("halting_count", 0)) if lane_obs else 0.0
        assigned = lane_halting_assigned.get(from_lane, 0.0)
        remainder = max(0.0, lane_halting - assigned)
        if remainder <= 0.0:
            continue

        proportions = _lane_turn_proportions(
            ix,
            from_lane,
            conn_ids,
            vehicles,
            lanes_obs,
            movement_queues,
        )
        for conn_id in conn_ids:
            movement_queues[conn_id] += remainder * proportions.get(conn_id, 0.0)

    return movement_queues


def _lane_turn_proportions(
    ix: _IntersectionIndex,
    from_lane: str,
    conn_ids: list[str],
    vehicles: dict[str, Any],
    lanes_obs: dict[str, Any],
    movement_queues: dict[str, float],
) -> dict[str, float]:
    counts = {conn_id: 0.0 for conn_id in conn_ids}

    for vehicle in vehicles.values():
        if not isinstance(vehicle, dict):
            continue
        lane_id, _speed = _vehicle_lane_speed(vehicle)
        if lane_id != from_lane:
            continue
        conn_id = _match_vehicle_movement(vehicle, from_lane, ix)
        if conn_id in counts:
            counts[conn_id] += 1.0

    total = sum(counts.values())
    if total > 0.0:
        return {conn_id: counts[conn_id] / total for conn_id in conn_ids}

    lane_obs = lanes_obs.get(from_lane)
    if lane_obs:
        conn_states = lane_obs.get("connection_signal_states")
        if isinstance(conn_states, list):
            for item in conn_states:
                if not isinstance(item, dict):
                    continue
                conn_id = str(item.get("connection_id", ""))
                if conn_id in counts:
                    counts[conn_id] += 1.0
            total = sum(counts.values())
            if total > 0.0:
                return {conn_id: counts[conn_id] / total for conn_id in conn_ids}

    assigned = sum(movement_queues.get(conn_id, 0.0) for conn_id in conn_ids)
    if assigned > 0.0:
        return {conn_id: movement_queues.get(conn_id, 0.0) / assigned for conn_id in conn_ids}

    equal = 1.0 / len(conn_ids)
    return {conn_id: equal for conn_id in conn_ids}


def _movement_turn_weights(
    ix: _IntersectionIndex,
    from_lane: str,
    conn_ids: list[str],
    lanes_obs: dict[str, Any],
    movement_queues: dict[str, float],
) -> dict[str, float]:
    proportions = _lane_turn_proportions(ix, from_lane, conn_ids, {}, lanes_obs, movement_queues)
    return {conn_id: proportions.get(conn_id, 0.0) for conn_id in conn_ids}


def _match_vehicle_movement(
    vehicle: dict[str, Any],
    lane_id: str,
    ix: _IntersectionIndex,
) -> str | None:
    loc = vehicle.get("location")
    if not isinstance(loc, dict):
        loc = vehicle

    route_edges = loc.get("route_edges")
    route_index = loc.get("route_index")
    if isinstance(route_edges, list) and route_index is not None:
        idx = int(route_index)
        if 0 <= idx < len(route_edges):
            next_edge = str(route_edges[idx + 1]) if idx + 1 < len(route_edges) else None
            if next_edge is not None:
                for conn_id in ix.lane_movements.get(lane_id, []):
                    movement = ix.movements[conn_id]
                    if movement.to_edge == next_edge:
                        return conn_id
                    to_edge = _lane_edge_id(movement.to_lane, ix.lane_edges)
                    if to_edge == next_edge:
                        return conn_id

    return None


def _intersection_vehicles(
    ix: _IntersectionIndex,
    vehicles: dict[str, Any],
) -> dict[str, Any]:
    if not vehicles:
        return {}
    relevant_lanes = set(ix.incoming_lanes) | set(ix.outgoing_lanes) | ix.all_lanes
    filtered: dict[str, Any] = {}
    for vid, vehicle in vehicles.items():
        if not isinstance(vehicle, dict):
            continue
        lane_id, _speed = _vehicle_lane_speed(vehicle)
        if lane_id is not None and lane_id in relevant_lanes:
            filtered[str(vid)] = vehicle
    return filtered


def _vehicle_lane_speed(vehicle: dict[str, Any]) -> tuple[str | None, float]:
    loc = vehicle.get("location")
    lane_id: str | None = None
    if isinstance(loc, dict):
        raw_lane = loc.get("lane_id")
        if raw_lane is not None:
            lane_id = str(raw_lane)

    if lane_id is None:
        raw_lane = vehicle.get("lane_id")
        if raw_lane is not None:
            lane_id = str(raw_lane)

    motion = vehicle.get("motion")
    speed = 0.0
    if isinstance(motion, dict) and motion.get("speed_mps") is not None:
        speed = float(motion["speed_mps"])
    elif vehicle.get("speed_mps") is not None:
        speed = float(vehicle["speed_mps"])

    return lane_id, speed


def _lane_edge_id(lane_id: str, lane_edges: dict[str, str]) -> str | None:
    if lane_id in lane_edges:
        return lane_edges[lane_id]
    parts = lane_id.rsplit("_", 1)
    if len(parts) == 2 and parts[1].isdigit():
        return parts[0]
    return None
