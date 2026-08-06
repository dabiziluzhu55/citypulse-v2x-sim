"""
Lane Actor State Builder — Protocol 2.0 / 41-dim state + action mask.

Static indices built once from init metadata.
Per-step, `build_lane_actor_batch()` produces:

    LaneActorBatch(
        states:      np.ndarray  [num_slots, 41]
        slot_mask:   np.ndarray  [num_slots]
        action_mask: np.ndarray  [num_slots, 3]
        vehicle_ids: list[str|None]
    )
"""

from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass
from typing import Any, Dict, List, Optional, Tuple

import numpy as np

# ============================================================
# Constants
# ============================================================
GUIDE_ZONE_MIN_M = 30.0
GUIDE_ZONE_MAX_M = 150.0
MIN_LANE_CHANGE_SPEED_MPS = 0.5
GAP_CAP_M = 100.0
TIME_CAP_S = 60.0
FLOW_WINDOW_S = 30.0
MAX_EXPECTED_FLOW = 2.0
LANE_STATE_DIM = 41
SPEED_LIMIT_DEFAULT_MPS = 13.9
ACCEL_SCALE = 5.0
DOWNSTREAM_QUEUE_CAP_M = 100.0
MAX_VEHICLES_PER_LANE = 20


@dataclass(frozen=True)
class LaneActorBatch:
    states: np.ndarray          # [num_slots, 41]
    slot_mask: np.ndarray       # [num_slots]
    action_mask: np.ndarray     # [num_slots, 3]
    vehicle_ids: List[Optional[str]]
    num_approach_slots: int = 0
    mask_stats: dict = None

    def __post_init__(self):
        if self.mask_stats is None:
            object.__setattr__(self, "mask_stats", {})


# ============================================================
# Module-level state
# ============================================================
_edge_to_lanes: Dict[str, List[str]] = {}
_lane_neighbors: Dict[str, Dict[str, Optional[str]]] = {}
_lane_lengths: Dict[str, float] = {}
_lane_permissions: Dict[str, Dict[int, set]] = {}  # edge_id → {lane_idx: set of allowed type_ids}
_phase_metadata: Dict[str, Dict[int, set]] = {}
_lane_connections: Dict[str, List[dict]] = {}
_downstream_lanes: Dict[str, List[str]] = {}
_min_green: Dict[str, float] = {}
_phase_durations: Dict[str, Dict[int, float]] = {}
_vehicle_type_lengths: Dict[str, float] = {}
_tls_order: List[str] = []
_approach_slots: List[Tuple[str, str]] = []  # (tls_id, incoming_edge_id)
_lane_to_tls: Dict[str, str] = {}  # lane_id → tls_id

_arrival_history: Dict[str, List[float]] = defaultdict(list)
_vehicle_action_history: Dict[str, dict] = {}


# ============================================================
# Static index builders (called once in initialize)
# ============================================================

def build_static_indices(payload: dict, edge_lanes_raw: dict) -> None:
    global _edge_to_lanes, _lane_neighbors, _lane_lengths, _lane_permissions
    global _phase_metadata, _lane_connections, _downstream_lanes
    global _min_green, _phase_durations, _vehicle_type_lengths, _tls_order

    intersections_meta = payload.get("intersections", {})
    vehicle_types_raw = payload.get("vehicle_types", {})

    _tls_order = sorted(intersections_meta.keys())

    # Build approach slots: (tls_id, incoming_edge_id)
    _approach_slots.clear()
    _lane_to_tls.clear()
    for tid in _tls_order:
        meta = intersections_meta.get(tid, {})
        incoming_lanes = meta.get("incoming_lanes", [])
        seen_edges = set()
        for lid in incoming_lanes:
            lid_s = str(lid)
            # Extract edge_id: lane_id format is "edge_id_index"
            parts = lid_s.rsplit("_", 1)
            edge_id = parts[0] if len(parts) == 2 and parts[1].isdigit() else lid_s
            if edge_id.startswith(":"):
                continue
            if edge_id not in seen_edges:
                seen_edges.add(edge_id)
                _approach_slots.append((tid, edge_id))
            _lane_to_tls[lid_s] = tid
    _approach_slots.sort()  # stable order

    # --- edge → lanes + permissions ---
    _edge_to_lanes.clear()
    _lane_lengths.clear()
    _lane_permissions.clear()
    for edge_id, lanes in edge_lanes_raw.items():
        sorted_lanes = sorted(lanes, key=lambda l: int(l.get("lane_index", 0)))
        _edge_to_lanes[edge_id] = [l["lane_id"] for l in sorted_lanes]
        perm_map: Dict[int, set] = {}
        for l in sorted_lanes:
            li = int(l.get("lane_index", 0))
            _lane_lengths[l["lane_id"]] = float(l.get("length_m", 100.0))
            atids = l.get("allowed_vehicle_type_ids", [])
            perm_map[li] = set(atids) if atids else set()
        _lane_permissions[edge_id] = perm_map

    # --- lane neighbors ---
    _lane_neighbors.clear()
    for edge_id, lane_ids in _edge_to_lanes.items():
        for i, lid in enumerate(lane_ids):
            # SUMO lane index 0 is the rightmost lane; indices increase leftward.
            _lane_neighbors[lid] = {
                "left": lane_ids[i + 1] if i + 1 < len(lane_ids) else None,
                "right": lane_ids[i - 1] if i > 0 else None,
            }

    # --- phase metadata ---
    _phase_metadata.clear()
    _phase_durations.clear()
    for tid, meta in intersections_meta.items():
        phases = meta.get("phases", {})
        pmap: Dict[int, set] = {}
        pdur: Dict[int, float] = {}
        for pid_str, pdata in phases.items():
            pid = int(pid_str)
            movement = str(pdata.get("movement", ""))
            movements = set(m.strip() for m in movement.split("|") if m.strip())
            pmap[pid] = movements
            pdur[pid] = float(pdata.get("green_seconds", 30.0))
        _phase_metadata[tid] = pmap
        _phase_durations[tid] = pdur

    # --- lane connections ---
    _lane_connections.clear()
    _downstream_lanes.clear()
    for tid, meta in intersections_meta.items():
        for conn in meta.get("connections", []):
            from_lane = str(conn.get("from_lane", ""))
            to_lane = str(conn.get("to_lane", ""))
            movement = str(conn.get("movement", ""))
            if from_lane:
                _lane_connections.setdefault(from_lane, []).append({
                    "to_lane": to_lane,
                    "movement": movement,
                })
        for lid, lmeta in meta.get("lanes", {}).items():
            downstream = lmeta.get("downstream_lane_ids", [])
            if downstream:
                _downstream_lanes[str(lid)] = [str(d) for d in downstream]

    # --- min green / phase durations ---
    _min_green.clear()
    for tid, meta in intersections_meta.items():
        _min_green[tid] = float(meta.get(
            "minimum_green", payload.get("minimum_green", 5.0)
        ))

    # --- vehicle type lengths ---
    _vehicle_type_lengths.clear()
    for type_id, vt in vehicle_types_raw.items():
        _vehicle_type_lengths[type_id] = float(vt.get("length_m", 5.0))

    print(f"[LaneState] {len(_edge_to_lanes)} edges, {len(_lane_neighbors)} lanes, "
          f"{len(_tls_order)} TLS → State dim={LANE_STATE_DIM}")


# ============================================================
# Movement resolver
# ============================================================

def _resolve_movement(vehicle: dict) -> Optional[str]:
    if vehicle is None or not isinstance(vehicle, dict):
        return None
    lane_id = vehicle.get("location", {}).get("lane_id", "")
    route_edges = list(vehicle.get("location", {}).get("route_edges", []))
    road_id = vehicle.get("location", {}).get("road_id", "")
    connections = _lane_connections.get(lane_id, [])

    if not connections:
        return None
    if not route_edges or len(route_edges) < 2:
        # Try to determine from lane connections only
        movements = [c["movement"] for c in connections if c.get("movement")]
        if movements:
            return movements[0]
        return None
    try:
        idx = route_edges.index(road_id)
        if idx + 1 >= len(route_edges):
            return None
        next_edge = route_edges[idx + 1]
    except ValueError:
        return None

    for conn in connections:
        to_lane = conn["to_lane"]
        to_edge = "_".join(to_lane.split("_")[:-1]) if "_" in to_lane else ""
        if to_edge == next_edge:
            return conn["movement"]
    return connections[0].get("movement") if connections else None


def _movement_onehot(movement: Optional[str]) -> np.ndarray:
    onehot = np.zeros(3, dtype=np.float32)
    if movement is None:
        return onehot
    m = movement.lower().strip()
    if "|" in m:
        return onehot  # 组合 movement 暂不处理
    if m in ("left", "turn_left"):
        onehot[0] = 1.0
    elif m in ("straight", "through", "go_straight"):
        onehot[1] = 1.0
    elif m in ("right", "turn_right"):
        onehot[2] = 1.0
    return onehot


# ============================================================
# Gap computation
# ============================================================

def _group_vehicles_by_lane(vehicles: dict) -> Dict[str, List[dict]]:
    result: Dict[str, List[dict]] = defaultdict(list)
    for vid, veh in vehicles.items():
        if veh is None or not isinstance(veh, dict):
            continue
        lane_id = veh.get("location", {}).get("lane_id", "")
        if lane_id:
            result[lane_id].append(veh)
    return result


def _compute_lane_gaps(
    ego_position: float,
    ego_length: float,
    target_lane_id: str,
    vehicles_by_lane: Dict[str, List[dict]],
) -> Tuple[float, float]:
    lane_vehicles = vehicles_by_lane.get(target_lane_id, [])
    if not lane_vehicles:
        return GAP_CAP_M, GAP_CAP_M

    sorted_vehs = sorted(
        lane_vehicles,
        key=lambda v: v.get("location", {}).get("lane_position_m", 0.0),
    )
    ego_end = ego_position + ego_length
    leader_gap = GAP_CAP_M
    follower_gap = GAP_CAP_M

    for i, veh in enumerate(sorted_vehs):
        vpos = float(veh.get("location", {}).get("lane_position_m", 0.0))
        vlen = _vehicle_type_lengths.get(veh.get("type_id", ""), 5.0)
        if vpos >= ego_position:
            leader_gap = max(0.0, min(vpos - ego_end, GAP_CAP_M))
            if i > 0:
                prev = sorted_vehs[i - 1]
                ppos = float(prev.get("location", {}).get("lane_position_m", 0.0))
                plen = _vehicle_type_lengths.get(prev.get("type_id", ""), 5.0)
                follower_gap = max(0.0, min(ego_position - ppos - plen, GAP_CAP_M))
            break
    else:
        last = sorted_vehs[-1]
        lpos = float(last.get("location", {}).get("lane_position_m", 0.0))
        llen = _vehicle_type_lengths.get(last.get("type_id", ""), 5.0)
        follower_gap = max(0.0, min(ego_position - lpos - llen, GAP_CAP_M))

    return leader_gap, follower_gap


# ============================================================
# Permission check
# ============================================================

def _is_vehicle_type_allowed_on_lane(road_id: str, lane_idx: int, type_id: str) -> bool:
    perms = _lane_permissions.get(road_id, {})
    allowed_set = perms.get(lane_idx)
    if allowed_set is None:
        return False
    if not allowed_set:
        return False  # 空 = 无匹配车型
    return type_id in allowed_set


# ============================================================
# Feature builders
# ============================================================

def _build_vehicle_features(vehicle: dict) -> np.ndarray:
    if vehicle is None or not isinstance(vehicle, dict):
        return np.zeros(8, dtype=np.float32)
    motion = vehicle.get("motion", {}) or {}
    loc = vehicle.get("location", {}) or {}

    speed = float(motion.get("speed_mps", 0.0))
    speed_limit = float(motion.get("allowed_speed_mps", SPEED_LIMIT_DEFAULT_MPS))
    accel = float(motion.get("acceleration_mps2", 0.0))

    lane_idx = int(loc.get("lane_index", 0))
    road_id = loc.get("road_id", "")
    max_lane = max(len(_edge_to_lanes.get(road_id, [])) - 1, 0)

    lane_id = loc.get("lane_id", "")
    lane_len = _lane_lengths.get(lane_id, 100.0)
    dist_to_stop = lane_len - float(loc.get("lane_position_m", 0.0))
    dist_ratio = np.clip(dist_to_stop / max(lane_len, 1.0), 0.0, 1.0)

    t_since = float(vehicle.get("time_since_last_lane_change_s", TIME_CAP_S) or TIME_CAP_S)
    t_since_norm = np.clip(t_since / TIME_CAP_S, 0.0, 1.0)

    movement = _resolve_movement(vehicle)
    movement_oh = _movement_onehot(movement)

    return np.array([
        np.clip(speed / max(speed_limit, 0.1), 0.0, 1.0),
        np.clip(accel / ACCEL_SCALE, -1.0, 1.0),
        dist_ratio,
        lane_idx / max(max_lane, 1),
        t_since_norm,
        movement_oh[0], movement_oh[1], movement_oh[2],
    ], dtype=np.float32)


def _build_lane_features(
    vehicle: dict,
    target_lane_id: Optional[str],
    vehicles_by_lane: Dict[str, List[dict]],
    intersections_obs: dict,
) -> np.ndarray:
    if target_lane_id is None:
        return np.zeros(6, dtype=np.float32)

    lane_obs = None
    for iobs in intersections_obs.values():
        if target_lane_id in iobs.get("lanes", {}):
            lane_obs = iobs["lanes"][target_lane_id]
            break

    vc = float(lane_obs.get("vehicle_count", 0)) if lane_obs else 0.0
    occ_raw = float(lane_obs.get("occupancy", 0)) if lane_obs else 0.0
    occ = occ_raw / 100.0
    ms = float(lane_obs.get("mean_speed", -1.0)) if lane_obs else -1.0

    ego_pos = float(vehicle.get("location", {}).get("lane_position_m", 0.0))
    ego_len = _vehicle_type_lengths.get(vehicle.get("type_id", ""), 5.0)
    leader_gap, follower_gap = _compute_lane_gaps(
        ego_pos, ego_len, target_lane_id, vehicles_by_lane,
    )

    downstream_storage = 1.0
    d_ids = _downstream_lanes.get(target_lane_id, [])
    if d_ids:
        for iobs in intersections_obs.values():
            lanes = iobs.get("lanes", {})
            for d in d_ids:
                if d in lanes:
                    d_occ_raw = float(lanes[d].get("occupancy", 0))
                    d_occ = d_occ_raw / 100.0
                    downstream_storage = min(downstream_storage, 1.0 - np.clip(d_occ, 0.0, 1.0))

    return np.array([
        np.clip(vc / MAX_VEHICLES_PER_LANE, 0.0, 1.0),
        np.clip(occ, 0.0, 1.0),
        np.clip(ms / max(SPEED_LIMIT_DEFAULT_MPS, 0.1), -1.0, 1.0) if ms >= 0 else -1.0,
        np.clip(leader_gap / GAP_CAP_M, 0.0, 1.0),
        np.clip(follower_gap / GAP_CAP_M, 0.0, 1.0),
        np.clip(downstream_storage, 0.0, 1.0),
    ], dtype=np.float32)


def _build_signal_intent_features(
    vehicle: dict,
    intersections_obs: dict,
    signal_actions: Dict[str, int],
    slot_tls_id: str = "",
) -> np.ndarray:
    # Prefer slot_tls_id over vehicle.next_signal
    tls_id = slot_tls_id or (vehicle.get("next_signal") or {}).get("tls_id", "")
    if not tls_id or tls_id not in intersections_obs:
        return np.zeros(7, dtype=np.float32)

    iobs = intersections_obs[tls_id]
    current_phase = int(iobs.get("current_phase", 1))
    target_phase = signal_actions.get(tls_id, current_phase)
    stage = str(iobs.get("stage", ""))
    stage_elapsed = float(iobs.get("stage_elapsed", 0.0))

    movement = _resolve_movement(vehicle)

    def _phase_serves(pid: int, mov: Optional[str]) -> bool:
        if mov is None:
            return False
        return mov in _phase_metadata.get(tls_id, {}).get(pid, set())

    current_serves = 1.0 if _phase_serves(current_phase, movement) else 0.0
    target_serves = 1.0 if _phase_serves(target_phase, movement) else 0.0
    same_phase = 1.0 if current_phase == target_phase else 0.0

    green_dur = _phase_durations.get(tls_id, {}).get(current_phase, 30.0)
    elapsed_ratio = np.clip(stage_elapsed / max(green_dur, 0.1), 0.0, 1.0)

    min_g = _min_green.get(tls_id, 5.0)
    met_min_green = 1.0 if stage == "GREEN" and stage_elapsed >= min_g else 0.0

    is_transition = 1.0 if stage not in ("GREEN", "RED") else 0.0

    # 下游车道是否绿灯 (从当前 lane 的下游 lane 信号状态获取)
    downstream_green = 0.0
    lane_id = vehicle.get("location", {}).get("lane_id", "")
    d_ids = _downstream_lanes.get(lane_id, [])
    for d_lid in d_ids:
        for iobs in intersections_obs.values():
            dl = iobs.get("lanes", {}).get(d_lid, {})
            if dl.get("signal_state") == "GREEN" or dl.get("lane_has_green"):
                downstream_green = 1.0
                break
        if downstream_green > 0:
            break

    return np.array([
        current_serves, target_serves, same_phase,
        elapsed_ratio, met_min_green, is_transition, downstream_green,
    ], dtype=np.float32)


def _build_region_features(
    vehicle: dict,
    intersections_obs: dict,
    current_time: float,
    slot_tls_id: str = "",
) -> np.ndarray:
    road_id = vehicle.get("location", {}).get("road_id", "")
    tls_id = slot_tls_id or (vehicle.get("next_signal") or {}).get("tls_id", "")
    lane_id = vehicle.get("location", {}).get("lane_id", "")

    # 短时到达率
    hist = _arrival_history.get(road_id, [])
    cutoff = current_time - FLOW_WINDOW_S
    hist = [t for t in hist if t >= cutoff]
    _arrival_history[road_id] = hist
    flow_rate = len(hist) / FLOW_WINDOW_S
    flow_norm = np.clip(flow_rate / MAX_EXPECTED_FLOW, 0.0, 1.0)

    # 下游道路 occupancy
    downstream_occ = 0.0
    for dlid in _downstream_lanes.get(lane_id, []):
        for iobs in intersections_obs.values():
            if dlid in iobs.get("lanes", {}):
                d_occ_raw = float(iobs["lanes"][dlid].get("occupancy", 0))
                d_occ = d_occ_raw / 100.0
                downstream_occ = max(downstream_occ, d_occ)

    # 当前路口总排队
    total_queue = 0.0
    if tls_id and tls_id in intersections_obs:
        for lo in intersections_obs[tls_id].get("lanes", {}).values():
            total_queue += float(lo.get("queue_length_m", 0))
    queue_ratio = np.clip(total_queue / DOWNSTREAM_QUEUE_CAP_M, 0.0, 1.0)

    return np.array([flow_norm, downstream_occ, queue_ratio], dtype=np.float32)


def _build_history_features(vehicle_id: str) -> np.ndarray:
    hist = _vehicle_action_history.get(vehicle_id, {})
    last_act = hist.get("last_action", 0)
    last_ok = 1.0 if hist.get("last_success", False) else 0.0
    cooldown = np.clip(hist.get("cooldown_left", 0.0), 0.0, 1.0)

    onehot = np.zeros(3, dtype=np.float32)
    if 0 <= last_act <= 2:
        onehot[last_act] = 1.0

    return np.concatenate([onehot, [last_ok, cooldown]]).astype(np.float32)


# ============================================================
# Action mask
# ============================================================

# Mask reason counters (diagnostic)
_mask_stats = {
    "left_missing": 0, "right_missing": 0,
    "left_permission_denied": 0, "right_permission_denied": 0,
    "left_route_incompatible": 0, "right_route_incompatible": 0,
    "stopped_filtered": 0,
}

def get_mask_stats() -> dict:
    return dict(_mask_stats)

def reset_mask_stats():
    for k in _mask_stats:
        _mask_stats[k] = 0
    _mask_stats["stopped_filtered"] = 0


def _can_receive_lane_change(vehicle: dict) -> bool:
    """检查车辆当前是否可接收换道推荐。"""
    speed = float(vehicle.get("motion", {}).get("speed_mps", 0))
    if speed < MIN_LANE_CHANGE_SPEED_MPS:
        return False
    road_id = vehicle.get("location", {}).get("road_id", "")
    if road_id.startswith(":"):
        return False
    # Protocol 2.0 没有显式 pending 字段，用 lane_change_status 判断
    # 如果上次换道未完成且时间很近，视为 pending
    return True


def _build_action_mask(vehicle: dict) -> np.ndarray:
    mask = np.array([True, False, False], dtype=bool)
    if not _can_receive_lane_change(vehicle):
        _mask_stats["stopped_filtered"] += 1
        return mask  # only KEEP valid
    lane_id = vehicle.get("location", {}).get("lane_id", "")
    type_id = vehicle.get("type_id", "")
    lane_idx = int(vehicle.get("location", {}).get("lane_index", 0))
    route_edges = list(vehicle.get("location", {}).get("route_edges", []))
    road_id = vehicle.get("location", {}).get("road_id", "")

    # 确定下一跳 edge
    next_edge = None
    if route_edges and len(route_edges) >= 2:
        try:
            idx = route_edges.index(road_id)
            if idx + 1 < len(route_edges):
                next_edge = route_edges[idx + 1]
        except ValueError:
            pass

    neighbors = _lane_neighbors.get(lane_id, {})
    if neighbors.get("left") is None:
        _mask_stats["left_missing"] += 1
    if neighbors.get("right") is None:
        _mask_stats["right_missing"] += 1

    for action_idx, neighbor_key, delta_idx in [
        (1, "left", 1),
        (2, "right", -1),
    ]:
        target_lid = neighbors.get(neighbor_key)
        if target_lid is None:
            continue
        target_li = lane_idx + delta_idx

        # 权限
        if not _is_vehicle_type_allowed_on_lane(road_id, target_li, type_id):
            if neighbor_key == "left":
                _mask_stats["left_permission_denied"] += 1
            else:
                _mask_stats["right_permission_denied"] += 1
            continue

        # 路线兼容：target lane 必须能到达 next_edge
        if next_edge:
            conns = _lane_connections.get(target_lid, [])
            to_edges = set()
            for c in conns:
                tl = c.get("to_lane", "")
                te = "_".join(tl.split("_")[:-1]) if "_" in tl else ""
                if te:
                    to_edges.add(te)
            if next_edge not in to_edges:
                if neighbor_key == "left":
                    _mask_stats["left_route_incompatible"] += 1
                else:
                    _mask_stats["right_route_incompatible"] += 1
                continue

        mask[action_idx] = True

    return mask


# ============================================================
# Candidate selection
# ============================================================

def _select_candidate_vehicle(
    edge_id: str,
    vehicles_on_edge: List[dict],
) -> Optional[dict]:
    best, best_dist = None, float('inf')
    for veh in vehicles_on_edge:
        if veh is None or not isinstance(veh, dict):
            continue
        lane_id = veh.get("location", {}).get("lane_id", "")
        pos = float(veh.get("location", {}).get("lane_position_m", 0))
        lane_len = _lane_lengths.get(lane_id, 100.0)
        dist_to_stop = lane_len - pos
        if not (GUIDE_ZONE_MIN_M <= dist_to_stop <= GUIDE_ZONE_MAX_M):
            continue
        if float(veh.get("motion", {}).get("speed_mps", 0)) <= 0.1:
            continue
        if dist_to_stop < best_dist:
            best_dist = dist_to_stop
            best = veh
    return best


# ============================================================
# Main batch builder
# ============================================================

def build_lane_actor_batch(
    payload: dict,
    signal_actions: Dict[str, int],
) -> LaneActorBatch:
    vehicles = payload.get("vehicles", {})
    intersections_obs = payload.get("intersections", {})

    # 按 edge 分组 + 建 vehicles 反查表
    vehicles_by_edge: Dict[str, List[dict]] = defaultdict(list)
    veh_id_map: Dict[int, str] = {}  # id(veh) → vid (fast lookup)
    for vid, veh in vehicles.items():
        if veh is None or not isinstance(veh, dict):
            continue
        edge_id = veh.get("location", {}).get("road_id", "")
        if edge_id and not edge_id.startswith(":"):
            vehicles_by_edge[edge_id].append(veh)
            veh_id_map[id(veh)] = vid

    vehicles_by_lane = _group_vehicles_by_lane(vehicles)

    # slot 列表: only approach roads
    slots: List[Tuple[Optional[dict], str, str]] = []  # (cand, edge_id, tls_id)
    for tls_id, edge_id in _approach_slots:
        cand = _select_candidate_vehicle(edge_id, vehicles_by_edge.get(edge_id, []))
        slots.append((cand, edge_id, tls_id))

    num_slots = len(slots)
    current_time = float(payload.get("simulation_time", 0.0))

    states = np.zeros((num_slots, LANE_STATE_DIM), dtype=np.float32)
    slot_mask = np.zeros(num_slots, dtype=np.float32)
    action_mask = np.zeros((num_slots, 3), dtype=bool)
    action_mask[:, 0] = True
    vehicle_ids: List[Optional[str]] = [None] * num_slots

    for i, (cand, edge_id, tls_id) in enumerate(slots):
        if cand is None:
            continue
        vid = veh_id_map.get(id(cand))
        if vid is None:
            continue

        vehicle_ids[i] = vid
        slot_mask[i] = 1.0

        if cand is None or not isinstance(cand, dict):
            slot_mask[i] = 0.0
            continue
        lane_id = cand.get("location", {}).get("lane_id", "")
        neighbors = _lane_neighbors.get(lane_id, {})

        segments = [
            _build_vehicle_features(cand),                                           # 0-7
            _build_lane_features(cand, neighbors.get("left"), vehicles_by_lane, intersections_obs),   # 8-13
            _build_lane_features(cand, lane_id, vehicles_by_lane, intersections_obs),                 # 14-19
            _build_lane_features(cand, neighbors.get("right"), vehicles_by_lane, intersections_obs),  # 20-25
            _build_signal_intent_features(cand, intersections_obs, signal_actions, tls_id),  # 26-32
            _build_region_features(cand, intersections_obs, current_time, tls_id),           # 33-35
            _build_history_features(vid),                                            # 36-40
        ]
        state = np.concatenate(segments)

        if state.shape[0] != LANE_STATE_DIM:
            slot_mask[i] = 0.0
            continue
        if not np.isfinite(state).all():
            slot_mask[i] = 0.0
            continue

        states[i] = state
        action_mask[i] = _build_action_mask(cand)

    return LaneActorBatch(
        states=states,
        slot_mask=slot_mask,
        action_mask=action_mask,
        vehicle_ids=vehicle_ids,
        num_approach_slots=len(_approach_slots),
        mask_stats=get_mask_stats(),
    )


# ============================================================
# History management
# ============================================================

def update_vehicle_history(vehicle_id: str, action: int, success: bool, cooldown_steps: int = 0) -> None:
    _vehicle_action_history[vehicle_id] = {
        "last_action": action,
        "last_success": success,
        "cooldown_left": max(0, cooldown_steps),
    }


def decay_cooldowns() -> None:
    for h in _vehicle_action_history.values():
        if h.get("cooldown_left", 0) > 0:
            h["cooldown_left"] -= 1


def record_arrival(edge_id: str, sim_time: float) -> None:
    _arrival_history[edge_id].append(sim_time)
    cutoff = sim_time - 2 * FLOW_WINDOW_S
    _arrival_history[edge_id] = [t for t in _arrival_history[edge_id] if t >= cutoff]


def reset_lane_state() -> None:
    _arrival_history.clear()
    _vehicle_action_history.clear()
    reset_mask_stats()
