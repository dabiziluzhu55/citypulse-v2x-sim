"""Build Observation V2 from a live SUMO snapshot for Traffic-Qwen control."""

from __future__ import annotations

from typing import Any, Mapping, Sequence

from simulation_protocol.dto import SimulationSnapshot

from traffic_llm_runtime.feature_builder import build_observation_v2
from traffic_llm_runtime.manifest import neighbor_map, tls_phase_orders
from traffic_llm_runtime.schema import EventSpec, ScenarioSpec
from traffic_llm_runtime.snapshot import compact_snapshot_summary


def _tuple_ids(value: Any) -> tuple[str, ...]:
    if isinstance(value, Sequence) and not isinstance(value, (str, bytes)):
        return tuple(str(item) for item in value if item)
    if value:
        return (str(value),)
    return ()


def event_spec_from_live(
    event: Any,
    *,
    primary_intersection: str,
) -> EventSpec:
    details = event.details if isinstance(getattr(event, "details", None), Mapping) else {}
    lane_ids = _tuple_ids(details.get("lane_ids"))
    lane_id = details.get("lane_id")
    if not lane_ids and lane_id:
        lane_ids = (str(lane_id),)
    return EventSpec(
        event_type=str(event.event_type),
        start_seconds=float(event.start_seconds),
        end_seconds=float(event.end_seconds),
        intersection_id=str(primary_intersection),
        lane_ids=lane_ids,
        lane_id=str(lane_id) if lane_id else None,
        max_speed=details.get("max_speed"),
        position_ratio=details.get("position_ratio"),
        venue_lane_id=str(details["venue_lane_id"]) if details.get("venue_lane_id") else None,
        source_lane_ids=_tuple_ids(details.get("source_lane_ids")),
        destination_lane_ids=_tuple_ids(details.get("destination_lane_ids")),
        vehicle_count=details.get("vehicle_count"),
        vehicle_type_id=str(
            details.get("vehicle_type_id") or "citypulse_event_passenger"
        ),
        severity=dict(details.get("severity") or {}),
    )


def neighbors_from_topology(topology: Any) -> dict[str, tuple[str, ...]]:
    if topology is None:
        return {}
    upstream = getattr(topology, "upstream_intersections", None) or {}
    downstream = getattr(topology, "downstream_intersections", None) or {}
    keys = set(str(item) for item in upstream) | set(str(item) for item in downstream)
    if not keys:
        return {}
    mapping: dict[str, tuple[str, ...]] = {}
    for key in keys:
        ordered = []
        seen: set[str] = set()
        for item in (*upstream.get(key, ()), *downstream.get(key, ())):
            value = str(item)
            if value in seen:
                continue
            seen.add(value)
            ordered.append(value)
        mapping[key] = tuple(ordered)
    return mapping


def build_live_observation_v2(
    snapshot: SimulationSnapshot,
    event: Any,
    *,
    primary_intersection: str,
    topology: Any = None,
    neighbors: Mapping[str, Sequence[str]] | None = None,
    allowed_phases: Mapping[str, Sequence[int]] | None = None,
    phase_service: Mapping[str, Mapping[str, Sequence[str]]] | None = None,
    scope_hops: int = 1,
    preset_id: str | None = None,
    period: str | None = None,
    seed: int | None = None,
) -> dict[str, Any]:
    """Match the 44001 Observation V2 distribution. No RAG / prediction JSON."""

    scene = preset_id or "xiongan_20"
    spec = ScenarioSpec(
        scenario_id=str(snapshot.session_id),
        scenario_group_id=str(snapshot.session_id),
        period=str(period or "morning_peak"),
        scope=str(scene),
        scenario_preset_id=str(scene),
        scenario_scope=str(scene),
        seed=int(seed or 0),
        intersection_ids=tuple(str(item) for item in snapshot.intersections),
        duration_seconds=float(snapshot.duration_seconds or 0.0),
        step_length=0.1,
        decision_interval=5.0,
        snapshot_interval_seconds=1.0,
        event=event_spec_from_live(event, primary_intersection=primary_intersection),
    )
    resolved_neighbors = dict(neighbors or neighbors_from_topology(topology))
    if not resolved_neighbors:
        try:
            resolved_neighbors = neighbor_map()
        except Exception:
            resolved_neighbors = {}
    resolved_phases = dict(allowed_phases or {})
    if not resolved_phases and topology is not None:
        raw = getattr(topology, "phase_orders", {}) or {}
        if isinstance(raw, Mapping):
            resolved_phases = {
                str(iid): tuple(int(item) for item in phases)
                for iid, phases in raw.items()
                if isinstance(phases, Sequence) and not isinstance(phases, (str, bytes))
            }
    if not resolved_phases:
        try:
            resolved_phases = tls_phase_orders()
        except Exception:
            resolved_phases = {}
    return build_observation_v2(
        spec=spec,
        simulation_time=float(snapshot.elapsed_seconds),
        snapshot_summary=compact_snapshot_summary(snapshot),
        allowed_phases=resolved_phases,
        neighbors=resolved_neighbors,
        scope_hops=int(scope_hops),
        phase_service=phase_service,
    )
