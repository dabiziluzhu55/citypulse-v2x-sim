"""Shared validation for HTTP and in-process algorithm transports."""

from __future__ import annotations

from dataclasses import fields, is_dataclass
from typing import Mapping

from .policy import AlgorithmDecision, PROTOCOL_VERSION


def to_protocol_payload(value: object) -> object:
    """Build a JSON-shaped value without serializing it to JSON."""
    if is_dataclass(value) and not isinstance(value, type):
        return {
            item.name: to_protocol_payload(getattr(value, item.name))
            for item in fields(value)
        }
    if isinstance(value, Mapping):
        return {str(key): to_protocol_payload(item) for key, item in value.items()}
    if isinstance(value, (tuple, list)):
        return [to_protocol_payload(item) for item in value]
    return value


def validate_initialize_response(
    response: object,
    *,
    episode_id: str,
    source: str,
) -> None:
    if not isinstance(response, dict):
        raise TypeError(f"{source} initialize response must be an object.")
    _validate_echo(response, episode_id=episode_id, source=source, operation="initialize")
    if response.get("ready") is not True:
        raise RuntimeError(f"{source} initialize must explicitly return ready=true.")


def validate_step_response(
    response: object,
    *,
    episode_id: str,
    step_id: int,
    source: str,
) -> AlgorithmDecision:
    if not isinstance(response, dict):
        raise TypeError(f"{source} step response must be an object.")
    _validate_echo(response, episode_id=episode_id, source=source, operation="step")
    if response.get("step_id") != step_id:
        raise ValueError(
            f"{source} step must echo step_id; expected {step_id}, "
            f"got {response.get('step_id')!r}."
        )
    actions = response.get("actions")
    if not isinstance(actions, dict):
        raise TypeError(f"{source} step response needs an actions object.")
    if set(actions) != {"signals", "vehicles"}:
        raise ValueError(
            f"{source} actions must contain exactly signals and vehicles objects."
        )
    if not isinstance(actions["signals"], dict) or not isinstance(
        actions["vehicles"], dict
    ):
        raise TypeError(f"{source} signal and vehicle actions must be objects.")
    return AlgorithmDecision(
        signal_actions=dict(actions["signals"]),
        vehicle_actions=dict(actions["vehicles"]),
    )


def _validate_echo(
    response: Mapping[str, object],
    *,
    episode_id: str,
    source: str,
    operation: str,
) -> None:
    if response.get("protocol_version") != PROTOCOL_VERSION:
        raise ValueError(
            f"{source} {operation} must use protocol_version {PROTOCOL_VERSION}."
        )
    if response.get("episode_id") != episode_id:
        raise ValueError(f"{source} {operation} must echo episode_id.")
