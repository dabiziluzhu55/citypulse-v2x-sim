"""Disturbance event DTOs crossing the Redis/Celery boundary."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Mapping


DEFAULT_ACTIVITY_VEHICLE_TYPE_ID = "citypulse_event_passenger"


class EventValidationError(ValueError):
    """Raised before an invalid disturbance reaches the worker runtime."""


@dataclass(frozen=True)
class LaneClosureEvent:
    event_id: str
    start_seconds: float
    end_seconds: float
    lane_ids: tuple[str, ...]
    event_type: str = "lane_closure"
    ai_control_enabled: bool = False


@dataclass(frozen=True)
class SpeedLimitEvent:
    event_id: str
    start_seconds: float
    end_seconds: float
    lane_ids: tuple[str, ...]
    max_speed: float
    event_type: str = "speed_limit"
    ai_control_enabled: bool = False


@dataclass(frozen=True)
class AccidentEvent:
    event_id: str
    start_seconds: float
    end_seconds: float
    lane_id: str
    position_ratio: float
    event_type: str = "accident"
    ai_control_enabled: bool = False


@dataclass(frozen=True)
class MajorEventOpeningEvent:
    event_id: str
    start_seconds: float
    end_seconds: float
    venue_lane_id: str
    vehicle_count: int
    source_lane_ids: tuple[str, ...] = ()
    vehicle_type_id: str = DEFAULT_ACTIVITY_VEHICLE_TYPE_ID
    event_type: str = "major_event_opening"
    ai_control_enabled: bool = False


@dataclass(frozen=True)
class MajorEventClosingEvent:
    event_id: str
    start_seconds: float
    end_seconds: float
    venue_lane_id: str
    vehicle_count: int
    destination_lane_ids: tuple[str, ...] = ()
    vehicle_type_id: str = DEFAULT_ACTIVITY_VEHICLE_TYPE_ID
    event_type: str = "major_event_closing"
    ai_control_enabled: bool = False


DisturbanceEvent = (
    LaneClosureEvent
    | SpeedLimitEvent
    | AccidentEvent
    | MajorEventOpeningEvent
    | MajorEventClosingEvent
)


@dataclass(frozen=True)
class EventSnapshot:
    event_id: str
    event_type: str
    state: str
    start_seconds: float
    end_seconds: float
    error: str | None
    details: Mapping[str, object]
