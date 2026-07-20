"""Compile generated official traffic into one isolated runtime session."""

from __future__ import annotations

import copy
import json
import math
import xml.etree.ElementTree as ET
from dataclasses import dataclass
from pathlib import Path
from typing import Mapping, Sequence

from .artifacts import DEFAULT_GENERATED_DIR, GeneratedArtifactLayout
from .vehicle_profiles import VehicleProfile, VehicleProfileError, parse_vehicle_profiles

PROJECT_ROOT = Path(__file__).resolve().parents[2]
DEFAULT_SESSION_ROOT = PROJECT_ROOT / "outputs" / "sessions"


class ScenarioCompilationError(ValueError):
    """Raised before SUMO starts when a requested session is not buildable."""


@dataclass(frozen=True)
class CompiledScenario:
    session_id: str
    directory: Path
    sumocfg: Path
    route_file: Path
    additional_file: Path
    period: str
    official_start_seconds: int
    window_start_seconds: float
    duration_seconds: float
    planned_vehicle_count: int
    official_complete_demand: bool
    selected_origins: Mapping[str, tuple[str, ...]]
    vehicle_type_profiles: Mapping[str, str]
    vehicle_profiles: Mapping[str, VehicleProfile]


@dataclass
class _CandidateFlow:
    flow_id: str
    element: ET.Element
    begin: float
    end: float
    expected: float
    count: int = 0


def _load_json(path: Path, description: str) -> Mapping[str, object]:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError as exc:
        raise ScenarioCompilationError(f"Missing {description}: {path}") from exc
    except json.JSONDecodeError as exc:
        raise ScenarioCompilationError(f"Invalid {description}: {path}: {exc}") from exc


def _clock_seconds(value: str) -> int:
    try:
        hour, minute, second = (int(part) for part in value.split(":"))
    except (TypeError, ValueError) as exc:
        raise ScenarioCompilationError(f"Invalid official clock {value!r}.") from exc
    return hour * 3600 + minute * 60 + second


def _format_number(value: float) -> str:
    return f"{value:g}"


def _allocate_counts(candidates: list[_CandidateFlow]) -> None:
    target = int(math.floor(sum(item.expected for item in candidates) + 0.5))
    for item in candidates:
        item.count = int(math.floor(item.expected))
    remainder = target - sum(item.count for item in candidates)
    ranked = sorted(
        candidates,
        key=lambda item: (-(item.expected - math.floor(item.expected)), item.flow_id),
    )
    for item in ranked[:remainder]:
        item.count += 1


def _validate_request(
    intersection_ids: Sequence[str],
    period: str,
    origins: Mapping[str, Sequence[str]],
    window_start_seconds: float,
    duration_seconds: float | None,
    flow_multiplier: float,
) -> None:
    if not intersection_ids or len(intersection_ids) != len(set(intersection_ids)):
        raise ScenarioCompilationError("intersection_ids must be non-empty and unique.")
    if period not in {"morning_peak", "off_peak", "evening_peak"}:
        raise ScenarioCompilationError(f"Unsupported period: {period!r}.")
    if window_start_seconds < 0:
        raise ScenarioCompilationError("window_start_seconds cannot be negative.")
    if duration_seconds is not None and duration_seconds <= 0:
        raise ScenarioCompilationError("duration_seconds must be positive.")
    if not 0.1 <= flow_multiplier <= 5.0:
        raise ScenarioCompilationError("flow_multiplier must be between 0.1 and 5.0.")
    unknown_origin_intersections = set(origins) - set(intersection_ids)
    if unknown_origin_intersections:
        raise ScenarioCompilationError(
            "Origins reference unselected intersections: "
            f"{sorted(unknown_origin_intersections)}"
        )
    if any(not values for values in origins.values()):
        raise ScenarioCompilationError("A provided origins list cannot be empty.")
    if any(len(values) != len(set(values)) for values in origins.values()):
        raise ScenarioCompilationError("A provided origins list cannot contain duplicates.")


def compile_session_scenario(
    session_id: str,
    intersection_ids: Sequence[str],
    period: str,
    *,
    origins: Mapping[str, Sequence[str]] | None = None,
    window_start_seconds: float = 0.0,
    duration_seconds: float | None = None,
    flow_multiplier: float = 1.0,
    step_length: float = 0.05,
    generated_dir: Path = DEFAULT_GENERATED_DIR,
    session_root: Path = DEFAULT_SESSION_ROOT,
) -> CompiledScenario:
    requested_origins = origins or {}
    _validate_request(
        intersection_ids,
        period,
        requested_origins,
        window_start_seconds,
        duration_seconds,
        flow_multiplier,
    )
    if step_length <= 0:
        raise ScenarioCompilationError("step_length must be positive.")

    layout = GeneratedArtifactLayout(generated_dir)
    traffic_manifest = _load_json(layout.traffic_manifest, "traffic manifest")
    if int(traffic_manifest.get("schema_version", 0)) != 3:
        raise ScenarioCompilationError(
            "traffic_manifest.json must use schema_version 3; rebuild global official traffic."
        )
    scenarios = traffic_manifest.get("scenarios", {})
    try:
        vehicle_profiles = parse_vehicle_profiles(
            {
                "schema_version": traffic_manifest.get(
                    "vehicle_profile_schema_version", 0
                ),
                "profiles": traffic_manifest.get("vehicle_profiles", {}),
            }
        )
    except VehicleProfileError as exc:
        raise ScenarioCompilationError(
            "Traffic manifest has invalid vehicle profiles; rebuild official TLS and "
            f"traffic artifacts: {exc}"
        ) from exc
    scenario_id = f"global_{period}"
    if scenario_id not in scenarios:
        raise ScenarioCompilationError(f"Traffic scenario {scenario_id!r} is unavailable.")
    scenario = scenarios[scenario_id]
    built_intersections = set(str(value) for value in scenario.get("intersection_ids", ()))
    missing_intersections = set(intersection_ids) - built_intersections
    if missing_intersections:
        raise ScenarioCompilationError(
            f"Global traffic scenario does not contain: {sorted(missing_intersections)}"
        )
    maximum_duration = float(scenario["demand_duration"]) - window_start_seconds
    if maximum_duration <= 0:
        raise ScenarioCompilationError(
            f"window_start_seconds is outside {scenario_id}."
        )
    official_start = _clock_seconds(scenario["official_time_range"]["start"])
    origin_catalog = traffic_manifest.get("origins", {})
    normalized_origins = {}
    for intersection_id in intersection_ids:
        available_origins = set(origin_catalog.get(intersection_id, {}))
        selected_origin_names = tuple(
            requested_origins.get(intersection_id, sorted(available_origins))
        )
        unknown = set(selected_origin_names) - available_origins
        if unknown:
            raise ScenarioCompilationError(
                f"{intersection_id} has unknown origins: {sorted(unknown)}"
            )
        normalized_origins[intersection_id] = selected_origin_names
    if not scenario.get("sumo_vehicle_type_id") or not scenario.get(
        "vehicle_profile_id"
    ):
        raise ScenarioCompilationError(
            f"Traffic scenario {scenario_id!r} has no vehicle profile metadata; "
            "rebuild official TLS and global traffic artifacts."
        )
    vehicle_type_profiles = {
        str(scenario["sumo_vehicle_type_id"]): str(scenario["vehicle_profile_id"])
    }

    actual_duration = maximum_duration if duration_seconds is None else duration_seconds
    if actual_duration > maximum_duration + 1e-9:
        raise ScenarioCompilationError(
            f"duration_seconds exceeds the remaining period ({maximum_duration:g}s)."
        )
    net_path = layout.network_file
    if not net_path.is_file():
        raise ScenarioCompilationError(f"Generated signal network is missing: {net_path}")
    window_end = window_start_seconds + actual_duration
    session_dir = session_root / session_id
    session_dir.mkdir(parents=True, exist_ok=False)

    route_root = ET.Element("routes")
    candidates: list[_CandidateFlow] = []
    additional_root = ET.Element("additional")
    route_path = generated_dir / str(scenario["route_file"])
    route_source = ET.parse(route_path).getroot()
    flow_elements = {
        element.get("id"): element for element in route_source.findall("flow")
    }
    for vehicle_type in route_source.findall("vType"):
        route_root.append(copy.deepcopy(vehicle_type))
    for record in scenario.get("flows", []):
        source_intersection = str(record["source_intersection_id"])
        source_approach = str(record["source_official_approach"])
        if (
            source_intersection in requested_origins
            and source_approach not in set(requested_origins[source_intersection])
        ):
            continue
        begin = float(record["begin"])
        end = float(record["end"])
        overlap_begin = max(begin, window_start_seconds)
        overlap_end = min(end, window_end)
        if overlap_end <= overlap_begin:
            continue
        flow_id = str(record["flow_id"])
        if flow_id not in flow_elements:
            raise ScenarioCompilationError(
                f"Flow {flow_id!r} is missing from {route_path}."
            )
        expected = (
            float(record["number"])
            * (overlap_end - overlap_begin)
            / (end - begin)
            * flow_multiplier
        )
        candidates.append(
            _CandidateFlow(
                flow_id=flow_id,
                element=copy.deepcopy(flow_elements[flow_id]),
                begin=overlap_begin - window_start_seconds,
                end=overlap_end - window_start_seconds,
                expected=expected,
            )
        )

    additional_source = ET.parse(
        generated_dir / str(scenario["additional_file"])
    ).getroot()
    for child in additional_source:
        additional_root.append(copy.deepcopy(child))

    if not candidates:
        raise ScenarioCompilationError("The selected origins and time window contain no traffic.")
    if not additional_root.findall("tlLogic"):
        raise ScenarioCompilationError("Selected scenarios contain no signal programs.")
    _allocate_counts(candidates)
    ET.SubElement(
        route_root,
        "vType",
        {
            "id": "citypulse_disturbance_vehicle",
            "vClass": "passenger",
            "color": "255,0,0",
            "length": "5",
            "maxSpeed": "1",
        },
    )
    for candidate in sorted(candidates, key=lambda item: (item.begin, item.flow_id)):
        if candidate.count <= 0:
            continue
        candidate.element.set("begin", _format_number(candidate.begin))
        candidate.element.set("end", _format_number(candidate.end))
        candidate.element.set("number", str(candidate.count))
        route_root.append(candidate.element)
    route_file = session_dir / "session.rou.xml"
    additional_file = session_dir / "session.add.xml"
    ET.indent(route_root, space="  ")
    ET.ElementTree(route_root).write(route_file, encoding="utf-8", xml_declaration=True)
    ET.indent(additional_root, space="  ")
    ET.ElementTree(additional_root).write(
        additional_file, encoding="utf-8", xml_declaration=True
    )

    config_root = ET.Element("configuration")
    input_node = ET.SubElement(config_root, "input")
    ET.SubElement(input_node, "net-file", {"value": str(net_path.resolve())})
    ET.SubElement(input_node, "route-files", {"value": route_file.name})
    ET.SubElement(input_node, "additional-files", {"value": additional_file.name})
    time_node = ET.SubElement(config_root, "time")
    ET.SubElement(time_node, "begin", {"value": "0"})
    ET.SubElement(time_node, "end", {"value": _format_number(actual_duration)})
    ET.SubElement(time_node, "step-length", {"value": _format_number(step_length)})
    processing = ET.SubElement(config_root, "processing")
    ET.SubElement(processing, "time-to-teleport", {"value": "-1"})
    sumocfg = session_dir / "session.sumocfg"
    ET.indent(config_root, space="  ")
    ET.ElementTree(config_root).write(sumocfg, encoding="utf-8", xml_declaration=True)

    planned_count = sum(item.count for item in candidates)
    official_complete_demand = (
        not requested_origins
        and abs(window_start_seconds) <= 1e-9
        and abs(actual_duration - float(scenario["demand_duration"])) <= 1e-9
        and abs(flow_multiplier - 1.0) <= 1e-9
    )
    session_manifest = {
        "schema_version": 1,
        "session_id": session_id,
        "intersection_ids": list(intersection_ids),
        "global_traffic_intersection_ids": sorted(built_intersections),
        "period": period,
        "official_start_seconds": official_start,
        "window_start_seconds": window_start_seconds,
        "duration_seconds": actual_duration,
        "flow_multiplier": flow_multiplier,
        "planned_vehicle_count": planned_count,
        "official_complete_demand": official_complete_demand,
        "origins": {key: list(value) for key, value in normalized_origins.items()},
        "vehicle_type_profiles": vehicle_type_profiles,
    }
    (session_dir / "session_manifest.json").write_text(
        json.dumps(session_manifest, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    return CompiledScenario(
        session_id=session_id,
        directory=session_dir,
        sumocfg=sumocfg,
        route_file=route_file,
        additional_file=additional_file,
        period=period,
        official_start_seconds=int(official_start),
        window_start_seconds=float(window_start_seconds),
        duration_seconds=float(actual_duration),
        planned_vehicle_count=planned_count,
        official_complete_demand=official_complete_demand,
        selected_origins=normalized_origins,
        vehicle_type_profiles=vehicle_type_profiles,
        vehicle_profiles=vehicle_profiles,
    )
