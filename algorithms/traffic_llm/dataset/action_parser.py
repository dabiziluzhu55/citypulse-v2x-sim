"""Parse Protocol 2.0 actions and classify teacher action spaces."""

from __future__ import annotations

from typing import Any, Mapping

from .schema import ACTION_SPACE_SIGNAL_ONLY, ACTION_SPACE_SIGNAL_VEHICLE

VEHICLE_ACTION_KEYS = (
    "target_speed",
    "target_speed_mps",
    "target_lane_index",
    "lane_change",
    "target_lane",
)


class IllegalPhaseError(ValueError):
    pass


def _is_mapping(value: Any) -> bool:
    return isinstance(value, Mapping)


def extract_protocol_actions(payload: Mapping[str, Any] | None) -> dict[str, dict[str, Any]]:
    if not payload:
        return {"signals": {}, "vehicles": {}}
    if "actions" in payload and _is_mapping(payload["actions"]):
        actions = payload["actions"]
    else:
        actions = payload
    signals = actions.get("signals") if _is_mapping(actions) else {}
    vehicles = actions.get("vehicles") if _is_mapping(actions) else {}
    return {
        "signals": dict(signals) if _is_mapping(signals) else {},
        "vehicles": dict(vehicles) if _is_mapping(vehicles) else {},
    }


def vehicle_action_is_material(command: Any) -> bool:
    if not _is_mapping(command) or not command:
        return False
    for key in VEHICLE_ACTION_KEYS:
        if command.get(key) is not None:
            return True
    return False


def has_material_vehicle_actions(vehicles: Mapping[str, Any] | None) -> bool:
    if not vehicles:
        return False
    return any(vehicle_action_is_material(command) for command in vehicles.values())


def classify_action_space(actions: Mapping[str, Any] | None) -> str:
    extracted = extract_protocol_actions(actions)
    if has_material_vehicle_actions(extracted["vehicles"]):
        return ACTION_SPACE_SIGNAL_VEHICLE
    return ACTION_SPACE_SIGNAL_ONLY


def parse_target_phases(signals: Mapping[str, Any] | None) -> dict[str, int]:
    phases: dict[str, int] = {}
    if not signals:
        return phases
    for intersection_id, command in signals.items():
        phase: int | None = None
        if isinstance(command, int) and not isinstance(command, bool):
            phase = int(command)
        elif _is_mapping(command) and command.get("target_phase") is not None:
            raw = command["target_phase"]
            if isinstance(raw, bool) or not isinstance(raw, int):
                raise IllegalPhaseError(
                    f"Illegal target_phase for {intersection_id!r}: {raw!r}"
                )
            phase = int(raw)
        if phase is None:
            continue
        phases[str(intersection_id)] = phase
    return phases


def validate_phases_against_allowed(
    phases: Mapping[str, int],
    allowed_phases: Mapping[str, tuple[int, ...]],
) -> list[str]:
    errors: list[str] = []
    for intersection_id, phase in phases.items():
        allowed = allowed_phases.get(intersection_id)
        if allowed is None:
            errors.append(f"control range error: {intersection_id} is outside allowed_phases")
            continue
        if phase not in allowed:
            errors.append(
                f"illegal phase {phase} for {intersection_id}; allowed={list(allowed)}"
            )
    return errors
