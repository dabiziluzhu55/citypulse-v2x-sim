"""Enumerate legal disturbance scenarios from catalog + YAML (no SUMO run)."""

from __future__ import annotations

import hashlib
import itertools
import random
from typing import Any, Iterable, Mapping, Sequence

from simulation.sumo.engine.events import (
    AccidentEvent,
    LaneClosureEvent,
    MajorEventClosingEvent,
    MajorEventOpeningEvent,
    SpeedLimitEvent,
)
from simulation.sumo.engine.session import SimulationCatalog

from .catalog import (
    all_intersection_lanes,
    incoming_lanes,
    list_scopes,
    resolve_scope,
)
from .schema import EventSpec, ScenarioSpec


class IllegalEventError(ValueError):
    pass


EVENT_TYPES = (
    "lane_closure",
    "speed_limit",
    "accident",
    "major_event_opening",
    "major_event_closing",
)


def _as_tuple(values: Iterable[Any]) -> tuple[Any, ...]:
    return tuple(values)


def _severity_axis(event_type: str, severity_cfg: Mapping[str, Any]) -> tuple[dict[str, Any], ...]:
    raw = dict(severity_cfg.get(event_type) or {})
    if event_type == "speed_limit":
        speeds = raw.get("target_speed_mps") or raw.get("max_speed") or [5.0]
        return tuple({"target_speed_mps": float(item)} for item in speeds)
    if event_type in {"major_event_opening", "major_event_closing"}:
        counts = raw.get("vehicle_count") or [20]
        return tuple({"vehicle_count": int(item)} for item in counts)
    if event_type == "accident":
        ratios = raw.get("position_ratio") or [0.5]
        return tuple({"position_ratio": float(item)} for item in ratios)
    if event_type == "lane_closure":
        counts = raw.get("lane_count") or [1]
        return tuple({"lane_count": int(item)} for item in counts)
    return ({},)


def _canonical_group_id(
    *,
    period: str,
    scope: str,
    seed: int,
    event_type: str,
    start: float,
    duration: float,
    severity: Mapping[str, Any],
) -> str:
    payload = {
        "period": period,
        "scope": scope,
        "seed": int(seed),
        "event_type": event_type,
        "start": float(start),
        "duration": float(duration),
        "severity": dict(severity),
    }
    blob = repr(sorted(payload.items())).encode("utf-8")
    return hashlib.sha256(blob).hexdigest()[:16]


def _scenario_id(index: int) -> str:
    return f"scenario_{index:06d}"


def _choose_intersection(
    rng: random.Random,
    intersection_ids: Sequence[str],
) -> str:
    if not intersection_ids:
        raise IllegalEventError("Scope has no intersections.")
    return str(rng.choice(list(intersection_ids)))


def _choose_incoming_lanes(
    rng: random.Random,
    catalog: SimulationCatalog,
    intersection_id: str,
    count: int,
) -> tuple[str, ...]:
    lanes = incoming_lanes(catalog, intersection_id)
    if not lanes:
        raise IllegalEventError(f"No catalog lanes for {intersection_id}")
    count = max(1, min(int(count), len(lanes)))
    picked = rng.sample(lanes, count)
    return tuple(lane.lane_id for lane in picked)


def _choose_incoming_lane(
    rng: random.Random,
    catalog: SimulationCatalog,
    intersection_id: str,
) -> str:
    return _choose_incoming_lanes(rng, catalog, intersection_id, 1)[0]


def _validate_speed_limit(
    catalog: SimulationCatalog,
    intersection_id: str,
    lane_ids: Sequence[str],
    max_speed: float,
) -> None:
    baselines = {
        lane.lane_id: float(lane.max_speed)
        for lane in all_intersection_lanes(catalog, intersection_id)
    }
    for lane_id in lane_ids:
        baseline = baselines.get(lane_id)
        if baseline is None:
            raise IllegalEventError(f"Unknown lane {lane_id} at {intersection_id}")
        if float(max_speed) + 1e-9 >= baseline:
            raise IllegalEventError(
                f"speed_limit {max_speed} is not below catalog max_speed {baseline} for {lane_id}"
            )


def build_event_spec(
    *,
    catalog: SimulationCatalog,
    event_type: str,
    start_seconds: float,
    duration_seconds: float,
    intersection_ids: Sequence[str],
    severity: Mapping[str, Any],
    rng: random.Random,
) -> EventSpec:
    if event_type not in EVENT_TYPES:
        raise IllegalEventError(f"Unsupported event_type={event_type!r}")
    end_seconds = float(start_seconds) + float(duration_seconds)
    if end_seconds <= start_seconds:
        raise IllegalEventError("Event duration must be positive.")
    intersection_id = _choose_intersection(rng, intersection_ids)
    if event_type == "lane_closure":
        lane_ids = _choose_incoming_lanes(
            rng, catalog, intersection_id, int(severity.get("lane_count", 1))
        )
        return EventSpec(
            event_type=event_type,
            start_seconds=float(start_seconds),
            end_seconds=end_seconds,
            intersection_id=intersection_id,
            lane_ids=lane_ids,
            severity=dict(severity),
        )
    if event_type == "speed_limit":
        lane_ids = _choose_incoming_lanes(rng, catalog, intersection_id, 1)
        max_speed = float(severity.get("target_speed_mps", 5.0))
        _validate_speed_limit(catalog, intersection_id, lane_ids, max_speed)
        return EventSpec(
            event_type=event_type,
            start_seconds=float(start_seconds),
            end_seconds=end_seconds,
            intersection_id=intersection_id,
            lane_ids=lane_ids,
            max_speed=max_speed,
            severity=dict(severity),
        )
    if event_type == "accident":
        lane_id = _choose_incoming_lane(rng, catalog, intersection_id)
        ratio = float(severity.get("position_ratio", 0.5))
        if not 0.0 <= ratio <= 1.0:
            raise IllegalEventError("accident position_ratio must be in [0, 1]")
        return EventSpec(
            event_type=event_type,
            start_seconds=float(start_seconds),
            end_seconds=end_seconds,
            intersection_id=intersection_id,
            lane_id=lane_id,
            position_ratio=ratio,
            severity=dict(severity),
        )
    venue_lane_id = _choose_incoming_lane(rng, catalog, intersection_id)
    vehicle_count = int(severity.get("vehicle_count", 20))
    if vehicle_count <= 0:
        raise IllegalEventError("major_event vehicle_count must be positive")
    if event_type == "major_event_opening":
        return EventSpec(
            event_type=event_type,
            start_seconds=float(start_seconds),
            end_seconds=end_seconds,
            intersection_id=intersection_id,
            venue_lane_id=venue_lane_id,
            source_lane_ids=(),
            vehicle_count=vehicle_count,
            severity=dict(severity),
        )
    return EventSpec(
        event_type=event_type,
        start_seconds=float(start_seconds),
        end_seconds=end_seconds,
        intersection_id=intersection_id,
        venue_lane_id=venue_lane_id,
        destination_lane_ids=(),
        vehicle_count=vehicle_count,
        severity=dict(severity),
    )


def event_spec_to_disturbance(event: EventSpec, event_id: str) -> Any:
    common = {
        "event_id": event_id,
        "start_seconds": float(event.start_seconds),
        "end_seconds": float(event.end_seconds),
        "ai_control_enabled": False,
    }
    if event.event_type == "lane_closure":
        return LaneClosureEvent(lane_ids=tuple(event.lane_ids), **common)
    if event.event_type == "speed_limit":
        if event.max_speed is None:
            raise IllegalEventError("speed_limit requires max_speed")
        return SpeedLimitEvent(
            lane_ids=tuple(event.lane_ids),
            max_speed=float(event.max_speed),
            **common,
        )
    if event.event_type == "accident":
        if not event.lane_id:
            raise IllegalEventError("accident requires lane_id")
        return AccidentEvent(
            lane_id=str(event.lane_id),
            position_ratio=float(event.position_ratio or 0.5),
            **common,
        )
    if event.event_type == "major_event_opening":
        return MajorEventOpeningEvent(
            venue_lane_id=str(event.venue_lane_id),
            vehicle_count=int(event.vehicle_count or 0),
            source_lane_ids=tuple(event.source_lane_ids),
            vehicle_type_id=event.vehicle_type_id,
            **common,
        )
    if event.event_type == "major_event_closing":
        return MajorEventClosingEvent(
            venue_lane_id=str(event.venue_lane_id),
            vehicle_count=int(event.vehicle_count or 0),
            destination_lane_ids=tuple(event.destination_lane_ids),
            vehicle_type_id=event.vehicle_type_id,
            **common,
        )
    raise IllegalEventError(f"Unsupported event_type={event.event_type!r}")


def generate_scenarios(
    config: Mapping[str, Any],
    catalog: SimulationCatalog,
    *,
    profile: str | None = None,
    limit: int | None = None,
) -> list[ScenarioSpec]:
    sim = dict(config.get("simulation") or {})
    grid = dict(config.get("grid") or {})
    if profile == "smoke":
        smoke = dict(config.get("smoke") or {})
        grid = {**grid, **{k: v for k, v in smoke.items() if k != "severity"}}
        if "severity" in smoke:
            grid["severity"] = smoke["severity"]
        for key in (
            "duration_seconds",
            "step_length",
            "snapshot_interval_seconds",
            "decision_interval",
        ):
            if key in smoke:
                sim[key] = smoke[key]
    periods = _as_tuple(grid.get("periods") or ())
    scopes = _as_tuple(grid.get("scopes") or ())
    seeds = tuple(int(item) for item in grid.get("seeds") or ())
    event_types = _as_tuple(grid.get("event_types") or ())
    starts = tuple(float(item) for item in grid.get("event_start_seconds") or ())
    durations = tuple(float(item) for item in grid.get("event_duration_seconds") or ())
    severity_cfg = dict(grid.get("severity") or {})
    duration_seconds = float(sim.get("duration_seconds", 300))
    legal_scopes = set(list_scopes(catalog)) | {"xiongan_20", "east_dense", "west_dense", "global"}
    scenarios: list[ScenarioSpec] = []
    index = 1
    for period, scope, seed, event_type, start, event_duration in itertools.product(
        periods, scopes, seeds, event_types, starts, durations
    ):
        if scope not in legal_scopes:
            raise IllegalEventError(f"Unknown scope {scope!r}")
        preset_id, traffic_scope, intersection_ids = resolve_scope(str(scope))
        if period not in catalog.intersections[intersection_ids[0]].periods:
            continue
        if float(start) + float(event_duration) > duration_seconds + 1e-9:
            continue
        required_horizon = float(
            sim.get("required_post_event_horizon_s")
            or grid.get("required_post_event_horizon_s")
            or 0.0
        )
        event_end = float(start) + float(event_duration)
        if event_end > duration_seconds - required_horizon + 1e-9:
            continue
        for severity in _severity_axis(str(event_type), severity_cfg):
            group_id = _canonical_group_id(
                period=str(period),
                scope=str(scope),
                seed=int(seed),
                event_type=str(event_type),
                start=float(start),
                duration=float(event_duration),
                severity=severity,
            )
            rng = random.Random(
                f"{config.get('dataset_version')}|{group_id}|{seed}"
            )
            try:
                event = build_event_spec(
                    catalog=catalog,
                    event_type=str(event_type),
                    start_seconds=float(start),
                    duration_seconds=float(event_duration),
                    intersection_ids=intersection_ids,
                    severity=severity,
                    rng=rng,
                )
            except IllegalEventError:
                continue
            scenarios.append(
                ScenarioSpec(
                    scenario_id=_scenario_id(index),
                    scenario_group_id=group_id,
                    period=str(period),
                    scope=str(scope),
                    scenario_preset_id=preset_id,
                    scenario_scope=traffic_scope,
                    seed=int(seed),
                    intersection_ids=tuple(intersection_ids),
                    duration_seconds=duration_seconds,
                    step_length=float(sim.get("step_length", 0.1)),
                    decision_interval=float(sim.get("decision_interval", 5.0)),
                    snapshot_interval_seconds=float(
                        sim.get("snapshot_interval_seconds", 1.0)
                    ),
                    event=event,
                    dataset_version=str(config.get("dataset_version") or "traffic_qwen_sft_v1"),
                )
            )
            index += 1
            if limit is not None and len(scenarios) >= int(limit):
                return scenarios
    return scenarios


def plan_summary(
    scenarios: Sequence[ScenarioSpec],
    control_modes: Sequence[str],
) -> dict[str, Any]:
    from collections import Counter

    event_counts = Counter(item.event.event_type for item in scenarios)
    period_counts = Counter(item.period for item in scenarios)
    scope_counts = Counter(item.scope for item in scenarios)
    n_scenarios = len(scenarios)
    n_modes = len(tuple(control_modes))
    return {
        "n_scenarios": n_scenarios,
        "event_type_counts": dict(event_counts),
        "period_counts": dict(period_counts),
        "scope_counts": dict(scope_counts),
        "n_control_modes": n_modes,
        "control_modes": list(control_modes),
        "estimated_episodes": n_scenarios * n_modes,
        "estimated_algorithm_runs": n_scenarios * n_modes,
    }
