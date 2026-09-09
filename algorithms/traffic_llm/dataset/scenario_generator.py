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
from simulation.sumo.building.artifacts import DEFAULT_GENERATED_DIR


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


def _lane_id(edge_id: str, lane_index: int) -> str:
    return f"{edge_id}_{int(lane_index)}"


def load_closure_safety_index(generated_dir: Any | None = None) -> dict[str, Any]:
    """Static SUMO connectivity index used to reject unroutable lane closures."""

    import json
    from pathlib import Path

    root = Path(generated_dir) if generated_dir is not None else DEFAULT_GENERATED_DIR
    payload = json.loads((root / "manifests" / "tls_manifest.json").read_text(encoding="utf-8"))
    lanes_by_edge: dict[str, set[str]] = {}
    incoming_by_intersection: dict[str, set[str]] = {}
    incoming_by_approach: dict[tuple[str, str], set[str]] = {}
    incoming_by_edge: dict[tuple[str, str], set[str]] = {}
    connections_by_intersection: dict[str, list[tuple[str, int, str, int]]] = {}
    synthetic_intersections: set[str] = set()
    for intersection_id, item in (payload.get("intersections") or {}).items():
        iid = str(intersection_id)
        for approach, lanes in (item.get("incoming_lanes") or {}).items():
            for raw_lane in lanes or ():
                lane_id = str(raw_lane)
                incoming_by_intersection.setdefault(iid, set()).add(lane_id)
                incoming_by_approach.setdefault((iid, str(approach)), set()).add(lane_id)
                edge_id = lane_id.rsplit("_", 1)[0]
                incoming_by_edge.setdefault((iid, edge_id), set()).add(lane_id)
                lanes_by_edge.setdefault(edge_id, set()).add(lane_id)
        for connection in item.get("connections") or ():
            from_edge = str(connection["from_edge"])
            to_edge = str(connection["to_edge"])
            from_lane = int(connection["from_lane"])
            to_lane = int(connection["to_lane"])
            from_id = _lane_id(from_edge, from_lane)
            to_id = _lane_id(to_edge, to_lane)
            lanes_by_edge.setdefault(from_edge, set()).add(from_id)
            lanes_by_edge.setdefault(to_edge, set()).add(to_id)
            connections_by_intersection.setdefault(iid, []).append(
                (from_edge, from_lane, to_edge, to_lane)
            )
            if "missing_arm" in from_edge or "missing_arm" in to_edge:
                synthetic_intersections.add(iid)
            approach = str(connection.get("approach") or "")
            if approach:
                incoming_by_approach.setdefault((iid, approach), set()).add(from_id)
                incoming_by_intersection.setdefault(iid, set()).add(from_id)
                incoming_by_edge.setdefault((iid, from_edge), set()).add(from_id)
    return {
        "lanes_by_edge": lanes_by_edge,
        "incoming_by_intersection": incoming_by_intersection,
        "incoming_by_approach": incoming_by_approach,
        "incoming_by_edge": incoming_by_edge,
        "connections_by_intersection": connections_by_intersection,
        "synthetic_intersections": synthetic_intersections,
    }


def _od_pairs(
    connections: Sequence[tuple[str, int, str, int]],
    closed: set[str],
) -> tuple[set[tuple[str, str]], set[tuple[str, str]]]:
    original: set[tuple[str, str]] = set()
    remaining: set[tuple[str, str]] = set()
    for from_edge, from_lane, to_edge, to_lane in connections:
        pair = (from_edge, to_edge)
        original.add(pair)
        from_id = _lane_id(from_edge, from_lane)
        to_id = _lane_id(to_edge, to_lane)
        if from_id in closed or to_id in closed:
            continue
        remaining.add(pair)
    return original, remaining


def lane_closure_is_safe(
    lane_ids: Sequence[str],
    *,
    catalog: SimulationCatalog,
    intersection_id: str,
    safety: Mapping[str, Any],
) -> tuple[bool, str]:
    incoming = {lane.lane_id for lane in incoming_lanes(catalog, intersection_id)}
    incoming |= set(safety["incoming_by_intersection"].get(intersection_id) or ())
    closing = [str(item) for item in lane_ids]
    closed = set(closing)
    if not closing:
        return False, "lane_closure requires at least one lane"
    if intersection_id in set(safety.get("synthetic_intersections") or ()):
        return False, (
            f"{intersection_id} has synthetic missing_arm connections and is not safe for lane_closure"
        )
    for lane_id in closing:
        if "missing_arm" in lane_id:
            return False, f"{lane_id} is a synthetic missing_arm lane, not a safe closure target"
        if lane_id not in incoming:
            return False, f"{lane_id} is not a legal incoming lane at {intersection_id}"
        edge_id = lane_id.rsplit("_", 1)[0]
        incoming_on_edge = set(
            safety["incoming_by_edge"].get((intersection_id, edge_id)) or ()
        )
        if not incoming_on_edge:
            incoming_on_edge = {item for item in incoming if item.rsplit("_", 1)[0] == edge_id}
        remaining_incoming = incoming_on_edge - closed
        if not remaining_incoming:
            return False, f"closing {lane_id} would block the only incoming lane on edge {edge_id}"
        edge_lanes = set(safety["lanes_by_edge"].get(edge_id) or incoming_on_edge)
        if not (edge_lanes - closed):
            return False, f"closing {lane_id} would close the only lane of edge {edge_id}"
        indices: list[int] = []
        for item in edge_lanes:
            try:
                indices.append(int(item.rsplit("_", 1)[1]))
            except ValueError:
                continue
        try:
            lane_index = int(lane_id.rsplit("_", 1)[1])
        except ValueError:
            lane_index = None
        if indices and lane_index is not None and lane_index not in {min(indices), max(indices)}:
            return False, f"{lane_id} is an interior lane; closing it blocks in-edge lane changing"
        for (iid, approach), lanes in safety["incoming_by_approach"].items():
            if iid != intersection_id or lane_id not in lanes:
                continue
            if not (lanes - closed):
                return False, (
                    f"closing {lane_id} would block the only {approach} approach at {intersection_id}"
                )
    original, remaining = _od_pairs(
        safety["connections_by_intersection"].get(intersection_id) or (),
        closed,
    )
    lost = original - remaining
    if lost:
        pair = sorted(lost)[0]
        return False, f"closing {closing} would disconnect {pair[0]} -> {pair[1]}"
    return True, "ok"


def _choose_safe_closure_lanes(
    rng: random.Random,
    catalog: SimulationCatalog,
    intersection_ids: Sequence[str],
    count: int,
    safety: Mapping[str, Any],
    *,
    preferred_intersection: str | None = None,
) -> tuple[str, tuple[str, ...]]:
    ordered = list(intersection_ids)
    if preferred_intersection in ordered:
        ordered.remove(preferred_intersection)
        rng.shuffle(ordered)
        ordered = [preferred_intersection, *ordered]
    else:
        rng.shuffle(ordered)
    skip_synthetic = set(safety.get("synthetic_intersections") or ())
    for intersection_id in ordered:
        if intersection_id in skip_synthetic:
            continue
        candidates = [
            lane.lane_id
            for lane in incoming_lanes(catalog, intersection_id)
            if lane_closure_is_safe(
                [lane.lane_id],
                catalog=catalog,
                intersection_id=intersection_id,
                safety=safety,
            )[0]
        ]
        if len(candidates) < max(1, int(count)):
            continue
        picked = tuple(rng.sample(candidates, max(1, min(int(count), len(candidates)))))
        ok, _reason = lane_closure_is_safe(
            picked, catalog=catalog, intersection_id=intersection_id, safety=safety
        )
        if ok:
            return str(intersection_id), picked
    raise IllegalEventError("No routable incoming lane remains for lane_closure")


def repair_unroutable_lane_closures(
    scenarios: Sequence[ScenarioSpec],
    catalog: SimulationCatalog,
    failed_ids: set[str],
    *,
    safety: Mapping[str, Any] | None = None,
) -> list[ScenarioSpec]:
    """Rebuild only failed lane_closure events; keep successful specs unchanged."""

    index = safety or load_closure_safety_index()
    repaired: list[ScenarioSpec] = []
    for spec in scenarios:
        if spec.scenario_id not in failed_ids or spec.event.event_type != "lane_closure":
            repaired.append(spec)
            continue
        rng = random.Random(f"repair-v2|{spec.scenario_group_id}|{spec.seed}")
        try:
            intersection_id, lane_ids = _choose_safe_closure_lanes(
                rng,
                catalog,
                spec.intersection_ids,
                int((spec.event.severity or {}).get("lane_count", 1) or 1),
                index,
                preferred_intersection=spec.event.intersection_id,
            )
        except IllegalEventError as exc:
            raise IllegalEventError(
                f"cannot repair unroutable lane_closure {spec.scenario_id}: {exc}"
            ) from exc
        event = EventSpec(
            event_type="lane_closure",
            start_seconds=spec.event.start_seconds,
            end_seconds=spec.event.end_seconds,
            intersection_id=intersection_id,
            lane_ids=lane_ids,
            severity=dict(spec.event.severity),
        )
        repaired.append(
            ScenarioSpec(
                scenario_id=spec.scenario_id,
                scenario_group_id=spec.scenario_group_id,
                period=spec.period,
                scope=spec.scope,
                scenario_preset_id=spec.scenario_preset_id,
                scenario_scope=spec.scenario_scope,
                seed=spec.seed,
                intersection_ids=spec.intersection_ids,
                duration_seconds=spec.duration_seconds,
                step_length=spec.step_length,
                decision_interval=spec.decision_interval,
                snapshot_interval_seconds=spec.snapshot_interval_seconds,
                event=event,
                dataset_version=spec.dataset_version,
            )
        )
    return repaired


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
    safety: Mapping[str, Any] | None = None,
) -> EventSpec:
    if event_type not in EVENT_TYPES:
        raise IllegalEventError(f"Unsupported event_type={event_type!r}")
    end_seconds = float(start_seconds) + float(duration_seconds)
    if end_seconds <= start_seconds:
        raise IllegalEventError("Event duration must be positive.")
    intersection_id = _choose_intersection(rng, intersection_ids)
    if event_type == "lane_closure":
        safety = safety or load_closure_safety_index()
        intersection_id, lane_ids = _choose_safe_closure_lanes(
            rng,
            catalog,
            intersection_ids,
            int(severity.get("lane_count", 1)),
            safety,
            preferred_intersection=intersection_id,
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
    safety = load_closure_safety_index()
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
                    safety=safety,
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
