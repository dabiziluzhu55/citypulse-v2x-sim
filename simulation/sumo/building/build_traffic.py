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
    RouteEndpointExtensions,
    RoutePath,
    RouteSplit,
    TrafficDemandError,
    TrafficGenerationPolicy,
    TrafficGenerationPolicyError,
    load_traffic_demands,
    load_traffic_generation_policy,
)
from .vehicle_profiles import (
    VehicleProfile,
    VehicleProfileError,
    load_vehicle_profiles,
)


PROJECT_ROOT = Path(__file__).resolve().parents[3]
SUMO_DIR = PROJECT_ROOT / "data" / "maps" / "sumo" / "official"
DEFAULT_DEMANDS = SUMO_DIR / "traffic" / "official_traffic_demands.json"
DEFAULT_VEHICLE_PROFILES = SUMO_DIR / "traffic" / "vehicle_profiles.json"
DEFAULT_TRAFFIC_POLICY = SUMO_DIR / "traffic" / "traffic_generation_policy.json"
DEFAULT_OUTPUT_DIR = DEFAULT_GENERATED_DIR
DEFAULT_MANIFEST = GeneratedArtifactLayout(DEFAULT_OUTPUT_DIR).tls_manifest
ROUTE_SAMPLER_SEEDS = (42, 43, 44, 45, 46)
MINIMIZE_VEHICLES = 0.1
ROUTE_SAMPLER_NO_SAMPLING_CAPABILITY = "routeSamplerSupportsNoSampling"
ROUTE_SAMPLER_VIA_MISMATCH_CAPABILITY = "routeSamplerSupportsViaMismatch"
ROUTE_SAMPLER_REQUIRED_OPTIONS = (
    "--interval",
    "--write-flows",
    "--optimize",
    "--minimize-vehicles",
    "--mismatch-output",
    "--seed",
    "--attributes",
)
DEFAULT_TRAFFIC_SCOPE_ID = "global"
DENSE_TRAFFIC_SCOPES = {
    "east_dense": ("demo_3", "demo_5", "demo_6", "demo_9"),
    "west_dense": ("demo_14", "demo_15", "demo_19"),
}
TRAFFIC_SCOPE_LABELS = {
    "global": "Global official demand",
    "east_dense": "East dense area",
    "west_dense": "West dense area",
}
SUPPORTED_TRAFFIC_SCOPE_IDS = (
    DEFAULT_TRAFFIC_SCOPE_ID,
    *DENSE_TRAFFIC_SCOPES,
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
class RouteSamplerCapabilities:
    no_sampling: bool
    via_mismatch: bool


@dataclass(frozen=True)
class NetworkRouteMetadata:
    edge_lengths: Mapping[str, float]
    edge_speeds: Mapping[str, float]
    u_turn_pairs: frozenset[Tuple[str, str]]
    upstream_extensions: Mapping[str, str]
    downstream_extensions: Mapping[str, str]
    configured_extensions: Mapping[str, RouteEndpointExtensions]


@dataclass(frozen=True)
class SampledFlow:
    flow_id: str
    type_id: str
    begin: float
    end: float
    number: int
    edges: Tuple[str, ...]


@dataclass(frozen=True)
class CandidateRoute:
    route_id: str
    kind: str
    edges: Tuple[str, ...]
    distance_m: float
    covered_count_paths: Tuple[Tuple[str, ...], ...] = ()


@dataclass(frozen=True)
class CandidatePool:
    path: Path
    fallback_routes: Mapping[Tuple[str, ...], Tuple[str, ...]]
    candidate_count: int


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
            f"TLS manifest not found: {path}. Run simulation.sumo.building.build_tls first."
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


def _validate_route_sampler(path: str) -> RouteSamplerCapabilities:
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
    return RouteSamplerCapabilities(
        no_sampling="--no-sampling" in source,
        via_mismatch=(
            "output for edge relations with more than 2 edges not supported"
            not in source
        ),
    )


def _toolchain(overrides: Mapping[str, str] | None) -> Mapping[str, str]:
    if overrides is not None:
        missing = {"duarouter", "sumo", "routeSampler"} - set(overrides)
        if missing:
            raise TrafficDemandError(f"Tool overrides are missing: {sorted(missing)}")
        result = dict(overrides)
        result.setdefault(ROUTE_SAMPLER_NO_SAMPLING_CAPABILITY, "true")
        result.setdefault(ROUTE_SAMPLER_VIA_MISMATCH_CAPABILITY, "true")
        return result
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
    capabilities = _validate_route_sampler(tools["routeSampler"])
    tools[ROUTE_SAMPLER_NO_SAMPLING_CAPABILITY] = str(
        capabilities.no_sampling
    ).lower()
    tools[ROUTE_SAMPLER_VIA_MISMATCH_CAPABILITY] = str(
        capabilities.via_mismatch
    ).lower()
    if not capabilities.no_sampling:
        print(
            "SUMO routeSampler does not support --no-sampling; continuing with "
            "--optimize full. The installed legacy version may perform a seeded "
            "pre-sampling pass before full optimization."
        )
    if not capabilities.via_mismatch:
        print(
            "SUMO routeSampler cannot write mismatch XML for edge relations with "
            "via edges. Native mismatch output will be disabled for those count "
            "files; CityPulse independent PCU/GEH quality reports remain enabled."
        )
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
    # Uppercase L/R are auxiliary signal paths unless demand explicitly splits to them.
    primary_matches = [
        item for item in matches if item.get("direction") not in {"L", "R"}
    ]
    if primary_matches:
        matches = primary_matches
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
    upstream_extensions: Mapping[str, str],
    downstream_extensions: Mapping[str, str],
) -> int:
    root = ET.Element("routes")
    candidate_type = f"candidate_{_safe_id(profile.v_class)}"
    ET.SubElement(root, "vType", {"id": candidate_type, "vClass": profile.v_class})
    forced_paths = []
    for location in locations:
        forced = _extend_route_endpoints(
            location.edges, upstream_extensions, downstream_extensions
        )
        if len(set(forced)) == len(forced):
            forced_paths.append((f"local_{location.location_id}", forced))
    for first in locations:
        for second in locations:
            if first.intersection_id == second.intersection_id:
                continue
            forced = _dedupe_adjacent(first.edges + second.edges)
            forced = _extend_route_endpoints(
                forced, upstream_extensions, downstream_extensions
            )
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


def _extend_route_endpoints(
    edges: Sequence[str],
    upstream_extensions: Mapping[str, str],
    downstream_extensions: Mapping[str, str],
) -> Tuple[str, ...]:
    if not edges:
        return ()
    result = list(edges)
    upstream = upstream_extensions.get(result[0])
    if upstream is not None:
        result.insert(0, upstream)
    downstream = downstream_extensions.get(result[-1])
    if downstream is not None:
        result.append(downstream)
    return tuple(result)


def _route_contains(route: Sequence[str], path: Sequence[str]) -> bool:
    width = len(path)
    return any(tuple(route[index : index + width]) == tuple(path) for index in range(len(route) - width + 1))


def _inspect_route_network(
    network_path: Path,
    locations: Sequence[CountLocation],
    official_junction_ids: Sequence[str],
    configured_extensions: Mapping[str, RouteEndpointExtensions] | None = None,
) -> NetworkRouteMetadata:
    try:
        root = ET.parse(network_path).getroot()
    except (FileNotFoundError, ET.ParseError) as exc:
        raise TrafficDemandError(
            f"Cannot inspect generated SUMO network: {network_path}"
        ) from exc
    endpoints = {}
    lane_counts = {}
    edge_lengths = {}
    edge_speeds = {}
    for edge in root.findall("edge"):
        edge_id = edge.get("id")
        if (
            not edge_id
            or edge_id.startswith(":")
            or edge.get("function") == "internal"
        ):
            continue
        if edge.get("from") is not None and edge.get("to") is not None:
            endpoints[str(edge_id)] = (
                str(edge.get("from")),
                str(edge.get("to")),
            )
        lanes = edge.findall("lane")
        lane_counts[str(edge_id)] = len(lanes)
        lengths = []
        speeds = []
        for lane in lanes:
            try:
                length = float(str(lane.get("length", "")))
            except ValueError:
                continue
            if math.isfinite(length) and length > 0:
                lengths.append(length)
            try:
                speed = float(str(lane.get("speed", "")))
            except ValueError:
                continue
            if math.isfinite(speed) and speed > 0:
                speeds.append(speed)
        if lengths:
            # Keep midpoint positions valid when parallel lanes differ slightly.
            edge_lengths[str(edge_id)] = min(lengths)
        if speeds:
            # A best-lane free-flow estimate is an intentional lower bound.
            edge_speeds[str(edge_id)] = max(speeds)

    by_endpoints: dict[Tuple[str, str], list[str]] = defaultdict(list)
    for edge_id, edge_endpoints in endpoints.items():
        by_endpoints[edge_endpoints].append(edge_id)
    u_turn_pairs = frozenset({
        (first, second)
        for first, (from_node, to_node) in endpoints.items()
        for second in by_endpoints.get((to_node, from_node), ())
    })

    predecessors: dict[str, dict[str, set[str]]] = defaultdict(
        lambda: defaultdict(set)
    )
    successors: dict[str, dict[str, set[str]]] = defaultdict(
        lambda: defaultdict(set)
    )
    for connection in root.findall("connection"):
        source = str(connection.get("from", ""))
        target = str(connection.get("to", ""))
        if source not in lane_counts or target not in lane_counts:
            continue
        direction = str(connection.get("dir", ""))
        predecessors[target][source].add(direction)
        successors[source][target].add(direction)

    network_protected_junctions = {str(item) for item in official_junction_ids}
    network_protected_junctions.update(
        str(junction.get("id"))
        for junction in root.findall("junction")
        if junction.get("id") and junction.get("type") == "traffic_light"
    )
    protected_junctions = set(network_protected_junctions)
    for location in locations:
        incoming_endpoints = endpoints.get(location.edges[0])
        outgoing_endpoints = endpoints.get(location.edges[-1])
        if incoming_endpoints is not None:
            protected_junctions.add(incoming_endpoints[1])
        if outgoing_endpoints is not None:
            protected_junctions.add(outgoing_endpoints[0])

    upstream_extensions = {}
    for near_edge in sorted({location.edges[0] for location in locations}):
        near_endpoints = endpoints.get(near_edge)
        if near_endpoints is None or near_endpoints[0] in protected_junctions:
            continue
        straight = [
            edge_id
            for edge_id, directions in predecessors[near_edge].items()
            if directions == {"s"}
        ]
        if len(straight) != 1:
            continue
        far_edge = straight[0]
        if lane_counts[near_edge] > lane_counts[far_edge]:
            if far_edge not in edge_lengths:
                raise TrafficDemandError(
                    f"Upstream endpoint extension edge {far_edge!r} has no positive "
                    "lane length in the generated SUMO network."
                )
            upstream_extensions[near_edge] = far_edge

    downstream_extensions = {}
    for near_edge in sorted({location.edges[-1] for location in locations}):
        near_endpoints = endpoints.get(near_edge)
        if near_endpoints is None or near_endpoints[1] in protected_junctions:
            continue
        straight = [
            edge_id
            for edge_id, directions in successors[near_edge].items()
            if directions == {"s"}
        ]
        if len(straight) != 1:
            continue
        far_edge = straight[0]
        if lane_counts[near_edge] > lane_counts[far_edge]:
            if far_edge not in edge_lengths:
                raise TrafficDemandError(
                    f"Downstream endpoint extension edge {far_edge!r} has no positive "
                    "lane length in the generated SUMO network."
                )
            downstream_extensions[near_edge] = far_edge

    configured_extensions = configured_extensions or {}
    endpoints_by_intersection = {
        intersection_id: {
            "upstream": {
                location.edges[0]
                for location in locations
                if location.intersection_id == intersection_id
            },
            "downstream": {
                location.edges[-1]
                for location in locations
                if location.intersection_id == intersection_id
            },
        }
        for intersection_id in configured_extensions
    }
    merged_by_direction = {
        "upstream": upstream_extensions,
        "downstream": downstream_extensions,
    }
    adjacency_by_direction = {
        "upstream": predecessors,
        "downstream": successors,
    }
    protected_endpoint_index = {"upstream": 0, "downstream": 1}
    for intersection_id, extensions in sorted(configured_extensions.items()):
        for direction in ("upstream", "downstream"):
            mapping = getattr(extensions, direction)
            valid_near_edges = endpoints_by_intersection[intersection_id][direction]
            merged = merged_by_direction[direction]
            adjacency = adjacency_by_direction[direction]
            endpoint_index = protected_endpoint_index[direction]
            for near_edge, far_edge in sorted(mapping.items()):
                context = (
                    f"{intersection_id}/route_endpoint_extensions/{direction}/"
                    f"{near_edge}"
                )
                if near_edge not in valid_near_edges:
                    raise TrafficDemandError(
                        f"{context}: near edge is not a {direction} endpoint of an "
                        "official count path."
                    )
                missing_edges = [
                    edge_id
                    for edge_id in (near_edge, far_edge)
                    if edge_id not in endpoints
                ]
                if missing_edges:
                    raise TrafficDemandError(
                        f"{context}: endpoint extension edges are missing from the "
                        f"generated SUMO network: {missing_edges}."
                    )
                invalid_lengths = [
                    edge_id
                    for edge_id in (near_edge, far_edge)
                    if edge_id not in edge_lengths
                ]
                if invalid_lengths:
                    raise TrafficDemandError(
                        f"{context}: endpoint extension edges have no positive lane "
                        f"length: {invalid_lengths}."
                    )
                junction_id = endpoints[near_edge][endpoint_index]
                if junction_id in network_protected_junctions:
                    raise TrafficDemandError(
                        f"{context}: extension would cross protected junction "
                        f"{junction_id!r}."
                    )
                directions = adjacency[near_edge].get(far_edge, set())
                if "s" not in directions:
                    first, second = (
                        (far_edge, near_edge)
                        if direction == "upstream"
                        else (near_edge, far_edge)
                    )
                    raise TrafficDemandError(
                        f"{context}: edges {first!r} and {second!r} must have a "
                        f"straight connection; found directions {sorted(directions)}."
                    )
                existing = merged.get(near_edge)
                if existing is not None and existing != far_edge:
                    raise TrafficDemandError(
                        f"{context}: conflicts with detected extension "
                        f"{near_edge!r}->{existing!r}."
                    )
                merged[near_edge] = far_edge

    return NetworkRouteMetadata(
        edge_lengths=edge_lengths,
        edge_speeds=edge_speeds,
        u_turn_pairs=u_turn_pairs,
        upstream_extensions=upstream_extensions,
        downstream_extensions=downstream_extensions,
        configured_extensions=configured_extensions,
    )


def _route_distance_m(
    edges: Sequence[str], network_metadata: NetworkRouteMetadata
) -> float:
    if not edges:
        return 0.0
    try:
        lengths = [network_metadata.edge_lengths[edge_id] for edge_id in edges]
    except KeyError as exc:
        raise TrafficDemandError(
            f"Candidate route references an edge without a positive length: {exc.args[0]!r}."
        ) from exc
    distance = sum(lengths)
    if len(lengths) == 1:
        return 0.0
    return distance - lengths[0] * 0.5 - lengths[-1] * 0.5


def _read_candidate_routes(
    path: Path,
    network_metadata: NetworkRouteMetadata,
    u_turn_pairs: set[Tuple[str, str]] | None = None,
    count_paths: Sequence[Tuple[str, ...]] = (),
) -> Tuple[CandidateRoute, ...]:
    try:
        root = ET.parse(path).getroot()
    except (FileNotFoundError, ET.ParseError) as exc:
        raise TrafficDemandError(f"Cannot read duarouter candidate output: {path}") from exc
    route_defs = {
        str(item.get("id")): tuple(str(item.get("edges", "")).split())
        for item in root.findall("route")
    }
    routes = {}
    forbidden_u_turns = u_turn_pairs or set()
    for vehicle in root.findall("vehicle"):
        route_node = vehicle.find("route")
        if route_node is not None:
            edges = tuple(str(route_node.get("edges", "")).split())
        else:
            edges = route_defs.get(str(vehicle.get("route")), ())
        route_id = str(vehicle.get("id", ""))
        kind = "pair" if route_id.startswith("pair_") else "local"
        has_u_turn = any(
            (first, second) in forbidden_u_turns
            for first, second in zip(edges, edges[1:])
        )
        if len(edges) >= 2 and len(set(edges)) == len(edges) and not has_u_turn:
            key = (kind, edges)
            routes[key] = CandidateRoute(
                route_id=route_id or f"{kind}_{len(routes)}",
                kind=kind,
                edges=edges,
                distance_m=_route_distance_m(edges, network_metadata),
                covered_count_paths=tuple(
                    sorted(
                        path
                        for path in count_paths
                        if _route_contains(edges, path)
                    )
                ),
            )
    return tuple(
        sorted(
            routes.values(),
            key=lambda item: (item.kind, item.distance_m, item.edges, item.route_id),
        )
    )


def _write_candidate_routes(path: Path, routes: Sequence[CandidateRoute]) -> None:
    root = ET.Element("routes")
    for index, route in enumerate(routes):
        vehicle = ET.SubElement(
            root,
            "vehicle",
            {
                "id": f"{route.kind}_{index:06d}",
                "depart": str(index),
            },
        )
        ET.SubElement(vehicle, "route", {"edges": " ".join(route.edges)})
    ET.indent(root, space="  ")
    ET.ElementTree(root).write(path, encoding="utf-8", xml_declaration=True)


def _preferred_candidate(
    route: CandidateRoute,
    profile_id: str,
    policy: TrafficGenerationPolicy,
) -> bool:
    profile_policy = policy.profiles[profile_id]
    if profile_policy.candidate_mode == "local_short":
        return (
            route.kind == "local"
            and profile_policy.maximum_local_distance_m is not None
            and route.distance_m < profile_policy.maximum_local_distance_m
        )
    if profile_policy.candidate_mode == "local_and_near_pair":
        return route.kind == "local" or (
            route.kind == "pair"
            and profile_policy.maximum_pair_distance_m is not None
            and route.distance_m <= profile_policy.maximum_pair_distance_m
        )
    return (
        route.kind == "pair"
        and profile_policy.minimum_pair_distance_m is not None
        and route.distance_m >= profile_policy.minimum_pair_distance_m
    )


def _fallback_order(
    route: CandidateRoute,
    profile_id: str,
    policy: TrafficGenerationPolicy,
) -> Tuple[int, float, int, Tuple[str, ...]]:
    mode = policy.profiles[profile_id].candidate_mode
    if mode == "local_short":
        kind_rank = 0 if route.kind == "local" else 1
    elif mode == "long_pair":
        kind_rank = 0 if route.kind == "pair" else 1
    else:
        kind_rank = 0
    return kind_rank, route.distance_m, len(route.edges), route.edges


def _filter_profile_candidates(
    routes: Sequence[CandidateRoute],
    profile_id: str,
    required_locations: Sequence[CountLocation],
    policy: TrafficGenerationPolicy,
) -> Tuple[Tuple[CandidateRoute, ...], Mapping[Tuple[str, ...], Tuple[str, ...]]]:
    selected = {
        (route.kind, route.edges): route
        for route in routes
        if _preferred_candidate(route, profile_id, policy)
    }
    mode = policy.profiles[profile_id].candidate_mode
    fallback_reasons: dict[Tuple[str, ...], list[str]] = defaultdict(list)
    for location in sorted(required_locations, key=lambda item: item.location_id):
        matches = [
            route
            for route in routes
            if location.edges in route.covered_count_paths
            or _route_contains(route.edges, location.edges)
        ]
        if not matches:
            raise TrafficDemandError(
                f"{profile_id} candidate routes do not cover count path "
                f"{location.location_id}: {location.edges}."
            )
        covered_by_selected = any(
            location.edges in route.covered_count_paths
            or _route_contains(route.edges, location.edges)
            for route in selected.values()
        )
        if not covered_by_selected:
            chosen = min(
                matches,
                key=lambda route: _fallback_order(route, profile_id, policy),
            )
            selected[(chosen.kind, chosen.edges)] = chosen
            fallback_reasons[chosen.edges].append(
                f"{mode}:no_preferred_route_for_count_path:"
                f"{location.location_id}"
            )

        # Long pair routes couple multiple count equations. Keep one local
        # degree of freedom per path so routeSampler can fit local residuals.
        if mode == "long_pair":
            local_matches = [route for route in matches if route.kind == "local"]
            if local_matches:
                residual = min(
                    local_matches,
                    key=lambda route: (
                        route.distance_m,
                        len(route.edges),
                        route.edges,
                        route.route_id,
                    ),
                )
                selected[(residual.kind, residual.edges)] = residual
                reason = (
                    "long_pair:quality_residual_for_count_path:"
                    f"{location.location_id}"
                )
                if reason not in fallback_reasons[residual.edges]:
                    fallback_reasons[residual.edges].append(reason)
    ordered = tuple(
        sorted(
            selected.values(),
            key=lambda item: (item.kind, item.distance_m, item.edges, item.route_id),
        )
    )
    return ordered, {
        edges: tuple(reasons)
        for edges, reasons in sorted(fallback_reasons.items())
    }


def _build_candidates(
    work_dir: Path,
    network_path: Path,
    network_metadata: NetworkRouteMetadata,
    compiled: CompiledGlobalDemand,
    profiles: Mapping[str, VehicleProfile],
    policy: TrafficGenerationPolicy,
    tools: Mapping[str, str],
    runner,
) -> Mapping[str, CandidatePool]:
    locations = tuple(sorted(compiled.locations.values(), key=lambda item: item.location_id))
    by_vclass: dict[str, list[str]] = defaultdict(list)
    for profile_id in sorted(profiles):
        by_vclass[profiles[profile_id].v_class].append(profile_id)
    result = {}
    for vclass, profile_ids in sorted(by_vclass.items()):
        profile = profiles[profile_ids[0]]
        trips_path = work_dir / f"candidate_{_safe_id(vclass)}.trips.xml"
        raw_path = work_dir / f"candidate_{_safe_id(vclass)}.raw.rou.xml"
        _write_candidate_trips(
            trips_path,
            profile,
            locations,
            network_metadata.upstream_extensions,
            network_metadata.downstream_extensions,
        )
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
        routes = _read_candidate_routes(
            raw_path,
            network_metadata,
            set(network_metadata.u_turn_pairs),
            tuple(compiled.locations),
        )
        if not routes:
            raise TrafficDemandError(f"duarouter produced no {vclass} candidate routes.")
        for profile_id in profile_ids:
            required = {
                edges
                for intervals in compiled.targets.values()
                for interval in intervals
                for edges, target in interval.items()
                if target > 0
            }
            required_locations = tuple(compiled.locations[edges] for edges in required)
            filtered, fallbacks = _filter_profile_candidates(
                routes,
                profile_id,
                required_locations,
                policy,
            )
            profile_path = work_dir / f"candidate_{_safe_id(profile_id)}.rou.xml"
            _write_candidate_routes(profile_path, filtered)
            result[profile_id] = CandidatePool(
                path=profile_path,
                fallback_routes=fallbacks,
                candidate_count=len(filtered),
            )
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


def _turn_counts_have_via(path: Path) -> bool:
    try:
        root = ET.parse(path).getroot()
    except (FileNotFoundError, ET.ParseError) as exc:
        raise TrafficDemandError(f"Cannot read routeSampler turn counts: {path}") from exc
    return any(relation.get("via") for relation in root.findall("interval/edgeRelation"))


def _write_legacy_mismatch_marker(
    path: Path,
    route_sampler_path: str,
    counts_path: Path,
) -> None:
    root = ET.Element(
        "data",
        {
            "native": "false",
            "reason": "legacy-routeSampler-via-mismatch-unsupported",
        },
    )
    warning = ET.SubElement(
        root,
        "warning",
        {
            "routeSampler": route_sampler_path,
            "turnCounts": str(counts_path),
        },
    )
    warning.text = (
        "The installed routeSampler can optimize via edge relations but crashes "
        "while serializing their native mismatch XML. Use the CityPulse traffic "
        "quality JSON/CSV for authoritative PCU, GEH, and zero-flow validation."
    )
    ET.indent(root, space="  ")
    ET.ElementTree(root).write(path, encoding="utf-8", xml_declaration=True)


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
    optimization_options = ["--optimize", "full"]
    if tools.get(ROUTE_SAMPLER_NO_SAMPLING_CAPABILITY, "true") == "true":
        optimization_options.append("--no-sampling")
    write_native_mismatch = not _turn_counts_have_via(counts_path) or (
        tools.get(ROUTE_SAMPLER_VIA_MISMATCH_CAPABILITY, "true") == "true"
    )
    mismatch_options = (
        ["--mismatch-output", str(mismatch_path)]
        if write_native_mismatch
        else []
    )
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
            *mismatch_options,
            "--begin",
            "0",
            "--end",
            str(period.duration),
            "--interval",
            str(interval_seconds),
            "--write-flows",
            "number",
            *optimization_options,
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
    if not write_native_mismatch:
        _write_legacy_mismatch_marker(
            mismatch_path,
            tools["routeSampler"],
            counts_path,
        )
    elif not mismatch_path.is_file():
        raise TrafficDemandError(
            f"routeSampler did not write the requested mismatch output: {mismatch_path}"
        )
    return _read_sampled_flows(output_path)


def _flow_interval(flow: SampledFlow, period: DemandPeriod) -> int:
    for index, interval in enumerate(period.intervals):
        begin = interval.start - period.start
        end = interval.end - period.start
        if begin - 1e-6 <= flow.begin < end - 1e-6:
            return index
    raise TrafficDemandError(
        f"Flow {flow.flow_id} begins outside official 15-minute intervals: "
        f"{flow.begin}."
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
        "failed_cell_count": sum(not bool(row["passed"]) for row in rows),
        "failed_intersection_interval_total_count": sum(
            not bool(item["passed"]) for item in intersection_totals
        ),
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


def _weighted_quantile(
    values: Sequence[Tuple[float, int]], quantile: float
) -> float:
    if not values:
        return 0.0
    ordered = sorted((float(value), int(weight)) for value, weight in values if weight > 0)
    total_weight = sum(weight for _, weight in ordered)
    if total_weight <= 0:
        return 0.0
    target = min(max(float(quantile), 0.0), 1.0) * (total_weight - 1)
    cumulative = 0
    for value, weight in ordered:
        cumulative += weight
        if cumulative > target:
            return value
    return ordered[-1][0]


def _route_freeflow_seconds(
    edges: Sequence[str],
    profile: VehicleProfile,
    network_metadata: NetworkRouteMetadata,
) -> float:
    if len(edges) <= 1:
        return 0.0
    total = 0.0
    for index, edge_id in enumerate(edges):
        try:
            length = network_metadata.edge_lengths[edge_id]
        except KeyError as exc:
            raise TrafficDemandError(
                f"Sampled route references an edge without a positive length: "
                f"{exc.args[0]!r}."
            ) from exc
        if index == 0 or index == len(edges) - 1:
            length *= 0.5
        edge_speed = network_metadata.edge_speeds.get(
            edge_id, profile.max_speed_mps
        )
        speed = min(profile.max_speed_mps, edge_speed)
        if speed <= 0:
            raise TrafficDemandError(
                f"Sampled route edge {edge_id!r} has no positive speed."
            )
        total += length / speed
    return total


def _route_distribution(
    flows: Sequence[SampledFlow],
    profile_by_type: Mapping[str, VehicleProfile],
    network_metadata: NetworkRouteMetadata,
    locations: Mapping[Tuple[str, ...], CountLocation],
    policy: TrafficGenerationPolicy,
    fallback_edges: frozenset[Tuple[str, ...]] = frozenset(),
    fleet_total: int | None = None,
) -> Mapping[str, object]:
    vehicle_count = sum(flow.number for flow in flows if flow.number > 0)
    weighted_distances = []
    short_count = 0
    medium_count = 0
    long_count = 0
    freeflow_vehicle_seconds = 0.0
    covered_intersection_sum = 0
    fallback_vehicle_count = 0
    for flow in flows:
        if flow.number <= 0:
            continue
        try:
            profile = profile_by_type[flow.type_id]
        except KeyError as exc:
            raise TrafficDemandError(
                f"Flow {flow.flow_id} references unknown type {flow.type_id!r}."
            ) from exc
        distance = _route_distance_m(flow.edges, network_metadata)
        weighted_distances.append((distance, flow.number))
        if distance < policy.short_route_max_m:
            short_count += flow.number
        elif distance < policy.long_route_min_m:
            medium_count += flow.number
        else:
            long_count += flow.number
        freeflow_vehicle_seconds += (
            _route_freeflow_seconds(flow.edges, profile, network_metadata)
            * flow.number
        )
        covered_intersections = {
            location.intersection_id
            for edges, location in locations.items()
            if _route_contains(flow.edges, edges)
        }
        covered_intersection_sum += len(covered_intersections) * flow.number
        if flow.edges in fallback_edges:
            fallback_vehicle_count += flow.number

    weighted_distance_sum = sum(
        distance * count for distance, count in weighted_distances
    )
    denominator = vehicle_count if vehicle_count else 1
    return {
        "vehicle_count": vehicle_count,
        "fleet_share": (
            vehicle_count / fleet_total
            if fleet_total is not None and fleet_total > 0
            else (1.0 if vehicle_count else 0.0)
        ),
        "short_route_count": short_count,
        "short_route_share": short_count / denominator if vehicle_count else 0.0,
        "medium_route_count": medium_count,
        "medium_route_share": medium_count / denominator if vehicle_count else 0.0,
        "long_route_count": long_count,
        "long_route_share": long_count / denominator if vehicle_count else 0.0,
        "distance_m": {
            "mean": weighted_distance_sum / denominator if vehicle_count else 0.0,
            "median": _weighted_quantile(weighted_distances, 0.50),
            "p90": _weighted_quantile(weighted_distances, 0.90),
            "p95": _weighted_quantile(weighted_distances, 0.95),
            "maximum": max(
                (distance for distance, _ in weighted_distances), default=0.0
            ),
        },
        "estimated_freeflow_vehicle_seconds": freeflow_vehicle_seconds,
        "average_official_intersections_covered": (
            covered_intersection_sum / denominator if vehicle_count else 0.0
        ),
        "fallback_vehicle_count": fallback_vehicle_count,
        "fallback_vehicle_share": (
            fallback_vehicle_count / denominator if vehicle_count else 0.0
        ),
    }


def _policy_violation(
    code: str,
    actual: float,
    target: float,
    comparator: str,
    deviation: float,
    scale: float,
    **context: object,
) -> Mapping[str, object]:
    return {
        "code": code,
        "actual": actual,
        "target": target,
        "comparator": comparator,
        "deviation": deviation,
        "normalized_deviation": deviation / max(scale, 1e-12),
        **context,
    }


def _route_policy_report(
    period_id: str,
    period: DemandPeriod,
    flows: Sequence[SampledFlow],
    profiles: Mapping[str, VehicleProfile],
    desired_shares: Mapping[str, float],
    compiled: CompiledGlobalDemand,
    network_metadata: NetworkRouteMetadata,
    policy: TrafficGenerationPolicy,
    candidate_pools: Mapping[str, CandidatePool],
) -> Mapping[str, object]:
    profile_by_type = {
        f"official_{_safe_id(profile_id)}": profile
        for profile_id, profile in profiles.items()
    }
    total_vehicles = sum(flow.number for flow in flows if flow.number > 0)
    profile_reports = {}
    fallback_details = {}
    for profile_id, profile in sorted(profiles.items()):
        own_flows = tuple(
            flow
            for flow in flows
            if flow.type_id == f"official_{_safe_id(profile_id)}"
        )
        pool = candidate_pools[profile_id]
        profile_reports[profile_id] = _route_distribution(
            own_flows,
            profile_by_type,
            network_metadata,
            compiled.locations,
            policy,
            frozenset(pool.fallback_routes),
            total_vehicles,
        )
        selected_by_route: dict[Tuple[str, ...], int] = defaultdict(int)
        for flow in own_flows:
            selected_by_route[flow.edges] += flow.number
        fallback_details[profile_id] = [
            {
                "edges": list(edges),
                "reasons": list(reasons),
                "selected_vehicle_count": selected_by_route.get(edges, 0),
            }
            for edges, reasons in sorted(pool.fallback_routes.items())
        ]

    overall = _route_distribution(
        flows,
        profile_by_type,
        network_metadata,
        compiled.locations,
        policy,
        fleet_total=total_vehicles,
    )
    baseline = policy.baselines[period_id]
    maximum_vehicle_count = (
        baseline.vehicle_count * policy.maximum_vehicle_count_multiplier
    )
    maximum_freeflow_seconds = (
        baseline.freeflow_vehicle_seconds
        * policy.maximum_freeflow_vehicle_seconds_ratio
    )
    freeflow_seconds = float(overall["estimated_freeflow_vehicle_seconds"])
    load = {
        "estimated_freeflow_vehicle_seconds": freeflow_seconds,
        "estimated_average_active_vehicles": (
            freeflow_seconds / period.duration if period.duration > 0 else 0.0
        ),
        "baseline_vehicle_count": baseline.vehicle_count,
        "baseline_freeflow_vehicle_seconds": baseline.freeflow_vehicle_seconds,
        "vehicle_count_ratio": (
            total_vehicles / baseline.vehicle_count
            if baseline.vehicle_count > 0
            else 0.0
        ),
        "freeflow_vehicle_seconds_ratio": (
            freeflow_seconds / baseline.freeflow_vehicle_seconds
            if baseline.freeflow_vehicle_seconds > 0
            else 0.0
        ),
        "maximum_vehicle_count": maximum_vehicle_count,
        "maximum_freeflow_vehicle_seconds": maximum_freeflow_seconds,
    }

    violations = []
    overall_long_share = float(overall["long_route_share"])
    if overall_long_share > policy.overall_long_share_max + 1e-12:
        violations.append(
            _policy_violation(
                "overall_long_route_share",
                overall_long_share,
                policy.overall_long_share_max,
                "<=",
                overall_long_share - policy.overall_long_share_max,
                policy.overall_long_share_max,
            )
        )
    for profile_id, report in profile_reports.items():
        profile_policy = policy.profiles[profile_id]
        actual_share = float(report["fleet_share"])
        desired_share = float(desired_shares[profile_id])
        tolerance = profile_policy.fleet_share_tolerance
        fleet_deviation = abs(actual_share - desired_share)
        if fleet_deviation > tolerance + 1e-12:
            violations.append(
                _policy_violation(
                    "fleet_share",
                    actual_share,
                    desired_share,
                    "+/-",
                    fleet_deviation - tolerance,
                    tolerance,
                    profile_id=profile_id,
                    tolerance=tolerance,
                )
            )
        long_share = float(report["long_route_share"])
        if (
            profile_policy.minimum_long_share is not None
            and long_share < profile_policy.minimum_long_share - 1e-12
        ):
            violations.append(
                _policy_violation(
                    "minimum_long_route_share",
                    long_share,
                    profile_policy.minimum_long_share,
                    ">=",
                    profile_policy.minimum_long_share - long_share,
                    profile_policy.minimum_long_share,
                    profile_id=profile_id,
                )
            )
        if (
            profile_policy.maximum_long_share is not None
            and long_share > profile_policy.maximum_long_share + 1e-12
        ):
            violations.append(
                _policy_violation(
                    "maximum_long_route_share",
                    long_share,
                    profile_policy.maximum_long_share,
                    "<=",
                    long_share - profile_policy.maximum_long_share,
                    profile_policy.maximum_long_share,
                    profile_id=profile_id,
                )
            )
    if total_vehicles > maximum_vehicle_count + 1e-12:
        violations.append(
            _policy_violation(
                "maximum_vehicle_count",
                total_vehicles,
                maximum_vehicle_count,
                "<=",
                total_vehicles - maximum_vehicle_count,
                maximum_vehicle_count,
            )
        )
    if freeflow_seconds > maximum_freeflow_seconds + 1e-12:
        violations.append(
            _policy_violation(
                "maximum_freeflow_vehicle_seconds",
                freeflow_seconds,
                maximum_freeflow_seconds,
                "<=",
                freeflow_seconds - maximum_freeflow_seconds,
                maximum_freeflow_seconds,
            )
        )
    return {
        "passed": not violations,
        "violation_count": len(violations),
        "normalized_total_violation": sum(
            float(item["normalized_deviation"]) for item in violations
        ),
        "violations": violations,
        "distance_classes": {
            "short": f"< {policy.short_route_max_m:g} m",
            "medium": (
                f">= {policy.short_route_max_m:g} m and "
                f"< {policy.long_route_min_m:g} m"
            ),
            "long": f">= {policy.long_route_min_m:g} m",
            "short_route_max_m": policy.short_route_max_m,
            "long_route_min_m": policy.long_route_min_m,
        },
        "overall": overall,
        "profiles": profile_reports,
        "load": load,
        "candidate_fallbacks": fallback_details,
        "candidate_counts": {
            profile_id: pool.candidate_count
            for profile_id, pool in sorted(candidate_pools.items())
        },
    }


def _fleet_shares_within_tolerance(
    actual_shares: Mapping[str, float],
    desired_shares: Mapping[str, float],
    policy: TrafficGenerationPolicy,
) -> bool:
    return all(
        abs(float(actual_shares.get(profile_id, 0.0)) - desired_share)
        <= policy.profiles[profile_id].fleet_share_tolerance + 1e-12
        for profile_id, desired_share in desired_shares.items()
    )


def _calibrate_vehicle_shares(
    current_shares: Mapping[str, float],
    desired_shares: Mapping[str, float],
    actual_shares: Mapping[str, float],
) -> Mapping[str, float]:
    adjusted = {}
    for profile_id in sorted(desired_shares):
        actual = float(actual_shares.get(profile_id, 0.0))
        correction = (
            float(desired_shares[profile_id]) / actual if actual > 0 else 2.0
        )
        adjusted[profile_id] = float(current_shares[profile_id]) * correction
    total = sum(adjusted.values())
    if total <= 0:
        raise TrafficDemandError("Vehicle share calibration produced zero total weight.")
    return {profile_id: value / total for profile_id, value in adjusted.items()}


def _attempt_policy_score(attempt: Mapping[str, object]) -> Tuple[float, ...]:
    report = attempt["report"]
    route_policy = report["route_policy"]
    return (
        float(route_policy["violation_count"]),
        float(route_policy["normalized_total_violation"]),
        float(route_policy["load"]["estimated_freeflow_vehicle_seconds"]),
        float(report["sampled_vehicle_count"]),
        float(attempt["seed"]),
        float(report["allocation_round"]),
    )


def _attempt_official_quality_score(
    attempt: Mapping[str, object],
) -> Tuple[float, ...]:
    report = attempt["report"]
    return (
        float(report["failed_cell_count"]),
        float(report["failed_intersection_interval_total_count"]),
        float(report["total_absolute_error_pcu"]),
        abs(
            float(report["actual_observation_pcu"])
            - float(report["target_observation_pcu"])
        ),
        -float(report["geh_below_5_percentage"]),
        float(attempt["seed"]),
    )


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


def _od_report(
    period_id: str,
    flows: Sequence[SampledFlow],
    profiles: Mapping[str, VehicleProfile],
    locations: Mapping[Tuple[str, ...], CountLocation],
    od_zones: Mapping[str, Sequence[str]],
) -> Mapping[str, object]:
    zone_ids = tuple(od_zones)
    zone_index = {zone_id: index for index, zone_id in enumerate(zone_ids)}
    intersection_zones = {
        intersection_id: zone_id
        for zone_id, intersection_ids in od_zones.items()
        for intersection_id in intersection_ids
    }
    missing_intersections = {
        location.intersection_id for location in locations.values()
    } - set(intersection_zones)
    if missing_intersections:
        raise TrafficDemandError(
            "OD zones do not cover sampled count locations: "
            f"{sorted(missing_intersections)}"
        )

    locations_by_first_edge: dict[
        str, list[Tuple[Tuple[str, ...], CountLocation]]
    ] = defaultdict(list)
    for edges, location in locations.items():
        locations_by_first_edge[edges[0]].append((edges, location))
    for candidates in locations_by_first_edge.values():
        candidates.sort(key=lambda item: (len(item[0]), item[1].location_id))

    profile_by_type = {
        f"official_{_safe_id(profile_id)}": profile
        for profile_id, profile in profiles.items()
    }
    matrix_pcu = [[0.0 for _ in zone_ids] for _ in zone_ids]
    matrix_vehicle_count = [[0 for _ in zone_ids] for _ in zone_ids]
    total_sampled_vehicle_count = 0
    total_sampled_pcu = 0.0
    interzonal_pcu = 0.0
    interzonal_vehicle_count = 0
    excluded_intra_zone_pcu = 0.0
    excluded_intra_zone_vehicle_count = 0

    for flow in flows:
        profile = profile_by_type.get(flow.type_id)
        if profile is None:
            raise TrafficDemandError(
                f"Flow {flow.flow_id} references unknown type {flow.type_id!r}."
            )
        occurrences = []
        for start, edge_id in enumerate(flow.edges):
            for path, location in locations_by_first_edge.get(edge_id, ()):
                end = start + len(path)
                if tuple(flow.edges[start:end]) == path:
                    occurrences.append((start, end - 1, location))
        if not occurrences:
            raise TrafficDemandError(
                f"Cannot assign OD zones for flow {flow.flow_id!r}: its route "
                "does not contain an official count location."
            )
        origin_location = min(
            occurrences,
            key=lambda item: (item[0], item[1], item[2].location_id),
        )[2]
        destination_location = max(
            occurrences,
            key=lambda item: (item[1], item[0], item[2].location_id),
        )[2]
        origin_zone = intersection_zones[origin_location.intersection_id]
        destination_zone = intersection_zones[destination_location.intersection_id]
        pcu = flow.number * profile.pcu_factor
        total_sampled_vehicle_count += flow.number
        total_sampled_pcu += pcu
        if origin_zone == destination_zone:
            excluded_intra_zone_pcu += pcu
            excluded_intra_zone_vehicle_count += flow.number
            continue
        origin_index = zone_index[origin_zone]
        destination_index = zone_index[destination_zone]
        matrix_pcu[origin_index][destination_index] += pcu
        matrix_vehicle_count[origin_index][destination_index] += flow.number
        interzonal_pcu += pcu
        interzonal_vehicle_count += flow.number

    return {
        "schema_version": 1,
        "period_id": period_id,
        "unit": "pcu",
        "row_axis": "origin_zone",
        "column_axis": "destination_zone",
        "endpoint_assignment": "first_and_last_official_count_location",
        "intermediate_zones_ignored": True,
        "diagonal_policy": "excluded_and_written_as_zero",
        "zones": [
            {
                "zone_id": zone_id,
                "intersection_ids": list(od_zones[zone_id]),
            }
            for zone_id in zone_ids
        ],
        "matrix_pcu": matrix_pcu,
        "matrix_vehicle_count": matrix_vehicle_count,
        "total_sampled_vehicle_count": total_sampled_vehicle_count,
        "total_sampled_pcu": total_sampled_pcu,
        "interzonal_pcu": interzonal_pcu,
        "interzonal_vehicle_count": interzonal_vehicle_count,
        "excluded_intra_zone_pcu": excluded_intra_zone_pcu,
        "excluded_intra_zone_vehicle_count": excluded_intra_zone_vehicle_count,
    }


def _write_od_report(
    json_path: Path,
    csv_path: Path,
    report: Mapping[str, object],
) -> None:
    json_path.write_text(
        json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    zone_ids = [str(item["zone_id"]) for item in report["zones"]]
    with csv_path.open("w", encoding="utf-8-sig", newline="") as stream:
        writer = csv.writer(stream)
        writer.writerow(["origin_zone/destination_zone", *zone_ids])
        for zone_id, values in zip(zone_ids, report["matrix_pcu"]):
            writer.writerow(
                [zone_id, *(_format_number(float(value)) for value in values)]
            )


def _write_routes(
    path: Path,
    profiles: Mapping[str, VehicleProfile],
    flows: Sequence[SampledFlow],
    network_metadata: NetworkRouteMetadata,
) -> None:
    root = ET.Element("routes")
    for profile_id in sorted(profiles):
        type_id = f"official_{_safe_id(profile_id)}"
        ET.SubElement(root, "vType", profiles[profile_id].sumo_attributes(type_id))
    for flow in sorted(flows, key=lambda item: (item.begin, item.flow_id)):
        if not flow.edges:
            raise TrafficDemandError(
                f"Sampled flow {flow.flow_id!r} has no route edges."
            )
        attributes = {
            "id": flow.flow_id,
            "type": flow.type_id,
            "begin": _format_number(flow.begin),
            "end": _format_number(flow.end),
            "number": str(flow.number),
            "departLane": "best",
            "departSpeed": "max",
        }
        first_edge = flow.edges[0]
        first_edge_length = network_metadata.edge_lengths.get(first_edge)
        if first_edge_length is None:
            raise TrafficDemandError(
                f"Cannot place sampled flow {flow.flow_id!r} at the midpoint "
                f"of first edge {first_edge!r}: the generated SUMO network "
                "has no positive lane length for that edge."
            )
        attributes["departPos"] = _format_number(first_edge_length * 0.5)
        final_edge = flow.edges[-1]
        final_edge_length = network_metadata.edge_lengths.get(final_edge)
        if final_edge_length is None:
            raise TrafficDemandError(
                f"Cannot place sampled flow {flow.flow_id!r} at the midpoint of "
                f"final edge {final_edge!r}: the generated SUMO network has no "
                "positive lane length for that edge."
            )
        attributes["arrivalPos"] = _format_number(final_edge_length * 0.5)
        node = ET.SubElement(root, "flow", attributes)
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
    ET.SubElement(time_node, "step-length", {"value": "0.1"})
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


def _traffic_report_path(
    layout: GeneratedArtifactLayout,
    scope_id: str,
    stem: str,
    period_id: str,
    suffix: str,
) -> Path:
    if scope_id == DEFAULT_TRAFFIC_SCOPE_ID:
        return layout.reports_dir / f"{stem}_{period_id}.{suffix}"
    return layout.reports_dir / f"{stem}_{scope_id}_{period_id}.{suffix}"


def _traffic_mismatch_path(
    layout: GeneratedArtifactLayout,
    scope_id: str,
    period_id: str,
    profile_id: str,
) -> Path:
    if scope_id == DEFAULT_TRAFFIC_SCOPE_ID:
        return layout.reports_dir / f"traffic_{period_id}_{profile_id}_mismatch.xml"
    return layout.reports_dir / (
        f"traffic_{scope_id}_{period_id}_{profile_id}_mismatch.xml"
    )


def build_traffic_scenarios(
    tls_manifest: Mapping[str, object],
    demand_path: Path = DEFAULT_DEMANDS,
    vehicle_profile_path: Path = DEFAULT_VEHICLE_PROFILES,
    output_dir: Path = DEFAULT_OUTPUT_DIR,
    intersection_ids: Sequence[str] | None = None,
    *,
    traffic_policy_path: Path = DEFAULT_TRAFFIC_POLICY,
    command_runner: Callable[..., subprocess.CompletedProcess[str]] = subprocess.run,
    tool_paths: Mapping[str, str] | None = None,
    validate_sumo: bool = True,
    scope_id: str = DEFAULT_TRAFFIC_SCOPE_ID,
    reset_output: bool = True,
    include_dense_scopes: bool = True,
    write_manifest: bool = True,
) -> Mapping[str, object]:
    if scope_id not in SUPPORTED_TRAFFIC_SCOPE_IDS:
        raise TrafficDemandError(f"Unsupported traffic scope: {scope_id!r}.")
    configuration = load_traffic_demands(demand_path)
    all_profiles = load_vehicle_profiles(vehicle_profile_path)
    policy = load_traffic_generation_policy(traffic_policy_path)
    referenced = set(configuration.vehicle_mix.shares)
    missing_profiles = referenced - set(all_profiles)
    if missing_profiles:
        raise VehicleProfileError(
            f"Traffic mix references unknown vehicle profiles: {sorted(missing_profiles)}"
        )
    profiles = {name: all_profiles[name] for name in sorted(referenced)}
    if set(policy.profiles) != set(profiles):
        raise TrafficGenerationPolicyError(
            "Traffic generation policy profiles must exactly match the traffic "
            f"mix profiles; policy={sorted(policy.profiles)}, "
            f"traffic_mix={sorted(profiles)}."
        )
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
    compiled = _compile_global_demand(
        requested, manifest_intersections, configuration
    )
    missing_baselines = set(compiled.periods) - set(policy.baselines)
    if missing_baselines:
        raise TrafficGenerationPolicyError(
            "Traffic generation policy has no load baseline for periods: "
            f"{sorted(missing_baselines)}."
        )
    official_junction_ids = tuple(
        str(junction_id)
        for intersection_id in requested
        for junction_id in manifest_intersections[intersection_id].get(
            "junction_ids", ()
        )
    )
    configured_extensions = {}
    for intersection_id in requested:
        extensions = configuration.intersections[
            intersection_id
        ].route_endpoint_extensions
        if extensions.upstream or extensions.downstream:
            configured_extensions[intersection_id] = extensions
    network_metadata = _inspect_route_network(
        layout.network_file,
        tuple(compiled.locations.values()),
        official_junction_ids,
        configured_extensions,
    )
    tools = _toolchain(tool_paths)
    layout.create_base_directories()
    traffic_root = output_dir / "traffic"
    if reset_output and traffic_root.exists():
        shutil.rmtree(traffic_root)
    traffic_root.mkdir(parents=True, exist_ok=True)
    if reset_output:
        for stale in layout.reports_dir.glob("traffic_*"):
            if stale.is_file():
                stale.unlink()
            elif stale.is_dir():
                shutil.rmtree(stale)
    work_dir = traffic_root / f".work_{scope_id}"
    if work_dir.exists():
        shutil.rmtree(work_dir)
    work_dir.mkdir()

    candidate_pools = _build_candidates(
        work_dir,
        layout.network_file,
        network_metadata,
        compiled,
        profiles,
        policy,
        tools,
        command_runner,
    )

    result = {
        "schema_version": 3,
        "scope": DEFAULT_TRAFFIC_SCOPE_ID,
        "source": str(demand_path.resolve()),
        "unit": configuration.unit,
        "interval_seconds": configuration.interval_seconds,
        "intersection_ids": list(requested),
        "available_scopes": {
            scope_id: {
                "scope_id": scope_id,
                "label": TRAFFIC_SCOPE_LABELS.get(scope_id, scope_id),
                "intersection_ids": list(requested),
                "periods": list(compiled.periods),
            }
        },
        "skipped_scopes": {},
        "vehicle_mix": {
            "basis": configuration.vehicle_mix.basis,
            "shares": dict(configuration.vehicle_mix.shares),
        },
        "vehicle_profile_source": str(vehicle_profile_path.resolve()),
        "traffic_generation_policy_source": str(traffic_policy_path.resolve()),
        "vehicle_profile_schema_version": 2,
        "vehicle_profiles": {
            profile_id: asdict(profile)
            for profile_id, profile in profiles.items()
        },
        "vehicle_type_profiles": {
            f"official_{_safe_id(profile_id)}": profile_id
            for profile_id in profiles
        },
        "od_zones": {
            zone_id: list(intersection_ids)
            for zone_id, intersection_ids in configuration.od_zones.items()
        },
        "route_endpoint_policy": {
            "strategy": (
                "route_endpoint_midpoint_with_lane_expansion_extension_and_"
                "validated_overrides"
            ),
            "fraction": 0.5,
            "upstream_extensions": dict(
                sorted(network_metadata.upstream_extensions.items())
            ),
            "downstream_extensions": dict(
                sorted(network_metadata.downstream_extensions.items())
            ),
            "configured_extensions": {
                intersection_id: {
                    "upstream": dict(sorted(extensions.upstream.items())),
                    "downstream": dict(sorted(extensions.downstream.items())),
                }
                for intersection_id, extensions in sorted(
                    network_metadata.configured_extensions.items()
                )
            },
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
        desired_shares = dict(configuration.vehicle_mix.shares)
        allocation_shares = dict(desired_shares)
        attempts = []
        passing_attempts = []
        for allocation_round in range(policy.mix_calibration_rounds):
            round_targets = _class_targets(
                compiled, allocation_shares, profiles
            )[period_id]
            counts_paths = {}
            for profile_id in profiles:
                counts_path = work_dir / (
                    f"counts_{scope_id}_{period_id}_round{allocation_round}_{profile_id}.xml"
                )
                _write_turn_counts(
                    counts_path,
                    period,
                    round_targets,
                    profile_id,
                )
                counts_paths[profile_id] = counts_path

            round_attempts = []
            for seed_index, seed in enumerate(ROUTE_SAMPLER_SEEDS):
                sampled_flows = []
                mismatch_paths = {}
                for profile_id in profiles:
                    sample_path = work_dir / (
                        f"sample_{scope_id}_{period_id}_round{allocation_round}_"
                        f"{profile_id}_{seed}.rou.xml"
                    )
                    mismatch_path = work_dir / (
                        f"mismatch_{scope_id}_{period_id}_round{allocation_round}_"
                        f"{profile_id}_{seed}.xml"
                    )
                    sampled_flows.extend(
                        _run_route_sampler(
                            tools,
                            command_runner,
                            candidate_pools[profile_id].path,
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
                        round_targets,
                    )
                )
                report["seed"] = seed
                report["allocation_round"] = allocation_round
                report["allocation_shares"] = dict(sorted(allocation_shares.items()))
                report["route_policy"] = _route_policy_report(
                    period_id,
                    period,
                    sampled_flows,
                    profiles,
                    desired_shares,
                    compiled,
                    network_metadata,
                    policy,
                    candidate_pools,
                )
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
                round_attempts.append(attempt)
                if report["passed"] and seed_index == 0:
                    break

            round_passing = [
                item for item in round_attempts if item["report"]["passed"]
            ]
            if round_passing:
                passing_attempts.extend(round_passing)
                round_selected = min(round_passing, key=_attempt_policy_score)
            else:
                round_selected = min(
                    round_attempts,
                    key=_attempt_official_quality_score,
                )
            actual_shares = round_selected["report"]["vehicle_shares"]
            if _fleet_shares_within_tolerance(
                actual_shares, desired_shares, policy
            ):
                break
            if allocation_round + 1 >= policy.mix_calibration_rounds:
                break
            if not round_passing:
                closest_report = round_selected["report"]
                print(
                    f"WARNING: official traffic quality did not pass in "
                    f"{period_id} allocation round {allocation_round}; using "
                    f"closest seed {round_selected['seed']} only to calibrate "
                    f"round {allocation_round + 1} "
                    f"({closest_report['failed_cell_count']} failed cells, "
                    f"{closest_report['failed_intersection_interval_total_count']} "
                    "failed intersection totals)."
                )
            allocation_shares = dict(
                _calibrate_vehicle_shares(
                    allocation_shares,
                    desired_shares,
                    actual_shares,
                )
            )

        if not passing_attempts:
            failure_path = layout.reports_dir / f"traffic_quality_{period_id}_failed.json"
            if scope_id != DEFAULT_TRAFFIC_SCOPE_ID:
                failure_path = layout.reports_dir / (
                    f"traffic_quality_{scope_id}_{period_id}_failed.json"
                )
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
                f"{scope_id} traffic quality thresholds failed for {period_id}; "
                f"see {failure_path}."
            )
        selected = min(passing_attempts, key=_attempt_policy_score)
        selected_policy = selected["report"]["route_policy"]
        if not selected_policy["passed"]:
            violation_codes = ", ".join(
                (
                    f"{item.get('profile_id')}:" if item.get("profile_id") else ""
                )
                + str(item["code"])
                for item in selected_policy["violations"]
            )
            print(
                f"WARNING: traffic policy targets were not fully met for "
                f"{period_id}; selected the best official-quality-passing "
                f"attempt. Violations: {violation_codes}."
            )

        scenario_dir = layout.traffic_scenario_scope_dir(scope_id, period_id)
        scenario_dir.mkdir(parents=True, exist_ok=True)
        route_path = scenario_dir / "routes.rou.xml"
        additional_path = scenario_dir / "signals.add.xml"
        sumocfg_path = scenario_dir / "simulation.sumocfg"
        _write_routes(route_path, profiles, selected["flows"], network_metadata)
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
            target = _traffic_mismatch_path(layout, scope_id, period_id, profile_id)
            shutil.copy2(source, target)
            mismatch_files[profile_id] = layout.relative(target)
        selected_report = dict(selected["report"])
        selected_report["route_sampler_mismatch_files"] = mismatch_files
        report_json = _traffic_report_path(
            layout, scope_id, "traffic_quality", period_id, "json"
        )
        report_csv = _traffic_report_path(
            layout, scope_id, "traffic_quality", period_id, "csv"
        )
        _write_quality_report(report_json, report_csv, selected_report)
        od_report = _od_report(
            period_id,
            selected["flows"],
            profiles,
            compiled.locations,
            configuration.od_zones,
        )
        od_report_json = _traffic_report_path(
            layout, scope_id, "traffic_od", period_id, "json"
        )
        od_matrix_csv = _traffic_report_path(
            layout, scope_id, "traffic_od", period_id, "csv"
        )
        _write_od_report(od_report_json, od_matrix_csv, od_report)

        scenario_id = f"{scope_id}_{period_id}"
        result["scenarios"][scenario_id] = {
            "scenario_id": scenario_id,
            "scope_id": scope_id,
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
            "od_report": layout.relative(od_report_json),
            "od_matrix_csv": layout.relative(od_matrix_csv),
            "od_interzonal_pcu": od_report["interzonal_pcu"],
            "od_excluded_intra_zone_pcu": od_report[
                "excluded_intra_zone_pcu"
            ],
            "route_sampler_mismatch_files": mismatch_files,
            "demand_duration": period.duration,
            "simulation_end": simulation_end,
            "selected_seed": selected["seed"],
            "selected_allocation_round": selected["report"]["allocation_round"],
            "route_policy_passed": selected["report"]["route_policy"]["passed"],
            "departure_position": {
                "strategy": "first_edge_midpoint",
                "fraction": 0.5,
                "remote_segment_when_extended": True,
                "extended_vehicle_count": sum(
                    flow.number
                    for flow in selected["flows"]
                    if flow.edges[0]
                    in frozenset(network_metadata.upstream_extensions.values())
                ),
            },
            "arrival_position": {
                "strategy": "final_edge_midpoint",
                "fraction": 0.5,
                "remote_segment_when_extended": True,
                "extended_vehicle_count": sum(
                    flow.number
                    for flow in selected["flows"]
                    if flow.edges[-1]
                    in frozenset(network_metadata.downstream_extensions.values())
                ),
            },
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
    if include_dense_scopes and scope_id == DEFAULT_TRAFFIC_SCOPE_ID:
        requested_set = set(requested)
        for dense_scope_id, dense_intersections in DENSE_TRAFFIC_SCOPES.items():
            missing_scope_intersections = set(dense_intersections) - requested_set
            if missing_scope_intersections:
                result["skipped_scopes"][dense_scope_id] = {
                    "scope_id": dense_scope_id,
                    "label": TRAFFIC_SCOPE_LABELS.get(
                        dense_scope_id, dense_scope_id
                    ),
                    "intersection_ids": list(dense_intersections),
                    "reason": "missing_intersections",
                    "missing_intersection_ids": sorted(
                        missing_scope_intersections
                    ),
                }
                print(
                    f"Skipping traffic scope {dense_scope_id}: selected TLS "
                    "intersections do not include "
                    f"{sorted(missing_scope_intersections)}."
                )
                continue
            dense_result = build_traffic_scenarios(
                tls_manifest,
                demand_path=demand_path,
                vehicle_profile_path=vehicle_profile_path,
                output_dir=output_dir,
                intersection_ids=dense_intersections,
                traffic_policy_path=traffic_policy_path,
                command_runner=command_runner,
                tool_paths=tool_paths,
                validate_sumo=validate_sumo,
                scope_id=dense_scope_id,
                reset_output=False,
                include_dense_scopes=False,
                write_manifest=False,
            )
            result["available_scopes"][dense_scope_id] = dense_result[
                "available_scopes"
            ][dense_scope_id]
            result["scenarios"].update(dense_result["scenarios"])
    if write_manifest:
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
    parser.add_argument(
        "--traffic-policy", type=Path, default=DEFAULT_TRAFFIC_POLICY
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
            traffic_policy_path=args.traffic_policy,
            output_dir=args.output_dir,
            intersection_ids=args.intersections,
        )
    except (
        TrafficDemandError,
        TrafficGenerationPolicyError,
        VehicleProfileError,
    ) as exc:
        raise SystemExit(f"Traffic build failed: {exc}") from exc
    print("Built global traffic scenarios: " + ", ".join(result["scenarios"]))


if __name__ == "__main__":
    main()
