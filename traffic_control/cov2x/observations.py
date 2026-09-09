"""Feature construction for the offline CV Joint V1 controller.

The builder deliberately owns only feature assembly.  It does not import the
old J0 controller or call a runtime/actor/optimizer.  Canonical topology is
used as a static source of route, signal-connection, and lane facts; all
vehicle and signal values come from the current pre-action payload.
"""
from __future__ import annotations

import copy
import json
import math
from collections import defaultdict
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Mapping

import numpy as np

from .contracts import (
    BASE_VEHICLE_DIM,
    CONTEXT_DIM,
    LANE_SLOTS,
    MOVEMENT_DIM,
    FeatureSnapshot,
    Leader,
    MovementKey,
)


GUIDE_ZONE_MIN_M = 20.0
GUIDE_ZONE_MAX_M = 250.0
HALT_SPEED_MPS = 0.1
LANE_CHANGE_SPEED_MPS = 0.3
HORIZON_S = 900.0
ROAD_OBS_DIM = 132
DEMO_IDS = tuple(f"demo_{i}" for i in range(1, 21))
MOVEMENT_NAMES = frozenset(("left", "right", "through"))
SIGNAL_INDEX = {"G": 14, "g": 15, "y": 16, "r": 17}


@dataclass(frozen=True)
class _Connection:
    tls_id: str
    demo_id: str
    link_index: int | None
    from_lane_id: str
    from_edge: str
    to_lane_id: str
    to_edge: str
    movement: str

    @property
    def key(self) -> MovementKey:
        return MovementKey(self.tls_id, self.from_edge, self.to_edge)


@dataclass
class _Vehicle:
    vehicle_id: str
    payload: Mapping[str, Any]
    lane_id: str | None
    road_id: str | None
    from_edge: str | None
    position_m: float | None
    speed_mps: float
    allowed_speed_mps: float | None
    native_valid: bool
    vehicle_class: str
    resolution_class: str
    movement: MovementKey | None
    route_candidates: tuple[_Connection, ...]
    signal_candidates: tuple[_Connection, ...]
    signal_mode: str | None
    distance_m: float | None
    in_guide_zone: bool


def _as_mapping(value: Any, label: str) -> Mapping[str, Any]:
    if not isinstance(value, Mapping):
        raise ValueError(f"{label} must be a mapping")
    return value


def _finite_number(value: Any, label: str, *, default: float | None = None) -> float | None:
    """Return a finite float, treating only an absent/None value as missing."""
    if value is None:
        return default
    try:
        result = float(value)
    except (TypeError, ValueError) as exc:
        raise ValueError(f"{label} must be numeric") from exc
    if not math.isfinite(result):
        raise ValueError(f"{label} must be finite")
    return result


def _edge_of_lane(lane_id: str | None) -> str | None:
    if not lane_id:
        return None
    if "_" not in lane_id:
        return lane_id
    return lane_id.rsplit("_", 1)[0]


def _lane_index(lane_id: str) -> int | None:
    if "_" not in lane_id:
        return None
    tail = lane_id.rsplit("_", 1)[1]
    try:
        return int(tail)
    except (TypeError, ValueError):
        return None


def _clip_ratio(value: float | None, scale: float, upper: float = 1.0) -> float:
    if value is None:
        return 0.0
    return float(np.clip(max(0.0, float(value)) / scale, 0.0, upper))


def _copy_array(value: Any, shape: tuple[int, ...], label: str) -> np.ndarray:
    try:
        arr = np.asarray(value, dtype=np.float32)
    except (TypeError, ValueError) as exc:
        raise ValueError(f"{label} must be a finite vector of shape {shape}") from exc
    if arr.shape != shape or not np.isfinite(arr).all():
        raise ValueError(f"{label} must be a finite vector of shape {shape}")
    return arr.copy()


class FeatureBuilder:
    """Build frozen, sensor-only feature snapshots from one callback payload."""

    def __init__(self, canonical: Mapping[str, Any] | str | Path | None = None,
                 lane_vclass: Mapping[str, Any] | None = None):
        self._canonical = self._load_canonical(canonical)
        self._lane_vclass = dict(lane_vclass or {})
        self._connections: tuple[_Connection, ...] = self._read_connections(self._canonical)
        self._by_lane: dict[str, tuple[_Connection, ...]] = self._group_connections(
            self._connections, lambda c: c.from_lane_id
        )
        self._by_edge: dict[str, tuple[_Connection, ...]] = self._group_connections(
            self._connections, lambda c: c.from_edge
        )
        self._demo_by_tls: dict[str, str] = {}
        for demo, entry in self._canonical.items():
            if not isinstance(entry, Mapping):
                continue
            tls_ids = entry.get("tls_ids") or entry.get("tls_id") or (demo,)
            if isinstance(tls_ids, str):
                tls_ids = (tls_ids,)
            for tls in tls_ids:
                if tls is not None:
                    self._demo_by_tls.setdefault(str(tls), str(demo))
        for conn in self._connections:
            self._demo_by_tls.setdefault(conn.tls_id, conn.demo_id)

        self._lane_edge: dict[str, str] = {}
        self._lane_index: dict[str, int | None] = {}
        self._lane_length: dict[str, float] = {}
        self._edge_lanes: dict[str, set[str]] = defaultdict(set)
        self._serving_lanes: dict[MovementKey, set[str]] = defaultdict(set)
        self._read_static_lanes()
        self._read_lane_vclass_inventory()
        for conn in self._connections:
            self._lane_edge.setdefault(conn.from_lane_id, conn.from_edge)
            self._lane_edge.setdefault(conn.to_lane_id, _edge_of_lane(conn.to_lane_id) or conn.to_edge)
            idx = self._lane_index.get(conn.from_lane_id)
            if idx is None:
                idx = _lane_index(conn.from_lane_id)
            self._lane_index.setdefault(conn.from_lane_id, idx)
            self._edge_lanes[conn.from_edge].add(conn.from_lane_id)
            self._serving_lanes[conn.key].add(conn.from_lane_id)
            # A connection's geometry is authoritative when no atom supplied it.
            length = _safe_connection_length(self._canonical, conn)
            if length is not None:
                self._lane_length.setdefault(conn.from_lane_id, length)

        self._movement_keys = tuple(sorted({conn.key for conn in self._connections
                                            if conn.movement in MOVEMENT_NAMES}))
        self._movement_index = {key: i for i, key in enumerate(self._movement_keys)}

    @staticmethod
    def _load_canonical(canonical: Mapping[str, Any] | str | Path | None) -> Mapping[str, Any]:
        if canonical is None:
            path = Path(__file__).resolve().parent / "models" / "cv_joint_v1_canonical_topology.json"
            with path.open(encoding="utf-8") as handle:
                canonical = json.load(handle)
        elif isinstance(canonical, (str, Path)):
            with Path(canonical).open(encoding="utf-8") as handle:
                canonical = json.load(handle)
        return _as_mapping(canonical, "canonical")

    @staticmethod
    def _group_connections(connections: tuple[_Connection, ...], key_fn):
        grouped: dict[str, list[_Connection]] = defaultdict(list)
        for conn in connections:
            grouped[key_fn(conn)].append(conn)
        return {key: tuple(sorted(value, key=_connection_sort_key))
                for key, value in grouped.items()}

    @property
    def movement_keys(self) -> tuple[MovementKey, ...]:
        return self._movement_keys

    @property
    def movement_index(self) -> dict[MovementKey, int]:
        return dict(self._movement_index)

    @staticmethod
    def _read_connections(canonical: Mapping[str, Any]) -> tuple[_Connection, ...]:
        rows: list[_Connection] = []
        for demo, entry in canonical.items():
            if not isinstance(entry, Mapping):
                continue
            tls_ids = entry.get("tls_ids") or entry.get("tls_id") or (demo,)
            if isinstance(tls_ids, str):
                tls_ids = (tls_ids,)
            tls = str(next(iter(tls_ids), demo))
            raw_connections = entry.get("connections") or []
            if not raw_connections:
                atoms = entry.get("connection_service_atoms") or {}
                raw_connections = list(atoms.values()) if isinstance(atoms, Mapping) else []
            for row in raw_connections:
                if not isinstance(row, Mapping):
                    continue
                from_lane = row.get("from_lane_id", row.get("from_lane"))
                to_lane = row.get("to_lane_id", row.get("to_lane"))
                from_edge = row.get("from_edge", row.get("from_edge_id")) or _edge_of_lane(from_lane)
                to_edge = row.get("to_edge", row.get("to_edge_id")) or _edge_of_lane(to_lane)
                movement = row.get("movement_decoded", row.get("movement"))
                if not all(isinstance(x, str) and x for x in (from_lane, to_lane, from_edge, to_edge, movement)):
                    continue
                row_tls = row.get("tls_id", tls)
                try:
                    link = int(row["link_index"]) if row.get("link_index") is not None else None
                except (TypeError, ValueError):
                    link = None
                rows.append(_Connection(
                    tls_id=str(row_tls), demo_id=str(demo), link_index=link,
                    from_lane_id=str(from_lane), from_edge=str(from_edge),
                    to_lane_id=str(to_lane), to_edge=str(to_edge), movement=str(movement),
                ))
        # Duplicate rows do not create duplicate legal slots or catalog entries.
        unique: dict[tuple[Any, ...], _Connection] = {}
        for conn in rows:
            unique.setdefault((conn.tls_id, conn.from_lane_id, conn.to_lane_id,
                               conn.from_edge, conn.to_edge, conn.movement), conn)
        return tuple(sorted(unique.values(), key=_connection_sort_key))

    def _read_static_lanes(self) -> None:
        for demo, entry in self._canonical.items():
            if not isinstance(entry, Mapping):
                continue
            atoms = entry.get("physical_lane_atoms") or {}
            if not isinstance(atoms, Mapping):
                continue
            for lane_id, atom in atoms.items():
                if not isinstance(atom, Mapping):
                    continue
                lid = str(atom.get("lane_id", lane_id))
                edge = atom.get("edge_id", atom.get("edge", _edge_of_lane(lid)))
                if not isinstance(edge, str) or not edge:
                    continue
                self._lane_edge[lid] = edge
                raw_index = atom.get("lane_index", _lane_index(lid))
                self._lane_index[lid] = _safe_int(raw_index)
                length = atom.get("length_m", atom.get("length"))
                if length is not None:
                    self._lane_length[lid] = _finite_number(length, f"lane {lid} length")  # type: ignore[assignment]
                self._edge_lanes[edge].add(lid)

    def _read_lane_vclass_inventory(self) -> None:
        """Add every lane known to the full edge inventory.

        ``lane_vclass`` is supplied from the unfiltered network lane list by
        the coordinator.  Its pedestrian-only lanes may have no canonical
        movement connection, but they still occupy native SUMO lane indices.
        """
        for raw_lane_id, metadata in self._lane_vclass.items():
            lane_id = str(raw_lane_id)
            edge = _edge_of_lane(lane_id)
            if not edge:
                continue
            self._lane_edge.setdefault(lane_id, edge)
            explicit_index = None
            if isinstance(metadata, Mapping):
                explicit_index = metadata.get("lane_index", metadata.get("index"))
            native_index = _safe_int(explicit_index) if explicit_index is not None else _lane_index(lane_id)
            self._lane_index.setdefault(lane_id, native_index)
            self._edge_lanes[edge].add(lane_id)

    def build(self, payload: Mapping[str, Any], *, road_observations: Mapping[str, Any],
              last_decisions: Mapping[str, Any], road_margins: Mapping[str, Any],
              action_interval: float = 15.0) -> FeatureSnapshot:
        payload = _as_mapping(payload, "payload")
        road_observations = _as_mapping(road_observations, "road_observations")
        last_decisions = _as_mapping(last_decisions, "last_decisions")
        road_margins = _as_mapping(road_margins, "road_margins")
        interval = _finite_number(action_interval, "action_interval")
        if interval is None or interval <= 0.0:
            raise ValueError("action_interval must be positive")

        now = _finite_number(payload.get("simulation_time", payload.get("time", 0.0)),
                             "simulation_time", default=0.0)
        if now is None or now < 0.0:
            raise ValueError("simulation_time must be non-negative")
        step_value = payload.get("step_id", payload.get("step", 0))
        step_id = _safe_int(step_value)
        if step_id is None or step_id < 0:
            raise ValueError("step_id must be a non-negative integer")
        episode_id = str(payload.get("episode_id", payload.get("episode", "offline")))

        intersections = payload.get("intersections") or {}
        intersections = _as_mapping(intersections, "intersections")
        for iid, intersection in intersections.items():
            if not isinstance(intersection, Mapping):
                continue
            for field in ("stage_elapsed", "phase_age_s"):
                if field in intersection:
                    _finite_number(intersection[field], f"{iid} {field}")
        vehicles = payload.get("vehicles") or {}
        vehicles = _as_mapping(vehicles, "vehicles")
        lane_payload = self._lane_payload(intersections)

        parsed: dict[str, _Vehicle] = {}
        vehicle_states: dict[str, dict[str, Any]] = {}
        for raw_vid, raw_vehicle in vehicles.items():
            vid = str(raw_vid)
            vehicle = _as_mapping(raw_vehicle, f"vehicle {vid}")
            item = self._parse_vehicle(vid, vehicle, lane_payload)
            parsed[vid] = item
            state = copy.deepcopy(dict(vehicle))
            state.update({
                "movement_valid": item.movement is not None,
                "movement_missing_reason": None if item.movement is not None else item.resolution_class,
                "movement_class": item.resolution_class,
                "movement_token": item.movement.token if item.movement is not None else None,
                "signal_mode": item.signal_mode,
                "distance_to_stopline_m": item.distance_m,
                "in_guide_zone": item.in_guide_zone,
            })
            vehicle_states[vid] = state

        filtered = [v for v in parsed.values() if v.movement is not None and v.in_guide_zone]
        demand = defaultdict(int)
        halted_by_movement = defaultdict(int)
        moving_by_movement = defaultdict(int)
        for item in filtered:
            demand[item.movement] += 1  # type: ignore[index]
            if item.speed_mps < HALT_SPEED_MPS:
                halted_by_movement[item.movement] += 1  # type: ignore[index]
            else:
                moving_by_movement[item.movement] += 1  # type: ignore[index]
        lane_groups = self._build_groups(filtered)
        lane_spatial = self._spatial_by_lane(filtered)

        leaders: dict[str, Leader] = {}
        for item, members in lane_groups:
            assert item.movement is not None
            group_size = len(members)
            base = self._base_observation(
                item=item,
                group_size=group_size,
                movement_demand=demand[item.movement],
                movement_halted=halted_by_movement[item.movement],
                movement_moving=moving_by_movement[item.movement],
                spatial=lane_spatial.get(item.lane_id or "", ()),
                now=now,
                interval=interval,
                intersections=intersections,
                last_decisions=last_decisions,
                road_margins=road_margins,
            )
            lane_mask, targets, target_ids = self._lane_targets(item)
            leader = Leader(
                vehicle_id=item.vehicle_id,
                movement=item.movement,
                movement_index=self._movement_index[item.movement],
                lane_id=item.lane_id or "",
                base_obs=base,
                lane_mask=lane_mask,
                lane_targets=targets,
                lane_target_ids=target_ids,
                native_ceiling=item.allowed_speed_mps if item.native_valid else None,
                speed_eligible=item.native_valid,
                members=members,
            )
            leaders[item.vehicle_id] = leader

        movements = self._movement_vectors(leaders)
        context = self._context(road_observations, leaders)
        road_states = copy.deepcopy({str(k): dict(v) if isinstance(v, Mapping) else v
                                     for k, v in intersections.items()})
        return FeatureSnapshot(
            episode_id=episode_id,
            step_id=step_id,
            simulation_time=now,
            context=context,
            leaders=leaders,
            movements=movements,
            road_states=road_states,
            vehicle_states=vehicle_states,
        )

    @staticmethod
    def _lane_payload(intersections: Mapping[str, Any]) -> dict[str, Mapping[str, Any]]:
        result: dict[str, Mapping[str, Any]] = {}
        for intersection in intersections.values():
            if not isinstance(intersection, Mapping):
                continue
            lanes = intersection.get("lanes") or {}
            if not isinstance(lanes, Mapping):
                continue
            for lane_id, row in lanes.items():
                if isinstance(row, Mapping):
                    result[str(lane_id)] = row
        return result

    def _parse_vehicle(self, vid: str, vehicle: Mapping[str, Any],
                       lane_payload: Mapping[str, Mapping[str, Any]]) -> _Vehicle:
        location = vehicle.get("location") or {}
        motion = vehicle.get("motion") or {}
        location = _as_mapping(location, f"vehicle {vid} location")
        motion = _as_mapping(motion, f"vehicle {vid} motion")
        lane_id = location.get("lane_id")
        road_id = location.get("road_id")
        lane_id = str(lane_id) if lane_id is not None else None
        road_id = str(road_id) if road_id is not None else _edge_of_lane(lane_id)
        position = _finite_number(location.get("lane_position_m", location.get("lane_position")),
                                  f"vehicle {vid} lane position")
        speed = _finite_number(motion.get("speed_mps", motion.get("speed", 0.0)),
                               f"vehicle {vid} speed", default=0.0)
        assert speed is not None
        allowed_present = "allowed_speed_mps" in motion
        allowed = _finite_number(motion.get("allowed_speed_mps"),
                                 f"vehicle {vid} allowed speed") if allowed_present else None
        native_valid = allowed is not None and allowed > 0.0
        vclass = self._vehicle_class(vehicle)

        resolution_class, movement, candidates, signal_candidates = self._resolve_movement(
            lane_id, road_id, location
        )
        signal = self._resolve_signal(signal_candidates, lane_payload)
        distance = None
        if lane_id is not None and position is not None:
            length = self._lane_length.get(lane_id)
            if length is not None:
                distance = length - position
        in_zone = distance is not None and GUIDE_ZONE_MIN_M <= distance <= GUIDE_ZONE_MAX_M
        return _Vehicle(
            vehicle_id=vid, payload=vehicle, lane_id=lane_id, road_id=road_id,
            from_edge=road_id, position_m=position, speed_mps=speed,
            allowed_speed_mps=allowed, native_valid=native_valid,
            vehicle_class=vclass, resolution_class=resolution_class,
            movement=movement, route_candidates=tuple(candidates),
            signal_candidates=tuple(signal_candidates), signal_mode=signal,
            distance_m=distance, in_guide_zone=bool(in_zone),
        )

    def _resolve_movement(self, lane_id: str | None, road_id: str | None,
                          location: Mapping[str, Any]):
        if not lane_id or not road_id or str(road_id).startswith(":") \
                or str(lane_id).startswith(":"):
            return "INTERNAL_OR_UNKNOWN_LOCATION", None, (), ()
        route_edges = location.get("route_edges")
        if not isinstance(route_edges, (list, tuple)):
            return "ROUTE_MISSING", None, (), ()
        ri = _safe_int(location.get("route_index"))
        if ri is None or ri < 0 or ri >= len(route_edges):
            return "ROUTE_MISSING", None, (), ()
        if route_edges[ri] != str(road_id):
            return "ROUTE_TOPOLOGY_MISMATCH", None, (), ()
        if ri + 1 >= len(route_edges):
            return "ROUTE_END", None, (), ()
        next_edge = route_edges[ri + 1]
        if not isinstance(next_edge, str) or not next_edge or next_edge.startswith(":"):
            return "INTERNAL_OR_UNKNOWN_LOCATION", None, (), ()
        from_edge = str(road_id)
        lane_candidates = tuple(c for c in self._by_lane.get(lane_id, ())
                                if c.to_edge == next_edge and c.from_edge == from_edge)
        edge_candidates = tuple(c for c in self._by_edge.get(from_edge, ())
                                if c.to_edge == next_edge)
        if lane_candidates:
            movements = {c.movement for c in lane_candidates}
            if len(movements) != 1 or next(iter(movements)) not in MOVEMENT_NAMES:
                return "AMBIGUOUS_MOVEMENT", None, lane_candidates, lane_candidates
            key = MovementKey(lane_candidates[0].tls_id, from_edge, next_edge)
            return ("RESOLVED" if len(lane_candidates) == 1
                    else "AMBIGUOUS_CONNECTION_SAME_MOVEMENT", key,
                    lane_candidates, lane_candidates)
        if edge_candidates:
            movements = {c.movement for c in edge_candidates}
            if len(movements) == 1 and next(iter(movements)) in MOVEMENT_NAMES:
                key = MovementKey(edge_candidates[0].tls_id, from_edge, next_edge)
                # A route served by another lane may be requested as a lane
                # change, but its current physical lane has no signal entry.
                return "RESOLVED_VIA_LANE_CHANGE", key, edge_candidates, ()
            return "AMBIGUOUS_MOVEMENT", None, edge_candidates, ()
        if not self._by_edge.get(from_edge):
            return "ROUTE_MISSING", None, (), ()
        return "ROUTE_TOPOLOGY_MISMATCH", None, (), ()

    @staticmethod
    def _resolve_signal(candidates: tuple[_Connection, ...] | list[_Connection],
                        lane_payload: Mapping[str, Mapping[str, Any]]) -> str | None:
        if not candidates:
            return None
        chars: list[str] = []
        for conn in candidates:
            lane = lane_payload.get(conn.from_lane_id)
            if lane is None:
                return None
            entries = lane.get("connection_signal_states") or []
            if not isinstance(entries, (list, tuple)):
                return None
            matched = []
            for entry in entries:
                if not isinstance(entry, Mapping):
                    continue
                downstream = entry.get("downstream_lane_id", entry.get("to_lane_id"))
                if downstream != conn.to_lane_id:
                    continue
                char = entry.get("signal_state", entry.get("state"))
                if not isinstance(char, str) or not char:
                    return None
                matched.append(char)
            if not matched:
                return None
            chars.extend(matched)
        return chars[0] if chars and len(set(chars)) == 1 and chars[0] in {"G", "g", "y", "r"} else None

    def _build_groups(self, filtered: list[_Vehicle]):
        by_lane: dict[str, list[_Vehicle]] = defaultdict(list)
        for item in filtered:
            if item.lane_id is not None:
                by_lane[item.lane_id].append(item)
        groups: list[tuple[_Vehicle, tuple[str, ...]]] = []
        for lane_id, values in by_lane.items():
            # Filtering happened before this sort/run construction.  Thus an
            # out-of-zone vehicle cannot split an otherwise contiguous run.
            values.sort(key=lambda v: (-(v.position_m if v.position_m is not None else -math.inf), v.vehicle_id))
            current: list[_Vehicle] = []
            current_key: MovementKey | None = None
            for item in values:
                if current and item.movement != current_key:
                    groups.append((current[0], tuple(v.vehicle_id for v in current)))
                    current = []
                if not current:
                    current_key = item.movement
                current.append(item)
            if current:
                groups.append((current[0], tuple(v.vehicle_id for v in current)))
        groups.sort(key=lambda pair: (pair[0].movement, pair[0].lane_id or "", pair[0].vehicle_id))
        return groups

    @staticmethod
    def _spatial_by_lane(filtered: list[_Vehicle]):
        result: dict[str, tuple[float | None, float | None]] = {}
        by_lane: dict[str, list[_Vehicle]] = defaultdict(list)
        for item in filtered:
            if item.lane_id:
                by_lane[item.lane_id].append(item)
        for lane, values in by_lane.items():
            halted = [v.position_m for v in values if v.position_m is not None and v.speed_mps < HALT_SPEED_MPS]
            moving = [v.position_m for v in values if v.position_m is not None and v.speed_mps >= HALT_SPEED_MPS]
            if lane not in result:
                result[lane] = (None, None)
            if halted:
                # Caller fills length-dependent quantities in _base_observation.
                tail_position = min(halted)
            else:
                tail_position = None
            gap = None
            if tail_position is not None:
                behind = [p for p in moving if p < tail_position]
                if behind:
                    gap = tail_position - max(behind)
            result[lane] = (tail_position, gap)
        return result

    def _base_observation(self, *, item: _Vehicle, group_size: int,
                          movement_demand: int, movement_halted: int,
                          movement_moving: int,
                          spatial: tuple[float | None, float | None],
                          now: float, interval: float, intersections: Mapping[str, Any],
                          last_decisions: Mapping[str, Any], road_margins: Mapping[str, Any]) -> np.ndarray:
        assert item.movement is not None
        tail_pos, gap = spatial
        length = self._lane_length.get(item.lane_id or "")
        tail_extent = length - tail_pos if length is not None and tail_pos is not None else None
        eta = None
        if length is not None and item.position_m is not None and item.speed_mps > 0.1:
            eta = max(0.0, length - item.position_m) / item.speed_mps
        speed_ratio = (max(0.0, item.speed_mps) / item.allowed_speed_mps
                       if item.native_valid and item.allowed_speed_mps is not None else None)
        demo = self._demo_by_tls.get(item.movement.tls_id)
        intersection = intersections.get(demo, {}) if demo is not None else {}
        if not isinstance(intersection, Mapping):
            intersection = {}
        age_value = intersection.get("stage_elapsed", intersection.get("phase_age_s", 0.0))
        age = _finite_number(age_value, f"{demo} phase age", default=0.0)
        margin_value = road_margins.get(demo, 0.0) if demo is not None else 0.0
        margin = _finite_number(margin_value, f"{demo} IPPO margin", default=0.0)
        last_value = last_decisions.get(demo) if demo is not None else None
        last = _decision_time(last_value, f"{demo} last decision")
        cooldown = max(0.0, (last + interval - now) / interval) if last is not None else 0.0
        remaining = float(np.clip((HORIZON_S - now) / HORIZON_S, 0.0, 1.0))
        signal = item.signal_mode
        signal_vec = [0.0] * 5
        signal_vec[SIGNAL_INDEX.get(signal, 18) - 14] = 1.0
        # The actual direction one-hot comes from the canonical movement label,
        # carried by the connection matching this key.
        movement_name = self._movement_name(item)
        turns = [1.0 if movement_name == name else 0.0 for name in ("left", "right", "through")]
        tls_one_hot = [1.0 if demo == d else 0.0 for d in DEMO_IDS]
        base = np.asarray([
            _clip_ratio(eta, 20.0, 3.0),
            _clip_ratio(float(group_size), 10.0),
            _clip_ratio(float(movement_demand), 20.0),
            _clip_ratio(float(movement_halted), 20.0),
            _clip_ratio(float(movement_moving), 20.0),
            _clip_ratio(tail_extent, 250.0),
            _clip_ratio(gap, 100.0),
            float(np.clip(speed_ratio if speed_ratio is not None else 0.0, 0.0, 1.0)),
            *turns,
            _clip_ratio(age, 20.0, 3.0),
            float(margin or 0.0),
            float(np.clip(cooldown, 0.0, 1.0)),
            *signal_vec,
            *([remaining, 1.0 if eta is not None else 0.0,
               1.0 if item.native_valid else 0.0]),
            *tls_one_hot,
        ], dtype=np.float32)
        if base.shape != (BASE_VEHICLE_DIM,) or not np.isfinite(base).all():
            raise ValueError("base observation is malformed or non-finite")
        return base

    def _movement_name(self, item: _Vehicle) -> str:
        for conn in item.route_candidates:
            if conn.key == item.movement:
                return conn.movement
        return ""

    def _lane_targets(self, item: _Vehicle):
        targets: list[int | None] = [None] * LANE_SLOTS
        target_ids: list[str | None] = [None] * LANE_SLOTS
        mask = np.zeros(LANE_SLOTS, dtype=np.bool_)
        mask[0] = True
        if item.speed_mps < LANE_CHANGE_SPEED_MPS or item.movement is None or not item.lane_id:
            return mask, tuple(targets), tuple(target_ids)
        edge = item.from_edge or self._lane_edge.get(item.lane_id)
        full = sorted(self._edge_lanes.get(edge or "", {item.lane_id}), key=self._absolute_lane_order)
        serving = self._serving_lanes.get(item.movement, set())
        options = []
        for lane_id in full:
            if lane_id == item.lane_id or lane_id not in serving:
                continue
            if not self._lane_allowed(lane_id, item.vehicle_class):
                continue
            absolute = self._lane_index.get(lane_id)
            if absolute is None:
                absolute = full.index(lane_id)
            if absolute < 0:
                continue
            # Native lane indices must be one-to-one within the complete edge
            # inventory; never compress them after vClass filtering.
            conflicts = [candidate for candidate in full
                         if candidate != lane_id and self._lane_index.get(candidate) == absolute]
            if conflicts:
                raise ValueError(f"duplicate native lane index {absolute} on edge {edge}")
            options.append((absolute, lane_id))
        for slot, (absolute, lane_id) in enumerate(options[:LANE_SLOTS - 1], start=1):
            mask[slot] = True
            targets[slot] = int(absolute)
            target_ids[slot] = lane_id
        return mask, tuple(targets), tuple(target_ids)

    def _absolute_lane_order(self, lane_id: str):
        return (self._lane_index.get(lane_id) if self._lane_index.get(lane_id) is not None else 10**9,
                lane_id)

    def _lane_allowed(self, lane_id: str, vehicle_class: str) -> bool:
        row = self._lane_vclass.get(lane_id)
        if row is None:
            return True
        if isinstance(row, Mapping):
            allowed = row.get("allowed", row.get("allow", ()))
            disallowed = row.get("disallowed", row.get("disallow", ()))
        elif isinstance(row, (tuple, list)) and len(row) == 2:
            allowed, disallowed = row
        else:
            return True
        if isinstance(allowed, str):
            allowed = tuple(allowed.split())
        if isinstance(disallowed, str):
            disallowed = tuple(disallowed.split())
        allowed = tuple(allowed or ())
        disallowed = tuple(disallowed or ())
        if vehicle_class in disallowed or "all" in disallowed:
            return False
        return not allowed or "all" in allowed or vehicle_class in allowed

    @staticmethod
    def _vehicle_class(vehicle: Mapping[str, Any]) -> str:
        raw = vehicle.get(
            "v_class",
            vehicle.get("vclass", vehicle.get("vehicle_class", vehicle.get("type_id", "passenger"))),
        )
        value = str(raw).lower()
        # Official traffic profiles map electric_bicycle and
        # official_electric_bicycle to SUMO vClass=bicycle.
        if "bicycle" in value or "bike" in value:
            return "bicycle"
        if "bus" in value:
            return "bus"
        if "truck" in value or "trailer" in value:
            return "truck"
        return "passenger"

    def _movement_vectors(self, leaders: Mapping[str, Leader]):
        by_movement: dict[MovementKey, list[Leader]] = defaultdict(list)
        for leader in leaders.values():
            by_movement[leader.movement].append(leader)
        result = {}
        for movement, rows in by_movement.items():
            tls_demo = self._demo_by_tls.get(movement.tls_id)
            tls_one = np.asarray([1.0 if tls_demo == d else 0.0 for d in DEMO_IDS], dtype=np.float32)
            first22 = np.stack([r.base_obs[:22] for r in rows], axis=0)
            result[movement] = np.concatenate((
                np.asarray([min(len(rows) / 20.0, 1.0)], dtype=np.float32),
                first22.mean(axis=0).astype(np.float32),
                first22.max(axis=0).astype(np.float32),
                tls_one,
            )).astype(np.float32)
            if result[movement].shape != (MOVEMENT_DIM,) or not np.isfinite(result[movement]).all():
                raise ValueError("movement observation is malformed or non-finite")
        return result

    def _context(self, road_observations: Mapping[str, Any], leaders: Mapping[str, Leader]) -> np.ndarray:
        by_demo: dict[str, list[np.ndarray]] = defaultdict(list)
        for leader in leaders.values():
            demo = self._demo_by_tls.get(leader.movement.tls_id)
            if demo is not None:
                by_demo[demo].append(np.concatenate((leader.base_obs[:14], [0.0, 0.0])))
        blocks = []
        for demo in DEMO_IDS:
            road = road_observations.get(demo)
            # A missing Road row is allowed and contributes zeros.  Once a row
            # is supplied, shape and finiteness are part of the frozen IPPO
            # contract and must fail loudly rather than becoming silent zeros.
            road_arr = (_copy_array(road, (ROAD_OBS_DIM,), f"{demo} road observation")
                        if road is not None else np.zeros(ROAD_OBS_DIM, dtype=np.float32))
            tokens = by_demo.get(demo, [])
            if tokens:
                matrix = np.stack(tokens, axis=0).astype(np.float32)
                pool = np.concatenate((
                    np.asarray([len(tokens)], dtype=np.float32),
                    matrix.mean(axis=0),
                    matrix.max(axis=0),
                )).astype(np.float32)
            else:
                pool = np.zeros(33, dtype=np.float32)
            blocks.append(np.concatenate((road_arr, pool)))
        context = np.concatenate(blocks).astype(np.float32)
        if context.shape != (CONTEXT_DIM,) or not np.isfinite(context).all():
            raise ValueError("context is malformed or non-finite")
        return context


def _safe_int(value: Any) -> int | None:
    if isinstance(value, bool):
        return None
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    if not math.isfinite(number) or number != int(number):
        return None
    return int(number)


def _decision_time(value: Any, label: str) -> float | None:
    """Normalize IPPO decision timestamps, including its -inf sentinel."""
    if value is None:
        return None
    try:
        number = float(value)
    except (TypeError, ValueError) as exc:
        raise ValueError(f"{label} must be numeric") from exc
    if number == -math.inf:
        return None
    if not math.isfinite(number):
        raise ValueError(f"{label} must be finite")
    return number


def _connection_sort_key(conn: _Connection):
    return (conn.tls_id, conn.from_edge, conn.to_edge, conn.from_lane_id,
            conn.to_lane_id, conn.link_index if conn.link_index is not None else 10**9)


def _safe_connection_length(canonical: Mapping[str, Any], conn: _Connection) -> float | None:
    for entry in canonical.values():
        if not isinstance(entry, Mapping):
            continue
        rows = entry.get("connections") or []
        if not rows:
            atoms = entry.get("connection_service_atoms") or {}
            rows = list(atoms.values()) if isinstance(atoms, Mapping) else []
        for row in rows:
            if not isinstance(row, Mapping):
                continue
            if row.get("from_lane_id", row.get("from_lane")) != conn.from_lane_id:
                continue
            value = row.get("from_lane_length")
            if value is not None:
                return _finite_number(value, f"lane {conn.from_lane_id} length")
    return None
