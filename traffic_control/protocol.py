"""交通管控算法协议"""

from __future__ import annotations

from typing import Any, Mapping, Optional

PROTOCOL_VERSION = "2.0"


def initialize_response(*, episode_id: str) -> dict[str, Any]:
    return {
        "protocol_version": PROTOCOL_VERSION,
        "episode_id": str(episode_id),
        "ready": True,
    }


def step_response(
    *,
    episode_id: str,
    step_id: Any,
    signals: Mapping[str, Mapping[str, int]] | None = None,
    vehicles: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    return {
        "protocol_version": PROTOCOL_VERSION,
        "episode_id": str(episode_id),
        "step_id": step_id,
        "actions": {
            "signals": dict(signals or {}),
            "vehicles": dict(vehicles or {}),
        },
    }


def finish_response(*, already_finished: bool = False) -> dict[str, Any]:
    payload: dict[str, Any] = {"ok": True}
    if already_finished:
        payload["already_finished"] = True
    return payload


def signals_from_phase_map(
    actions: Mapping[str, Optional[int]],
) -> dict[str, dict[str, int]]:
    """Convert ``{intersection_id: target_phase|None}`` to Protocol 2.0 signals."""

    return {
        intersection_id: {"target_phase": int(phase)}
        for intersection_id, phase in actions.items()
        if phase is not None
    }
