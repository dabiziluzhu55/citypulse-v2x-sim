"""Generate exact-count SUMO traffic scenarios from official 15-minute data."""

from __future__ import annotations

import argparse
import copy
import json
import os
import re
import shutil
import xml.etree.ElementTree as ET
from dataclasses import asdict
from pathlib import Path
from typing import Mapping, Sequence, Tuple

from .artifacts import DEFAULT_GENERATED_DIR, GeneratedArtifactLayout
from .traffic import (
    ApproachDemandMapping,
    DemandPeriod,
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


def _write_routes(
    path: Path,
    intersection_id: str,
    intersection_manifest: Mapping[str, object],
    demand,
    period: DemandPeriod,
    vehicle_profile,
) -> Tuple[int, list[Mapping[str, object]]]:
    root = ET.Element("routes")
    vehicle_type_id = (
        f"{_safe_id(intersection_id)}_official_{_safe_id(demand.vehicle_type)}"
    )
    ET.SubElement(root, "vType", vehicle_profile.sumo_attributes(vehicle_type_id))
    routes = {
        (official_approach, official_movement): _movement_route(
            intersection_id,
            intersection_manifest,
            approach,
            official_movement,
        )
        for official_approach, approach in demand.approaches.items()
        for official_movement in approach.movements
    }
    flow_count = 0
    flow_records = []
    for interval_index, interval in enumerate(period.intervals):
        begin = interval.start - period.start
        end = interval.end - period.start
        for official_approach, approach in demand.approaches.items():
            for official_movement in approach.movements:
                count = interval.volumes[official_approach][official_movement]
                if count == 0:
                    continue
                flow_id = _safe_id(
                    f"{intersection_id}_{period.period_id}_{interval_index:02d}_"
                    f"{official_approach}_{official_movement}"
                )
                flow = ET.SubElement(
                    root,
                    "flow",
                    {
                        "id": flow_id,
                        "type": vehicle_type_id,
                        "begin": str(begin),
                        "end": str(end),
                        "number": str(count),
                        "departLane": "best",
                        "departSpeed": "max",
                    },
                )
                from_edge, to_edge = routes[(official_approach, official_movement)]
                ET.SubElement(flow, "route", {"edges": f"{from_edge} {to_edge}"})
                flow_records.append(
                    {
                        "flow_id": flow_id,
                        "official_approach": official_approach,
                        "official_movement": official_movement,
                        "begin": begin,
                        "end": end,
                        "number": count,
                    }
                )
                flow_count += 1
    ET.indent(root, space="  ")
    ET.ElementTree(root).write(path, encoding="utf-8", xml_declaration=True)
    return flow_count, flow_records


def _write_program_additional(
    source_path: Path,
    target_path: Path,
    program_id: str,
) -> None:
    try:
        source_root = ET.parse(source_path).getroot()
    except (FileNotFoundError, ET.ParseError) as exc:
        raise TrafficDemandError(f"Cannot read generated TLS programs: {source_path}") from exc
    root = ET.Element("additional")
    matches = 0
    for child in source_root:
        if child.tag != "tlLogic" or child.get("programID") == program_id:
            root.append(copy.deepcopy(child))
            if child.tag == "tlLogic":
                matches += 1
    if matches == 0:
        raise TrafficDemandError(
            f"Signal program {program_id!r} was not found in {source_path}."
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
    drain_seconds = 300
    simulation_end = period.duration + drain_seconds
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


def build_traffic_scenarios(
    tls_manifest: Mapping[str, object],
    demand_path: Path = DEFAULT_DEMANDS,
    vehicle_profile_path: Path = DEFAULT_VEHICLE_PROFILES,
    output_dir: Path = DEFAULT_OUTPUT_DIR,
    intersection_ids: Sequence[str] | None = None,
) -> Mapping[str, object]:
    configuration = load_traffic_demands(demand_path)
    profiles = load_vehicle_profiles(vehicle_profile_path)
    referenced_profiles = {
        demand.vehicle_type for demand in configuration.intersections.values()
    }
    missing_profiles = referenced_profiles - set(profiles)
    if missing_profiles:
        raise VehicleProfileError(
            f"Traffic demand references unknown vehicle profiles: {sorted(missing_profiles)}"
        )
    manifest_intersections = tls_manifest.get("intersections", {})
    requested = (
        tuple(intersection_ids)
        if intersection_ids is not None
        else tuple(configuration.intersections)
    )
    unknown = set(requested) - set(configuration.intersections)
    if unknown:
        raise TrafficDemandError(
            f"No official traffic demand is configured for: {sorted(unknown)}"
        )
    if intersection_ids is not None:
        missing_from_manifest = set(requested) - set(manifest_intersections)
        if missing_from_manifest:
            raise TrafficDemandError(
                "Requested intersections are absent from the TLS manifest: "
                f"{sorted(missing_from_manifest)}"
            )
    layout = GeneratedArtifactLayout(output_dir)
    layout.create_base_directories()
    if (output_dir / "traffic").exists():
        shutil.rmtree(output_dir / "traffic")
        (output_dir / "traffic").mkdir(parents=True)
    result = {
        "schema_version": 2,
        "source": str(demand_path.resolve()),
        "vehicle_profile_source": str(vehicle_profile_path.resolve()),
        "vehicle_profile_schema_version": 1,
        "unit": configuration.unit,
        "vehicle_profiles": {
            profile_id: asdict(profiles[profile_id])
            for profile_id in sorted(referenced_profiles)
        },
        "scenarios": {},
    }
    for intersection_id in requested:
        if intersection_id not in manifest_intersections:
            continue
        intersection_manifest = manifest_intersections[intersection_id]
        demand = configuration.intersections[intersection_id]
        available_programs = set(intersection_manifest.get("program_ids", []))
        for period in demand.periods.values():
            if period.program_id not in available_programs:
                raise TrafficDemandError(
                    f"{intersection_id}/{period.period_id}: signal program "
                    f"{period.program_id!r} is absent from the TLS manifest."
                )
            scenario_id = f"{intersection_id}_{period.period_id}"
            scenario_dir = layout.traffic_scenario_dir(
                intersection_id, period.period_id
            )
            scenario_dir.mkdir(parents=True, exist_ok=True)
            route_path = scenario_dir / "routes.rou.xml"
            sumocfg_path = scenario_dir / "simulation.sumocfg"
            additional_path = scenario_dir / "signals.add.xml"
            flow_count, flow_records = _write_routes(
                route_path,
                intersection_id,
                intersection_manifest,
                demand,
                period,
                profiles[demand.vehicle_type],
            )
            _write_program_additional(
                layout.signal_programs_file,
                additional_path,
                period.program_id,
            )
            simulation_end = _write_sumocfg(
                sumocfg_path,
                layout.network_file,
                route_path.name,
                additional_path.name,
                period,
            )
            result["scenarios"][scenario_id] = {
                "intersection_id": intersection_id,
                "period_id": period.period_id,
                "label": period.label,
                "program_id": period.program_id,
                "official_time_range": {
                    "start": _clock(period.start),
                    "end": _clock(period.end),
                },
                "route_file": layout.relative(route_path),
                "additional_file": layout.relative(additional_path),
                "sumocfg": layout.relative(sumocfg_path),
                "demand_duration": period.duration,
                "simulation_end": simulation_end,
                "flow_count": flow_count,
                "vehicle_profile_id": demand.vehicle_type,
                "sumo_vehicle_type_id": (
                    f"{_safe_id(intersection_id)}_official_"
                    f"{_safe_id(demand.vehicle_type)}"
                ),
                "flows": flow_records,
                "origins": {
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
                },
                "total_pcu": period.totals["all"],
                "approach_totals": {
                    name: value for name, value in period.totals.items() if name != "all"
                },
            }
    manifest_path = layout.traffic_manifest
    manifest_path.write_text(
        json.dumps(result, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
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
    print("Built official traffic scenarios: " + ", ".join(result["scenarios"]))


if __name__ == "__main__":
    main()
