"""Build a derived SUMO network and official signal programs."""

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

from .artifacts import DEFAULT_GENERATED_DIR, GeneratedArtifactLayout
from .config import (
    IntersectionConfiguration,
    PhaseMovement,
    SignalConfigurationError,
    load_signal_configuration,
)
from .traffic import TrafficDemandError
from .vehicle_profiles import VehicleProfileError


PROJECT_ROOT = Path(__file__).resolve().parents[2]
SUMO_DIR = PROJECT_ROOT / "data" / "maps" / "sumo"
DEFAULT_MAPPING = SUMO_DIR / "TotalMap_20.intersections.json"
DEFAULT_PLANS = SUMO_DIR / "official_tls_plans.json"
DEFAULT_TOPOLOGY = SUMO_DIR / "official_tls_topology.json"
DEFAULT_BASE_NET = SUMO_DIR / "TotalMap_20.net.xml"
DEFAULT_OUTPUT_DIR = DEFAULT_GENERATED_DIR


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
    blocked_turnarounds: Sequence[Tuple[str, str]] = (),
    refresh_junction_ids: Sequence[str] = (),
) -> Tuple[bool, int]:
    target_net.parent.mkdir(parents=True, exist_ok=True)
    junction_types = _read_junction_types(source_net, junction_ids)
    refresh_junctions = set(refresh_junction_ids) & set(junction_ids)
    junctions_to_signal = [
        junction_id
        for junction_id in junction_ids
        if junction_types[junction_id] != "traffic_light"
        or junction_id in refresh_junctions
    ]
    if not junctions_to_signal and not blocked_turnarounds:
        shutil.copy2(source_net, target_net)
        return False, 0

    sanitized_source = target_net.with_name(
        f".{target_net.name}.netconvert-input.net.xml"
    )
    connection_deletions = target_net.with_name(
        f".{target_net.name}.blocked-turnarounds.con.xml"
    )
    shutil.copy2(source_net, sanitized_source)
    try:
        removed_empty_params = _remove_empty_params(sanitized_source)
        _reset_tls_control_attrs(sanitized_source, refresh_junctions)
        command = [
            netconvert,
            "--sumo-net-file",
            str(sanitized_source),
        ]
        if junctions_to_signal:
            command.extend(
                [
                    "--tls.set",
                    ",".join(junctions_to_signal),
                    "--tls.default-type",
                    "static",
                ]
            )
        if blocked_turnarounds:
            root = ET.Element("connections")
            for from_edge, to_edge in blocked_turnarounds:
                ET.SubElement(root, "delete", {"from": from_edge, "to": to_edge})
            ET.indent(root, space="  ")
            ET.ElementTree(root).write(
                connection_deletions, encoding="utf-8", xml_declaration=True
            )
            command.extend(["--connection-files", str(connection_deletions)])
        command.extend(
            [
                "--offset.disable-normalization",
                "true",
                "--output-file",
                str(target_net),
            ]
        )
        subprocess.run(command, check=True)
    finally:
        sanitized_source.unlink(missing_ok=True)
        connection_deletions.unlink(missing_ok=True)
    return True, removed_empty_params


def _junction_id_from_via(via: str, junction_ids: Iterable[str]) -> str | None:
    for junction_id in sorted(junction_ids, key=len, reverse=True):
        if re.match(rf"^:{re.escape(junction_id)}_\d+_", via):
            return junction_id
    return None


def _reset_tls_control_attrs(net_path: Path, junction_ids: Iterable[str]) -> int:
    refresh_junctions = tuple(junction_ids)
    if not refresh_junctions:
        return 0
    tree = ET.parse(net_path)
    root = tree.getroot()
    removed = 0
    for elem in root.iter("connection"):
        if _junction_id_from_via(elem.get("via", ""), refresh_junctions) is None:
            continue
        for attribute in ("tl", "linkIndex", "linkIndex2", "uncontrolled", "state"):
            if attribute in elem.attrib:
                del elem.attrib[attribute]
                removed += 1
    if not removed:
        return 0

    temporary_path = net_path.with_name(f"{net_path.name}.tls-refresh.tmp")
    try:
        tree.write(temporary_path, encoding="utf-8", xml_declaration=True)
        temporary_path.replace(net_path)
    finally:
        if temporary_path.exists():
            temporary_path.unlink()
    return removed


def _junctions_requiring_tls_refresh(
    net_path: Path,
    selected: Sequence[IntersectionConfiguration],
) -> Tuple[str, ...]:
    edge_owner = {
        edge_id: config
        for config in selected
        for edge_id in config.topology.incoming_edges
    }
    junction_ids = sorted({junction for config in selected for junction in config.junction_ids})
    junction_types = _read_junction_types(net_path, junction_ids)
    refresh = set()
    for _, elem in ET.iterparse(net_path, events=("end",)):
        if elem.tag == "connection":
            config = edge_owner.get(elem.get("from", ""))
            if config is not None:
                junction_id = _junction_id_from_via(
                    elem.get("via", ""), config.junction_ids
                )
                if (
                    junction_id is not None
                    and junction_types.get(junction_id) == "traffic_light"
                    and (
                        elem.get("uncontrolled") == "1"
                        or elem.get("tl") is None
                        or elem.get("linkIndex") is None
                    )
                ):
                    refresh.add(junction_id)
        elem.clear()
    return tuple(sorted(refresh))


def _blocked_turnaround_deletions(
    net_path: Path,
    selected: Sequence[IntersectionConfiguration],
) -> Tuple[Tuple[str, str], ...]:
    edge_owner = {
        edge_id: config
        for config in selected
        for edge_id in config.topology.incoming_edges
    }
    deletions = set()
    for _, elem in ET.iterparse(net_path, events=("end",)):
        if elem.tag == "connection":
            from_edge = elem.get("from", "")
            config = edge_owner.get(from_edge)
            direction = elem.get("dir", "")
            if (
                config is not None
                and direction == "t"
                and config.topology.movement_for_direction(from_edge, direction)
                == "blocked"
            ):
                deletions.add((from_edge, elem.get("to", "")))
        elem.clear()
    return tuple(sorted(deletions))


def _read_junction_types(
    net_path: Path, junction_ids: Sequence[str]
) -> Mapping[str, str]:
    requested = set(junction_ids)
    result = {}
    for _, elem in ET.iterparse(net_path, events=("end",)):
        if elem.tag == "junction":
            junction_id = elem.get("id", "")
            if junction_id in requested:
                result[junction_id] = elem.get("type", "")
        elem.clear()
    missing = requested - set(result)
    if missing:
        raise SignalConfigurationError(
            f"Mapped junctions are missing from source network: {sorted(missing)}"
        )
    return result


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
        movement = config.topology.movement_for_direction(raw["from"], direction)
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


def _movement_matches(
    config: IntersectionConfiguration,
    connection: ControlledConnection,
    requested_movement: str,
) -> bool:
    if connection.movement == requested_movement:
        return True
    return (
        config.topology.u_turn_policy == "with_left"
        and requested_movement == "left"
        and connection.movement == "uturn"
    )


def _build_templates(
    config: IntersectionConfiguration,
    connections: Sequence[ControlledConnection],
    state_lengths: Mapping[str, int],
    request_foes: Mapping[str, Mapping[int, str]],
    phase_mappings: Sequence[PhaseMovement] | None = None,
) -> Mapping[int, Mapping[str, Mapping[str, str]]]:
    own_connections = [
        item for item in connections if item.intersection_id == config.intersection_id
    ]
    tls_ids = sorted({item.tls_id for item in own_connections})
    templates = {}
    served_connections = set()
    if phase_mappings is None:
        phase_mappings = config.topology.phases
    for phase_mapping in phase_mappings:
        primary = [
            item
            for item in own_connections
            if item.approach in phase_mapping.approaches
            and _movement_matches(config, item, phase_mapping.movement)
        ]
        if not primary:
            raise SignalConfigurationError(
                f"{config.intersection_id}/phase {phase_mapping.phase_number}: "
                "no primary connections matched."
            )
        protected = list(primary) if phase_mapping.priority == "protected" else []
        permissive = list(primary) if phase_mapping.priority == "permissive" else []
        for group in phase_mapping.protected:
            matches = [
                item
                for item in own_connections
                if item.approach in group.approaches
                and _movement_matches(config, item, group.movement)
            ]
            if not matches:
                raise SignalConfigurationError(
                    f"{config.intersection_id}/phase {phase_mapping.phase_number}: "
                    f"no protected {group.movement} connections matched for "
                    f"{group.approaches}."
                )
            protected.extend(matches)
        protected = list(dict.fromkeys(protected))
        _validate_protected_movements(
            protected,
            request_foes,
            config.intersection_id,
            phase_mapping.phase_number,
        )
        for group in phase_mapping.permissive:
            matches = [
                item
                for item in own_connections
                if item.approach in group.approaches
                and _movement_matches(config, item, group.movement)
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
            if (
                connection.movement == "right"
                and config.topology.right_turn_policy == "permissive_always"
            ):
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
    templates_by_intersection: Mapping[
        str,
        Mapping[str, Mapping[int, Mapping[str, Mapping[str, str]]]],
    ],
) -> None:
    root = ET.Element("additional")
    seen = set()
    for config in selected:
        for program in config.programs.values():
            templates = templates_by_intersection[config.intersection_id][
                program.program_id
            ]
            tls_ids = sorted({tls for value in templates.values() for tls in value})
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
    layout = GeneratedArtifactLayout(output_dir)
    layout.reset()
    target_net = layout.network_file
    blocked_turnarounds = _blocked_turnaround_deletions(source_net, selected)
    refresh_junctions = _junctions_requiring_tls_refresh(source_net, selected)
    netconvert_applied, removed_empty_params = _run_netconvert(
        netconvert,
        source_net,
        target_net,
        junction_ids,
        blocked_turnarounds,
        refresh_junctions,
    )
    removed_empty_params += _remove_empty_params(target_net)
    if removed_empty_params:
        print(
            f"Removed {removed_empty_params} empty SUMO <param> elements "
            f"from {target_net}."
        )
    connections, state_lengths, request_foes = _inspect_generated_network(target_net, selected)
    retained_blocked_turnarounds = [
        item
        for item in connections
        if item.direction == "t" and item.movement == "blocked"
    ]
    if retained_blocked_turnarounds:
        details = ", ".join(
            f"{item.from_edge}->{item.to_edge}"
            for item in retained_blocked_turnarounds
        )
        raise SignalConfigurationError(
            f"netconvert retained blocked turnaround connections: {details}."
        )
    templates = {
        config.intersection_id: {
            program.program_id: _build_templates(
                config,
                connections,
                state_lengths,
                request_foes,
                config.topology.phases_for(program.program_id),
            )
            for program in config.programs.values()
        }
        for config in selected
    }
    additional_path = layout.signal_programs_file
    _write_additional(additional_path, selected, templates)
    _write_connection_report(layout.connections_report, connections)

    manifest = {
        "schema_version": 2,
        "source_net": str(source_net.resolve()),
        "source_net_sha256": _sha256(source_net),
        "netconvert_version": _version(netconvert),
        "sumo_version": _version(sumo),
        "netconvert_applied": netconvert_applied,
        "refreshed_tls_junctions": list(refresh_junctions),
        "removed_blocked_turnarounds": [
            {"from_edge": from_edge, "to_edge": to_edge}
            for from_edge, to_edge in blocked_turnarounds
        ],
        "removed_empty_params": removed_empty_params,
        "artifacts": {
            "network_file": layout.relative(layout.network_file),
            "signal_programs_file": layout.relative(layout.signal_programs_file),
            "connections_report": layout.relative(layout.connections_report),
        },
        "intersections": {},
    }
    for config in selected:
        own_connections = [
            item for item in connections if item.intersection_id == config.intersection_id
        ]
        intersection_templates = templates[config.intersection_id]
        program_views = {}
        for program in config.programs.values():
            phase_movements = config.topology.phases_for(program.program_id)
            program_templates = intersection_templates[program.program_id]
            program_views[program.program_id] = {
                "phase_order": [item.phase_number for item in phase_movements],
                "phase_movements": [asdict(item) for item in phase_movements],
                "templates": {
                    str(phase_number): value
                    for phase_number, value in program_templates.items()
                },
            }
        default_program_view = program_views[next(iter(config.programs))]
        manifest["intersections"][config.intersection_id] = {
            "junction_ids": list(config.junction_ids),
            "tls_ids": sorted({item.tls_id for item in own_connections}),
            "program_ids": list(config.programs),
            "phase_order": default_program_view["phase_order"],
            "phase_movements": default_program_view["phase_movements"],
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
            "templates": default_program_view["templates"],
            "programs": program_views,
            "connections": [asdict(item) for item in own_connections],
        }
    manifest_path = layout.tls_manifest
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
        VehicleProfileError,
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

