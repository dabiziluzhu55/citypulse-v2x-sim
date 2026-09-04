"""Shared validation for in-process algorithm modules."""

from __future__ import annotations

from dataclasses import fields, is_dataclass
from typing import Mapping

from .policy import AlgorithmDecision, PROTOCOL_VERSION


def _extract_v2x_events(
    response: Mapping[str, object],
    *,
    source: str,
) -> tuple[Mapping[str, object], ...]:
    """Validate the optional CoV2X event batch without coupling other algorithms."""

    batch = response.get("v2x")
    if batch is None:
        return ()
    if not isinstance(batch, Mapping):
        raise TypeError(f"{source} v2x must be an object.")
    if batch.get("schema") != "cov2x.v2x.event_batch":
        raise ValueError(f"{source} returned an unknown v2x schema.")
    if batch.get("schema_version") != "1.0":
        raise ValueError(f"{source} returned an unsupported v2x schema version.")

    raw_events = batch.get("events", ())
    if not isinstance(raw_events, (list, tuple)):
        raise TypeError(f"{source} v2x.events must be an array.")
    try:
        declared_count = int(batch.get("event_count", len(raw_events)))
    except (TypeError, ValueError) as exc:
        raise TypeError(f"{source} v2x.event_count must be an integer.") from exc
    if declared_count != len(raw_events):
        raise ValueError(f"{source} v2x event_count does not match events.")

    events: list[Mapping[str, object]] = []
    for index, event in enumerate(raw_events):
        if not isinstance(event, Mapping):
            raise TypeError(f"{source} v2x.events[{index}] must be an object.")
        if event.get("schema") != "cov2x.v2x.event":
            raise ValueError(f"{source} v2x.events[{index}] has an invalid schema.")
        events.append(dict(event))
    return tuple(events)


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
        v2x_events=_extract_v2x_events(response, source=source),
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
