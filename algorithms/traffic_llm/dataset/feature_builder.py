"""Stable, versioned Traffic-Qwen observations. No invented snapshot fields."""

from __future__ import annotations

from typing import Any, Mapping, Sequence

from .catalog import expand_scope
from .schema import OBSERVATION_VERSION, ScenarioSpec


LANE_FIELDS = (
    "vehicle_count",
    "halting_count",
    "mean_speed",
    "occupancy",
    "queue_length_m",
    "lane_length_m",
    "role",
)


def _lane_payload(lane: Mapping[str, Any]) -> dict[str, Any]:
    return {key: lane.get(key) for key in LANE_FIELDS if key in lane}


def event_payload(spec: ScenarioSpec, status: str | None = None) -> dict[str, Any]:
    event = spec.event
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
    allowed = set(spec.intersection_ids)
    if len(allowed) <= 6:
        controlled = tuple(sorted(allowed))
    else:
        seeds = (
            (spec.event.intersection_id,)
            if spec.event.intersection_id in allowed
            else tuple(sorted(allowed)[:1])
        )
        controlled = expand_scope(seeds, scope_hops, neighbors, allowed)
        if not controlled:
            controlled = tuple(sorted(allowed))
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
