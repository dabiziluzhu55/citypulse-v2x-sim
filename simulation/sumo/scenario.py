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
    if requested_origins:
        raise ScenarioCompilationError(
            "Origin filtering is unavailable for globally calibrated traffic; "
            "intersection_ids only select control and observation scope."
        )
    if step_length <= 0:
        raise ScenarioCompilationError("step_length must be positive.")

    layout = GeneratedArtifactLayout(generated_dir)
    traffic_manifest = _load_json(layout.traffic_manifest, "traffic manifest")
    if int(traffic_manifest.get("schema_version", 0)) != 3:
        raise ScenarioCompilationError(
            "traffic_manifest.json must use schema_version 3; rebuild official TLS."
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
    unavailable = set(intersection_ids) - set(scenario.get("intersection_ids", ()))
    if unavailable:
        raise ScenarioCompilationError(
            f"Global traffic scenario does not include intersections: {sorted(unavailable)}"
        )
    maximum_duration = float(scenario["demand_duration"]) - window_start_seconds
    if maximum_duration <= 0:
        raise ScenarioCompilationError(
            f"window_start_seconds is outside {scenario_id}."
        )
    official_start = _clock_seconds(scenario["official_time_range"]["start"])
    normalized_origins = {}
    vehicle_type_profiles = {
        str(type_id): str(profile_id)
        for type_id, profile_id in traffic_manifest.get(
            "vehicle_type_profiles", {}
        ).items()
    }
    if not vehicle_type_profiles:
        raise ScenarioCompilationError(
            "Global traffic manifest has no vehicle type/profile mapping."
        )

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
    seen_logics = set()
    route_path = generated_dir / str(scenario["route_file"])
    route_source = ET.parse(route_path).getroot()
    for vehicle_type in route_source.findall("vType"):
        vehicle_type_id = str(vehicle_type.get("id"))
        if vehicle_type_id not in vehicle_type_profiles:
            raise ScenarioCompilationError(
                f"Vehicle type {vehicle_type_id!r} has no profile mapping."
            )
        route_root.append(copy.deepcopy(vehicle_type))
    for element in route_source.findall("flow"):
        begin = float(element.get("begin", "0"))
        end = float(element.get("end", "0"))
        if end <= begin:
            raise ScenarioCompilationError(
                f"Global flow {element.get('id')!r} has an invalid time range."
            )
        overlap_begin = max(begin, window_start_seconds)
        overlap_end = min(end, window_end)
        if overlap_end <= overlap_begin:
            continue
        expected = (
            float(element.get("number", "0"))
            * (overlap_end - overlap_begin)
            / (end - begin)
            * flow_multiplier
        )
        candidates.append(
            _CandidateFlow(
                flow_id=str(element.get("id")),
                element=copy.deepcopy(element),
                begin=overlap_begin - window_start_seconds,
                end=overlap_end - window_start_seconds,
                expected=expected,
            )
        )

    additional_source = ET.parse(
        generated_dir / str(scenario["additional_file"])
    ).getroot()
    for child in additional_source:
        if child.tag != "tlLogic":
            additional_root.append(copy.deepcopy(child))
            continue
        key = (child.get("id"), child.get("programID"))
        if key not in seen_logics:
            additional_root.append(copy.deepcopy(child))
            seen_logics.add(key)

    if not candidates:
        raise ScenarioCompilationError("The selected time window contains no global traffic.")
    if not seen_logics:
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
    session_manifest = {
        "schema_version": 1,
        "session_id": session_id,
        "intersection_ids": list(intersection_ids),
        "period": period,
        "official_start_seconds": official_start,
        "window_start_seconds": window_start_seconds,
        "duration_seconds": actual_duration,
        "flow_multiplier": flow_multiplier,
        "planned_vehicle_count": planned_count,
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
        selected_origins=normalized_origins,
        vehicle_type_profiles=vehicle_type_profiles,
        vehicle_profiles=vehicle_profiles,
    )
