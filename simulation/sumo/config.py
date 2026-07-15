"""Validated source configuration for official traffic-signal plans."""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Dict, Iterable, Mapping, Tuple


class SignalConfigurationError(ValueError):
    """Raised when source signal data is incomplete or inconsistent."""


@dataclass(frozen=True)
class OfficialPhase:
    number: int
    name: str
    green: float
    yellow: float
    clearance: float

    @property
    def total(self) -> float:
        return self.green + self.yellow + self.clearance


@dataclass(frozen=True)
class SignalProgram:
    program_id: str
    period_type: str
    time_start: str
    time_end: str
    cycle_duration: float
    phases: Tuple[OfficialPhase, ...]

    def phase(self, number: int) -> OfficialPhase:
        for phase in self.phases:
            if phase.number == number:
                return phase
        raise SignalConfigurationError(
            f"Program {self.program_id!r} has no official phase {number}."
        )


@dataclass(frozen=True)
class MovementGroup:
    movement: str
    approaches: Tuple[str, ...]


@dataclass(frozen=True)
class PhaseMovement:
    phase_number: int
    movement: str
    approaches: Tuple[str, ...]
    protected: Tuple[MovementGroup, ...] = ()
    permissive: Tuple[MovementGroup, ...] = ()


@dataclass(frozen=True)
class IntersectionTopology:
    approaches: Mapping[str, Tuple[str, ...]]
    direction_mapping: Mapping[str, str]
    right_turn_policy: str
    u_turn_policy: str
    phases: Tuple[PhaseMovement, ...]
    program_phases: Mapping[str, Tuple[PhaseMovement, ...]] = field(
        default_factory=dict
    )

    @property
    def incoming_edges(self) -> Tuple[str, ...]:
        return tuple(
            edge
            for approach_edges in self.approaches.values()
            for edge in approach_edges
        )

    def approach_for_edge(self, edge_id: str) -> str:
        matches = [
            approach
            for approach, edge_ids in self.approaches.items()
            if edge_id in edge_ids
        ]
        if len(matches) != 1:
            raise SignalConfigurationError(
                f"Incoming edge {edge_id!r} belongs to {len(matches)} approaches."
            )
        return matches[0]

    def phases_for(self, program_id: str) -> Tuple[PhaseMovement, ...]:
        if self.program_phases:
            try:
                return self.program_phases[program_id]
            except KeyError as exc:
                raise SignalConfigurationError(
                    f"No phase topology is configured for program {program_id!r}."
                ) from exc
        return self.phases


@dataclass(frozen=True)
class IntersectionConfiguration:
    intersection_id: str
    junction_ids: Tuple[str, ...]
    programs: Mapping[str, SignalProgram]
    topology: IntersectionTopology


@dataclass(frozen=True)
class SignalConfiguration:
    intersections: Mapping[str, IntersectionConfiguration]

    def select(self, intersection_ids: Iterable[str]) -> Tuple[IntersectionConfiguration, ...]:
        selected = []
        for intersection_id in intersection_ids:
            if intersection_id not in self.intersections:
                raise SignalConfigurationError(
                    f"Unknown or unconfigured intersection: {intersection_id}"
                )
            selected.append(self.intersections[intersection_id])
        if not selected:
            raise SignalConfigurationError("At least one intersection is required.")
        return tuple(selected)


def _read_json(path: Path) -> Dict[str, Any]:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError as exc:
        raise SignalConfigurationError(f"Configuration file not found: {path}") from exc
    except json.JSONDecodeError as exc:
        raise SignalConfigurationError(f"Invalid JSON in {path}: {exc}") from exc


def _junction_ids(raw: Mapping[str, Any], intersection_id: str) -> Tuple[str, ...]:
    values = raw.get("junction_ids")
    if values is None:
        value = raw.get("junction_id")
        values = [value] if value is not None else []
    result = tuple(str(value) for value in values if value is not None)
    if not result or len(result) != len(set(result)):
        raise SignalConfigurationError(
            f"{intersection_id}: junction mapping is empty or duplicated: {result}"
        )
    return result


def _parse_program(intersection_id: str, raw: Mapping[str, Any]) -> SignalProgram:
    program_id = str(raw["program_id"])
    phases = []
    seen = set()
    for item in raw.get("phases", []):
        number = int(item["official_phase_no"])
        if number in seen:
            raise SignalConfigurationError(
                f"{intersection_id}/{program_id}: duplicated phase {number}."
            )
        seen.add(number)
        phase = OfficialPhase(
            number=number,
            name=str(item["official_phase_name"]),
            green=float(item["green"]),
            yellow=float(item["yellow"]),
            clearance=float(item["all_red"]),
        )
        declared_total = float(item["total"])
        if min(phase.green, phase.yellow, phase.clearance) < 0:
            raise SignalConfigurationError(
                f"{intersection_id}/{program_id}/phase {number}: negative duration."
            )
        if abs(phase.total - declared_total) > 1e-9:
            raise SignalConfigurationError(
                f"{intersection_id}/{program_id}/phase {number}: "
                f"green + yellow + all_red={phase.total}, total={declared_total}."
            )
        phases.append(phase)
    if not phases:
        raise SignalConfigurationError(f"{intersection_id}/{program_id}: no phases.")
    cycle = float(raw["cycle_duration"])
    computed_cycle = sum(phase.total for phase in phases)
    if abs(cycle - computed_cycle) > 1e-9:
        raise SignalConfigurationError(
            f"{intersection_id}/{program_id}: cycle={cycle}, phases={computed_cycle}."
        )
    time_range = raw.get("time_range", {})
    return SignalProgram(
        program_id=program_id,
        period_type=str(raw.get("period_type", "")),
        time_start=str(time_range.get("start", "")),
        time_end=str(time_range.get("end", "")),
        cycle_duration=cycle,
        phases=tuple(phases),
    )


def _parse_phase_movements(
    intersection_id: str,
    raw_phases: Iterable[Mapping[str, Any]],
    approaches: Mapping[str, Tuple[str, ...]],
    context: str,
) -> Tuple[PhaseMovement, ...]:
    phases = tuple(
        PhaseMovement(
            phase_number=int(item["official_phase_no"]),
            movement=str(item["movement"]),
            approaches=tuple(str(value) for value in item["approaches"]),
            protected=tuple(
                MovementGroup(
                    movement=str(group["movement"]),
                    approaches=tuple(str(value) for value in group["approaches"]),
                )
                for group in item.get("protected", [])
            ),
            permissive=tuple(
                MovementGroup(
                    movement=str(group["movement"]),
                    approaches=tuple(str(value) for value in group["approaches"]),
                )
                for group in item.get("permissive", [])
            ),
        )
        for item in raw_phases
    )
    if not phases or len({item.phase_number for item in phases}) != len(phases):
        raise SignalConfigurationError(
            f"{intersection_id}/{context}: phase topology is empty or duplicated."
        )
    for item in phases:
        if item.movement not in {"through", "left"}:
            raise SignalConfigurationError(
                f"{intersection_id}/{context}/phase {item.phase_number}: "
                "invalid movement."
            )
        unknown = set(item.approaches) - set(approaches)
        if unknown:
            raise SignalConfigurationError(
                f"{intersection_id}/{context}/phase {item.phase_number}: "
                f"unknown approaches {unknown}."
            )
        for priority, groups in (
            ("protected", item.protected),
            ("permissive", item.permissive),
        ):
            for group in groups:
                if group.movement not in {"through", "left"}:
                    raise SignalConfigurationError(
                        f"{intersection_id}/{context}/phase {item.phase_number}: "
                        f"invalid {priority} movement {group.movement!r}."
                    )
                unknown = set(group.approaches) - set(approaches)
                if unknown:
                    raise SignalConfigurationError(
                        f"{intersection_id}/{context}/phase {item.phase_number}: "
                        f"unknown {priority} approaches {unknown}."
                    )
    return phases


def _parse_topology(intersection_id: str, raw: Mapping[str, Any]) -> IntersectionTopology:
    approaches = {
        str(name): tuple(str(edge) for edge in item.get("incoming_edges", []))
        for name, item in raw.get("approaches", {}).items()
    }
    if not approaches or any(not edges for edges in approaches.values()):
        raise SignalConfigurationError(f"{intersection_id}: every approach needs an edge.")
    all_edges = [edge for edges in approaches.values() for edge in edges]
    if len(all_edges) != len(set(all_edges)):
        raise SignalConfigurationError(f"{intersection_id}: incoming edges are duplicated.")
    direction_mapping = {
        str(key): str(value)
        for key, value in raw.get("direction_mapping", {}).items()
    }
    if set(direction_mapping.values()) - {"through", "left", "right", "blocked"}:
        raise SignalConfigurationError(f"{intersection_id}: unsupported movement mapping.")
    raw_programs = raw.get("programs", {})
    if raw.get("phases") and raw_programs:
        raise SignalConfigurationError(
            f"{intersection_id}: use either shared phases or program-specific phases."
        )
    phases = (
        _parse_phase_movements(
            intersection_id, raw.get("phases", []), approaches, "shared"
        )
        if raw.get("phases")
        else ()
    )
    program_phases = {
        str(program_id): _parse_phase_movements(
            intersection_id,
            program_raw.get("phases", []),
            approaches,
            str(program_id),
        )
        for program_id, program_raw in raw_programs.items()
    }
    if not phases and not program_phases:
        raise SignalConfigurationError(f"{intersection_id}: no phase topology configured.")
    right_policy = str(raw.get("right_turn_policy", ""))
    if right_policy != "permissive_always":
        raise SignalConfigurationError(
            f"{intersection_id}: only permissive_always is implemented for right turns."
        )
    u_turn_policy = str(raw.get("u_turn_policy", ""))
    if u_turn_policy not in {"with_left", "blocked"}:
        raise SignalConfigurationError(
            f"{intersection_id}: u_turn_policy must be with_left or blocked."
        )
    expected_u_turn_movement = "left" if u_turn_policy == "with_left" else "blocked"
    if direction_mapping.get("t") != expected_u_turn_movement:
        raise SignalConfigurationError(
            f"{intersection_id}: direction 't' must map to "
            f"{expected_u_turn_movement!r} for u_turn_policy={u_turn_policy!r}."
        )
    return IntersectionTopology(
        approaches=approaches,
        direction_mapping=direction_mapping,
        right_turn_policy=right_policy,
        u_turn_policy=u_turn_policy,
        phases=phases,
        program_phases=program_phases,
    )


def load_signal_configuration(
    mapping_path: Path,
    plans_path: Path,
    topology_path: Path,
) -> SignalConfiguration:
    mapping = _read_json(mapping_path)
    plans = _read_json(plans_path)
    topology = _read_json(topology_path)
    if int(plans.get("schema_version", 0)) != 2:
        raise SignalConfigurationError("official_tls_plans.json must use schema_version 2.")
    plan_entries = plans.get("intersections", {})
    topology_entries = topology.get("intersections", {})
    if set(plan_entries) != set(topology_entries):
        raise SignalConfigurationError(
            "Plan and topology intersection sets differ: "
            f"plans={sorted(plan_entries)}, topology={sorted(topology_entries)}"
        )
    result = {}
    for intersection_id, plan_raw in plan_entries.items():
        if intersection_id not in mapping:
            raise SignalConfigurationError(
                f"{intersection_id}: missing from canonical intersection mapping."
            )
        programs = {}
        for raw_program in plan_raw.get("programs", []):
            program = _parse_program(intersection_id, raw_program)
            if program.program_id in programs:
                raise SignalConfigurationError(
                    f"{intersection_id}: duplicated program {program.program_id}."
                )
            programs[program.program_id] = program
        if not programs:
            raise SignalConfigurationError(f"{intersection_id}: no programs configured.")
        parsed_topology = _parse_topology(intersection_id, topology_entries[intersection_id])
        if parsed_topology.program_phases and set(parsed_topology.program_phases) != set(
            programs
        ):
            raise SignalConfigurationError(
                f"{intersection_id}: program topology keys "
                f"{sorted(parsed_topology.program_phases)} do not match programs "
                f"{sorted(programs)}."
            )
        shared_official_phases = None
        for program in programs.values():
            official_phases = {phase.number for phase in program.phases}
            topology_phases = {
                phase.phase_number
                for phase in parsed_topology.phases_for(program.program_id)
            }
            if official_phases != topology_phases:
                raise SignalConfigurationError(
                    f"{intersection_id}/{program.program_id}: official phases "
                    f"{official_phases} do not match topology phases {topology_phases}."
                )
            if not parsed_topology.program_phases:
                if shared_official_phases is None:
                    shared_official_phases = official_phases
                elif official_phases != shared_official_phases:
                    raise SignalConfigurationError(
                        f"{intersection_id}/{program.program_id}: phase set differs "
                        "from the shared topology."
                    )
        result[intersection_id] = IntersectionConfiguration(
            intersection_id=intersection_id,
            junction_ids=_junction_ids(mapping[intersection_id], intersection_id),
            programs=programs,
            topology=parsed_topology,
        )
    return SignalConfiguration(intersections=result)

