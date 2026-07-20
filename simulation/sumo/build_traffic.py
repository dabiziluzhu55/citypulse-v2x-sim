"""Build globally coupled SUMO traffic from official 15-minute turn counts."""

from __future__ import annotations

import argparse
import copy
import importlib.util
import json
import math
import os
import re
import shutil
import subprocess
import sys
import tempfile
import xml.etree.ElementTree as ET
from collections import defaultdict
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Callable, Iterable, Mapping, Sequence, Tuple

from .artifacts import DEFAULT_GENERATED_DIR, GeneratedArtifactLayout
from .traffic import (
    ApproachDemandMapping,
    DemandPeriod,
    RouteSplit,
    TrafficDemandError,
    load_traffic_demands,
)
from .vehicle_profiles import VehicleProfileError, load_vehicle_profiles


PROJECT_ROOT = Path(__file__).resolve().parents[2]
SUMO_DIR = PROJECT_ROOT / "data" / "maps" / "sumo"
DEFAULT_DEMANDS = SUMO_DIR / "official_traffic_demands.json"
DEFAULT_VEHICLE_PROFILES = SUMO_DIR / "vehicle_profiles.json"
DEFAULT_OUTPUT_DIR = DEFAULT_GENERATED_DIR
DEFAULT_MANIFEST = GeneratedArtifactLayout(DEFAULT_OUTPUT_DIR).tls_manifest
DEFAULT_SAMPLER_SEEDS = (42, 43, 44)
DEFAULT_AUDIT_DRAIN_SECONDS = 3600
DEFAULT_AUDIT_TOLERANCE = 0.05


@dataclass(frozen=True)
class PhysicalMovement:
    movement_id: str
    intersection_id: str
    official_approach: str
    official_movement: str
    from_edge: str
    to_edge: str

    def manifest_view(self) -> Mapping[str, object]:
        return asdict(self)


@dataclass(frozen=True)
class IntervalTargets:
    begin: int
    end: int
    counts: Mapping[str, int]


@dataclass
class SampleResult:
    seed: int
    route_root: ET.Element
    flow_records: list[Mapping[str, object]]
    assigned: Mapping[Tuple[int, int], Mapping[str, int]]
    vehicle_count: int
    multi_intersection_vehicle_count: int
    average_intersections_per_vehicle: float


def _safe_id(value: str) -> str:
    return re.sub(r"[^A-Za-z0-9_.-]+", "_", value)


def _clock(seconds: int) -> str:
    return f"{seconds // 3600:02d}:{seconds % 3600 // 60:02d}:{seconds % 60:02d}"


def _load_manifest(path: Path) -> Mapping[str, object]:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError as exc:
        raise TrafficDemandError(
            f"TLS manifest not found: {path}. Run simulation.sumo.build_tls first."
        ) from exc
    except json.JSONDecodeError as exc:
        raise TrafficDemandError(f"Invalid TLS manifest {path}: {exc}") from exc


def _movement_route(
    intersection_id: str,
    intersection_manifest: Mapping[str, object],
    approach: ApproachDemandMapping,
    official_movement: str,
) -> Tuple[str, str]:
    sumo_movement = approach.movements[official_movement]
    route_pairs = sorted(
        {
            (str(item["from_edge"]), str(item["to_edge"]))
            for item in intersection_manifest.get("connections", [])
            if item.get("approach") == approach.sumo_approach
            and item.get("movement") == sumo_movement
        }
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
) -> Tuple[Tuple[Tuple[str, str], int], ...]:
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
    if (
        len(routes_by_target) != len(route_pairs)
        or set(routes_by_target) != configured_targets
    ):
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
    weighted_routes: Sequence[Tuple[Tuple[str, str], int]],
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


def _movement_has_demand(demand, official_approach: str, official_movement: str) -> bool:
    return any(
        interval.volumes[official_approach][official_movement] > 0
        for period in demand.periods.values()
        for interval in period.intervals
    )


def _find_binary(name: str) -> str:
    executable = f"{name}.exe" if os.name == "nt" else name
    candidates = []
    sumo_home = os.environ.get("SUMO_HOME")
    if sumo_home:
        candidates.append(Path(sumo_home) / "bin" / executable)
    located = shutil.which(name)
    if located:
        candidates.append(Path(located))
    for candidate in candidates:
        if candidate.is_file():
            return str(candidate)
    raise TrafficDemandError(
        f"Cannot find SUMO binary {name!r}. Set SUMO_HOME or add SUMO/bin to PATH."
    )


def _find_route_sampler() -> Path:
    sumo_home = os.environ.get("SUMO_HOME")
    candidates = []
    if sumo_home:
        candidates.append(Path(sumo_home) / "tools" / "routeSampler.py")
    for binary_name in ("sumo", "duarouter"):
        located = shutil.which(binary_name)
        if located:
            candidates.append(Path(located).resolve().parent.parent / "tools" / "routeSampler.py")
    for candidate in candidates:
        if candidate.is_file():
            return candidate
    raise TrafficDemandError(
        "Cannot find SUMO tools/routeSampler.py. Set SUMO_HOME to the SUMO installation."
    )


def _require_numpy() -> None:
    if importlib.util.find_spec("numpy") is None:
        raise TrafficDemandError(
            "SUMO routeSampler requires NumPy. Install the project requirements in the "
            "same Python environment used to run the builder."
        )


def _physical_movements(
    intersection_ids: Sequence[str],
    manifest_intersections: Mapping[str, object],
    demands,
) -> tuple[PhysicalMovement, ...]:
    movements_by_pair: dict[tuple[str, str], PhysicalMovement] = {}
    for intersection_id in intersection_ids:
        demand = demands.intersections[intersection_id]
        intersection_manifest = manifest_intersections[intersection_id]
        for official_approach, approach in demand.approaches.items():
            for official_movement in approach.movements:
                if not _movement_has_demand(
                    demand, official_approach, official_movement
                ):
                    continue
                route_pairs = set()
                for period in demand.periods.values():
                    weighted = _movement_routes(
                        intersection_id,
                        intersection_manifest,
                        approach,
                        official_movement,
                        period.route_splits.get(official_approach, {}).get(
                            official_movement, ()
                        ),
                    )
                    route_pairs.update(route for route, _ in weighted)
                for from_edge, to_edge in sorted(route_pairs):
                    movement = PhysicalMovement(
                        movement_id=_safe_id(
                            f"{intersection_id}:{official_approach}:"
                            f"{official_movement}:{from_edge}:{to_edge}"
                        ),
                        intersection_id=intersection_id,
                        official_approach=official_approach,
                        official_movement=official_movement,
                        from_edge=from_edge,
                        to_edge=to_edge,
                    )
                    pair = (from_edge, to_edge)
                    previous = movements_by_pair.get(pair)
                    if previous is not None and previous != movement:
                        raise TrafficDemandError(
                            f"Physical turn {from_edge}->{to_edge} maps to both "
                            f"{previous.movement_id} and {movement.movement_id}."
                        )
                    movements_by_pair[pair] = movement
    return tuple(
        sorted(
            movements_by_pair.values(),
            key=lambda item: (
                item.intersection_id,
                item.official_approach,
                item.official_movement,
                item.from_edge,
                item.to_edge,
            ),
        )
    )


def _period_targets(
    period_id: str,
    intersection_ids: Sequence[str],
    manifest_intersections: Mapping[str, object],
    demands,
    movements: Sequence[PhysicalMovement],
) -> tuple[DemandPeriod, tuple[IntervalTargets, ...]]:
    reference = demands.intersections[intersection_ids[0]].periods[period_id]
    movement_by_pair = {(item.from_edge, item.to_edge): item for item in movements}
    targets = [
        IntervalTargets(
            begin=interval.start - reference.start,
            end=interval.end - reference.start,
            counts={item.movement_id: 0 for item in movements},
        )
        for interval in reference.intervals
    ]
    mutable = [dict(item.counts) for item in targets]

    for intersection_id in intersection_ids:
        demand = demands.intersections[intersection_id]
        period = demand.periods.get(period_id)
        if period is None:
            raise TrafficDemandError(f"{intersection_id} has no {period_id!r} demand.")
        if (
            period.start != reference.start
            or period.end != reference.end
            or len(period.intervals) != len(reference.intervals)
        ):
            raise TrafficDemandError(
                f"{intersection_id}/{period_id} does not share the global time grid."
            )
        intersection_manifest = manifest_intersections[intersection_id]
        active_movements = {
            (official_approach, official_movement)
            for official_approach, approach in demand.approaches.items()
            for official_movement in approach.movements
            if _movement_has_demand(demand, official_approach, official_movement)
        }
        for index, (interval, reference_interval) in enumerate(
            zip(period.intervals, reference.intervals)
        ):
            if interval.start != reference_interval.start or interval.end != reference_interval.end:
                raise TrafficDemandError(
                    f"{intersection_id}/{period_id} uses different 15-minute intervals."
                )
            for official_approach, approach in demand.approaches.items():
                for official_movement in approach.movements:
                    if (official_approach, official_movement) not in active_movements:
                        continue
                    count = interval.volumes[official_approach][official_movement]
                    weighted = _movement_routes(
                        intersection_id,
                        intersection_manifest,
                        approach,
                        official_movement,
                        period.route_splits.get(official_approach, {}).get(
                            official_movement, ()
                        ),
                    )
                    allocated = _allocate_route_counts(count, weighted)
                    for ((pair, _), route_count) in zip(weighted, allocated):
                        mutable[index][movement_by_pair[pair].movement_id] += route_count

    return reference, tuple(
        IntervalTargets(item.begin, item.end, mutable[index])
        for index, item in enumerate(targets)
    )


def _route_coverage(
    edges: Sequence[str],
    movements_by_pair: Mapping[Tuple[str, str], PhysicalMovement],
) -> tuple[PhysicalMovement, ...] | None:
    if len(edges) < 2 or len(edges) != len(set(edges)):
        return None
    monitored_from = {item.from_edge for item in movements_by_pair.values()}
    seen_incoming = set()
    coverage = []
    for from_edge, to_edge in zip(edges, edges[1:]):
        if from_edge not in monitored_from:
            continue
        if from_edge in seen_incoming:
            return None
        seen_incoming.add(from_edge)
        movement = movements_by_pair.get((from_edge, to_edge))
        if movement is None:
            return None
        coverage.append(movement)
    intersection_ids = [item.intersection_id for item in coverage]
    if len(intersection_ids) != len(set(intersection_ids)):
        return None
    return tuple(coverage)


def _write_candidate_trips(
    path: Path, movements: Sequence[PhysicalMovement]
) -> Mapping[str, Tuple[str, str]]:
    root = ET.Element("routes")
    expected = {}
    index = 0
    for first in movements:
        for second in movements:
            if first.intersection_id == second.intersection_id:
                continue
            trip_id = f"candidate_pair_{index:06d}"
            via_edges = [first.to_edge]
            if second.from_edge != first.to_edge:
                via_edges.append(second.from_edge)
            ET.SubElement(
                root,
                "trip",
                {
                    "id": trip_id,
                    "depart": "0",
                    "from": first.from_edge,
                    "to": second.to_edge,
                    "via": " ".join(via_edges),
                },
            )
            expected[trip_id] = (first.movement_id, second.movement_id)
            index += 1
    ET.indent(root, space="  ")
    ET.ElementTree(root).write(path, encoding="utf-8", xml_declaration=True)
    return expected


def _read_duarouter_routes(path: Path) -> Iterable[Tuple[str, tuple[str, ...]]]:
    root = ET.parse(path).getroot()
    for element in root:
        if element.tag not in {"vehicle", "trip", "route"}:
            continue
        route = element if element.tag == "route" else element.find("route")
        if route is None or not route.get("edges"):
            continue
        yield str(element.get("id", route.get("id", ""))), tuple(route.get("edges", "").split())


def _build_candidate_routes(
    path: Path,
    network_path: Path,
    movements: Sequence[PhysicalMovement],
    duarouter_binary: str,
) -> Mapping[str, object]:
    movements_by_pair = {(item.from_edge, item.to_edge): item for item in movements}
    routes = {(item.from_edge, item.to_edge) for item in movements}
    long_routes = set()
    rejected = defaultdict(int)
    pair_count = 0

    if len({item.intersection_id for item in movements}) > 1:
        with tempfile.TemporaryDirectory(prefix=".candidate-", dir=str(path.parent)) as directory:
            directory_path = Path(directory)
            trips_path = directory_path / "pairs.trips.xml"
            routed_path = directory_path / "pairs.rou.xml"
            expected = _write_candidate_trips(trips_path, movements)
            pair_count = len(expected)
            command = [
                duarouter_binary,
                "--net-file",
                str(network_path),
                "--route-files",
                str(trips_path),
                "--output-file",
                str(routed_path),
                "--routing-algorithm",
                "dijkstra",
                "--ignore-errors",
                "true",
                "--no-step-log",
                "true",
            ]
            completed = subprocess.run(command, capture_output=True, text=True)
            if completed.returncode != 0 or not routed_path.is_file():
                details = (completed.stderr or completed.stdout)[-4000:]
                raise TrafficDemandError(f"duarouter candidate generation failed: {details}")
            for trip_id, edges in _read_duarouter_routes(routed_path):
                coverage = _route_coverage(edges, movements_by_pair)
                if coverage is None:
                    rejected["invalid_or_repeated_controlled_turn"] += 1
                    continue
                covered_ids = {item.movement_id for item in coverage}
                required = expected.get(trip_id)
                if required is None or not set(required).issubset(covered_ids):
                    rejected["missing_required_pair"] += 1
                    continue
                if len({item.intersection_id for item in coverage}) < 2:
                    rejected["not_cross_intersection"] += 1
                    continue
                long_routes.add(edges)
    routes.update(long_routes)

    root = ET.Element("routes")
    for index, edges in enumerate(sorted(routes, key=lambda value: (len(value), value))):
        ET.SubElement(
            root,
            "route",
            {"id": f"candidate_{index:06d}", "edges": " ".join(edges)},
        )
    ET.indent(root, space="  ")
    ET.ElementTree(root).write(path, encoding="utf-8", xml_declaration=True)
    return {
        "ordered_pair_trips": pair_count,
        "fallback_routes": len(movements),
        "cross_intersection_routes": len(long_routes),
        "candidate_routes": len(routes),
        "rejected": dict(sorted(rejected.items())),
    }


def _write_turn_counts(
    path: Path,
    targets: Sequence[IntervalTargets],
    movements: Sequence[PhysicalMovement],
) -> None:
    root = ET.Element("data")
    for interval in targets:
        node = ET.SubElement(
            root,
            "interval",
            {"begin": str(interval.begin), "end": str(interval.end)},
        )
        for movement in movements:
            ET.SubElement(
                node,
                "edgeRelation",
                {
                    "from": movement.from_edge,
                    "to": movement.to_edge,
                    "count": str(interval.counts[movement.movement_id]),
                },
            )
    ET.indent(root, space="  ")
    ET.ElementTree(root).write(path, encoding="utf-8", xml_declaration=True)


def _sample_result(
    sample_path: Path,
    seed: int,
    period_id: str,
    targets: Sequence[IntervalTargets],
    movements: Sequence[PhysicalMovement],
    vehicle_type_id: str,
    vehicle_profile,
) -> SampleResult:
    source_root = ET.parse(sample_path).getroot()
    named_routes = {
        item.get("id"): tuple(item.get("edges", "").split())
        for item in source_root.findall("route")
        if item.get("id") and item.get("edges")
    }
    movements_by_pair = {(item.from_edge, item.to_edge): item for item in movements}
    target_keys = {(item.begin, item.end): item for item in targets}
    assigned = {
        key: {movement.movement_id: 0 for movement in movements}
        for key in target_keys
    }
    normalized_root = ET.Element("routes")
    ET.SubElement(normalized_root, "vType", vehicle_profile.sumo_attributes(vehicle_type_id))
    records = []
    vehicle_count = 0
    multi_count = 0
    intersection_visits = 0

    flows = list(source_root.findall("flow"))
    if not flows:
        raise TrafficDemandError(f"routeSampler seed {seed} produced no flows.")
    for index, source_flow in enumerate(flows):
        sampled_begin = float(source_flow.get("begin", "0"))
        sampled_end = float(source_flow.get("end", "0"))
        number = int(source_flow.get("number", "0"))
        matching_intervals = [
            key
            for key in target_keys
            if key[0] - 1e-6 <= sampled_begin < key[1]
            # Older routeSampler versions extend a one-vehicle flow by up to
            # one second beyond the count interval to keep its duration positive.
            and sampled_begin < sampled_end <= key[1] + 1.01
        ]
        if len(matching_intervals) != 1 or number <= 0:
            raise TrafficDemandError(
                f"routeSampler seed {seed} produced invalid flow interval/count: "
                f"{sampled_begin:g}-{sampled_end:g}/{number}."
            )
        key = matching_intervals[0]
        begin, end = key
        route_node = source_flow.find("route")
        if route_node is not None and route_node.get("edges"):
            edges = tuple(route_node.get("edges", "").split())
        elif source_flow.get("route") in named_routes:
            edges = named_routes[source_flow.get("route")]
        else:
            raise TrafficDemandError(
                f"routeSampler seed {seed} flow {source_flow.get('id')} has no route."
            )
        coverage = _route_coverage(edges, movements_by_pair)
        if not coverage:
            raise TrafficDemandError(
                f"routeSampler seed {seed} emitted an invalid or unrestricted route: {edges}."
            )
        for movement in coverage:
            assigned[key][movement.movement_id] += number
        intersections = tuple(dict.fromkeys(item.intersection_id for item in coverage))
        vehicle_count += number
        intersection_visits += number * len(intersections)
        if len(intersections) >= 2:
            multi_count += number

        flow_id = f"global_{period_id}_{index:06d}"
        flow = ET.SubElement(
            normalized_root,
            "flow",
            {
                "id": flow_id,
                "type": vehicle_type_id,
                "begin": str(begin),
                "end": str(end),
                "number": str(number),
                "departLane": "best",
                "departSpeed": "max",
            },
        )
        ET.SubElement(flow, "route", {"edges": " ".join(edges)})
        first = coverage[0]
        records.append(
            {
                "flow_id": flow_id,
                "begin": begin,
                "end": end,
                "number": number,
                "route_edges": list(edges),
                "source_intersection_id": first.intersection_id,
                "source_official_approach": first.official_approach,
                "covered_movements": [item.movement_id for item in coverage],
                "covered_intersection_ids": list(intersections),
            }
        )

    mismatches = []
    for interval in targets:
        key = (interval.begin, interval.end)
        for movement in movements:
            expected = interval.counts[movement.movement_id]
            actual = assigned[key][movement.movement_id]
            if actual != expected:
                mismatches.append(
                    f"{interval.begin}-{interval.end}/{movement.movement_id}: "
                    f"expected {expected}, assigned {actual}"
                )
    if mismatches:
        raise TrafficDemandError(
            f"routeSampler seed {seed} did not satisfy turn counts: "
            + "; ".join(mismatches[:20])
        )
    ET.indent(normalized_root, space="  ")
    return SampleResult(
        seed=seed,
        route_root=normalized_root,
        flow_records=records,
        assigned=assigned,
        vehicle_count=vehicle_count,
        multi_intersection_vehicle_count=multi_count,
        average_intersections_per_vehicle=(
            intersection_visits / vehicle_count if vehicle_count else 0.0
        ),
    )


def _run_route_sampler(
    candidates_path: Path,
    counts_path: Path,
    scenario_dir: Path,
    period_id: str,
    targets: Sequence[IntervalTargets],
    movements: Sequence[PhysicalMovement],
    route_sampler_path: Path,
    sampler_seeds: Sequence[int],
    vehicle_type_id: str,
    vehicle_profile,
) -> SampleResult:
    successful = []
    failures = []
    for seed in sampler_seeds:
        sample_path = scenario_dir / f".route_sampler_{seed}.rou.xml"
        mismatch_path = scenario_dir / f".route_sampler_{seed}.mismatch.xml"
        command = [
            sys.executable,
            str(route_sampler_path),
            "--route-files",
            str(candidates_path),
            "--turn-files",
            str(counts_path),
            "--output-file",
            str(sample_path),
            "--mismatch-output",
            str(mismatch_path),
            "--write-flows",
            "number",
            "--seed",
            str(seed),
            "--prefix",
            f"global_{period_id}_{seed}_",
        ]
        completed = subprocess.run(command, capture_output=True, text=True)
        try:
            if completed.returncode != 0 or not sample_path.is_file():
                details = (completed.stderr or completed.stdout)[-4000:]
                raise TrafficDemandError(f"routeSampler failed: {details}")
            successful.append(
                _sample_result(
                    sample_path,
                    seed,
                    period_id,
                    targets,
                    movements,
                    vehicle_type_id,
                    vehicle_profile,
                )
            )
        except (TrafficDemandError, ET.ParseError) as exc:
            failures.append(f"seed {seed}: {exc}")
        finally:
            sample_path.unlink(missing_ok=True)
            mismatch_path.unlink(missing_ok=True)
    if not successful:
        raise TrafficDemandError(
            "No routeSampler seed produced an exact assignment. " + " | ".join(failures)
        )
    return min(
        successful,
        key=lambda item: (
            item.vehicle_count,
            -item.multi_intersection_vehicle_count,
            item.seed,
        ),
    )


def _write_program_additional(
    source_path: Path, target_path: Path, program_ids: Iterable[str]
) -> None:
    try:
        source_root = ET.parse(source_path).getroot()
    except (FileNotFoundError, ET.ParseError) as exc:
        raise TrafficDemandError(f"Cannot read generated TLS programs: {source_path}") from exc
    requested = set(program_ids)
    root = ET.Element("additional")
    found = set()
    for child in source_root:
        if child.tag != "tlLogic":
            root.append(copy.deepcopy(child))
            continue
        if child.get("programID") in requested:
            root.append(copy.deepcopy(child))
            found.add(child.get("programID"))
    missing = requested - found
    if missing:
        raise TrafficDemandError(
            f"Signal programs are missing from {source_path}: {sorted(missing)}"
        )
    ET.indent(root, space="  ")
    ET.ElementTree(root).write(target_path, encoding="utf-8", xml_declaration=True)


def _write_sumocfg(
    path: Path,
    network_path: Path,
    route_filename: str,
    additional_filename: str,
    demand_duration: int,
    drain_seconds: int,
) -> int:
    simulation_end = demand_duration + drain_seconds
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


def _geh(expected: int, actual: int) -> float:
    if expected + actual == 0:
        return 0.0
    return math.sqrt(2 * (actual - expected) ** 2 / (actual + expected))


def _audit_vehroute_output(
    vehroute_path: Path,
    targets: Sequence[IntervalTargets],
    movements: Sequence[PhysicalMovement],
    demand_duration: int,
    simulation_end: int,
    tolerance: float,
) -> Mapping[str, object]:
    movements_by_pair = {(item.from_edge, item.to_edge): item for item in movements}
    by_id = {item.movement_id: item for item in movements}
    actual_total = {item.movement_id: 0 for item in movements}
    actual_intervals = {
        (item.begin, item.end): {movement.movement_id: 0 for movement in movements}
        for item in targets
    }
    unfinished = 0
    vehicles = 0
    illegal = []
    last_exit = 0.0

    for _, vehicle in ET.iterparse(vehroute_path, events=("end",)):
        if vehicle.tag != "vehicle":
            continue
        route = vehicle.find("route")
        if route is None:
            vehicle.clear()
            continue
        edges = route.get("edges", "").split()
        exit_times = [float(value) for value in route.get("exitTimes", "").split()]
        vehicles += 1
        if len(exit_times) < len(edges):
            unfinished += 1
        if exit_times:
            last_exit = max(last_exit, max(exit_times))
        monitored_from = {item.from_edge for item in movements}
        for index, (from_edge, to_edge) in enumerate(zip(edges, edges[1:])):
            if from_edge not in monitored_from or index >= len(exit_times):
                continue
            movement = movements_by_pair.get((from_edge, to_edge))
            if movement is None:
                illegal.append(
                    {
                        "vehicle_id": vehicle.get("id"),
                        "from_edge": from_edge,
                        "to_edge": to_edge,
                    }
                )
                continue
            passage_time = exit_times[index]
            if passage_time > simulation_end + 1e-9:
                continue
            actual_total[movement.movement_id] += 1
            for interval in targets:
                if interval.begin <= passage_time < interval.end:
                    actual_intervals[(interval.begin, interval.end)][movement.movement_id] += 1
                    break
        vehicle.clear()

    target_total = {item.movement_id: 0 for item in movements}
    assignment_rows = []
    for interval in targets:
        key = (interval.begin, interval.end)
        for movement in movements:
            target = interval.counts[movement.movement_id]
            target_total[movement.movement_id] += target
            actual = actual_intervals[key][movement.movement_id]
            assignment_rows.append(
                {
                    "begin": interval.begin,
                    "end": interval.end,
                    "movement_id": movement.movement_id,
                    "intersection_id": movement.intersection_id,
                    "target": target,
                    "actual": actual,
                    "absolute_error": abs(actual - target),
                    "relative_error": (
                        abs(actual - target) / target if target else (0.0 if actual == 0 else None)
                    ),
                    "geh": _geh(target, actual),
                }
            )

    movement_rows = []
    intersection_totals = defaultdict(lambda: {"target": 0, "actual": 0})
    for movement_id, target in target_total.items():
        movement = by_id[movement_id]
        actual = actual_total[movement_id]
        intersection_totals[movement.intersection_id]["target"] += target
        intersection_totals[movement.intersection_id]["actual"] += actual
        movement_rows.append(
            {
                **movement.manifest_view(),
                "target": target,
                "actual": actual,
                "absolute_error": abs(actual - target),
                "relative_error": (
                    abs(actual - target) / target if target else (0.0 if actual == 0 else None)
                ),
                "geh": _geh(target, actual),
            }
        )

    intersection_rows = []
    failed = []
    for intersection_id, values in sorted(intersection_totals.items()):
        target = values["target"]
        actual = values["actual"]
        relative = abs(actual - target) / target if target else (0.0 if actual == 0 else None)
        passed = actual == 0 if target == 0 else relative <= tolerance + 1e-12
        if not passed:
            failed.append(intersection_id)
        intersection_rows.append(
            {
                "intersection_id": intersection_id,
                "target": target,
                "actual": actual,
                "absolute_error": abs(actual - target),
                "relative_error": relative,
                "passed": passed,
            }
        )
    status = "passed" if not failed and not illegal else "failed"
    return {
        "status": status,
        "tolerance": tolerance,
        "demand_duration": demand_duration,
        "simulation_end": simulation_end,
        "last_recorded_exit": last_exit,
        "vehicles": vehicles,
        "unfinished_vehicles": unfinished,
        "failed_intersections": failed,
        "illegal_controlled_turns": illegal,
        "intersections": intersection_rows,
        "movements": movement_rows,
        "intervals": assignment_rows,
    }


def _run_sumo_audit(
    sumo_binary: str,
    sumocfg_path: Path,
    report_path: Path,
    targets: Sequence[IntervalTargets],
    movements: Sequence[PhysicalMovement],
    demand_duration: int,
    simulation_end: int,
    tolerance: float,
) -> Mapping[str, object]:
    vehroute_path = sumocfg_path.parent / ".audit.vehroute.xml"
    command = [
        sumo_binary,
        "--configuration-file",
        str(sumocfg_path),
        "--step-length",
        "1",
        "--end",
        str(simulation_end),
        "--no-step-log",
        "true",
        "--vehroute-output",
        str(vehroute_path),
        "--vehroute-output.exit-times",
        "true",
        "--vehroute-output.write-unfinished",
        "true",
    ]
    completed = subprocess.run(command, capture_output=True, text=True)
    try:
        if completed.returncode != 0 or not vehroute_path.is_file():
            details = (completed.stderr or completed.stdout)[-4000:]
            raise TrafficDemandError(f"SUMO traffic audit failed: {details}")
        report = _audit_vehroute_output(
            vehroute_path,
            targets,
            movements,
            demand_duration,
            simulation_end,
            tolerance,
        )
        report_path.write_text(
            json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
        )
        if report["status"] != "passed":
            raise TrafficDemandError(
                "SUMO traffic audit exceeded the allowed 5% intersection error or "
                f"found illegal turns. See {report_path}."
            )
        return report
    finally:
        vehroute_path.unlink(missing_ok=True)


def _origin_metadata(
    intersection_ids: Sequence[str], manifest_intersections: Mapping[str, object], demands
) -> Mapping[str, object]:
    result = {}
    for intersection_id in intersection_ids:
        demand = demands.intersections[intersection_id]
        intersection_manifest = manifest_intersections[intersection_id]
        result[intersection_id] = {
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
    return result


def build_traffic_scenarios(
    tls_manifest: Mapping[str, object],
    demand_path: Path = DEFAULT_DEMANDS,
    vehicle_profile_path: Path = DEFAULT_VEHICLE_PROFILES,
    output_dir: Path = DEFAULT_OUTPUT_DIR,
    intersection_ids: Sequence[str] | None = None,
    *,
    duarouter_binary: str | None = None,
    route_sampler_path: Path | None = None,
    sumo_binary: str | None = None,
    sampler_seeds: Sequence[int] = DEFAULT_SAMPLER_SEEDS,
    skip_audit: bool = False,
    audit_drain_seconds: int = DEFAULT_AUDIT_DRAIN_SECONDS,
    audit_tolerance: float = DEFAULT_AUDIT_TOLERANCE,
    candidate_builder: Callable[
        [Path, Path, Sequence[PhysicalMovement], str], Mapping[str, object]
    ] = _build_candidate_routes,
    sampler: Callable[..., SampleResult] = _run_route_sampler,
) -> Mapping[str, object]:
    demands = load_traffic_demands(demand_path)
    profiles = load_vehicle_profiles(vehicle_profile_path)
    manifest_intersections = tls_manifest.get("intersections", {})
    requested = tuple(intersection_ids or manifest_intersections)
    if not requested:
        raise TrafficDemandError("No intersections were selected for global traffic.")
    unknown = set(requested) - set(demands.intersections)
    missing_manifest = set(requested) - set(manifest_intersections)
    if unknown:
        raise TrafficDemandError(f"No official demand is configured for: {sorted(unknown)}")
    if missing_manifest:
        raise TrafficDemandError(
            f"Selected intersections are absent from the TLS manifest: {sorted(missing_manifest)}"
        )
    vehicle_types = {demands.intersections[item].vehicle_type for item in requested}
    if len(vehicle_types) != 1:
        raise TrafficDemandError(
            "Global route sampling currently requires one shared vehicle profile; found "
            f"{sorted(vehicle_types)}."
        )
    vehicle_profile_id = next(iter(vehicle_types))
    if vehicle_profile_id not in profiles:
        raise VehicleProfileError(f"Unknown vehicle profile: {vehicle_profile_id}")
    if not sampler_seeds or len(set(sampler_seeds)) != len(sampler_seeds):
        raise TrafficDemandError("routeSampler seeds must be non-empty and unique.")
    if audit_drain_seconds < 0 or not 0 <= audit_tolerance < 1:
        raise TrafficDemandError("Traffic audit drain/tolerance settings are invalid.")

    _require_numpy()
    duarouter_binary = duarouter_binary or _find_binary("duarouter")
    route_sampler_path = route_sampler_path or _find_route_sampler()
    if not skip_audit:
        sumo_binary = sumo_binary or _find_binary("sumo")

    layout = GeneratedArtifactLayout(output_dir)
    layout.create_base_directories()
    if (output_dir / "traffic").exists():
        shutil.rmtree(output_dir / "traffic")
    layout.traffic_global_dir.mkdir(parents=True)
    if layout.traffic_reports_dir.exists():
        shutil.rmtree(layout.traffic_reports_dir)
    layout.traffic_reports_dir.mkdir(parents=True)

    movements = _physical_movements(requested, manifest_intersections, demands)
    candidate_stats = candidate_builder(
        layout.traffic_candidates_file,
        layout.network_file,
        movements,
        duarouter_binary,
    )
    candidate_report = {
        **candidate_stats,
        "intersection_ids": list(requested),
        "physical_movements": [item.manifest_view() for item in movements],
    }
    (layout.traffic_reports_dir / "candidate_routes.json").write_text(
        json.dumps(candidate_report, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )

    period_ids = tuple(demands.intersections[requested[0]].periods)
    result = {
        "schema_version": 3,
        "source": str(demand_path.resolve()),
        "vehicle_profile_source": str(vehicle_profile_path.resolve()),
        "vehicle_profile_schema_version": 1,
        "unit": demands.unit,
        "intersection_ids": list(requested),
        "vehicle_profiles": {vehicle_profile_id: asdict(profiles[vehicle_profile_id])},
        "physical_movements": [item.manifest_view() for item in movements],
        "origins": _origin_metadata(requested, manifest_intersections, demands),
        "candidate_routes": layout.relative(layout.traffic_candidates_file),
        "candidate_statistics": candidate_stats,
        "scenarios": {},
    }
    vehicle_type_id = f"global_official_{_safe_id(vehicle_profile_id)}"

    for period_id in period_ids:
        period, targets = _period_targets(
            period_id, requested, manifest_intersections, demands, movements
        )
        scenario_dir = layout.traffic_scenario_dir(period_id)
        scenario_dir.mkdir(parents=True)
        counts_path = scenario_dir / "official_turn_counts.xml"
        routes_path = scenario_dir / "routes.rou.xml"
        additional_path = scenario_dir / "signals.add.xml"
        sumocfg_path = scenario_dir / "simulation.sumocfg"
        _write_turn_counts(counts_path, targets, movements)
        sample = sampler(
            layout.traffic_candidates_file,
            counts_path,
            scenario_dir,
            period_id,
            targets,
            movements,
            route_sampler_path,
            sampler_seeds,
            vehicle_type_id,
            profiles[vehicle_profile_id],
        )
        ET.ElementTree(sample.route_root).write(
            routes_path, encoding="utf-8", xml_declaration=True
        )
        program_ids = {
            intersection_id: demands.intersections[intersection_id]
            .periods[period_id]
            .program_id
            for intersection_id in requested
        }
        for intersection_id, program_id in program_ids.items():
            if program_id not in manifest_intersections[intersection_id].get("program_ids", []):
                raise TrafficDemandError(
                    f"{intersection_id}/{period_id}: signal program {program_id!r} is "
                    "absent from the TLS manifest."
                )
        _write_program_additional(
            layout.signal_programs_file, additional_path, program_ids.values()
        )
        simulation_end = _write_sumocfg(
            sumocfg_path,
            layout.network_file,
            routes_path.name,
            additional_path.name,
            period.duration,
            audit_drain_seconds,
        )
        assignment_report = {
            "status": "exact",
            "period_id": period_id,
            "seed": sample.seed,
            "vehicle_count": sample.vehicle_count,
            "multi_intersection_vehicle_count": sample.multi_intersection_vehicle_count,
            "multi_intersection_vehicle_share": (
                sample.multi_intersection_vehicle_count / sample.vehicle_count
                if sample.vehicle_count
                else 0.0
            ),
            "average_intersections_per_vehicle": sample.average_intersections_per_vehicle,
            "intervals": [
                {
                    "begin": interval.begin,
                    "end": interval.end,
                    "targets": dict(interval.counts),
                    "assigned": dict(sample.assigned[(interval.begin, interval.end)]),
                }
                for interval in targets
            ],
        }
        assignment_path = layout.traffic_reports_dir / f"{period_id}.assignment.json"
        assignment_path.write_text(
            json.dumps(assignment_report, ensure_ascii=False, indent=2) + "\n",
            encoding="utf-8",
        )
        audit_path = layout.traffic_reports_dir / f"{period_id}.sumo_audit.json"
        if skip_audit:
            audit_report = {
                "status": "skipped",
                "reason": "--skip-traffic-audit",
                "tolerance": audit_tolerance,
                "drain_seconds": audit_drain_seconds,
            }
            audit_path.write_text(
                json.dumps(audit_report, ensure_ascii=False, indent=2) + "\n",
                encoding="utf-8",
            )
        else:
            audit_report = _run_sumo_audit(
                str(sumo_binary),
                sumocfg_path,
                audit_path,
                targets,
                movements,
                period.duration,
                simulation_end,
                audit_tolerance,
            )

        scenario_id = f"global_{period_id}"
        result["scenarios"][scenario_id] = {
            "scenario_id": scenario_id,
            "intersection_ids": list(requested),
            "period_id": period_id,
            "label": period.label,
            "program_ids": program_ids,
            "official_time_range": {
                "start": _clock(period.start),
                "end": _clock(period.end),
            },
            "route_file": layout.relative(routes_path),
            "turn_count_file": layout.relative(counts_path),
            "additional_file": layout.relative(additional_path),
            "sumocfg": layout.relative(sumocfg_path),
            "assignment_report": layout.relative(assignment_path),
            "audit_report": layout.relative(audit_path),
            "audit_status": audit_report["status"],
            "demand_duration": period.duration,
            "simulation_end": simulation_end,
            "flow_count": len(sample.flow_records),
            "planned_vehicle_count": sample.vehicle_count,
            "vehicle_profile_id": vehicle_profile_id,
            "sumo_vehicle_type_id": vehicle_type_id,
            "flows": sample.flow_records,
            "assignment_statistics": {
                key: assignment_report[key]
                for key in (
                    "seed",
                    "vehicle_count",
                    "multi_intersection_vehicle_count",
                    "multi_intersection_vehicle_share",
                    "average_intersections_per_vehicle",
                )
            },
            "intersection_totals": {
                intersection_id: demands.intersections[intersection_id]
                .periods[period_id]
                .totals["all"]
                for intersection_id in requested
            },
        }

    layout.traffic_manifest.write_text(
        json.dumps(result, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    return result


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--manifest", type=Path, default=DEFAULT_MANIFEST)
    parser.add_argument("--demand", type=Path, default=DEFAULT_DEMANDS)
    parser.add_argument("--vehicle-profiles", type=Path, default=DEFAULT_VEHICLE_PROFILES)
    parser.add_argument("--output-dir", type=Path, default=DEFAULT_OUTPUT_DIR)
    parser.add_argument("--intersections", nargs="+", default=None)
    parser.add_argument("--sampler-seeds", nargs="+", type=int, default=list(DEFAULT_SAMPLER_SEEDS))
    parser.add_argument("--skip-traffic-audit", action="store_true")
    parser.add_argument("--audit-drain-seconds", type=int, default=DEFAULT_AUDIT_DRAIN_SECONDS)
    parser.add_argument("--audit-tolerance", type=float, default=DEFAULT_AUDIT_TOLERANCE)
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
            sampler_seeds=args.sampler_seeds,
            skip_audit=args.skip_traffic_audit,
            audit_drain_seconds=args.audit_drain_seconds,
            audit_tolerance=args.audit_tolerance,
        )
    except (TrafficDemandError, VehicleProfileError) as exc:
        raise SystemExit(f"Traffic build failed: {exc}") from exc
    print("Built global official traffic scenarios: " + ", ".join(result["scenarios"]))


if __name__ == "__main__":
    main()
