"""Build a derived SUMO network and official signal programs.

The canonical TotalMap_20.net.xml is read-only. All generated files are placed
under data/maps/sumo/generated.
"""

from __future__ import annotations

import argparse
import csv
import hashlib
import json
import os
import re
import shutil
import subprocess
import sys
import xml.etree.ElementTree as ET
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Dict, Iterable, List, Mapping, MutableMapping, Sequence, Tuple

from .config import (
    IntersectionConfiguration,
    SignalConfigurationError,
    load_signal_configuration,
)
from .traffic import TrafficDemandError


PROJECT_ROOT = Path(__file__).resolve().parents[2]
SUMO_DIR = PROJECT_ROOT / "data" / "maps" / "sumo"
DEFAULT_MAPPING = SUMO_DIR / "TotalMap_20.intersections.json"
DEFAULT_PLANS = SUMO_DIR / "official_tls_plans.json"
DEFAULT_TOPOLOGY = SUMO_DIR / "official_tls_topology.json"
DEFAULT_BASE_NET = SUMO_DIR / "TotalMap_20.net.xml"
DEFAULT_OUTPUT_DIR = SUMO_DIR / "generated"


@dataclass(frozen=True)
class ControlledConnection:
    intersection_id: str
    junction_id: str
    tls_id: str
    link_index: int
    approach: str
    movement: str
    from_edge: str
    from_lane: int
    to_edge: str
    to_lane: int
    direction: str
    via: str
    request_index: int

    @property
    def lane_id(self) -> str:
        return f"{self.from_edge}_{self.from_lane}"


def _binary(name: str) -> str:
    executable = f"{name}.exe" if os.name == "nt" else name
    sumo_home = os.environ.get("SUMO_HOME")
    candidates = []
    if sumo_home:
        candidates.append(Path(sumo_home) / "bin" / executable)
    located = shutil.which(name)
    if located:
        candidates.append(Path(located))
    for candidate in candidates:
        if candidate.is_file():
            return str(candidate)
    raise RuntimeError(
        f"Cannot find {name!r}. Set SUMO_HOME or add the SUMO bin directory to PATH."
    )


def _version(binary: str) -> str:
    result = subprocess.run(
        [binary, "--version"],
        check=True,
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
    )
    return result.stdout.splitlines()[0].strip()


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _run_netconvert(
    netconvert: str,
    source_net: Path,
    target_net: Path,
    junction_ids: Sequence[str],
) -> None:
    target_net.parent.mkdir(parents=True, exist_ok=True)
    command = [
        netconvert,
        "--sumo-net-file",
        str(source_net),
        "--tls.set",
        ",".join(junction_ids),
        "--tls.default-type",
        "static",
        "--offset.disable-normalization",
        "true",
        "--output-file",
        str(target_net),
    ]
    subprocess.run(command, check=True)


def _remove_empty_params(net_path: Path) -> int:
    """Remove empty SUMO params that crash older NLHandler implementations."""
    ET.register_namespace("xsi", "http://www.w3.org/2001/XMLSchema-instance")
    tree = ET.parse(net_path)
    root = tree.getroot()
    removed = 0
    for parent in root.iter():
        for child in list(parent):
            if child.tag != "param":
                continue
            if child.get("value", "").strip():
                continue
            parent.remove(child)
            removed += 1
    if not removed:
        return 0

    temporary_path = net_path.with_name(f"{net_path.name}.sanitized.tmp")
    try:
        tree.write(temporary_path, encoding="utf-8", xml_declaration=True)
        temporary_path.replace(net_path)
    finally:
        if temporary_path.exists():
            temporary_path.unlink()
    return removed


def _junction_and_request_index(via: str, junction_ids: Iterable[str]) -> Tuple[str, int]:
    for junction_id in sorted(junction_ids, key=len, reverse=True):
        match = re.match(rf"^:{re.escape(junction_id)}_(\d+)_", via)
        if match:
            return junction_id, int(match.group(1))
    raise SignalConfigurationError(
        f"Controlled connection via={via!r} does not belong to a mapped junction."
    )


def _inspect_generated_network(
    net_path: Path,
    selected: Sequence[IntersectionConfiguration],
) -> Tuple[
    List[ControlledConnection],
    Mapping[str, int],
    Mapping[str, Mapping[int, str]],
]:
    edge_owner = {}
    junction_owner = {}
    for config in selected:
        for edge_id in config.topology.incoming_edges:
            if edge_id in edge_owner:
                raise SignalConfigurationError(
                    f"Incoming edge {edge_id!r} is shared by multiple official intersections."
                )
            edge_owner[edge_id] = config
        for junction_id in config.junction_ids:
            if junction_id in junction_owner:
                raise SignalConfigurationError(
                    f"Junction {junction_id!r} is shared by multiple official intersections."
                )
            junction_owner[junction_id] = config

    found_junction_types = {}
    request_foes: Dict[str, Dict[int, str]] = {}
    max_link_index: Dict[str, int] = {}
    raw_connections = []

    for _, elem in ET.iterparse(net_path, events=("end",)):
        if elem.tag == "junction":
            junction_id = elem.get("id", "")
            if junction_id in junction_owner:
                found_junction_types[junction_id] = elem.get("type", "")
                request_foes[junction_id] = {
                    int(item.get("index", "-1")): item.get("foes", "")
                    for item in elem.findall("request")
                }
            elem.clear()
            continue
        if elem.tag != "connection":
            if elem.tag in {"edge", "tlLogic"}:
                elem.clear()
            continue

        tls_id = elem.get("tl")
        link_index = elem.get("linkIndex")
        if tls_id is not None and link_index is not None:
            index = int(link_index)
            max_link_index[tls_id] = max(max_link_index.get(tls_id, -1), index)

        from_edge = elem.get("from", "")
        if from_edge in edge_owner:
            raw_connections.append(dict(elem.attrib))
        elem.clear()

    missing_junctions = set(junction_owner) - set(found_junction_types)
    if missing_junctions:
        raise SignalConfigurationError(
            f"Mapped junctions are missing from generated network: {sorted(missing_junctions)}"
        )
    non_tls = {
        junction_id: junction_type
        for junction_id, junction_type in found_junction_types.items()
        if junction_type != "traffic_light"
    }
    if non_tls:
        raise SignalConfigurationError(
            f"netconvert did not signalize mapped junctions: {non_tls}"
        )

    connections = []
    for raw in raw_connections:
        config = edge_owner[raw["from"]]
        if "tl" not in raw or "linkIndex" not in raw:
            raise SignalConfigurationError(
                f"{config.intersection_id}: connection from {raw['from']} is not TLS controlled."
            )
        direction = raw.get("dir", "")
        movement = config.topology.direction_mapping.get(direction)
        if movement is None:
            raise SignalConfigurationError(
                f"{config.intersection_id}: unsupported SUMO direction {direction!r} "
                f"on {raw.get('from')} -> {raw.get('to')}."
            )
        junction_id, request_index = _junction_and_request_index(
            raw.get("via", ""), config.junction_ids
        )
        connections.append(
            ControlledConnection(
                intersection_id=config.intersection_id,
                junction_id=junction_id,
                tls_id=raw["tl"],
                link_index=int(raw["linkIndex"]),
                approach=config.topology.approach_for_edge(raw["from"]),
                movement=movement,
                from_edge=raw["from"],
                from_lane=int(raw.get("fromLane", 0)),
                to_edge=raw.get("to", ""),
                to_lane=int(raw.get("toLane", 0)),
                direction=direction,
                via=raw.get("via", ""),
                request_index=request_index,
            )
        )

    if not connections:
        raise SignalConfigurationError("No official controlled connections were found.")
    for config in selected:
        present_edges = {
            item.from_edge
            for item in connections
            if item.intersection_id == config.intersection_id
        }
        missing_edges = set(config.topology.incoming_edges) - present_edges
        if missing_edges:
            raise SignalConfigurationError(
                f"{config.intersection_id}: no controlled connections for {sorted(missing_edges)}"
            )
    return connections, {key: value + 1 for key, value in max_link_index.items()}, request_foes


def _set_state_char(
    states: MutableMapping[str, List[str]],
    connection: ControlledConnection,
    value: str,
) -> None:
    current = states[connection.tls_id][connection.link_index]
    if current != "r" and current != value:
        raise SignalConfigurationError(
            f"TLS {connection.tls_id} linkIndex {connection.link_index} needs both "
            f"{current!r} and {value!r}. Check topology grouping."
        )
    states[connection.tls_id][connection.link_index] = value


def _is_foe(foes: str, other_request_index: int) -> bool:
    bit_index = len(foes) - 1 - other_request_index
    return 0 <= bit_index < len(foes) and foes[bit_index] == "1"


def _validate_protected_movements(
    protected: Sequence[ControlledConnection],
    request_foes: Mapping[str, Mapping[int, str]],
    intersection_id: str,
    phase_number: int,
) -> None:
    for position, first in enumerate(protected):
        for second in protected[position + 1 :]:
            if first.junction_id != second.junction_id:
                continue
            if first.request_index == second.request_index:
                continue
            matrix = request_foes.get(first.junction_id, {})
            first_foes = matrix.get(first.request_index, "")
            second_foes = matrix.get(second.request_index, "")
            if _is_foe(first_foes, second.request_index) or _is_foe(
                second_foes, first.request_index
            ):
                raise SignalConfigurationError(
                    f"{intersection_id}/phase {phase_number}: protected connections "
                    f"{first.via} and {second.via} conflict according to the SUMO foe matrix."
                )


def _build_templates(
    config: IntersectionConfiguration,
    connections: Sequence[ControlledConnection],
    state_lengths: Mapping[str, int],
    request_foes: Mapping[str, Mapping[int, str]],
) -> Mapping[int, Mapping[str, Mapping[str, str]]]:
    own_connections = [
        item for item in connections if item.intersection_id == config.intersection_id
    ]
    tls_ids = sorted({item.tls_id for item in own_connections})
    templates = {}
    served_connections = set()
    for phase_mapping in config.topology.phases:
        protected = [
            item
            for item in own_connections
            if item.approach in phase_mapping.approaches
            and item.movement == phase_mapping.movement
        ]
        if not protected:
            raise SignalConfigurationError(
                f"{config.intersection_id}/phase {phase_mapping.phase_number}: "
                "no protected connections matched."
            )
        _validate_protected_movements(
            protected,
            request_foes,
            config.intersection_id,
            phase_mapping.phase_number,
        )
        permissive = []
        for group in phase_mapping.permissive:
            matches = [
                item
                for item in own_connections
                if item.approach in group.approaches
                and item.movement == group.movement
            ]
            if not matches:
                raise SignalConfigurationError(
                    f"{config.intersection_id}/phase {phase_mapping.phase_number}: "
                    f"no permissive {group.movement} connections matched for "
                    f"{group.approaches}."
                )
            permissive.extend(matches)
        green = {tls: ["r"] * state_lengths[tls] for tls in tls_ids}
        yellow = {tls: ["r"] * state_lengths[tls] for tls in tls_ids}
        clearance = {tls: ["r"] * state_lengths[tls] for tls in tls_ids}
        for connection in own_connections:
            if connection.movement == "right":
                _set_state_char(green, connection, "g")
                _set_state_char(yellow, connection, "g")
                _set_state_char(clearance, connection, "g")
                served_connections.add(connection)
        for connection in permissive:
            _set_state_char(green, connection, "g")
            _set_state_char(yellow, connection, "y")
            served_connections.add(connection)
        for connection in protected:
            _set_state_char(green, connection, "G")
            _set_state_char(yellow, connection, "y")
            served_connections.add(connection)
        templates[phase_mapping.phase_number] = {
            tls_id: {
                "green": "".join(green[tls_id]),
                "yellow": "".join(yellow[tls_id]),
                "clearance": "".join(clearance[tls_id]),
            }
            for tls_id in tls_ids
        }
    unserved = [
        connection
        for connection in own_connections
        if connection.movement != "blocked" and connection not in served_connections
    ]
    if unserved:
        details = ", ".join(
            f"{item.from_edge}->{item.to_edge}({item.movement})"
            for item in unserved
        )
        raise SignalConfigurationError(
            f"{config.intersection_id}: normal movements are never served: {details}."
        )
    return templates


def _append_phase(parent: ET.Element, duration: float, state: str, name: str) -> None:
    if duration <= 0:
        return
    ET.SubElement(
        parent,
        "phase",
        {"duration": f"{duration:g}", "state": state, "name": name},
    )


def _write_additional(
    path: Path,
    selected: Sequence[IntersectionConfiguration],
    templates_by_intersection: Mapping[str, Mapping[int, Mapping[str, Mapping[str, str]]]],
) -> None:
    root = ET.Element("additional")
    seen = set()
    for config in selected:
        templates = templates_by_intersection[config.intersection_id]
        tls_ids = sorted({tls for value in templates.values() for tls in value})
        for program in config.programs.values():
            for tls_id in tls_ids:
                key = (tls_id, program.program_id)
                if key in seen:
                    raise SignalConfigurationError(f"Duplicated SUMO program: {key}")
                seen.add(key)
                logic = ET.SubElement(
                    root,
                    "tlLogic",
                    {
                        "id": tls_id,
                        "type": "static",
                        "programID": program.program_id,
                        "offset": "0",
                    },
                )
                for phase in program.phases:
                    states = templates[phase.number][tls_id]
                    _append_phase(logic, phase.green, states["green"], f"p{phase.number}_green")
                    _append_phase(logic, phase.yellow, states["yellow"], f"p{phase.number}_yellow")
                    _append_phase(
                        logic,
                        phase.clearance,
                        states["clearance"],
                        f"p{phase.number}_clearance",
                    )
    ET.indent(root, space="  ")
    ET.ElementTree(root).write(path, encoding="utf-8", xml_declaration=True)


def _write_connection_report(path: Path, connections: Sequence[ControlledConnection]) -> None:
    fieldnames = list(asdict(connections[0]).keys()) + ["lane_id"]
    with path.open("w", encoding="utf-8-sig", newline="") as stream:
        writer = csv.DictWriter(stream, fieldnames=fieldnames)
        writer.writeheader()
        for connection in sorted(
            connections,
            key=lambda item: (item.intersection_id, item.tls_id, item.link_index),
        ):
            row = asdict(connection)
            row["lane_id"] = connection.lane_id
            writer.writerow(row)


def _write_validation_routes(path: Path, connections: Sequence[ControlledConnection]) -> None:
    root = ET.Element("routes")
    ET.SubElement(
        root,
        "vType",
        {
            "id": "validation_car",
            "vClass": "passenger",
            "accel": "2.6",
            "decel": "4.5",
            "length": "5.0",
            "maxSpeed": "13.9",
        },
    )
    candidates = {}
    for connection in sorted(
        connections,
        key=lambda item: (item.direction == "t", item.from_lane, item.to_lane),
    ):
        if connection.movement == "blocked":
            continue
        key = (connection.intersection_id, connection.approach, connection.movement)
        candidates.setdefault(key, connection)
    for index, (key, connection) in enumerate(sorted(candidates.items())):
        intersection_id, approach, movement = key
        flow = ET.SubElement(
            root,
            "flow",
            {
                "id": f"{intersection_id}_{approach}_{movement}",
                "type": "validation_car",
                "begin": f"{index * 1.5:g}",
                "end": "200",
                "period": "30",
                "departLane": str(connection.from_lane),
                "departSpeed": "max",
            },
        )
        ET.SubElement(
            flow,
            "route",
            {"edges": f"{connection.from_edge} {connection.to_edge}"},
        )
    ET.indent(root, space="  ")
    ET.ElementTree(root).write(path, encoding="utf-8", xml_declaration=True)


def _write_sumocfg(path: Path) -> None:
    root = ET.Element("configuration")
    input_node = ET.SubElement(root, "input")
    ET.SubElement(input_node, "net-file", {"value": "TotalMap_20.signals.net.xml"})
    ET.SubElement(input_node, "route-files", {"value": "official_tls_validation.rou.xml"})
    ET.SubElement(input_node, "additional-files", {"value": "official_tls.add.xml"})
    time_node = ET.SubElement(root, "time")
    ET.SubElement(time_node, "begin", {"value": "0"})
    ET.SubElement(time_node, "end", {"value": "200"})
    ET.SubElement(time_node, "step-length", {"value": "0.05"})
    processing = ET.SubElement(root, "processing")
    ET.SubElement(processing, "time-to-teleport", {"value": "-1"})
    ET.indent(root, space="  ")
    ET.ElementTree(root).write(path, encoding="utf-8", xml_declaration=True)


def build(
    intersection_ids: Sequence[str],
    mapping_path: Path = DEFAULT_MAPPING,
    plans_path: Path = DEFAULT_PLANS,
    topology_path: Path = DEFAULT_TOPOLOGY,
    source_net: Path = DEFAULT_BASE_NET,
    output_dir: Path = DEFAULT_OUTPUT_DIR,
) -> Mapping[str, object]:
    configuration = load_signal_configuration(mapping_path, plans_path, topology_path)
    selected = configuration.select(intersection_ids)
    junction_ids = sorted({item for config in selected for item in config.junction_ids})
    netconvert = _binary("netconvert")
    sumo = _binary("sumo")
    output_dir.mkdir(parents=True, exist_ok=True)
    target_net = output_dir / "TotalMap_20.signals.net.xml"
    _run_netconvert(netconvert, source_net, target_net, junction_ids)
    removed_empty_params = _remove_empty_params(target_net)
    if removed_empty_params:
        print(
            f"Removed {removed_empty_params} empty SUMO <param> elements "
            f"from {target_net}."
        )
    connections, state_lengths, request_foes = _inspect_generated_network(target_net, selected)
    templates = {
        config.intersection_id: _build_templates(
            config, connections, state_lengths, request_foes
        )
        for config in selected
    }
    additional_path = output_dir / "official_tls.add.xml"
    _write_additional(additional_path, selected, templates)
    _write_connection_report(output_dir / "official_tls_connections.csv", connections)
    _write_validation_routes(output_dir / "official_tls_validation.rou.xml", connections)
    _write_sumocfg(output_dir / "official_tls.sumocfg")

    manifest = {
        "schema_version": 1,
        "source_net": str(source_net.resolve()),
        "source_net_sha256": _sha256(source_net),
        "netconvert_version": _version(netconvert),
        "sumo_version": _version(sumo),
        "removed_empty_params": removed_empty_params,
        "intersections": {},
    }
    for config in selected:
        own_connections = [
            item for item in connections if item.intersection_id == config.intersection_id
        ]
        intersection_templates = templates[config.intersection_id]
        manifest["intersections"][config.intersection_id] = {
            "junction_ids": list(config.junction_ids),
            "tls_ids": sorted({item.tls_id for item in own_connections}),
            "program_ids": list(config.programs),
            "phase_order": [item.phase_number for item in config.topology.phases],
            "phase_movements": [asdict(item) for item in config.topology.phases],
            "incoming_lanes": {
                approach: sorted(
                    {
                        item.lane_id
                        for item in own_connections
                        if item.approach == approach
                    }
                )
                for approach in config.topology.approaches
            },
            "templates": {
                str(phase_number): value
                for phase_number, value in intersection_templates.items()
            },
            "connections": [asdict(item) for item in own_connections],
        }
    manifest_path = output_dir / "tls_manifest.json"
    manifest_path.write_text(
        json.dumps(manifest, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    from .build_traffic import build_traffic_scenarios

    build_traffic_scenarios(
        manifest,
        output_dir=output_dir,
    )
    return manifest


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--intersections", nargs="+", default=["demo_2"])
    parser.add_argument("--mapping", type=Path, default=DEFAULT_MAPPING)
    parser.add_argument("--plans", type=Path, default=DEFAULT_PLANS)
    parser.add_argument("--topology", type=Path, default=DEFAULT_TOPOLOGY)
    parser.add_argument("--source-net", type=Path, default=DEFAULT_BASE_NET)
    parser.add_argument("--output-dir", type=Path, default=DEFAULT_OUTPUT_DIR)
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    try:
        manifest = build(
            intersection_ids=args.intersections,
            mapping_path=args.mapping,
            plans_path=args.plans,
            topology_path=args.topology,
            source_net=args.source_net,
            output_dir=args.output_dir,
        )
    except (
        SignalConfigurationError,
        TrafficDemandError,
        RuntimeError,
        subprocess.CalledProcessError,
    ) as exc:
        raise SystemExit(f"TLS build failed: {exc}") from exc
    print(
        "Built official TLS artifacts for: "
        + ", ".join(manifest["intersections"].keys())
    )


if __name__ == "__main__":
    main()

