"""Validated official traffic-demand configuration types."""

from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Dict, Mapping, Tuple


class TrafficDemandError(ValueError):
    """Raised when official traffic data is incomplete or inconsistent."""


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
class DemandPeriod:
    period_id: str
    label: str
    program_id: str
    start: int
    end: int
    intervals: Tuple[DemandInterval, ...]
    totals: Mapping[str, int]
    route_splits: Mapping[str, Mapping[str, Tuple[RouteSplit, ...]]]

    @property
    def duration(self) -> int:
        return self.end - self.start


@dataclass(frozen=True)
class IntersectionDemand:
    intersection_id: str
    vehicle_type: str
    pcu_per_vehicle: float
    approaches: Mapping[str, ApproachDemandMapping]
    periods: Mapping[str, DemandPeriod]


@dataclass(frozen=True)
class TrafficDemandConfiguration:
    unit: str
    interval_seconds: int
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


def _volume(value: object, context: str) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or value < 0:
        raise TrafficDemandError(f"{context}: volume must be a non-negative integer.")
    return value


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
        periods[period_id] = DemandPeriod(
            period_id=period_id,
            label=str(raw_period.get("label", period_id)),
            program_id=program_id,
            start=start,
            end=end,
            intervals=tuple(intervals),
            totals=computed_totals,
            route_splits=route_splits,
        )
    if not periods:
        raise TrafficDemandError(f"{intersection_id}: no demand periods.")

    pcu_per_vehicle = float(raw.get("pcu_per_vehicle", 0))
    if pcu_per_vehicle != 1.0:
        raise TrafficDemandError(
            f"{intersection_id}: only 1.0 PCU passenger demand is currently supported."
        )
    return IntersectionDemand(
        intersection_id=intersection_id,
        vehicle_type=str(raw.get("vehicle_type", "passenger")),
        pcu_per_vehicle=pcu_per_vehicle,
        approaches=approaches,
        periods=periods,
    )


def load_traffic_demands(path: Path) -> TrafficDemandConfiguration:
    raw = _read_json(path)
    if int(raw.get("schema_version", 0)) != 1:
        raise TrafficDemandError("official_traffic_demands.json must use schema_version 1.")
    if str(raw.get("unit", "")) != "pcu":
        raise TrafficDemandError("Traffic demand unit must be 'pcu'.")
    interval_seconds = int(raw.get("interval_seconds", 0))
    if interval_seconds <= 0:
        raise TrafficDemandError("interval_seconds must be positive.")
    intersections = {
        str(intersection_id): _parse_intersection(
            str(intersection_id), item, interval_seconds
        )
        for intersection_id, item in raw.get("intersections", {}).items()
    }
    if not intersections:
        raise TrafficDemandError("No official traffic demands are configured.")
    return TrafficDemandConfiguration(
        unit="pcu",
        interval_seconds=interval_seconds,
        intersections=intersections,
    )
