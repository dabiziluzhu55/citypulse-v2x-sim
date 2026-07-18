"""Minimal in-process control algorithm for protocol 2.0."""

PROTOCOL_VERSION = "2.0"


def initialize(payload: dict) -> dict:
    return {
        "protocol_version": PROTOCOL_VERSION,
        "episode_id": payload["episode_id"],
        "ready": True,
    }


def step(payload: dict) -> dict:
    signals = {
        intersection_id: {"target_phase": state["current_phase"]}
        for intersection_id, state in payload["intersections"].items()
    }
    return {
        "protocol_version": PROTOCOL_VERSION,
        "episode_id": payload["episode_id"],
        "step_id": payload["step_id"],
        "actions": {"signals": signals, "vehicles": {}},
    }


def finish(payload: dict) -> object:
    return None
