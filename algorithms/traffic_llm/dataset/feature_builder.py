"""Stable, versioned Traffic-Qwen observations. No invented snapshot fields."""

from __future__ import annotations

from typing import Any, Mapping, Sequence

from .catalog import expand_scope
from .phase_service import load_phase_service_index
from .schema import OBSERVATION_VERSION, OBSERVATION_VERSION_V2, ScenarioSpec


LANE_FIELDS = (
    "vehicle_count",
    "halting_count",
    "mean_speed",
    "occupancy",
    "queue_length_m",
    "lane_length_m",
    "waiting_time",
    "role",
)

V2_INCOMING_ROLES = frozenset({"incoming", "both", ""})


def _lane_payload(lane: Mapping[str, Any]) -> dict[str, Any]:
    return {key: lane.get(key) for key in LANE_FIELDS if key in lane}


def _round(value: Any, digits: int) -> float | None:
    if value is None:
        return None
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    return round(number, digits)


def _intish(value: Any) -> int | None:
    if value is None:
        return None
    try:
        return int(round(float(value)))
    except (TypeError, ValueError):
        return None


def _first(mapping: Mapping[str, Any], *keys: str) -> Any:
    for key in keys:
        if key in mapping and mapping[key] is not None:
            return mapping[key]
    return None


def _compact_incoming_lane(lane_id: str, lane: Mapping[str, Any]) -> dict[str, Any]:
    payload: dict[str, Any] = {"id": str(lane_id)}
    veh = _intish(_first(lane, "vehicle_count", "veh"))
    halt = _intish(_first(lane, "halting_count", "halt"))
    speed = _round(_first(lane, "mean_speed", "speed"), 1)
    occ = _round(_first(lane, "occupancy", "occ"), 2)
    queue = _round(_first(lane, "queue_length_m", "queue_m"), 1)
    wait = _round(_first(lane, "waiting_time", "wait"), 1)
    if veh is not None:
        payload["veh"] = veh
    if halt is not None:
        payload["halt"] = halt
    if speed is not None:
        payload["speed"] = speed
    if occ is not None:
        payload["occ"] = occ
    if queue is not None:
        payload["queue_m"] = queue
    if wait is not None:
        payload["wait"] = wait
    return payload


def event_payload(spec: ScenarioSpec, status: str | None = None, *, compact: bool = False) -> dict[str, Any]:
    event = spec.event
    if compact:
        payload: dict[str, Any] = {
            "type": event.event_type,
            "status": status,
            "tgt": event.intersection_id,
            "t0": float(event.start_seconds),
            "t1": float(event.end_seconds),
        }
        target_lane = event.lane_id or (event.lane_ids[0] if event.lane_ids else event.venue_lane_id)
        if target_lane:
            payload["lane"] = target_lane
        if event.severity:
            payload["sev"] = dict(event.severity)
        return payload
    return {
        "type": event.event_type,
        "status": status,
        "target_intersection": event.intersection_id,
        "target_lane": event.lane_id or (event.lane_ids[0] if event.lane_ids else event.venue_lane_id),
        "lane_ids": list(event.lane_ids),
        "start_seconds": event.start_seconds,
        "end_seconds": event.end_seconds,
        "severity": dict(event.severity),
    }


def event_status_at(spec: ScenarioSpec, simulation_time: float) -> str:
    if simulation_time + 1e-9 < spec.event.start_seconds:
        return "scheduled"
    if simulation_time > spec.event.end_seconds + 1e-9:
        return "completed"
    return "active"


def _controlled_region(
    spec: ScenarioSpec,
    neighbors: Mapping[str, Sequence[str]],
    scope_hops: int,
) -> tuple[str, ...]:
    allowed = set(spec.intersection_ids)
    if len(allowed) <= 6:
        return tuple(sorted(allowed))
    seeds = (
        (spec.event.intersection_id,)
        if spec.event.intersection_id in allowed
        else tuple(sorted(allowed)[:1])
    )
    controlled = expand_scope(seeds, scope_hops, neighbors, allowed)
    return controlled or tuple(sorted(allowed))


def build_observation(
    *,
    spec: ScenarioSpec,
    simulation_time: float,
    snapshot_summary: Mapping[str, Any],
    allowed_phases: Mapping[str, Sequence[int]],
    neighbors: Mapping[str, Sequence[str]],
    scope_hops: int,
    prediction: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    controlled = _controlled_region(spec, neighbors, scope_hops)
    raw_intersections = dict(snapshot_summary.get("intersections") or {})
    intersections: dict[str, Any] = {}
    for iid in controlled:
        i_obs = raw_intersections.get(iid) or {}
        lanes_in = dict(i_obs.get("lanes") or {})
        intersections[iid] = {
            "current_phase": i_obs.get("current_phase"),
            "pending_phase": i_obs.get("pending_phase"),
            "stage": i_obs.get("stage"),
            "lanes": {
                lane_id: _lane_payload(lane) if isinstance(lane, Mapping) else {}
                for lane_id, lane in lanes_in.items()
            },
        }
    metrics = dict(snapshot_summary.get("metrics") or {})
    return {
        "observation_version": OBSERVATION_VERSION,
        "scene": {
            "period": spec.period,
            "scope": spec.scope,
            "simulation_time": float(simulation_time),
            "seed": spec.seed,
        },
        "event": event_payload(spec, event_status_at(spec, simulation_time)),
        "controlled_region": list(controlled),
        "intersections": intersections,
        "network_summary": {
            "active_vehicles": metrics.get("active_vehicles"),
            "departed_vehicles": metrics.get("departed_vehicles"),
            "arrived_vehicles": metrics.get("arrived_vehicles"),
            "halting_vehicles": metrics.get("halting_vehicles"),
            "mean_speed": metrics.get("mean_speed"),
            "hard_braking_events": metrics.get("hard_braking_events"),
        },
        "allowed_phases": {
            iid: list(allowed_phases.get(iid, ())) for iid in controlled
        },
        "prediction": prediction,
        "prediction_available": prediction is not None,
    }


def build_observation_v2(
    *,
    spec: ScenarioSpec,
    simulation_time: float,
    snapshot_summary: Mapping[str, Any],
    allowed_phases: Mapping[str, Sequence[int]],
    neighbors: Mapping[str, Sequence[str]],
    scope_hops: int,
    phase_service: Mapping[str, Mapping[str, Sequence[str]]] | None = None,
) -> dict[str, Any]:
    """Compact observation for Traffic-Qwen SFT. Does not mutate raw snapshots."""

    controlled = _controlled_region(spec, neighbors, scope_hops)
    raw_intersections = dict(snapshot_summary.get("intersections") or {})
    service_index = phase_service if phase_service is not None else load_phase_service_index()
    intersections: dict[str, Any] = {}
    allowed: dict[str, list[int]] = {}
    phase_map: dict[str, dict[str, list[str]]] = {}
    for iid in controlled:
        i_obs = raw_intersections.get(iid) or {}
        lanes_in = dict(i_obs.get("lanes") or {})
        incoming: list[dict[str, Any]] = []
        for lane_id, lane in lanes_in.items():
            if not isinstance(lane, Mapping):
                continue
            role = str(lane.get("role") or "incoming")
            if role not in V2_INCOMING_ROLES:
                continue
            incoming.append(_compact_incoming_lane(str(lane_id), lane))
        intersections[str(iid)] = {
            "ph": i_obs.get("current_phase"),
            "lanes": incoming,
        }
        allowed[str(iid)] = [int(item) for item in allowed_phases.get(iid, ())]
        raw_service = dict(service_index.get(str(iid)) or {})
        phase_map[str(iid)] = {
            str(phase): list(raw_service.get(str(phase)) or ())
            for phase in allowed[str(iid)]
        }
    metrics = dict(snapshot_summary.get("metrics") or {})
    return {
        "observation_version": OBSERVATION_VERSION_V2,
        "scene": {
            "period": spec.period,
            "scope": spec.scope,
            "t": float(simulation_time),
            "seed": spec.seed,
        },
        "event": event_payload(spec, event_status_at(spec, simulation_time), compact=True),
        "controlled_region": list(controlled),
        "ix": intersections,
        "net": {
            "veh": _intish(metrics.get("active_vehicles")),
            "halt": _intish(metrics.get("halting_vehicles")),
            "speed": _round(metrics.get("mean_speed"), 1),
        },
        "allowed_phases": allowed,
        "phase_service": phase_map,
    }
