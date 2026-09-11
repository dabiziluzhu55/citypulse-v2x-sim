"""Load simulation catalog from generated manifests (no libsumo)."""

from __future__ import annotations

import json
import xml.etree.ElementTree as ET
from dataclasses import dataclass
from pathlib import Path
from typing import Mapping

from .dto import (
    IntersectionCapability,
    LaneCapability,
    OriginCapability,
    ScenarioScopeCapability,
    SimulationCatalog,
)
from .exceptions import SessionError
from .validation import DEFAULT_TRAFFIC_SCOPE_ID


@dataclass(frozen=True)
class GeneratedArtifactLayout:
    root: Path

    @property
    def network_file(self) -> Path:
        return self.root / "network" / "TotalMap_20.signals.net.xml"

    @property
    def tls_manifest(self) -> Path:
        return self.root / "manifests" / "tls_manifest.json"

    @property
    def traffic_manifest(self) -> Path:
        return self.root / "manifests" / "traffic_manifest.json"


def _read_json(path: Path) -> Mapping[str, object]:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (FileNotFoundError, json.JSONDecodeError) as exc:
        raise SessionError(f"Cannot read generated metadata {path}: {exc}") from exc


def _lane_specs(net_path: Path, required: set[str]) -> dict[str, tuple[float, float]]:
    result: dict[str, tuple[float, float]] = {}
    try:
        for _, element in ET.iterparse(net_path, events=("end",)):
            if element.tag == "lane" and element.get("id") in required:
                result[element.get("id")] = (
                    float(element.get("length", "0")),
                    float(element.get("speed", "0")),
                )
            element.clear()
    except (FileNotFoundError, ET.ParseError) as exc:
        raise SessionError(f"Cannot inspect generated network {net_path}: {exc}") from exc
    missing = required - set(result)
    if missing:
        raise SessionError(f"Generated network is missing lanes: {sorted(missing)}")
    return result


def load_catalog(generated_dir: Path | None = None) -> SimulationCatalog:
    if generated_dir is None:
        generated_dir = Path("data/maps/sumo/generated")
    layout = GeneratedArtifactLayout(generated_dir)
    traffic = _read_json(layout.traffic_manifest)
    tls = _read_json(layout.tls_manifest)
    if int(traffic.get("schema_version", 0)) != 3:
        raise SessionError("Rebuild traffic artifacts to obtain manifest schema_version 3.")
    if int(tls.get("schema_version", 0)) != 2:
        raise SessionError("Rebuild signal artifacts to obtain manifest schema_version 2.")
    mapping_path = generated_dir.parent / "official" / "map" / "TotalMap_20.intersections.json"
    mapping = _read_json(mapping_path)
    traffic_intersections = traffic.get("intersections", {})
    tls_intersections = tls.get("intersections", {})
    missing_tls = set(traffic_intersections) - set(tls_intersections)
    if missing_tls:
        raise SessionError(
            "Traffic manifest references intersections missing from the TLS manifest: "
            f"{sorted(missing_tls)}"
        )
    required_lanes = set()
    for intersection_id in traffic_intersections:
        for connection in tls_intersections[intersection_id]["connections"]:
            required_lanes.add(f"{connection['from_edge']}_{connection['from_lane']}")
            required_lanes.add(f"{connection['to_edge']}_{connection['to_lane']}")
    specs = _lane_specs(layout.network_file, required_lanes)

    intersections = {}
    period_order = {"morning_peak": 0, "off_peak": 1, "evening_peak": 2}
    raw_scopes = traffic.get("available_scopes", {})
    if not isinstance(raw_scopes, Mapping) or not raw_scopes:
        raw_scopes = {
            DEFAULT_TRAFFIC_SCOPE_ID: {
                "scope_id": DEFAULT_TRAFFIC_SCOPE_ID,
                "label": "Global official demand",
                "periods": sorted(
                    {
                        str(item.get("period_id"))
                        for item in traffic.get("scenarios", {}).values()
                        if str(item.get("scope_id", DEFAULT_TRAFFIC_SCOPE_ID))
                        == DEFAULT_TRAFFIC_SCOPE_ID
                    },
                    key=lambda value: (period_order.get(value, 99), value),
                ),
                "intersection_ids": list(traffic_intersections),
            }
        }
    scenario_scopes = {}
    for raw_scope_id, raw_scope in sorted(raw_scopes.items()):
        scope_id = str(raw_scope.get("scope_id", raw_scope_id))
        scenario_scopes[scope_id] = ScenarioScopeCapability(
            scope_id=scope_id,
            label=str(raw_scope.get("label", scope_id)),
            periods=tuple(
                sorted(
                    (str(value) for value in raw_scope.get("periods", ())),
                    key=lambda value: (period_order.get(value, 99), value),
                )
            ),
            intersection_ids=tuple(
                str(value) for value in raw_scope.get("intersection_ids", ())
            ),
        )
    for intersection_id, traffic_item in sorted(traffic_intersections.items()):
        tls_item = tls_intersections.get(intersection_id)
        if tls_item is None:
            continue
        origin_data = traffic_item.get("origins", {})
        origins = tuple(
            OriginCapability(
                origin_id=name,
                label=str(item["label"]),
                lane_ids=tuple(str(value) for value in item["lane_ids"]),
            )
            for name, item in sorted(origin_data.items())
        )
        sumo_to_official = {
            str(item["sumo_approach"]): (name, str(item["label"]))
            for name, item in origin_data.items()
        }
        lane_values = {}
        for connection in tls_item["connections"]:
            incoming = f"{connection['from_edge']}_{connection['from_lane']}"
            outgoing = f"{connection['to_edge']}_{connection['to_lane']}"
            official = sumo_to_official.get(str(connection["approach"]))
            lane_values[incoming] = (
                str(connection["from_edge"]),
                int(connection["from_lane"]),
                "incoming",
                official,
            )
            lane_values.setdefault(
                outgoing,
                (str(connection["to_edge"]), int(connection["to_lane"]), "outgoing", None),
            )
        lanes = tuple(
            LaneCapability(
                lane_id=lane_id,
                edge_id=value[0],
                lane_index=value[1],
                role=value[2],
                approach=value[3][0] if value[3] else None,
                approach_label=value[3][1] if value[3] else None,
                length=specs[lane_id][0],
                max_speed=specs[lane_id][1],
            )
            for lane_id, value in sorted(lane_values.items())
        )
        mapped = mapping.get(intersection_id, {})
        intersections[intersection_id] = IntersectionCapability(
            intersection_id=intersection_id,
            longitude=float(mapped["lon"]) if "lon" in mapped else None,
            latitude=float(mapped["lat"]) if "lat" in mapped else None,
            periods=tuple(
                sorted(
                    (str(value) for value in traffic_item.get("periods", ())),
                    key=lambda value: (period_order.get(value, 99), value),
                )
            ),
            origins=origins,
            lanes=lanes,
        )
    return SimulationCatalog(
        intersections=intersections,
        scenario_scopes=scenario_scopes,
    )

