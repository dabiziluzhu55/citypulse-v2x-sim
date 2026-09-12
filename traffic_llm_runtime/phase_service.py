"""Map official TLS phases to real SUMO connections. No invented movements."""

from __future__ import annotations

import json
from functools import lru_cache
from pathlib import Path
from typing import Any, Mapping, Sequence

from simulation_protocol.artifacts import DEFAULT_GENERATED_DIR

GREEN_STATES = frozenset("gG")


def _lane_id(edge_id: str, lane_index: int) -> str:
    return f"{edge_id}_{int(lane_index)}"


@lru_cache(maxsize=4)
def load_tls_manifest(generated_dir: str | None = None) -> dict[str, Any]:
    root = Path(generated_dir) if generated_dir else DEFAULT_GENERATED_DIR
    return json.loads((root / "manifests" / "tls_manifest.json").read_text(encoding="utf-8"))


def _connection_lanes(connection: Mapping[str, Any]) -> tuple[str, str]:
    from_id = _lane_id(str(connection["from_edge"]), int(connection["from_lane"]))
    to_id = _lane_id(str(connection["to_edge"]), int(connection["to_lane"]))
    return from_id, to_id


def _service_token(connection: Mapping[str, Any]) -> str:
    """Compact real-connection token: incoming_lane:movement, else from>to."""

    from_id, to_id = _connection_lanes(connection)
    movement = str(connection.get("movement") or "").strip()
    if movement:
        return f"{from_id}:{movement}"
    return f"{from_id}>{to_id}"


def _green_string(item: Mapping[str, Any], phase: int, tls_id: str) -> str:
    templates = dict(item.get("templates") or {})
    phase_templates = templates.get(str(phase)) or templates.get(phase) or {}
    if not isinstance(phase_templates, Mapping):
        return ""
    states = phase_templates.get(str(tls_id)) or {}
    if not isinstance(states, Mapping):
        return ""
    return str(states.get("green") or "")


def phase_service_for_intersection(item: Mapping[str, Any]) -> dict[str, list[str]]:
    """Return phase_number -> unique connection tokens served by green states."""

    connections = list(item.get("connections") or ())
    phases = [int(phase) for phase in item.get("phase_order") or ()]
    served: dict[str, list[str]] = {str(phase): [] for phase in phases}
    seen: dict[str, set[str]] = {str(phase): set() for phase in phases}
    for phase in phases:
        key = str(phase)
        for connection in connections:
            tls_id = str(connection.get("tls_id") or "")
            green = _green_string(item, phase, tls_id)
            if not green:
                continue
            try:
                link_index = int(connection["link_index"])
            except (KeyError, TypeError, ValueError):
                continue
            if link_index < 0 or link_index >= len(green):
                continue
            if green[link_index] not in GREEN_STATES:
                continue
            token = _service_token(connection)
            if token in seen[key]:
                continue
            seen[key].add(token)
            served[key].append(token)
    return served


def connection_lane_ids(item: Mapping[str, Any]) -> set[str]:
    lanes: set[str] = set()
    for connection in item.get("connections") or ():
        from_id, to_id = _connection_lanes(connection)
        lanes.add(from_id)
        lanes.add(to_id)
    for lanes_in in dict(item.get("incoming_lanes") or {}).values():
        for lane_id in lanes_in or ():
            lanes.add(str(lane_id))
    return lanes


def load_phase_service_index(generated_dir: str | None = None) -> dict[str, dict[str, list[str]]]:
    payload = load_tls_manifest(generated_dir)
    index: dict[str, dict[str, list[str]]] = {}
    for intersection_id, item in (payload.get("intersections") or {}).items():
        index[str(intersection_id)] = phase_service_for_intersection(item)
    return index


def load_connection_lanes_index(generated_dir: str | None = None) -> dict[str, set[str]]:
    payload = load_tls_manifest(generated_dir)
    return {
        str(intersection_id): connection_lane_ids(item)
        for intersection_id, item in (payload.get("intersections") or {}).items()
    }


def validate_phase_service(
    phase_service: Mapping[str, Mapping[str, Sequence[str]]],
    *,
    allowed_phases: Mapping[str, Sequence[int]],
    generated_dir: str | None = None,
) -> list[str]:
    """Return errors if a phase is missing or a served lane is not a real connection."""

    errors: list[str] = []
    lanes_index = load_connection_lanes_index(generated_dir)
    for iid, phases in allowed_phases.items():
        services = dict(phase_service.get(str(iid)) or {})
        legal = lanes_index.get(str(iid)) or set()
        for phase in phases:
            key = str(int(phase))
            if key not in services:
                errors.append(f"{iid} allowed phase {phase} missing phase_service")
                continue
            tokens = list(services[key] or ())
            for token in tokens:
                from_id = str(token).split(">", 1)[0].split(":", 1)[0]
                if from_id not in legal:
                    errors.append(f"{iid} phase {phase} token {token} is not a real connection lane")
    return errors
