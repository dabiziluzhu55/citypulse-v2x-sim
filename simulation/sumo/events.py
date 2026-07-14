"""Scheduled SUMO disturbances with overlap-safe lane restoration."""

from __future__ import annotations

from dataclasses import asdict, dataclass
from enum import Enum
from typing import Mapping


BLOCKED_VEHICLE_CLASSES = (
    "private",
    "passenger",
    "taxi",
    "bus",
    "coach",
    "delivery",
    "truck",
    "trailer",
    "motorcycle",
    "moped",
    "bicycle",
)


class EventValidationError(ValueError):
    """Raised before an invalid disturbance reaches TraCI."""


class EventState(str, Enum):
    SCHEDULED = "SCHEDULED"
    ACTIVE = "ACTIVE"
    COMPLETED = "COMPLETED"
    CANCELLED = "CANCELLED"
    FAILED = "FAILED"


@dataclass(frozen=True)
class LaneTarget:
    lane_id: str
    edge_id: str
    lane_index: int
    length: float


@dataclass(frozen=True)
class LaneClosureEvent:
    event_id: str
    start_seconds: float
    end_seconds: float
    lane_ids: tuple[str, ...]
    event_type: str = "lane_closure"


@dataclass(frozen=True)
class SpeedLimitEvent:
    event_id: str
    start_seconds: float
    end_seconds: float
    lane_ids: tuple[str, ...]
    max_speed: float
    event_type: str = "speed_limit"


@dataclass(frozen=True)
class AccidentEvent:
    event_id: str
    start_seconds: float
    end_seconds: float
    lane_id: str
    position_ratio: float
    event_type: str = "accident"


DisturbanceEvent = LaneClosureEvent | SpeedLimitEvent | AccidentEvent


@dataclass(frozen=True)
class EventSnapshot:
    event_id: str
    event_type: str
    state: str
    start_seconds: float
    end_seconds: float
    error: str | None
    details: Mapping[str, object]


@dataclass(frozen=True)
class _LaneBaseline:
    allowed: tuple[str, ...]
    disallowed: tuple[str, ...]
    max_speed: float


@dataclass
class _EventRuntime:
    event: DisturbanceEvent
    state: EventState = EventState.SCHEDULED
    error: str | None = None
    vehicle_id: str | None = None


def _overlaps(first: DisturbanceEvent, second: DisturbanceEvent) -> bool:
    return first.start_seconds < second.end_seconds and second.start_seconds < first.end_seconds


def _event_lanes(event: DisturbanceEvent) -> tuple[str, ...]:
    if isinstance(event, AccidentEvent):
        return (event.lane_id,)
    return event.lane_ids


class DisturbanceScheduler:
    def __init__(
        self,
        traci,
        lane_targets: Mapping[str, LaneTarget],
        duration_seconds: float,
    ) -> None:
        self.traci = traci
        self.lane_targets = dict(lane_targets)
        self.duration_seconds = float(duration_seconds)
        self._events: dict[str, _EventRuntime] = {}
        self._baselines: dict[str, _LaneBaseline] = {}
        self._closures: dict[str, set[str]] = {}
        self._speed_limits: dict[str, dict[str, float]] = {}

    def schedule(self, event: DisturbanceEvent, current_time: float = 0.0) -> str:
        self._validate(event, current_time)
        self._events[event.event_id] = _EventRuntime(event=event)
        return event.event_id

    def cancel(self, event_id: str) -> None:
        runtime = self._events.get(event_id)
        if runtime is None:
            raise EventValidationError(f"Unknown event: {event_id}")
        if runtime.state == EventState.SCHEDULED:
            runtime.state = EventState.CANCELLED
        elif runtime.state == EventState.ACTIVE:
            self._deactivate(runtime)
            runtime.state = EventState.CANCELLED
        elif runtime.state not in {EventState.CANCELLED, EventState.COMPLETED}:
            raise EventValidationError(
                f"Event {event_id} cannot be cancelled from {runtime.state.value}."
            )

    def tick(self, current_time: float) -> None:
        now = float(current_time)
        for runtime in tuple(self._events.values()):
            if runtime.state == EventState.ACTIVE and now + 1e-9 >= runtime.event.end_seconds:
                self._deactivate(runtime)
                if runtime.state != EventState.FAILED:
                    runtime.state = EventState.COMPLETED
        for runtime in tuple(self._events.values()):
            if (
                runtime.state == EventState.SCHEDULED
                and runtime.event.start_seconds <= now + 1e-9
                and now < runtime.event.end_seconds
            ):
                self._activate(runtime, now)

    def snapshots(self) -> tuple[EventSnapshot, ...]:
        values = []
        for event_id in sorted(self._events):
            runtime = self._events[event_id]
            raw = asdict(runtime.event)
            for key in ("event_id", "event_type", "start_seconds", "end_seconds"):
                raw.pop(key, None)
            values.append(
                EventSnapshot(
                    event_id=runtime.event.event_id,
                    event_type=runtime.event.event_type,
                    state=runtime.state.value,
                    start_seconds=runtime.event.start_seconds,
                    end_seconds=runtime.event.end_seconds,
                    error=runtime.error,
                    details=raw,
                )
            )
        return tuple(values)

    def close(self) -> None:
        for runtime in tuple(self._events.values()):
            if runtime.state == EventState.ACTIVE:
                self._deactivate(runtime)
                if runtime.state != EventState.FAILED:
                    runtime.state = EventState.CANCELLED
            elif runtime.state == EventState.SCHEDULED:
                runtime.state = EventState.CANCELLED

    def _validate(self, event: DisturbanceEvent, current_time: float) -> None:
        if not event.event_id or event.event_id in self._events:
            raise EventValidationError(f"Event ID is empty or duplicated: {event.event_id!r}")
        if event.start_seconds < 0 or event.end_seconds <= event.start_seconds:
            raise EventValidationError("Event time range is invalid.")
        if event.end_seconds > self.duration_seconds + 1e-9:
            raise EventValidationError("Event ends after the simulation window.")
        if event.end_seconds <= current_time + 1e-9:
            raise EventValidationError("Event has already ended.")
        lanes = _event_lanes(event)
        if not lanes or len(lanes) != len(set(lanes)):
            raise EventValidationError("Event lanes must be non-empty and unique.")
        unknown = set(lanes) - set(self.lane_targets)
        if unknown:
            raise EventValidationError(f"Unknown event lanes: {sorted(unknown)}")
        if isinstance(event, SpeedLimitEvent) and event.max_speed <= 0:
            raise EventValidationError("Speed limit must be positive.")
        if isinstance(event, AccidentEvent) and not 0 <= event.position_ratio <= 1:
            raise EventValidationError("Accident position_ratio must be between 0 and 1.")
        if isinstance(event, (AccidentEvent, LaneClosureEvent)):
            own_lanes = set(lanes)
            for existing in self._events.values():
                if existing.state in {
                    EventState.CANCELLED,
                    EventState.COMPLETED,
                    EventState.FAILED,
                }:
                    continue
                other = existing.event
                if not _overlaps(event, other):
                    continue
                if own_lanes & set(_event_lanes(other)) and (
                    isinstance(event, AccidentEvent)
                    or isinstance(other, AccidentEvent)
                ):
                    raise EventValidationError(
                        "An accident cannot overlap another accident or closure on the same lane."
                    )

    def _baseline(self, lane_id: str) -> _LaneBaseline:
        if lane_id not in self._baselines:
            self._baselines[lane_id] = _LaneBaseline(
                allowed=tuple(self.traci.lane.getAllowed(lane_id)),
                disallowed=tuple(self.traci.lane.getDisallowed(lane_id)),
                max_speed=float(self.traci.lane.getMaxSpeed(lane_id)),
            )
        return self._baselines[lane_id]

    def _activate(self, runtime: _EventRuntime, current_time: float) -> None:
        event = runtime.event
        try:
            if isinstance(event, LaneClosureEvent):
                self._activate_closure(event)
            elif isinstance(event, SpeedLimitEvent):
                self._activate_speed_limit(event)
            else:
                self._activate_accident(runtime, event, current_time)
            runtime.state = EventState.ACTIVE
        except Exception as exc:
            runtime.error = str(exc)
            runtime.state = EventState.FAILED

    def _activate_closure(self, event: LaneClosureEvent) -> None:
        changed = []
        try:
            for lane_id in event.lane_ids:
                self._baseline(lane_id)
                self._closures.setdefault(lane_id, set()).add(event.event_id)
                changed.append(lane_id)
            for lane_id in changed:
                self._recompute_lane(lane_id)
        except Exception:
            for lane_id in changed:
                self._closures.get(lane_id, set()).discard(event.event_id)
                self._best_effort_recompute(lane_id)
            raise

    def _activate_speed_limit(self, event: SpeedLimitEvent) -> None:
        changed = []
        try:
            for lane_id in event.lane_ids:
                baseline = self._baseline(lane_id)
                if event.max_speed >= baseline.max_speed:
                    raise EventValidationError(
                        f"Speed limit for {lane_id} must be below {baseline.max_speed:g}."
                    )
                self._speed_limits.setdefault(lane_id, {})[event.event_id] = event.max_speed
                changed.append(lane_id)
            for lane_id in changed:
                self._recompute_lane(lane_id)
        except Exception:
            for lane_id in changed:
                self._speed_limits.get(lane_id, {}).pop(event.event_id, None)
                self._best_effort_recompute(lane_id)
            raise

    def _activate_accident(
        self,
        runtime: _EventRuntime,
        event: AccidentEvent,
        current_time: float,
    ) -> None:
        target = self.lane_targets[event.lane_id]
        position = min(max(target.length * event.position_ratio, 0.1), target.length - 0.1)
        route_id = f"event_route_{event.event_id}"
        vehicle_id = f"event_vehicle_{event.event_id}"
        self.traci.route.add(route_id, [target.edge_id])
        try:
            self.traci.vehicle.add(
                vehicle_id,
                route_id,
                typeID="citypulse_disturbance_vehicle",
                depart="now",
                departLane=str(target.lane_index),
                departPos=f"{position:g}",
                departSpeed="0",
            )
            self.traci.vehicle.setStop(
                vehicle_id,
                target.edge_id,
                pos=position,
                laneIndex=target.lane_index,
                duration=max(0.1, event.end_seconds - current_time),
            )
        except Exception:
            try:
                if vehicle_id in set(self.traci.vehicle.getIDList()):
                    self.traci.vehicle.remove(vehicle_id)
            except Exception:
                pass
            raise
        runtime.vehicle_id = vehicle_id

    def _deactivate(self, runtime: _EventRuntime) -> None:
        event = runtime.event
        try:
            if isinstance(event, LaneClosureEvent):
                for lane_id in event.lane_ids:
                    self._closures.get(lane_id, set()).discard(event.event_id)
                    self._recompute_lane(lane_id)
            elif isinstance(event, SpeedLimitEvent):
                for lane_id in event.lane_ids:
                    self._speed_limits.get(lane_id, {}).pop(event.event_id, None)
                    self._recompute_lane(lane_id)
            elif runtime.vehicle_id is not None:
                if runtime.vehicle_id in set(self.traci.vehicle.getIDList()):
                    self.traci.vehicle.remove(runtime.vehicle_id)
        except Exception as exc:
            runtime.error = str(exc)
            runtime.state = EventState.FAILED

    def _recompute_lane(self, lane_id: str) -> None:
        baseline = self._baseline(lane_id)
        if self._closures.get(lane_id):
            disallowed = sorted(set(baseline.disallowed) | set(BLOCKED_VEHICLE_CLASSES))
            self.traci.lane.setDisallowed(lane_id, disallowed)
        elif baseline.allowed:
            self.traci.lane.setAllowed(lane_id, list(baseline.allowed))
        else:
            self.traci.lane.setDisallowed(lane_id, list(baseline.disallowed))
        limits = self._speed_limits.get(lane_id, {}).values()
        self.traci.lane.setMaxSpeed(lane_id, min((baseline.max_speed, *limits)))

    def _best_effort_recompute(self, lane_id: str) -> None:
        try:
            self._recompute_lane(lane_id)
        except Exception:
            pass
