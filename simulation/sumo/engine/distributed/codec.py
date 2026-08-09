"""Versioned JSON codecs used at the Redis/Celery boundary."""

from __future__ import annotations

import json
from dataclasses import asdict
from typing import Any, Mapping

from ..events import (
    AccidentEvent,
    DisturbanceEvent,
    EventSnapshot,
    LaneClosureEvent,
    MajorEventClosingEvent,
    MajorEventOpeningEvent,
    SpeedLimitEvent,
)
from ..session import (
    IntersectionRuntimeSnapshot,
    LaneRuntimeSnapshot,
    SessionMetrics,
    SimulationConfig,
    SimulationSnapshot,
    VehicleRuntimeSnapshot,
)

SCHEMA_VERSION = 1


class CodecError(ValueError):
    pass


def _dumps(kind: str, payload: Mapping[str, Any]) -> str:
    return json.dumps(
        {"schema_version": SCHEMA_VERSION, "kind": kind, "data": payload},
        ensure_ascii=False,
        separators=(",", ":"),
        allow_nan=False,
    )


def _loads(raw: str | bytes, kind: str) -> Mapping[str, Any]:
    try:
        value = json.loads(raw)
    except (TypeError, json.JSONDecodeError) as exc:
        raise CodecError(f"Invalid {kind} JSON: {exc}") from exc
    if not isinstance(value, Mapping):
        raise CodecError(f"Invalid {kind} envelope.")
    if int(value.get("schema_version", 0)) != SCHEMA_VERSION:
        raise CodecError(f"Unsupported {kind} schema version.")
    if value.get("kind") != kind or not isinstance(value.get("data"), Mapping):
        raise CodecError(f"Invalid {kind} envelope.")
    return value["data"]


def event_to_dict(event: DisturbanceEvent) -> dict[str, Any]:
    return asdict(event)


def event_from_dict(value: Mapping[str, Any]) -> DisturbanceEvent:
    event_type = str(value.get("event_type", ""))
    common = {
        "event_id": str(value["event_id"]),
        "start_seconds": float(value["start_seconds"]),
        "end_seconds": float(value["end_seconds"]),
    }
    if event_type == "lane_closure":
        return LaneClosureEvent(
            **common,
            lane_ids=tuple(str(item) for item in value.get("lane_ids", ())),
        )
    if event_type == "speed_limit":
        return SpeedLimitEvent(
            **common,
            lane_ids=tuple(str(item) for item in value.get("lane_ids", ())),
            max_speed=float(value["max_speed"]),
        )
    if event_type == "accident":
        return AccidentEvent(
            **common,
            lane_id=str(value["lane_id"]),
            position_ratio=float(value["position_ratio"]),
        )
    if event_type == "major_event_opening":
        return MajorEventOpeningEvent(
            **common,
            venue_lane_id=str(value["venue_lane_id"]),
            vehicle_count=int(value["vehicle_count"]),
            source_lane_ids=tuple(
                str(item) for item in value.get("source_lane_ids", ())
            ),
            vehicle_type_id=str(value.get("vehicle_type_id", "citypulse_event_passenger")),
        )
    if event_type == "major_event_closing":
        return MajorEventClosingEvent(
            **common,
            venue_lane_id=str(value["venue_lane_id"]),
            vehicle_count=int(value["vehicle_count"]),
            destination_lane_ids=tuple(
                str(item) for item in value.get("destination_lane_ids", ())
            ),
            vehicle_type_id=str(value.get("vehicle_type_id", "citypulse_event_passenger")),
        )
    raise CodecError(f"Unknown disturbance event type: {event_type!r}.")


def dumps_config(config: SimulationConfig) -> str:
    data = asdict(config)
    data["intersection_ids"] = list(config.intersection_ids)
    data["origins"] = {
        str(key): list(values) for key, values in config.origins.items()
    }
    data["initial_events"] = [event_to_dict(event) for event in config.initial_events]
    return _dumps("simulation_config", data)


def loads_config(raw: str | bytes) -> SimulationConfig:
    data = dict(_loads(raw, "simulation_config"))
    data["intersection_ids"] = tuple(str(item) for item in data["intersection_ids"])
    data["origins"] = {
        str(key): tuple(str(item) for item in values)
        for key, values in data.get("origins", {}).items()
    }
    data["initial_events"] = tuple(
        event_from_dict(item) for item in data.get("initial_events", ())
    )
    return SimulationConfig(**data)


def snapshot_to_dict(snapshot: SimulationSnapshot) -> dict[str, Any]:
    return asdict(snapshot)


def dumps_snapshot(snapshot: SimulationSnapshot) -> str:
    return _dumps("simulation_snapshot", snapshot_to_dict(snapshot))


def loads_snapshot(raw: str | bytes) -> SimulationSnapshot:
    data = _loads(raw, "simulation_snapshot")
    intersections: dict[str, IntersectionRuntimeSnapshot] = {}
    for intersection_id, item in data.get("intersections", {}).items():
        lanes = {
            str(lane_id): LaneRuntimeSnapshot(**lane)
            for lane_id, lane in item.get("lanes", {}).items()
        }
        intersections[str(intersection_id)] = IntersectionRuntimeSnapshot(
            current_phase=int(item["current_phase"]),
            pending_phase=(
                None if item.get("pending_phase") is None else int(item["pending_phase"])
            ),
            stage=str(item["stage"]),
            stage_elapsed=float(item["stage_elapsed"]),
            lanes=lanes,
        )
    events = tuple(
        EventSnapshot(
            event_id=str(item["event_id"]),
            event_type=str(item["event_type"]),
            state=str(item["state"]),
            start_seconds=float(item["start_seconds"]),
            end_seconds=float(item["end_seconds"]),
            error=None if item.get("error") is None else str(item["error"]),
            details=dict(item.get("details", {})),
        )
        for item in data.get("events", ())
    )
    return SimulationSnapshot(
        session_id=str(data["session_id"]),
        state=str(data["state"]),
        sequence=int(data["sequence"]),
        elapsed_seconds=float(data["elapsed_seconds"]),
        duration_seconds=float(data["duration_seconds"]),
        progress=float(data["progress"]),
        official_time=str(data["official_time"]),
        playback_speed=(
            None
            if data.get("playback_speed") is None
            else float(data["playback_speed"])
        ),
        intersections=intersections,
        vehicles=tuple(VehicleRuntimeSnapshot(**item) for item in data.get("vehicles", ())),
        events=events,
        metrics=SessionMetrics(**data.get("metrics", {})),
        error=None if data.get("error") is None else str(data["error"]),
    )


def encode_command_payload(name: str, payload: object) -> object:
    if name == "add_event":
        return event_to_dict(payload)  # type: ignore[arg-type]
    return payload


def decode_command_payload(name: str, payload: object) -> object:
    if name == "add_event":
        if not isinstance(payload, Mapping):
            raise CodecError("add_event command payload must be an object.")
        return event_from_dict(payload)
    return payload
