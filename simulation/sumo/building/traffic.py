"""Validated official traffic-demand configuration types."""

from __future__ import annotations

import json
import math
from dataclasses import dataclass
from pathlib import Path
from types import MappingProxyType
from typing import Any, Dict, Mapping, Tuple


class TrafficDemandError(ValueError):
    """Raised when official traffic data is incomplete or inconsistent."""


class TrafficGenerationPolicyError(ValueError):
    """Raised when the route-generation policy is incomplete or invalid."""


@dataclass(frozen=True)
class ProfileRoutePolicy:
    candidate_mode: str
    maximum_pair_distance_m: float | None
    maximum_local_distance_m: float | None
    minimum_pair_distance_m: float | None
    minimum_long_share: float | None
    maximum_long_share: float | None
    fleet_share_tolerance: float


@dataclass(frozen=True)
class PeriodLoadBaseline:
    vehicle_count: int
    freeflow_vehicle_seconds: float


@dataclass(frozen=True)
class TrafficGenerationPolicy:
    short_route_max_m: float
    long_route_min_m: float
    overall_long_share_max: float
    maximum_vehicle_count_multiplier: float
    maximum_freeflow_vehicle_seconds_ratio: float
    mix_calibration_rounds: int
    selection_mode: str
    profiles: Mapping[str, ProfileRoutePolicy]
    baselines: Mapping[str, PeriodLoadBaseline]


@dataclass(frozen=True)
class ApproachDemandMapping:
    official_name: str
    label: str
    sumo_approach: str
    movements: Mapping[str, str]


@dataclass(frozen=True)
class DemandInterval:
    start: int
    end: int
    volumes: Mapping[str, Mapping[str, int]]

    @property
    def total(self) -> int:
        return sum(sum(values.values()) for values in self.volumes.values())


@dataclass(frozen=True)
class RouteSplit:
    to_edge: str
    weight: int


@dataclass(frozen=True)
class RoutePath:
    edges: Tuple[str, ...]
    weight: int


@dataclass(frozen=True)
class RouteEndpointExtensions:
    upstream: Mapping[str, str]
    downstream: Mapping[str, str]

    def __post_init__(self) -> None:
        object.__setattr__(self, "upstream", MappingProxyType(dict(self.upstream)))
        object.__setattr__(self, "downstream", MappingProxyType(dict(self.downstream)))


@dataclass(frozen=True)
class DemandPeriod:
    period_id: str
    label: str
    program_id: str
    start: int
    end: int
    intervals: Tuple[DemandInterval, ...]
    totals: Mapping[str, int]
    route_splits: Mapping[str, Mapping[str, Tuple[RouteSplit, ...]]]
    route_overrides: Mapping[str, Mapping[str, Tuple[RoutePath, ...]]]

    @property
    def duration(self) -> int:
        return self.end - self.start


@dataclass(frozen=True)
class IntersectionDemand:
    intersection_id: str
    approaches: Mapping[str, ApproachDemandMapping]
    periods: Mapping[str, DemandPeriod]
    route_endpoint_extensions: RouteEndpointExtensions


@dataclass(frozen=True)
class VehicleMix:
    basis: str
    shares: Mapping[str, float]


@dataclass(frozen=True)
class TrafficDemandConfiguration:
    unit: str
    interval_seconds: int
    vehicle_mix: VehicleMix
    od_zones: Mapping[str, Tuple[str, ...]]
    intersections: Mapping[str, IntersectionDemand]


def parse_clock(value: str) -> int:
    parts = value.split(":")
    if len(parts) != 3:
        raise TrafficDemandError(f"Invalid time {value!r}; expected HH:MM:SS.")
    try:
        hour, minute, second = (int(part) for part in parts)
    except ValueError as exc:
        raise TrafficDemandError(f"Invalid time {value!r}; expected HH:MM:SS.") from exc
    if not 0 <= hour <= 23 or not 0 <= minute <= 59 or not 0 <= second <= 59:
        raise TrafficDemandError(f"Invalid clock time {value!r}.")
    return hour * 3600 + minute * 60 + second


def _read_json(path: Path) -> Dict[str, Any]:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError as exc:
        raise TrafficDemandError(f"Traffic demand file not found: {path}") from exc
    except json.JSONDecodeError as exc:
        raise TrafficDemandError(f"Invalid JSON in {path}: {exc}") from exc


def _finite_number(value: object, context: str, *, positive: bool = False) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise TrafficGenerationPolicyError(f"{context} must be a number.")
    result = float(value)
    if not math.isfinite(result) or (positive and result <= 0):
        qualifier = "positive and finite" if positive else "finite"
        raise TrafficGenerationPolicyError(f"{context} must be {qualifier}.")
    return result


def _optional_distance(value: object, context: str) -> float | None:
    if value is None:
        return None
    return _finite_number(value, context, positive=True) * 1000.0


def _optional_share(value: object, context: str) -> float | None:
    if value is None:
        return None
    result = _finite_number(value, context)
    if not 0 <= result <= 1:
        raise TrafficGenerationPolicyError(f"{context} must be between 0 and 1.")
    return result


def _volume(value: object, context: str) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or value < 0:
        raise TrafficDemandError(f"{context}: volume must be a non-negative integer.")
    return value


def _parse_route_endpoint_extensions(
    intersection_id: str,
    raw: Mapping[str, Any],
) -> RouteEndpointExtensions:
    raw_extensions = raw.get("route_endpoint_extensions", {})
    if not isinstance(raw_extensions, dict):
        raise TrafficDemandError(
            f"{intersection_id}: route_endpoint_extensions must be an object."
        )
    unknown_directions = set(raw_extensions) - {"upstream", "downstream"}
    if unknown_directions:
        raise TrafficDemandError(
            f"{intersection_id}: route_endpoint_extensions has unknown directions "
            f"{sorted(unknown_directions)}."
        )

    parsed = {}
    for direction in ("upstream", "downstream"):
        raw_mapping = raw_extensions.get(direction, {})
        if not isinstance(raw_mapping, dict):
            raise TrafficDemandError(
                f"{intersection_id}: route_endpoint_extensions/{direction} "
                "must be an object."
            )
        mapping = {}
        for near_edge, far_edge in raw_mapping.items():
            if (
                not isinstance(near_edge, str)
                or not near_edge.strip()
                or not isinstance(far_edge, str)
                or not far_edge.strip()
            ):
                raise TrafficDemandError(
                    f"{intersection_id}: route_endpoint_extensions/{direction} "
                    "edge IDs must be non-empty strings."
                )
            if near_edge == far_edge:
                raise TrafficDemandError(
                    f"{intersection_id}: route_endpoint_extensions/{direction} "
                    f"cannot map edge {near_edge!r} to itself."
                )
            mapping[near_edge] = far_edge
        parsed[direction] = mapping
    return RouteEndpointExtensions(
        upstream=parsed["upstream"],
        downstream=parsed["downstream"],
    )


def _parse_intersection(
    intersection_id: str,
    raw: Mapping[str, Any],
    interval_seconds: int,
) -> IntersectionDemand:
    approaches = {}
    for official_name, item in raw.get("approaches", {}).items():
        movements = {
            str(name): str(sumo_name)
            for name, sumo_name in item.get("movements", {}).items()
        }
        if not movements:
            raise TrafficDemandError(
                f"{intersection_id}/{official_name}: no movements are mapped."
            )
        approaches[str(official_name)] = ApproachDemandMapping(
            official_name=str(official_name),
            label=str(item.get("label", official_name)),
            sumo_approach=str(item.get("sumo_approach", "")),
            movements=movements,
        )
    if not approaches or any(not item.sumo_approach for item in approaches.values()):
        raise TrafficDemandError(f"{intersection_id}: approach mapping is incomplete.")
    shared_sumo_approaches = (
        len({item.sumo_approach for item in approaches.values()}) != len(approaches)
    )
    if shared_sumo_approaches and raw.get("allow_shared_sumo_approaches") is not True:
        raise TrafficDemandError(f"{intersection_id}: SUMO approaches must be unique.")

    route_endpoint_extensions = _parse_route_endpoint_extensions(
        intersection_id, raw
    )
    periods = {}
    for raw_period in raw.get("periods", []):
        period_id = str(raw_period["period_id"])
        if period_id in periods:
            raise TrafficDemandError(f"{intersection_id}: duplicate period {period_id!r}.")
        time_range = raw_period.get("time_range", {})
        start = parse_clock(str(time_range.get("start", "")))
        end = parse_clock(str(time_range.get("end", "")))
        if end <= start:
            raise TrafficDemandError(f"{intersection_id}/{period_id}: invalid time range.")
        intervals = []
        for index, raw_interval in enumerate(raw_period.get("intervals", [])):
            interval_start = parse_clock(str(raw_interval.get("start", "")))
            interval_end = parse_clock(str(raw_interval.get("end", "")))
            if interval_end - interval_start != interval_seconds:
                raise TrafficDemandError(
                    f"{intersection_id}/{period_id}/interval {index}: expected "
                    f"{interval_seconds} seconds."
                )
            raw_volumes = raw_interval.get("volumes", {})
            if set(raw_volumes) != set(approaches):
                raise TrafficDemandError(
                    f"{intersection_id}/{period_id}/interval {index}: approaches differ "
                    "from the declared mapping."
                )
            volumes = {}
            for approach_name, approach in approaches.items():
                movement_values = raw_volumes[approach_name]
                if set(movement_values) != set(approach.movements):
                    raise TrafficDemandError(
                        f"{intersection_id}/{period_id}/interval {index}/{approach_name}: "
                        "movements differ from the declared mapping."
                    )
                volumes[approach_name] = {
                    movement: _volume(
                        value,
                        f"{intersection_id}/{period_id}/interval {index}/"
                        f"{approach_name}/{movement}",
                    )
                    for movement, value in movement_values.items()
                }
            intervals.append(DemandInterval(interval_start, interval_end, volumes))
        if not intervals:
            raise TrafficDemandError(f"{intersection_id}/{period_id}: no intervals.")
        if intervals[0].start != start or intervals[-1].end != end:
            raise TrafficDemandError(
                f"{intersection_id}/{period_id}: intervals do not cover the time range."
            )
        for first, second in zip(intervals, intervals[1:]):
            if first.end != second.start:
                raise TrafficDemandError(
                    f"{intersection_id}/{period_id}: intervals are not contiguous."
                )

        computed_totals = {
            approach_name: sum(
                sum(interval.volumes[approach_name].values()) for interval in intervals
            )
            for approach_name in approaches
        }
        computed_totals["all"] = sum(computed_totals.values())
        expected_totals = {
            str(name): _volume(value, f"{intersection_id}/{period_id}/total/{name}")
            for name, value in raw_period.get("expected_totals", {}).items()
        }
        if expected_totals != computed_totals:
            raise TrafficDemandError(
                f"{intersection_id}/{period_id}: declared totals {expected_totals} "
                f"do not match interval totals {computed_totals}."
            )
        program_id = str(raw_period.get("program_id", ""))
        if not program_id:
            raise TrafficDemandError(f"{intersection_id}/{period_id}: no program_id.")
        route_splits = {}
        for approach_name, raw_movements in raw_period.get("route_splits", {}).items():
            if approach_name not in approaches:
                raise TrafficDemandError(
                    f"{intersection_id}/{period_id}: route split uses unknown "
                    f"approach {approach_name!r}."
                )
            movement_splits = {}
            for movement, raw_splits in raw_movements.items():
                if movement not in approaches[approach_name].movements:
                    raise TrafficDemandError(
                        f"{intersection_id}/{period_id}/{approach_name}: route split "
                        f"uses unknown movement {movement!r}."
                    )
                splits = tuple(
                    RouteSplit(
                        to_edge=str(item.get("to_edge", "")),
                        weight=_volume(
                            item.get("weight"),
                            f"{intersection_id}/{period_id}/{approach_name}/"
                            f"{movement}/route split {index}",
                        ),
                    )
                    for index, item in enumerate(raw_splits)
                )
                if len(splits) < 2:
                    raise TrafficDemandError(
                        f"{intersection_id}/{period_id}/{approach_name}/{movement}: "
                        "a route split needs at least two targets."
                    )
                if any(not item.to_edge or item.weight == 0 for item in splits):
                    raise TrafficDemandError(
                        f"{intersection_id}/{period_id}/{approach_name}/{movement}: "
                        "route split targets and weights must be non-empty and positive."
                    )
                if len({item.to_edge for item in splits}) != len(splits):
                    raise TrafficDemandError(
                        f"{intersection_id}/{period_id}/{approach_name}/{movement}: "
                        "route split targets must be unique."
                    )
                movement_splits[str(movement)] = splits
            route_splits[str(approach_name)] = movement_splits
        route_overrides = {}
        for approach_name, raw_movements in raw_period.get(
            "route_overrides", {}
        ).items():
            if approach_name not in approaches:
                raise TrafficDemandError(
                    f"{intersection_id}/{period_id}: route override uses unknown "
                    f"approach {approach_name!r}."
                )
            movement_routes = {}
            for movement, raw_routes in raw_movements.items():
                if movement not in approaches[approach_name].movements:
                    raise TrafficDemandError(
                        f"{intersection_id}/{period_id}/{approach_name}: route "
                        f"override uses unknown movement {movement!r}."
                    )
                if (
                    approach_name in route_splits
                    and movement in route_splits[approach_name]
                ):
                    raise TrafficDemandError(
                        f"{intersection_id}/{period_id}/{approach_name}/{movement}: "
                        "use either route_splits or route_overrides, not both."
                    )
                routes = tuple(
                    RoutePath(
                        edges=tuple(str(edge) for edge in item.get("edges", ())),
                        weight=_volume(
                            item.get("weight", 1),
                            f"{intersection_id}/{period_id}/{approach_name}/"
                            f"{movement}/route override {index}",
                        ),
                    )
                    for index, item in enumerate(raw_routes)
                )
                if not routes:
                    raise TrafficDemandError(
                        f"{intersection_id}/{period_id}/{approach_name}/{movement}: "
                        "a route override needs at least one route."
                    )
                if any(len(item.edges) < 2 or item.weight == 0 for item in routes):
                    raise TrafficDemandError(
                        f"{intersection_id}/{period_id}/{approach_name}/{movement}: "
                        "route override edges need at least two edges and positive weights."
                    )
                if len({item.edges for item in routes}) != len(routes):
                    raise TrafficDemandError(
                        f"{intersection_id}/{period_id}/{approach_name}/{movement}: "
                        "route override paths must be unique."
                    )
                movement_routes[str(movement)] = routes
            route_overrides[str(approach_name)] = movement_routes
        periods[period_id] = DemandPeriod(
            period_id=period_id,
            label=str(raw_period.get("label", period_id)),
            program_id=program_id,
            start=start,
            end=end,
            intervals=tuple(intervals),
            totals=computed_totals,
            route_splits=route_splits,
            route_overrides=route_overrides,
        )
    if not periods:
        raise TrafficDemandError(f"{intersection_id}: no demand periods.")

    return IntersectionDemand(
        intersection_id=intersection_id,
        approaches=approaches,
        periods=periods,
        route_endpoint_extensions=route_endpoint_extensions,
    )


def load_traffic_demands(path: Path) -> TrafficDemandConfiguration:
    raw = _read_json(path)
    if int(raw.get("schema_version", 0)) != 2:
        raise TrafficDemandError("official_traffic_demands.json must use schema_version 2.")
    if str(raw.get("unit", "")) != "pcu":
        raise TrafficDemandError("Traffic demand unit must be 'pcu'.")
    interval_seconds = int(raw.get("interval_seconds", 0))
    if interval_seconds <= 0:
        raise TrafficDemandError("interval_seconds must be positive.")
    raw_mix = raw.get("vehicle_mix", {})
    if str(raw_mix.get("basis", "")) != "vehicle_count":
        raise TrafficDemandError("vehicle_mix.basis must be 'vehicle_count'.")
    shares = {}
    for profile_id, value in raw_mix.get("shares", {}).items():
        if isinstance(value, bool) or not isinstance(value, (int, float)):
            raise TrafficDemandError(
                f"vehicle_mix.shares/{profile_id} must be a positive number."
            )
        share = float(value)
        if not share > 0:
            raise TrafficDemandError(
                f"vehicle_mix.shares/{profile_id} must be a positive number."
            )
        shares[str(profile_id)] = share
    if not shares or abs(sum(shares.values()) - 1.0) > 1e-9:
        raise TrafficDemandError("vehicle_mix shares must sum to 1.0.")
    intersections = {
        str(intersection_id): _parse_intersection(
            str(intersection_id), item, interval_seconds
        )
        for intersection_id, item in raw.get("intersections", {}).items()
    }
    if not intersections:
        raise TrafficDemandError("No official traffic demands are configured.")
    raw_od_zones = raw.get("od_zones")
    if raw_od_zones is None:
        # Compatibility for synthetic and legacy schema-v2 demand files.
        od_zones = {
            f"zone_{index}": (intersection_id,)
            for index, intersection_id in enumerate(intersections, start=1)
        }
    else:
        if not isinstance(raw_od_zones, Mapping) or not raw_od_zones:
            raise TrafficDemandError("od_zones must be a non-empty object.")
        od_zones = {}
        assigned_intersections: Dict[str, str] = {}
        for raw_zone_id, raw_intersection_ids in raw_od_zones.items():
            zone_id = str(raw_zone_id).strip()
            if not zone_id:
                raise TrafficDemandError("od_zones contains an empty zone ID.")
            if not isinstance(raw_intersection_ids, list) or not raw_intersection_ids:
                raise TrafficDemandError(
                    f"od_zones/{zone_id} must be a non-empty array of intersection IDs."
                )
            intersection_ids = tuple(str(item) for item in raw_intersection_ids)
            if any(not item for item in intersection_ids):
                raise TrafficDemandError(
                    f"od_zones/{zone_id} contains an empty intersection ID."
                )
            if len(set(intersection_ids)) != len(intersection_ids):
                raise TrafficDemandError(
                    f"od_zones/{zone_id} contains duplicate intersection IDs."
                )
            for intersection_id in intersection_ids:
                if intersection_id not in intersections:
                    raise TrafficDemandError(
                        f"od_zones/{zone_id} references unknown intersection "
                        f"{intersection_id!r}."
                    )
                existing = assigned_intersections.get(intersection_id)
                if existing is not None:
                    raise TrafficDemandError(
                        f"Intersection {intersection_id!r} belongs to multiple OD "
                        f"zones: {existing!r} and {zone_id!r}."
                    )
                assigned_intersections[intersection_id] = zone_id
            od_zones[zone_id] = intersection_ids
        missing_zone_assignments = set(intersections) - set(assigned_intersections)
        if missing_zone_assignments:
            raise TrafficDemandError(
                "OD zones do not cover configured intersections: "
                f"{sorted(missing_zone_assignments)}"
            )
    for direction in ("upstream", "downstream"):
        configured: Dict[str, Tuple[str, str]] = {}
        for intersection_id, demand in intersections.items():
            mapping = getattr(demand.route_endpoint_extensions, direction)
            for near_edge, far_edge in mapping.items():
                existing = configured.get(near_edge)
                if existing is not None and existing[1] != far_edge:
                    raise TrafficDemandError(
                        f"Conflicting {direction} route endpoint extension for "
                        f"{near_edge!r}: {existing[0]} maps to {existing[1]!r}, "
                        f"but {intersection_id} maps to {far_edge!r}."
                    )
                configured[near_edge] = (intersection_id, far_edge)
    return TrafficDemandConfiguration(
        unit="pcu",
        interval_seconds=interval_seconds,
        vehicle_mix=VehicleMix(basis="vehicle_count", shares=shares),
        od_zones=od_zones,
        intersections=intersections,
    )


def load_traffic_generation_policy(path: Path) -> TrafficGenerationPolicy:
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError as exc:
        raise TrafficGenerationPolicyError(
            f"Traffic generation policy not found: {path}"
        ) from exc
    except json.JSONDecodeError as exc:
        raise TrafficGenerationPolicyError(
            f"Invalid traffic generation policy {path}: {exc}"
        ) from exc
    if not isinstance(raw, dict):
        raise TrafficGenerationPolicyError(
            "Traffic generation policy root must be an object."
        )
    if raw.get("schema_version") != 1:
        raise TrafficGenerationPolicyError(
            "Traffic generation policy schema_version must be 1."
        )

    distance = raw.get("distance_classes", {})
    short_route_max_m = _finite_number(
        distance.get("short_max_km"), "distance_classes.short_max_km", positive=True
    ) * 1000.0
    long_route_min_m = _finite_number(
        distance.get("long_min_km"), "distance_classes.long_min_km", positive=True
    ) * 1000.0
    if short_route_max_m >= long_route_min_m:
        raise TrafficGenerationPolicyError(
            "short_max_km must be smaller than long_min_km."
        )

    raw_profiles = raw.get("profiles", {})
    if not isinstance(raw_profiles, dict) or not raw_profiles:
        raise TrafficGenerationPolicyError("profiles must be a non-empty object.")
    profiles = {}
    allowed_modes = {"local_short", "local_and_near_pair", "long_pair"}
    for profile_id, item in raw_profiles.items():
        if not isinstance(item, dict):
            raise TrafficGenerationPolicyError(
                f"profiles.{profile_id} must be an object."
            )
        mode = str(item.get("candidate_mode", ""))
        if mode not in allowed_modes:
            raise TrafficGenerationPolicyError(
                f"profiles.{profile_id}.candidate_mode must be one of "
                f"{sorted(allowed_modes)}."
            )
        tolerance = _finite_number(
            item.get("fleet_share_tolerance"),
            f"profiles.{profile_id}.fleet_share_tolerance",
        )
        if not 0 <= tolerance <= 1:
            raise TrafficGenerationPolicyError(
                f"profiles.{profile_id}.fleet_share_tolerance must be between 0 and 1."
            )
        profiles[str(profile_id)] = ProfileRoutePolicy(
            candidate_mode=mode,
            maximum_pair_distance_m=_optional_distance(
                item.get("maximum_pair_distance_km"),
                f"profiles.{profile_id}.maximum_pair_distance_km",
            ),
            maximum_local_distance_m=_optional_distance(
                item.get("maximum_local_distance_km"),
                f"profiles.{profile_id}.maximum_local_distance_km",
            ),
            minimum_pair_distance_m=_optional_distance(
                item.get("minimum_pair_distance_km"),
                f"profiles.{profile_id}.minimum_pair_distance_km",
            ),
            minimum_long_share=_optional_share(
                item.get("minimum_long_share"),
                f"profiles.{profile_id}.minimum_long_share",
            ),
            maximum_long_share=_optional_share(
                item.get("maximum_long_share"),
                f"profiles.{profile_id}.maximum_long_share",
            ),
            fleet_share_tolerance=tolerance,
        )
        parsed = profiles[str(profile_id)]
        required_distance = {
            "local_short": parsed.maximum_local_distance_m,
            "local_and_near_pair": parsed.maximum_pair_distance_m,
            "long_pair": parsed.minimum_pair_distance_m,
        }[mode]
        if required_distance is None:
            required_key = {
                "local_short": "maximum_local_distance_km",
                "local_and_near_pair": "maximum_pair_distance_km",
                "long_pair": "minimum_pair_distance_km",
            }[mode]
            raise TrafficGenerationPolicyError(
                f"profiles.{profile_id}.{required_key} is required for "
                f"candidate_mode {mode!r}."
            )
        if (
            parsed.minimum_long_share is not None
            and parsed.maximum_long_share is not None
            and parsed.minimum_long_share > parsed.maximum_long_share
        ):
            raise TrafficGenerationPolicyError(
                f"profiles.{profile_id}.minimum_long_share cannot exceed "
                "maximum_long_share."
            )

    raw_baselines = raw.get("load_baselines", {})
    if not isinstance(raw_baselines, dict) or not raw_baselines:
        raise TrafficGenerationPolicyError("load_baselines must be a non-empty object.")
    baselines = {}
    for period_id, item in raw_baselines.items():
        if not isinstance(item, dict):
            raise TrafficGenerationPolicyError(
                f"load_baselines.{period_id} must be an object."
            )
        vehicle_count = item.get("vehicle_count")
        if (
            isinstance(vehicle_count, bool)
            or not isinstance(vehicle_count, int)
            or vehicle_count <= 0
        ):
            raise TrafficGenerationPolicyError(
                f"load_baselines.{period_id}.vehicle_count must be a positive integer."
            )
        baselines[str(period_id)] = PeriodLoadBaseline(
            vehicle_count=vehicle_count,
            freeflow_vehicle_seconds=_finite_number(
                item.get("freeflow_vehicle_seconds"),
                f"load_baselines.{period_id}.freeflow_vehicle_seconds",
                positive=True,
            ),
        )

    guardrails = raw.get("load_guardrails", {})
    mix_calibration_rounds = raw.get("mix_calibration_rounds")
    if (
        isinstance(mix_calibration_rounds, bool)
        or not isinstance(mix_calibration_rounds, int)
        or not 1 <= mix_calibration_rounds <= 2
    ):
        raise TrafficGenerationPolicyError(
            "mix_calibration_rounds must be an integer between 1 and 2."
        )
    selection_mode = str(raw.get("selection_mode", ""))
    if selection_mode != "best_effort":
        raise TrafficGenerationPolicyError("selection_mode must be 'best_effort'.")
    overall_long_share_max = _optional_share(
        raw.get("overall_long_share_max"), "overall_long_share_max"
    )
    if overall_long_share_max is None:
        raise TrafficGenerationPolicyError("overall_long_share_max is required.")
    return TrafficGenerationPolicy(
        short_route_max_m=short_route_max_m,
        long_route_min_m=long_route_min_m,
        overall_long_share_max=overall_long_share_max,
        maximum_vehicle_count_multiplier=_finite_number(
            guardrails.get("maximum_vehicle_count_multiplier"),
            "load_guardrails.maximum_vehicle_count_multiplier",
            positive=True,
        ),
        maximum_freeflow_vehicle_seconds_ratio=_finite_number(
            guardrails.get("maximum_freeflow_vehicle_seconds_ratio"),
            "load_guardrails.maximum_freeflow_vehicle_seconds_ratio",
            positive=True,
        ),
        mix_calibration_rounds=mix_calibration_rounds,
        selection_mode=selection_mode,
        profiles=profiles,
        baselines=baselines,
    )
