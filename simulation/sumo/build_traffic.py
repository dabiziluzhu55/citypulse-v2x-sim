"""Build globally calibrated SUMO traffic with the official routeSampler tool."""

from __future__ import annotations

import argparse
import copy
import csv
import json
import math
import os
import re
import shutil
import subprocess
import sys
import xml.etree.ElementTree as ET
from collections import defaultdict
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Callable, Mapping, Sequence, Tuple

from .artifacts import DEFAULT_GENERATED_DIR, GeneratedArtifactLayout
from .traffic import (
    ApproachDemandMapping,
    DemandPeriod,
    RoutePath,
    RouteSplit,
    TrafficDemandError,
    load_traffic_demands,
)
from .vehicle_profiles import (
    VehicleProfile,
    VehicleProfileError,
    load_vehicle_profiles,
)


PROJECT_ROOT = Path(__file__).resolve().parents[2]
SUMO_DIR = PROJECT_ROOT / "data" / "maps" / "sumo"
DEFAULT_DEMANDS = SUMO_DIR / "official_traffic_demands.json"
DEFAULT_VEHICLE_PROFILES = SUMO_DIR / "vehicle_profiles.json"
DEFAULT_OUTPUT_DIR = DEFAULT_GENERATED_DIR
DEFAULT_MANIFEST = GeneratedArtifactLayout(DEFAULT_OUTPUT_DIR).tls_manifest
ROUTE_SAMPLER_SEEDS = (42, 43, 44, 45, 46)
MINIMIZE_VEHICLES = 0.1
ROUTE_SAMPLER_REQUIRED_OPTIONS = (
    "--interval",
    "--write-flows",
    "--optimize",
    "--no-sampling",
    "--minimize-vehicles",
    "--mismatch-output",
    "--seed",
    "--attributes",
)


@dataclass(frozen=True)
class CountLocation:
    intersection_id: str
    official_approach: str
    official_movement: str
    edges: Tuple[str, ...]

    @property
    def location_id(self) -> str:
        return _safe_id(
            f"{self.intersection_id}_{self.official_approach}_"
            f"{self.official_movement}_{'_'.join(self.edges)}"
        )


@dataclass(frozen=True)
class SampledFlow:
    flow_id: str
    type_id: str
    begin: float
    end: float
    number: int
    edges: Tuple[str, ...]


@dataclass(frozen=True)
class CompiledGlobalDemand:
    periods: Mapping[str, DemandPeriod]
    program_ids: Mapping[str, Mapping[str, str]]
    locations: Mapping[Tuple[str, ...], CountLocation]
    targets: Mapping[str, Tuple[Mapping[Tuple[str, ...], int], ...]]
    cell_targets: Mapping[
        str, Tuple[Mapping[Tuple[str, str, str], int], ...]
    ]
    cell_paths: Mapping[
        str,
        Tuple[
            Mapping[Tuple[str, str, str], Mapping[Tuple[str, ...], int]], ...
        ],
    ]


def _safe_id(value: str) -> str:
    return re.sub(r"[^A-Za-z0-9_.-]+", "_", value)


def _clock(seconds: int) -> str:
    return f"{seconds // 3600:02d}:{seconds % 3600 // 60:02d}:{seconds % 60:02d}"


def _format_number(value: float) -> str:
    return f"{value:g}"


def _load_manifest(path: Path) -> Mapping[str, object]:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError as exc:
        raise TrafficDemandError(
            f"TLS manifest not found: {path}. Run simulation.sumo.build_tls first."
        ) from exc
    except json.JSONDecodeError as exc:
        raise TrafficDemandError(f"Invalid TLS manifest {path}: {exc}") from exc


def _binary(name: str) -> str:
    executable = f"{name}.exe" if os.name == "nt" else name
    candidates = []
    if os.environ.get("SUMO_HOME"):
        candidates.append(Path(os.environ["SUMO_HOME"]) / "bin" / executable)
    located = shutil.which(name)
    if located:
        candidates.append(Path(located))
    for candidate in candidates:
        if candidate.is_file():
            return str(candidate)
    raise TrafficDemandError(
        f"Cannot find SUMO binary {name!r}; set SUMO_HOME or update PATH."
    )


def _sumo_tool(name: str) -> str:
    sumo_home = os.environ.get("SUMO_HOME")
    if sumo_home:
        candidate = Path(sumo_home) / "tools" / name
        if candidate.is_file():
            return str(candidate)
    raise TrafficDemandError(
        f"Cannot find $SUMO_HOME/tools/{name}; set SUMO_HOME to the SUMO installation."
    )


def _validate_route_sampler(path: str) -> None:
    try:
        source = Path(path).read_text(encoding="utf-8")
    except (OSError, UnicodeError) as exc:
        raise TrafficDemandError(
            f"Cannot read SUMO routeSampler script at {path}: {exc}"
        ) from exc
    missing = [
        option for option in ROUTE_SAMPLER_REQUIRED_OPTIONS if option not in source
    ]
    if missing:
        raise TrafficDemandError(
            f"SUMO routeSampler at {path} does not support required options: {missing}. "
            "Install a current SUMO release and point SUMO_HOME to it."
        )


def _toolchain(overrides: Mapping[str, str] | None) -> Mapping[str, str]:
    if overrides is not None:
        missing = {"duarouter", "sumo", "routeSampler"} - set(overrides)
        if missing:
            raise TrafficDemandError(f"Tool overrides are missing: {sorted(missing)}")
        return dict(overrides)
    try:
        import numpy  # noqa: F401
        import scipy.optimize  # noqa: F401
    except ImportError as exc:
        raise TrafficDemandError(
            "routeSampler optimization needs numpy and scipy; install requirements.txt."
        ) from exc
    tools = {
        "duarouter": _binary("duarouter"),
        "sumo": _binary("sumo"),
        "routeSampler": _sumo_tool("routeSampler.py"),
    }
    _validate_route_sampler(tools["routeSampler"])
    return tools


def _run_command(
    command: Sequence[str],
    runner: Callable[..., subprocess.CompletedProcess[str]],
) -> subprocess.CompletedProcess[str]:
    try:
        return runner(
            list(command),
            check=True,
            text=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
        )
    except subprocess.CalledProcessError as exc:
        output = (exc.stdout or "").strip()
        raise TrafficDemandError(
            f"SUMO command failed ({' '.join(command)}): {output}"
        ) from exc


def _movement_route(
    intersection_id: str,
    intersection_manifest: Mapping[str, object],
    approach: ApproachDemandMapping,
    official_movement: str,
) -> Tuple[str, str]:
    sumo_movement = approach.movements[official_movement]
    matches = [
        item
        for item in intersection_manifest.get("connections", [])
        if item.get("approach") == approach.sumo_approach
        and item.get("movement") == sumo_movement
    ]
    route_pairs = sorted(
        {(str(item["from_edge"]), str(item["to_edge"])) for item in matches}
    )
    if len(route_pairs) != 1:
        raise TrafficDemandError(
            f"{intersection_id}/{approach.official_name}/{official_movement}: "
            f"expected one SUMO route for {approach.sumo_approach}/{sumo_movement}, "
            f"found {route_pairs}."
        )
    return route_pairs[0]


def _movement_routes(
    intersection_id: str,
    intersection_manifest: Mapping[str, object],
    approach: ApproachDemandMapping,
    official_movement: str,
    splits: Sequence[RouteSplit],
    overrides: Sequence[RoutePath] = (),
) -> Tuple[Tuple[Tuple[str, ...], int], ...]:
    if overrides:
        return tuple((item.edges, item.weight) for item in overrides)
    if not splits:
        return (
            (
                _movement_route(
                    intersection_id,
                    intersection_manifest,
                    approach,
                    official_movement,
                ),
                1,
            ),
        )
    sumo_movement = approach.movements[official_movement]
    route_pairs = sorted(
        {
            (str(item["from_edge"]), str(item["to_edge"]))
            for item in intersection_manifest.get("connections", [])
            if item.get("approach") == approach.sumo_approach
            and item.get("movement") == sumo_movement
        }
    )
    routes_by_target = {
        to_edge: (from_edge, to_edge) for from_edge, to_edge in route_pairs
    }
    configured_targets = {item.to_edge for item in splits}
    if len(routes_by_target) != len(route_pairs) or set(routes_by_target) != configured_targets:
        raise TrafficDemandError(
            f"{intersection_id}/{approach.official_name}/{official_movement}: "
            f"configured split targets {sorted(configured_targets)} do not match "
            f"SUMO routes {route_pairs}."
        )
    return tuple(
        (routes_by_target[item.to_edge], item.weight)
        for item in sorted(splits, key=lambda value: value.to_edge)
    )


def _allocate_route_counts(
    count: int,
    weighted_routes: Sequence[Tuple[Tuple[str, ...], int]],
) -> Tuple[int, ...]:
    total_weight = sum(weight for _, weight in weighted_routes)
    allocated = [count * weight // total_weight for _, weight in weighted_routes]
    remainders = [count * weight % total_weight for _, weight in weighted_routes]
    order = sorted(
        range(len(weighted_routes)),
        key=lambda index: (-remainders[index], weighted_routes[index][0]),
    )
    for index in order[: count - sum(allocated)]:
        allocated[index] += 1
    return tuple(allocated)


def _allocate_vehicle_mix(
    target_pcu: int,
    shares: Mapping[str, float],
    profiles: Mapping[str, VehicleProfile],
) -> Mapping[str, int]:
    """Allocate integer vehicles with a half-PCU dynamic program."""
    profile_ids = tuple(sorted(shares))
    factors = {name: int(round(profiles[name].pcu_factor * 2)) for name in profile_ids}
    target_units = target_pcu * 2
    max_units = target_units + max(factors.values())
    average_pcu = sum(shares[name] * profiles[name].pcu_factor for name in profile_ids)
    raw_total = target_pcu / average_pcu if average_pcu else 0.0
    raw_counts = {name: raw_total * shares[name] for name in profile_ids}

    states: dict[int, Tuple[Tuple[int, ...], float]] = {0: ((), 0.0)}
    for name in profile_ids:
        next_states: dict[int, Tuple[Tuple[int, ...], float]] = {}
        factor = factors[name]
        for prior_units, (prior_counts, prior_penalty) in states.items():
            for count in range((max_units - prior_units) // factor + 1):
                units = prior_units + count * factor
                penalty = prior_penalty + (
                    (count - raw_counts[name]) ** 2 / max(raw_counts[name], 1.0)
                )
                candidate = (prior_counts + (count,), penalty)
                current = next_states.get(units)
                if current is None or (penalty, candidate[0]) < (
                    current[1],
                    current[0],
                ):
                    next_states[units] = candidate
        states = next_states

    _, (counts, _) = min(
        states.items(),
        key=lambda item: (
            abs(item[0] - target_units),
            item[1][1],
            item[1][0],
        ),
    )
    return dict(zip(profile_ids, counts))


def _aligned_periods(
    requested: Sequence[str], configuration
) -> Mapping[str, DemandPeriod]:
    first = configuration.intersections[requested[0]].periods
    expected_ids = set(first)
    for intersection_id in requested[1:]:
        periods = configuration.intersections[intersection_id].periods
        if set(periods) != expected_ids:
            raise TrafficDemandError("All intersections must define the same demand periods.")
        for period_id, reference in first.items():
            candidate = periods[period_id]
            if (
                candidate.start != reference.start
                or candidate.end != reference.end
                or len(candidate.intervals) != len(reference.intervals)
                or any(
                    (a.start, a.end) != (b.start, b.end)
                    for a, b in zip(candidate.intervals, reference.intervals)
                )
            ):
                raise TrafficDemandError(
                    f"{intersection_id}/{period_id}: official intervals are not globally aligned."
                )
    return first


def _compile_global_demand(
    requested: Sequence[str],
    manifest_intersections: Mapping[str, object],
    configuration,
) -> CompiledGlobalDemand:
    periods = _aligned_periods(requested, configuration)
    locations: dict[Tuple[str, ...], CountLocation] = {}
    targets: dict[str, list[dict[Tuple[str, ...], int]]] = {
        period_id: [defaultdict(int) for _ in period.intervals]
        for period_id, period in periods.items()
    }
    cell_targets: dict[str, list[dict[Tuple[str, str, str], int]]] = {
        period_id: [dict() for _ in period.intervals]
        for period_id, period in periods.items()
    }
    cell_paths: dict[
        str,
        list[dict[Tuple[str, str, str], dict[Tuple[str, ...], int]]],
    ] = {
        period_id: [dict() for _ in period.intervals]
        for period_id, period in periods.items()
    }
    program_ids = {period_id: {} for period_id in periods}

    for intersection_id in requested:
        demand = configuration.intersections[intersection_id]
        own_manifest = manifest_intersections[intersection_id]
        available_programs = set(own_manifest.get("program_ids", []))
        for period_id, period in demand.periods.items():
            if period.program_id not in available_programs:
                raise TrafficDemandError(
                    f"{intersection_id}/{period_id}: signal program "
                    f"{period.program_id!r} is absent from the TLS manifest."
                )
            program_ids[period_id][intersection_id] = period.program_id
            routes = {}
            for official_approach, approach in demand.approaches.items():
                for official_movement in approach.movements:
                    has_positive_demand = any(
                        interval.volumes[official_approach][official_movement] > 0
                        for interval in period.intervals
                    )
                    try:
                        routes[(official_approach, official_movement)] = (
                            _movement_routes(
                                intersection_id,
                                own_manifest,
                                approach,
                                official_movement,
                                period.route_splits.get(official_approach, {}).get(
                                    official_movement, ()
                                ),
                                period.route_overrides.get(official_approach, {}).get(
                                    official_movement, ()
                                ),
                            )
                        )
                    except TrafficDemandError:
                        if has_positive_demand:
                            raise
                        sumo_movement = approach.movements[official_movement]
                        zero_paths = sorted(
                            {
                                (
                                    str(item["from_edge"]),
                                    str(item["to_edge"]),
                                )
                                for item in own_manifest.get("connections", [])
                                if item.get("approach") == approach.sumo_approach
                                and item.get("movement") == sumo_movement
                            }
                        )
                        if zero_paths:
                            routes[(official_approach, official_movement)] = tuple(
                                (edges, 1) for edges in zero_paths
                            )
                        # If no physical connection exists, the all-zero cell is
                        # naturally satisfied because no route can traverse it.
                        continue
            for interval_index, interval in enumerate(period.intervals):
                for official_approach, approach in demand.approaches.items():
                    for official_movement in approach.movements:
                        cell = (
                            intersection_id,
                            official_approach,
                            official_movement,
                        )
                        target = interval.volumes[official_approach][official_movement]
                        route_key = (official_approach, official_movement)
                        cell_targets[period_id][interval_index][cell] = target
                        if route_key not in routes:
                            if target != 0:
                                raise TrafficDemandError(
                                    f"{intersection_id}/{period_id}/{official_approach}/"
                                    f"{official_movement}: non-zero demand has no physical route."
                                )
                            cell_paths[period_id][interval_index][cell] = {}
                            continue
                        weighted_routes = routes[route_key]
                        allocated = _allocate_route_counts(target, weighted_routes)
                        own_paths: dict[Tuple[str, ...], int] = {}
                        for (edges, _), count in zip(weighted_routes, allocated):
                            edges = tuple(edges)
                            if edges not in locations:
                                locations[edges] = CountLocation(
                                    intersection_id=intersection_id,
                                    official_approach=official_approach,
                                    official_movement=official_movement,
                                    edges=edges,
                                )
                            elif locations[edges].intersection_id != intersection_id:
                                raise TrafficDemandError(
                                    f"Physical count path {edges} is shared by multiple intersections."
                                )
                            targets[period_id][interval_index][edges] += count
                            own_paths[edges] = count
                        cell_paths[period_id][interval_index][cell] = own_paths

    return CompiledGlobalDemand(
        periods=periods,
        program_ids=program_ids,
        locations=locations,
        targets={key: tuple(value) for key, value in targets.items()},
        cell_targets={key: tuple(value) for key, value in cell_targets.items()},
        cell_paths={key: tuple(value) for key, value in cell_paths.items()},
    )


def _class_targets(
    compiled: CompiledGlobalDemand,
    shares: Mapping[str, float],
    profiles: Mapping[str, VehicleProfile],
) -> Mapping[
    str, Tuple[Mapping[Tuple[str, ...], Mapping[str, int]], ...]
]:
    result = {}
    for period_id, intervals in compiled.targets.items():
        own_intervals = []
        for targets in intervals:
            own_intervals.append(
                {
                    edges: _allocate_vehicle_mix(target, shares, profiles)
                    for edges, target in targets.items()
                }
            )
        result[period_id] = tuple(own_intervals)
    return result


def _write_turn_counts(
    path: Path,
    period: DemandPeriod,
    interval_targets: Sequence[Mapping[Tuple[str, ...], Mapping[str, int]]],
    profile_id: str,
) -> None:
    root = ET.Element("data")
    for index, targets in enumerate(interval_targets):
        interval = period.intervals[index]
        interval_node = ET.SubElement(
            root,
            "interval",
            {
                "begin": str(interval.start - period.start),
                "end": str(interval.end - period.start),
            },
        )
        for edges, counts in sorted(targets.items()):
            attributes = {
                "from": edges[0],
                "to": edges[-1],
                "count": str(counts[profile_id]),
            }
            if len(edges) > 2:
                attributes["via"] = " ".join(edges[1:-1])
            ET.SubElement(interval_node, "edgeRelation", attributes)
    ET.indent(root, space="  ")
    ET.ElementTree(root).write(path, encoding="utf-8", xml_declaration=True)


def _dedupe_adjacent(edges: Sequence[str]) -> Tuple[str, ...]:
    result = []
    for edge in edges:
        if not result or result[-1] != edge:
            result.append(edge)
    return tuple(result)


def _write_candidate_trips(
    path: Path,
    profile: VehicleProfile,
    locations: Sequence[CountLocation],
) -> int:
    root = ET.Element("routes")
    candidate_type = f"candidate_{_safe_id(profile.v_class)}"
    ET.SubElement(root, "vType", {"id": candidate_type, "vClass": profile.v_class})
    forced_paths = []
    for location in locations:
        forced_paths.append((f"local_{location.location_id}", location.edges))
    for first in locations:
        for second in locations:
            if first.intersection_id == second.intersection_id:
                continue
            forced = _dedupe_adjacent(first.edges + second.edges)
            if len(set(forced)) != len(forced):
                continue
            forced_paths.append(
                (f"pair_{first.location_id}_{second.location_id}", forced)
            )
    for index, (trip_id, forced) in enumerate(forced_paths):
        attributes = {
            "id": _safe_id(trip_id),
            "type": candidate_type,
            "depart": str(index),
            "from": forced[0],
            "to": forced[-1],
        }
        if len(forced) > 2:
            attributes["via"] = " ".join(forced[1:-1])
        ET.SubElement(root, "trip", attributes)
    ET.indent(root, space="  ")
    ET.ElementTree(root).write(path, encoding="utf-8", xml_declaration=True)
    return len(forced_paths)


def _route_contains(route: Sequence[str], path: Sequence[str]) -> bool:
    width = len(path)
    return any(tuple(route[index : index + width]) == tuple(path) for index in range(len(route) - width + 1))


def _u_turn_edge_pairs(network_path: Path) -> set[Tuple[str, str]]:
    try:
        root = ET.parse(network_path).getroot()
    except (FileNotFoundError, ET.ParseError) as exc:
        raise TrafficDemandError(
            f"Cannot inspect generated SUMO network: {network_path}"
        ) from exc
    endpoints = {
        str(edge.get("id")): (str(edge.get("from")), str(edge.get("to")))
        for edge in root.findall("edge")
        if edge.get("id")
        and edge.get("from") is not None
        and edge.get("to") is not None
        and edge.get("function") != "internal"
    }
    by_endpoints: dict[Tuple[str, str], list[str]] = defaultdict(list)
    for edge_id, edge_endpoints in endpoints.items():
        by_endpoints[edge_endpoints].append(edge_id)
    return {
        (first, second)
        for first, (from_node, to_node) in endpoints.items()
        for second in by_endpoints.get((to_node, from_node), ())
    }


def _read_candidate_routes(
    path: Path,
    u_turn_pairs: set[Tuple[str, str]] | None = None,
) -> Tuple[Tuple[str, ...], ...]:
    try:
        root = ET.parse(path).getroot()
    except (FileNotFoundError, ET.ParseError) as exc:
        raise TrafficDemandError(f"Cannot read duarouter candidate output: {path}") from exc
    route_defs = {
        str(item.get("id")): tuple(str(item.get("edges", "")).split())
        for item in root.findall("route")
    }
    routes = set()
    forbidden_u_turns = u_turn_pairs or set()
    for vehicle in root.findall("vehicle"):
        route_node = vehicle.find("route")
        if route_node is not None:
            edges = tuple(str(route_node.get("edges", "")).split())
        else:
            edges = route_defs.get(str(vehicle.get("route")), ())
        has_u_turn = any(
            (first, second) in forbidden_u_turns
            for first, second in zip(edges, edges[1:])
        )
        if len(edges) >= 2 and len(set(edges)) == len(edges) and not has_u_turn:
            routes.add(edges)
    return tuple(sorted(routes))


def _write_candidate_routes(path: Path, routes: Sequence[Tuple[str, ...]]) -> None:
    root = ET.Element("routes")
    for index, edges in enumerate(routes):
        vehicle = ET.SubElement(
            root,
            "vehicle",
            {"id": f"candidate_{index:06d}", "depart": str(index)},
        )
        ET.SubElement(vehicle, "route", {"edges": " ".join(edges)})
    ET.indent(root, space="  ")
    ET.ElementTree(root).write(path, encoding="utf-8", xml_declaration=True)


def _build_candidates(
    work_dir: Path,
    network_path: Path,
    compiled: CompiledGlobalDemand,
    class_targets,
    profiles: Mapping[str, VehicleProfile],
    tools: Mapping[str, str],
    runner,
) -> Mapping[str, Path]:
    locations = tuple(sorted(compiled.locations.values(), key=lambda item: item.location_id))
    u_turn_pairs = _u_turn_edge_pairs(network_path)
    by_vclass: dict[str, list[str]] = defaultdict(list)
    for profile_id in sorted(profiles):
        by_vclass[profiles[profile_id].v_class].append(profile_id)
    result = {}
    for vclass, profile_ids in sorted(by_vclass.items()):
        profile = profiles[profile_ids[0]]
        trips_path = work_dir / f"candidate_{_safe_id(vclass)}.trips.xml"
        raw_path = work_dir / f"candidate_{_safe_id(vclass)}.raw.rou.xml"
        clean_path = work_dir / f"candidate_{_safe_id(vclass)}.rou.xml"
        _write_candidate_trips(trips_path, profile, locations)
        _run_command(
            [
                tools["duarouter"],
                "--net-file",
                str(network_path),
                "--route-files",
                str(trips_path),
                "--output-file",
                str(raw_path),
                "--ignore-errors",
                "true",
                "--remove-loops",
                "true",
                "--no-step-log",
                "true",
            ],
            runner,
        )
        routes = _read_candidate_routes(raw_path, u_turn_pairs)
        if not routes:
            raise TrafficDemandError(f"duarouter produced no {vclass} candidate routes.")
        _write_candidate_routes(clean_path, routes)
        for profile_id in profile_ids:
            required = {
                edges
                for intervals in class_targets.values()
                for interval in intervals
                for edges, counts in interval.items()
                if counts[profile_id] > 0
            }
            missing = [
                edges
                for edges in sorted(required)
                if not any(_route_contains(route, edges) for route in routes)
            ]
            if missing:
                raise TrafficDemandError(
                    f"{profile_id} candidate routes do not cover count paths: {missing[:10]}"
                )
            result[profile_id] = clean_path
    return result


def _read_sampled_flows(path: Path) -> Tuple[SampledFlow, ...]:
    try:
        root = ET.parse(path).getroot()
    except (FileNotFoundError, ET.ParseError) as exc:
        raise TrafficDemandError(f"Cannot read routeSampler output: {path}") from exc
    result = []
    for flow in root.findall("flow"):
        route = flow.find("route")
        if route is None:
            raise TrafficDemandError(f"routeSampler flow {flow.get('id')} has no inline route.")
        result.append(
            SampledFlow(
                flow_id=str(flow.get("id")),
                type_id=str(flow.get("type")),
                begin=float(flow.get("begin", "0")),
                end=float(flow.get("end", "0")),
                number=int(flow.get("number", "0")),
                edges=tuple(str(route.get("edges", "")).split()),
            )
        )
    return tuple(result)


def _run_route_sampler(
    tools: Mapping[str, str],
    runner,
    candidate_path: Path,
    counts_path: Path,
    output_path: Path,
    mismatch_path: Path,
    profile_id: str,
    period: DemandPeriod,
    interval_seconds: int,
    seed: int,
) -> Tuple[SampledFlow, ...]:
    type_id = f"official_{_safe_id(profile_id)}"
    _run_command(
        [
            sys.executable,
            tools["routeSampler"],
            "--route-files",
            str(candidate_path),
            "--turn-files",
            str(counts_path),
            "--output-file",
            str(output_path),
            "--mismatch-output",
            str(mismatch_path),
            "--begin",
            "0",
            "--end",
            str(period.duration),
            "--interval",
            str(interval_seconds),
            "--write-flows",
            "number",
            "--optimize",
            "full",
            "--no-sampling",
            "--minimize-vehicles",
            str(MINIMIZE_VEHICLES),
            "--seed",
            str(seed),
            "--prefix",
            f"{_safe_id(profile_id)}_",
            "--attributes",
            f'type="{type_id}" departLane="best" departSpeed="max"',
        ],
        runner,
    )
    if not mismatch_path.is_file():
        raise TrafficDemandError(
            f"routeSampler did not write the requested mismatch output: {mismatch_path}"
        )
    return _read_sampled_flows(output_path)


def _flow_interval(flow: SampledFlow, period: DemandPeriod) -> int:
    for index, interval in enumerate(period.intervals):
        end = interval.end - period.start
        if abs(flow.end - end) < 1e-6:
            return index
    raise TrafficDemandError(
        f"Flow {flow.flow_id} ends outside official 15-minute intervals: {flow.end}."
    )


def _geh(target: float, actual: float) -> float:
    target_hourly = target * 4
    actual_hourly = actual * 4
    if target_hourly + actual_hourly == 0:
        return 0.0
    return math.sqrt(
        2 * (actual_hourly - target_hourly) ** 2 / (actual_hourly + target_hourly)
    )


def _quality_report(
    period_id: str,
    compiled: CompiledGlobalDemand,
    flows: Sequence[SampledFlow],
    profiles: Mapping[str, VehicleProfile],
    class_targets: Sequence[
        Mapping[Tuple[str, ...], Mapping[str, int]]
    ],
) -> Mapping[str, object]:
    period = compiled.periods[period_id]
    profile_by_type = {
        f"official_{_safe_id(profile_id)}": profile
        for profile_id, profile in profiles.items()
    }
    achieved = [defaultdict(float) for _ in period.intervals]
    achieved_by_profile = [
        defaultdict(lambda: defaultdict(float)) for _ in period.intervals
    ]
    type_counts: dict[str, int] = {profile_id: 0 for profile_id in profiles}
    route_intersections = {
        edges: location.intersection_id
        for edges, location in compiled.locations.items()
    }
    multi_count = 0
    route_intersection_distribution: dict[int, int] = defaultdict(int)
    multi_covered_paths: set[Tuple[str, ...]] = set()
    for flow in flows:
        if flow.type_id not in profile_by_type:
            raise TrafficDemandError(
                f"Flow {flow.flow_id} references unknown type {flow.type_id!r}."
            )
        interval_index = _flow_interval(flow, period)
        profile = profile_by_type[flow.type_id]
        type_counts[profile.profile_id] += flow.number
        crossed = set()
        crossed_paths = set()
        for edges, intersection_id in route_intersections.items():
            if _route_contains(flow.edges, edges):
                contribution = flow.number * profile.pcu_factor
                achieved[interval_index][edges] += contribution
                achieved_by_profile[interval_index][edges][
                    profile.profile_id
                ] += contribution
                crossed.add(intersection_id)
                crossed_paths.add(edges)
        route_intersection_distribution[len(crossed)] += flow.number
        if len(crossed) >= 2:
            multi_count += flow.number
            if flow.number > 0:
                multi_covered_paths.update(crossed_paths)

    rows = []
    cell_pass = True
    nonzero_geh = []
    group_totals: dict[Tuple[int, str], list[float]] = defaultdict(lambda: [0.0, 0.0])
    for interval_index, cells in enumerate(compiled.cell_targets[period_id]):
        for cell, target in sorted(cells.items()):
            actual = 0.0
            profile_contributions = {profile_id: 0.0 for profile_id in profiles}
            for edges, allocated in compiled.cell_paths[period_id][interval_index][cell].items():
                location_target = compiled.targets[period_id][interval_index][edges]
                if location_target > 0:
                    ratio = allocated / location_target
                    actual += achieved[interval_index][edges] * ratio
                    for profile_id in profiles:
                        profile_contributions[profile_id] += (
                            achieved_by_profile[interval_index][edges][profile_id]
                            * ratio
                        )
                elif achieved[interval_index][edges] != 0:
                    actual += achieved[interval_index][edges]
                    for profile_id in profiles:
                        profile_contributions[profile_id] += achieved_by_profile[
                            interval_index
                        ][edges][profile_id]
            error = actual - target
            tolerance = 0.0 if target == 0 else max(3.0, target * 0.05)
            passed = abs(error) <= tolerance + 1e-9
            cell_pass = cell_pass and passed
            geh = _geh(target, actual)
            if target > 0:
                nonzero_geh.append(geh)
            group_totals[(interval_index, cell[0])][0] += target
            group_totals[(interval_index, cell[0])][1] += actual
            rows.append(
                {
                    "interval_index": interval_index,
                    "begin": period.intervals[interval_index].start - period.start,
                    "end": period.intervals[interval_index].end - period.start,
                    "intersection_id": cell[0],
                    "official_approach": cell[1],
                    "official_movement": cell[2],
                    "target_pcu": target,
                    "actual_pcu": actual,
                    "error_pcu": error,
                    "relative_error": None if target == 0 else error / target,
                    "geh": geh,
                    "vehicle_contributions_pcu": profile_contributions,
                    "passed": passed,
                }
            )
    intersection_totals = []
    totals_pass = True
    for (interval_index, intersection_id), (target, actual) in sorted(group_totals.items()):
        relative_error = 0.0 if target == 0 else abs(actual - target) / target
        passed = relative_error <= 0.03 + 1e-9
        totals_pass = totals_pass and passed
        intersection_totals.append(
            {
                "interval_index": interval_index,
                "intersection_id": intersection_id,
                "target_pcu": target,
                "actual_pcu": actual,
                "relative_error": relative_error,
                "passed": passed,
            }
        )
    geh_ok_percentage = (
        100.0 * sum(value < 5 for value in nonzero_geh) / len(nonzero_geh)
        if nonzero_geh
        else 100.0
    )
    total_vehicles = sum(type_counts.values())
    total_abs_error = sum(abs(float(row["error_pcu"])) for row in rows)
    requires_multi_intersection = (
        len({item.intersection_id for item in compiled.locations.values()}) > 1
    )
    allocation_rows = []
    for interval_index, targets in enumerate(class_targets):
        for edges, counts in sorted(targets.items()):
            target = compiled.targets[period_id][interval_index][edges]
            allocated_pcu = sum(
                counts[profile_id] * profiles[profile_id].pcu_factor
                for profile_id in profiles
            )
            location = compiled.locations[edges]
            allocation_rows.append(
                {
                    "interval_index": interval_index,
                    "location_id": location.location_id,
                    "intersection_id": location.intersection_id,
                    "official_approach": location.official_approach,
                    "official_movement": location.official_movement,
                    "edges": list(edges),
                    "target_pcu": target,
                    "vehicle_targets": dict(sorted(counts.items())),
                    "allocated_pcu": allocated_pcu,
                    "rounding_error_pcu": allocated_pcu - target,
                }
            )
    uncovered = [
        {
            "location_id": location.location_id,
            "intersection_id": location.intersection_id,
            "official_approach": location.official_approach,
            "official_movement": location.official_movement,
            "edges": list(edges),
        }
        for edges, location in sorted(
            compiled.locations.items(), key=lambda item: item[1].location_id
        )
        if edges not in multi_covered_paths
    ]
    passed = (
        cell_pass
        and totals_pass
        and geh_ok_percentage + 1e-9 >= 90.0
        and (multi_count > 0 or not requires_multi_intersection)
    )
    return {
        "schema_version": 1,
        "period_id": period_id,
        "passed": passed,
        "target_observation_pcu": sum(int(row["target_pcu"]) for row in rows),
        "actual_observation_pcu": sum(float(row["actual_pcu"]) for row in rows),
        "total_absolute_error_pcu": total_abs_error,
        "geh_below_5_percentage": geh_ok_percentage,
        "sampled_vehicle_count": total_vehicles,
        "multi_intersection_vehicle_count": multi_count,
        "multi_intersection_vehicle_share": (
            multi_count / total_vehicles if total_vehicles else 0.0
        ),
        "multi_intersection_required": requires_multi_intersection,
        "vehicle_counts": dict(sorted(type_counts.items())),
        "vehicle_shares": {
            key: value / total_vehicles if total_vehicles else 0.0
            for key, value in sorted(type_counts.items())
        },
        "vehicle_target_allocations": allocation_rows,
        "vehicle_target_total_absolute_rounding_error_pcu": sum(
            abs(float(item["rounding_error_pcu"])) for item in allocation_rows
        ),
        "route_intersection_count_distribution": {
            str(count): vehicle_count
            for count, vehicle_count in sorted(route_intersection_distribution.items())
        },
        "locations_without_multi_intersection_route": uncovered,
        "cells": rows,
        "intersection_interval_totals": intersection_totals,
    }


def _write_quality_report(
    json_path: Path,
    csv_path: Path,
    report: Mapping[str, object],
) -> None:
    json_path.write_text(
        json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    rows = list(report["cells"])
    with csv_path.open("w", encoding="utf-8-sig", newline="") as stream:
        writer = csv.DictWriter(stream, fieldnames=list(rows[0]) if rows else [])
        if rows:
            writer.writeheader()
            writer.writerows(rows)


def _write_routes(
    path: Path,
    profiles: Mapping[str, VehicleProfile],
    flows: Sequence[SampledFlow],
) -> None:
    root = ET.Element("routes")
    for profile_id in sorted(profiles):
        type_id = f"official_{_safe_id(profile_id)}"
        ET.SubElement(root, "vType", profiles[profile_id].sumo_attributes(type_id))
    for flow in sorted(flows, key=lambda item: (item.begin, item.flow_id)):
        node = ET.SubElement(
            root,
            "flow",
            {
                "id": flow.flow_id,
                "type": flow.type_id,
                "begin": _format_number(flow.begin),
                "end": _format_number(flow.end),
                "number": str(flow.number),
                "departLane": "best",
                "departSpeed": "max",
            },
        )
        ET.SubElement(node, "route", {"edges": " ".join(flow.edges)})
    ET.indent(root, space="  ")
    ET.ElementTree(root).write(path, encoding="utf-8", xml_declaration=True)


def _write_program_additional(
    source_path: Path,
    target_path: Path,
    program_ids: Mapping[str, str],
) -> None:
    try:
        source_root = ET.parse(source_path).getroot()
    except (FileNotFoundError, ET.ParseError) as exc:
        raise TrafficDemandError(f"Cannot read generated TLS programs: {source_path}") from exc
    expected = set(program_ids.values())
    found = set()
    root = ET.Element("additional")
    for child in source_root:
        if child.tag != "tlLogic":
            root.append(copy.deepcopy(child))
        elif child.get("programID") in expected:
            root.append(copy.deepcopy(child))
            found.add(str(child.get("programID")))
    if found != expected:
        raise TrafficDemandError(
            f"Signal programs are missing from {source_path}: {sorted(expected - found)}"
        )
    ET.indent(root, space="  ")
    ET.ElementTree(root).write(target_path, encoding="utf-8", xml_declaration=True)


def _write_sumocfg(
    path: Path,
    network_path: Path,
    route_filename: str,
    additional_filename: str,
    period: DemandPeriod,
) -> int:
    simulation_end = period.duration + 300
    root = ET.Element("configuration")
    input_node = ET.SubElement(root, "input")
    relative_network = os.path.relpath(network_path, path.parent).replace(os.sep, "/")
    ET.SubElement(input_node, "net-file", {"value": relative_network})
    ET.SubElement(input_node, "route-files", {"value": route_filename})
    ET.SubElement(input_node, "additional-files", {"value": additional_filename})
    time_node = ET.SubElement(root, "time")
    ET.SubElement(time_node, "begin", {"value": "0"})
    ET.SubElement(time_node, "end", {"value": str(simulation_end)})
    ET.SubElement(time_node, "step-length", {"value": "0.05"})
    processing = ET.SubElement(root, "processing")
    ET.SubElement(processing, "time-to-teleport", {"value": "-1"})
    ET.indent(root, space="  ")
    ET.ElementTree(root).write(path, encoding="utf-8", xml_declaration=True)
    return simulation_end


def _origin_metadata(intersection_manifest, demand) -> Mapping[str, object]:
    return {
        official_name: {
            "label": approach.label,
            "sumo_approach": approach.sumo_approach,
            "lane_ids": sorted(
                {
                    f"{connection['from_edge']}_{connection['from_lane']}"
                    for connection in intersection_manifest["connections"]
                    if connection["approach"] == approach.sumo_approach
                }
            ),
        }
        for official_name, approach in demand.approaches.items()
    }


def build_traffic_scenarios(
    tls_manifest: Mapping[str, object],
    demand_path: Path = DEFAULT_DEMANDS,
    vehicle_profile_path: Path = DEFAULT_VEHICLE_PROFILES,
    output_dir: Path = DEFAULT_OUTPUT_DIR,
    intersection_ids: Sequence[str] | None = None,
    *,
    command_runner: Callable[..., subprocess.CompletedProcess[str]] = subprocess.run,
    tool_paths: Mapping[str, str] | None = None,
    validate_sumo: bool = True,
) -> Mapping[str, object]:
    configuration = load_traffic_demands(demand_path)
    all_profiles = load_vehicle_profiles(vehicle_profile_path)
    referenced = set(configuration.vehicle_mix.shares)
    missing_profiles = referenced - set(all_profiles)
    if missing_profiles:
        raise VehicleProfileError(
            f"Traffic mix references unknown vehicle profiles: {sorted(missing_profiles)}"
        )
    profiles = {name: all_profiles[name] for name in sorted(referenced)}
    manifest_intersections = tls_manifest.get("intersections", {})
    requested = (
        tuple(intersection_ids)
        if intersection_ids is not None
        else tuple(configuration.intersections)
    )
    if not requested:
        raise TrafficDemandError("At least one intersection is required.")
    if len(requested) != len(set(requested)):
        raise TrafficDemandError("Intersection IDs must be unique.")
    unknown = set(requested) - set(configuration.intersections)
    if unknown:
        raise TrafficDemandError(
            f"No official traffic demand is configured for: {sorted(unknown)}"
        )
    missing_manifest = set(requested) - set(manifest_intersections)
    if missing_manifest:
        raise TrafficDemandError(
            f"Requested intersections are absent from the TLS manifest: {sorted(missing_manifest)}"
        )

    layout = GeneratedArtifactLayout(output_dir)
    missing_artifacts = [
        path
        for path in (layout.network_file, layout.signal_programs_file)
        if not path.is_file()
    ]
    if missing_artifacts:
        raise TrafficDemandError(
            "Generated TLS artifacts are missing: "
            f"{[str(path) for path in missing_artifacts]}. Run build_tls first."
        )
    tools = _toolchain(tool_paths)
    compiled = _compile_global_demand(
        requested, manifest_intersections, configuration
    )
    class_targets = _class_targets(
        compiled, configuration.vehicle_mix.shares, profiles
    )

    layout.create_base_directories()
    traffic_root = output_dir / "traffic"
    if traffic_root.exists():
        shutil.rmtree(traffic_root)
    traffic_root.mkdir(parents=True)
    for stale in layout.reports_dir.glob("traffic_*"):
        if stale.is_file():
            stale.unlink()
        elif stale.is_dir():
            shutil.rmtree(stale)
    work_dir = traffic_root / ".work"
    work_dir.mkdir()

    candidate_paths = _build_candidates(
        work_dir,
        layout.network_file,
        compiled,
        class_targets,
        profiles,
        tools,
        command_runner,
    )

    result = {
        "schema_version": 3,
        "scope": "global",
        "source": str(demand_path.resolve()),
        "unit": configuration.unit,
        "interval_seconds": configuration.interval_seconds,
        "intersection_ids": list(requested),
        "vehicle_mix": {
            "basis": configuration.vehicle_mix.basis,
            "shares": dict(configuration.vehicle_mix.shares),
        },
        "vehicle_profile_source": str(vehicle_profile_path.resolve()),
        "vehicle_profile_schema_version": 2,
        "vehicle_profiles": {
            profile_id: asdict(profile)
            for profile_id, profile in profiles.items()
        },
        "vehicle_type_profiles": {
            f"official_{_safe_id(profile_id)}": profile_id
            for profile_id in profiles
        },
        "intersections": {
            intersection_id: {
                "periods": list(compiled.periods),
                "origins": _origin_metadata(
                    manifest_intersections[intersection_id],
                    configuration.intersections[intersection_id],
                ),
            }
            for intersection_id in requested
        },
        "scenarios": {},
    }

    for period_id, period in compiled.periods.items():
        counts_paths = {}
        for profile_id in profiles:
            counts_path = work_dir / f"counts_{period_id}_{profile_id}.xml"
            _write_turn_counts(
                counts_path,
                period,
                class_targets[period_id],
                profile_id,
            )
            counts_paths[profile_id] = counts_path

        attempts = []
        selected = None
        seeds = ROUTE_SAMPLER_SEEDS
        for seed_index, seed in enumerate(seeds):
            sampled_flows = []
            mismatch_paths = {}
            for profile_id in profiles:
                sample_path = work_dir / f"sample_{period_id}_{profile_id}_{seed}.rou.xml"
                mismatch_path = work_dir / f"mismatch_{period_id}_{profile_id}_{seed}.xml"
                sampled_flows.extend(
                    _run_route_sampler(
                        tools,
                        command_runner,
                        candidate_paths[profile_id],
                        counts_paths[profile_id],
                        sample_path,
                        mismatch_path,
                        profile_id,
                        period,
                        configuration.interval_seconds,
                        seed,
                    )
                )
                mismatch_paths[profile_id] = mismatch_path
            report = dict(
                _quality_report(
                    period_id,
                    compiled,
                    sampled_flows,
                    profiles,
                    class_targets[period_id],
                )
            )
            report["seed"] = seed
            report["route_sampler_mismatch_files"] = {
                profile_id: layout.relative(path)
                for profile_id, path in mismatch_paths.items()
            }
            attempt = {
                "seed": seed,
                "flows": tuple(sampled_flows),
                "mismatch_paths": mismatch_paths,
                "report": report,
            }
            attempts.append(attempt)
            if report["passed"] and seed_index == 0:
                selected = attempt
                break
        if selected is None:
            passing = [item for item in attempts if item["report"]["passed"]]
            if passing:
                selected = min(
                    passing,
                    key=lambda item: (
                        item["report"]["total_absolute_error_pcu"],
                        item["report"]["sampled_vehicle_count"],
                        -item["report"]["multi_intersection_vehicle_count"],
                        item["seed"],
                    ),
                )
            else:
                failure_path = layout.reports_dir / f"traffic_quality_{period_id}_failed.json"
                failure_path.write_text(
                    json.dumps(
                        {
                            "period_id": period_id,
                            "attempts": [item["report"] for item in attempts],
                        },
                        ensure_ascii=False,
                        indent=2,
                    )
                    + "\n",
                    encoding="utf-8",
                )
                raise TrafficDemandError(
                    f"Global traffic quality thresholds failed for {period_id}; "
                    f"see {failure_path}."
                )

        scenario_dir = layout.global_traffic_scenario_dir(period_id)
        scenario_dir.mkdir(parents=True, exist_ok=True)
        route_path = scenario_dir / "routes.rou.xml"
        additional_path = scenario_dir / "signals.add.xml"
        sumocfg_path = scenario_dir / "simulation.sumocfg"
        _write_routes(route_path, profiles, selected["flows"])
        _write_program_additional(
            layout.signal_programs_file,
            additional_path,
            compiled.program_ids[period_id],
        )
        simulation_end = _write_sumocfg(
            sumocfg_path,
            layout.network_file,
            route_path.name,
            additional_path.name,
            period,
        )
        mismatch_files = {}
        for profile_id, source in selected["mismatch_paths"].items():
            target = layout.reports_dir / f"traffic_{period_id}_{profile_id}_mismatch.xml"
            shutil.copy2(source, target)
            mismatch_files[profile_id] = layout.relative(target)
        selected_report = dict(selected["report"])
        selected_report["route_sampler_mismatch_files"] = mismatch_files
        report_json = layout.reports_dir / f"traffic_quality_{period_id}.json"
        report_csv = layout.reports_dir / f"traffic_quality_{period_id}.csv"
        _write_quality_report(report_json, report_csv, selected_report)

        scenario_id = f"global_{period_id}"
        result["scenarios"][scenario_id] = {
            "scenario_id": scenario_id,
            "period_id": period_id,
            "label": period.label,
            "intersection_ids": list(requested),
            "program_ids": dict(compiled.program_ids[period_id]),
            "official_time_range": {
                "start": _clock(period.start),
                "end": _clock(period.end),
            },
            "route_file": layout.relative(route_path),
            "additional_file": layout.relative(additional_path),
            "sumocfg": layout.relative(sumocfg_path),
            "quality_report": layout.relative(report_json),
            "quality_report_csv": layout.relative(report_csv),
            "route_sampler_mismatch_files": mismatch_files,
            "demand_duration": period.duration,
            "simulation_end": simulation_end,
            "selected_seed": selected["seed"],
            "target_observation_pcu": selected["report"]["target_observation_pcu"],
            "sampled_vehicle_count": selected["report"]["sampled_vehicle_count"],
            "multi_intersection_vehicle_count": selected["report"][
                "multi_intersection_vehicle_count"
            ],
            "multi_intersection_vehicle_share": selected["report"][
                "multi_intersection_vehicle_share"
            ],
        }
        if validate_sumo:
            _run_command(
                [
                    tools["sumo"],
                    "--configuration-file",
                    str(sumocfg_path),
                    "--step-length",
                    "1",
                    "--no-step-log",
                    "true",
                    "--duration-log.disable",
                    "true",
                ],
                command_runner,
            )

    shutil.rmtree(work_dir)
    layout.traffic_manifest.write_text(
        json.dumps(result, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    return result


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--manifest", type=Path, default=DEFAULT_MANIFEST)
    parser.add_argument("--demand", type=Path, default=DEFAULT_DEMANDS)
    parser.add_argument(
        "--vehicle-profiles", type=Path, default=DEFAULT_VEHICLE_PROFILES
    )
    parser.add_argument("--output-dir", type=Path, default=DEFAULT_OUTPUT_DIR)
    parser.add_argument("--intersections", nargs="+", default=None)
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    try:
        result = build_traffic_scenarios(
            _load_manifest(args.manifest),
            demand_path=args.demand,
            vehicle_profile_path=args.vehicle_profiles,
            output_dir=args.output_dir,
            intersection_ids=args.intersections,
        )
    except (TrafficDemandError, VehicleProfileError) as exc:
        raise SystemExit(f"Traffic build failed: {exc}") from exc
    print("Built global traffic scenarios: " + ", ".join(result["scenarios"]))


if __name__ == "__main__":
    main()
