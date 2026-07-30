"""Scheduled SUMO disturbances with overlap-safe lane restoration."""

from __future__ import annotations

from dataclasses import asdict, dataclass
from enum import Enum
from typing import Mapping


SUMO_EVENT_TYPES = (
    "lane_closure",
    "stopped_vehicle",
    "collision_blockage",
    "speed_restriction",
    "queue_spillback",
)

LEGACY_EVENT_TYPES = (
    "accident",
    "speed_limit",
)

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
    successor_edge_ids: tuple[str, ...] = ()


@dataclass(frozen=True)
class LaneClosureEvent:
    event_id: str
    start_seconds: float
    end_seconds: float
    lane_ids: tuple[str, ...]
    max_speed: float | None = None
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
class StoppedVehicleEvent:
    event_id: str
    start_seconds: float
    end_seconds: float
    lane_id: str
    position_ratio: float = 0.5
    vehicle_type: str = "citypulse_disturbance_vehicle"
    event_type: str = "stopped_vehicle"


@dataclass(frozen=True)
class CollisionBlockageEvent:
    event_id: str
    start_seconds: float
    end_seconds: float
    lane_ids: tuple[str, ...]
    position_ratio: float = 0.5
    vehicle_type: str = "citypulse_disturbance_vehicle"
    event_type: str = "collision_blockage"


@dataclass(frozen=True)
class QueueSpillbackEvent:
    event_id: str
    start_seconds: float
    end_seconds: float
    lane_ids: tuple[str, ...]
    blocked_lane_ids: tuple[str, ...]
    max_speed: float | None = None
    position_ratio: float = 0.5
    vehicle_type: str = "citypulse_disturbance_vehicle"
    event_type: str = "queue_spillback"


@dataclass(frozen=True)
class AccidentEvent:
    event_id: str
    start_seconds: float
    end_seconds: float
    lane_id: str
    position_ratio: float
    max_speed: float | None = None
    event_type: str = "accident"


DisturbanceEvent = (
    LaneClosureEvent
    | SpeedLimitEvent
    | StoppedVehicleEvent
    | CollisionBlockageEvent
    | QueueSpillbackEvent
    | AccidentEvent
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
    vehicle_ids: tuple[str, ...] = ()
    created_vehicle_ids: tuple[str, ...] = ()
    pending_stops: tuple[tuple[str, LaneTarget, float, bool], ...] = ()
    pending_existing_stop_lane: str | None = None
    forced_speed_vehicle_ids: tuple[str, ...] = ()


def _overlaps(first: DisturbanceEvent, second: DisturbanceEvent) -> bool:
    return first.start_seconds < second.end_seconds and second.start_seconds < first.end_seconds


def _event_lanes(event: DisturbanceEvent) -> tuple[str, ...]:
    if isinstance(event, QueueSpillbackEvent):
        return event.blocked_lane_ids
    if isinstance(event, (AccidentEvent, StoppedVehicleEvent)):
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
            if runtime.state == EventState.ACTIVE and runtime.pending_stops:
                self._apply_pending_stops(runtime, now)
            if runtime.state == EventState.ACTIVE and runtime.pending_existing_stop_lane:
                if self._try_stop_existing_vehicle(
                    runtime,
                    runtime.event,
                    now,
                    lane_id=runtime.pending_existing_stop_lane,
                ):
                    runtime.pending_existing_stop_lane = None
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
        if isinstance(event, QueueSpillbackEvent):
            if not event.lane_ids or len(event.lane_ids) != len(set(event.lane_ids)):
                raise EventValidationError(
                    "Queue spillback target lanes must be non-empty and unique."
                )
            unknown_target_lanes = set(event.lane_ids) - set(self.lane_targets)
            if unknown_target_lanes:
                raise EventValidationError(
                    f"Unknown queue spillback target lanes: {sorted(unknown_target_lanes)}"
                )
        unknown = set(lanes) - set(self.lane_targets)
        if unknown:
            raise EventValidationError(f"Unknown event lanes: {sorted(unknown)}")
        if isinstance(event, SpeedLimitEvent) and event.max_speed <= 0:
            raise EventValidationError("Speed limit must be positive.")
        if isinstance(event, LaneClosureEvent) and event.max_speed is not None:
            if event.max_speed <= 0:
                raise EventValidationError("Lane closure max_speed must be positive.")
        if isinstance(event, QueueSpillbackEvent) and event.max_speed is not None:
            if event.max_speed <= 0:
                raise EventValidationError("Queue spillback max_speed must be positive.")
        if isinstance(event, AccidentEvent) and event.max_speed is not None:
            if event.max_speed <= 0:
                raise EventValidationError("Accident max_speed must be positive.")
        if isinstance(
            event,
            (
                AccidentEvent,
                StoppedVehicleEvent,
                CollisionBlockageEvent,
                QueueSpillbackEvent,
            ),
        ):
            if not 0 <= event.position_ratio <= 1:
                raise EventValidationError("Blocking vehicle position_ratio must be between 0 and 1.")
        if isinstance(
            event,
            (
                AccidentEvent,
                StoppedVehicleEvent,
                CollisionBlockageEvent,
                QueueSpillbackEvent,
                LaneClosureEvent,
            ),
        ):
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
                    isinstance(
                        event,
                        (
                            AccidentEvent,
                            StoppedVehicleEvent,
                            CollisionBlockageEvent,
                            QueueSpillbackEvent,
                        ),
                    )
                    or isinstance(
                        other,
                        (
                            AccidentEvent,
                            StoppedVehicleEvent,
                            CollisionBlockageEvent,
                            QueueSpillbackEvent,
                        ),
                    )
                ):
                    raise EventValidationError(
                        "A blocking vehicle event cannot overlap another blocking event "
                        "or closure on the same lane."
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
            elif isinstance(event, QueueSpillbackEvent) and event.max_speed is not None:
                self._activate_queue_spillback(event)
            elif isinstance(event, AccidentEvent) and event.max_speed is not None:
                self._activate_accident_speed_limit(event)
            elif isinstance(event, (QueueSpillbackEvent, AccidentEvent)):
                target_lane_id = (
                    event.blocked_lane_ids[0]
                    if isinstance(event, QueueSpillbackEvent)
                    else event.lane_id
                )
                if not self._try_stop_existing_vehicle(
                    runtime,
                    event,
                    current_time,
                    lane_id=target_lane_id,
                ):
                    runtime.pending_existing_stop_lane = target_lane_id
            else:
                self._activate_blocking_vehicles(runtime, event, current_time)
            runtime.state = EventState.ACTIVE
        except Exception as exc:
            runtime.error = str(exc)
            runtime.state = EventState.FAILED

    def _activate_closure(self, event: LaneClosureEvent) -> None:
        changed = []
        try:
            for lane_id in event.lane_ids:
                baseline = self._baseline(lane_id)
                if event.max_speed is not None:
                    if event.max_speed >= baseline.max_speed:
                        raise EventValidationError(
                            f"Lane closure speed for {lane_id} must be below "
                            f"{baseline.max_speed:g}."
                        )
                    self._speed_limits.setdefault(lane_id, {})[event.event_id] = event.max_speed
                else:
                    self._closures.setdefault(lane_id, set()).add(event.event_id)
                changed.append(lane_id)
            for lane_id in changed:
                self._recompute_lane(lane_id)
        except Exception:
            for lane_id in changed:
                self._closures.get(lane_id, set()).discard(event.event_id)
                self._speed_limits.get(lane_id, {}).pop(event.event_id, None)
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

    def _activate_queue_spillback(self, event: QueueSpillbackEvent) -> None:
        changed = []
        try:
            for lane_id in event.blocked_lane_ids:
                baseline = self._baseline(lane_id)
                if event.max_speed is None or event.max_speed >= baseline.max_speed:
                    raise EventValidationError(
                        f"Queue spillback speed for {lane_id} must be below "
                        f"{baseline.max_speed:g}."
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

    def _activate_accident_speed_limit(self, event: AccidentEvent) -> None:
        baseline = self._baseline(event.lane_id)
        if event.max_speed is None or event.max_speed >= baseline.max_speed:
            raise EventValidationError(
                f"Accident speed for {event.lane_id} must be below {baseline.max_speed:g}."
            )
        try:
            self._speed_limits.setdefault(event.lane_id, {})[event.event_id] = event.max_speed
            self._recompute_lane(event.lane_id)
        except Exception:
            self._speed_limits.get(event.lane_id, {}).pop(event.event_id, None)
            self._best_effort_recompute(event.lane_id)
            raise

    def _activate_blocking_vehicles(
        self,
        runtime: _EventRuntime,
        event: AccidentEvent | StoppedVehicleEvent | CollisionBlockageEvent | QueueSpillbackEvent,
        current_time: float,
    ) -> None:
        if isinstance(event, (StoppedVehicleEvent, AccidentEvent)):
            self._activate_existing_stopped_vehicle(runtime, event, current_time)
            return

        vehicle_ids = []
        try:
            for index, lane_id in enumerate(_event_lanes(event)):
                target = self.lane_targets[lane_id]
                position = min(
                    max(target.length * event.position_ratio, 1.0),
                    target.length - 0.1,
                )
                depart_position = max(0.1, position - 1.0)
                suffix = "" if len(_event_lanes(event)) == 1 else f"_{index}"
                route_id = f"event_route_{event.event_id}{suffix}"
                vehicle_id = f"event_vehicle_{event.event_id}{suffix}"
                route_edges = [target.edge_id]
                if target.successor_edge_ids:
                    route_edges.append(target.successor_edge_ids[0])
                self.traci.route.add(route_id, route_edges)
                self.traci.vehicle.add(
                    vehicle_id,
                    route_id,
                    typeID=getattr(event, "vehicle_type", "citypulse_disturbance_vehicle"),
                    depart="now",
                    # SUMO 1.12 rejects a numeric departLane for some routes when
                    # the event vehicle is inserted on a dynamically-built route.
                    # Let SUMO select a valid lane first, then pin the vehicle to
                    # the requested disturbance lane before applying the stop.
                    departLane="best",
                    departPos=f"{depart_position:g}",
                    departSpeed="0",
                )
                # setStop's laneIndex is the authoritative lane request.  Do
                # not also issue changeLane: SUMO 1.12 can abort the stop when
                # a pending lane-change command competes with it.
                vehicle_ids.append(vehicle_id)
        except Exception:
            self._remove_vehicles(vehicle_ids)
            raise
        runtime.vehicle_ids = tuple(vehicle_ids)
        runtime.created_vehicle_ids = tuple(vehicle_ids)
        runtime.pending_stops = tuple(
            (
                vehicle_id, self.lane_targets[lane_id], min(
                    max(self.lane_targets[lane_id].length * event.position_ratio, 1.0),
                    self.lane_targets[lane_id].length - 0.1,
                ), False,
            )
            for vehicle_id, lane_id in zip(vehicle_ids, _event_lanes(event))
        )

    def _apply_pending_stops(self, runtime: _EventRuntime, current_time: float) -> None:
        remaining = []
        live = set(self.traci.vehicle.getIDList())
        for vehicle_id, target, position, positioned in runtime.pending_stops:
            if vehicle_id not in live:
                remaining.append((vehicle_id, target, position, positioned))
                continue
            if not positioned:
                # Let SUMO apply moveTo for one simulation step before sending
                # setStop; issuing both in the same step aborts the stop on 1.12.
                self.traci.vehicle.moveTo(vehicle_id, target.lane_id, max(0.1, position - 1.0))
                remaining.append((vehicle_id, target, position, True))
                continue
            self.traci.vehicle.setStop(
                vehicle_id,
                target.edge_id,
                pos=position,
                laneIndex=target.lane_index,
                duration=max(0.1, runtime.event.end_seconds - current_time),
            )
        runtime.pending_stops = tuple(remaining)

    def _activate_existing_stopped_vehicle(
        self,
        runtime: _EventRuntime,
        event: StoppedVehicleEvent | AccidentEvent | QueueSpillbackEvent,
        current_time: float,
        *,
        lane_id: str | None = None,
    ) -> None:
        if not self._try_stop_existing_vehicle(
            runtime,
            event,
            current_time,
            lane_id=lane_id,
        ):
            target_lane_id = lane_id or event.lane_id
            raise EventValidationError(
                f"No live vehicle upstream of stopped_vehicle position on {target_lane_id}."
            )

    def _try_stop_existing_vehicle(
        self,
        runtime: _EventRuntime,
        event: StoppedVehicleEvent | AccidentEvent | QueueSpillbackEvent,
        current_time: float,
        *,
        lane_id: str | None = None,
    ) -> bool:
        target_lane_id = lane_id or event.lane_id
        target = self.lane_targets[target_lane_id]
        position = min(
            max(target.length * event.position_ratio, 1.0),
            target.length - 0.1,
        )
        candidates = []
        for vehicle_id in self.traci.vehicle.getIDList():
            try:
                if self.traci.vehicle.getLaneID(vehicle_id) != target_lane_id:
                    continue
                vehicle_position = float(self.traci.vehicle.getLanePosition(vehicle_id))
            except Exception:
                continue
            # A vehicle that has passed the requested ratio can still be
            # stopped safely at its current position.  Restricting candidates
            # to vehicles before that ratio made long-running incidents miss
            # every live vehicle on some SUMO routes.
            if vehicle_position < target.length - 0.2:
                candidates.append((abs(position - vehicle_position), vehicle_position, vehicle_id))
        if not candidates:
            return False
        _, vehicle_position, vehicle_id = min(candidates)
        stop_position = min(
            max(position, vehicle_position + 0.1),
            target.length - 0.1,
        )
        if isinstance(event, AccidentEvent):
            # SUMO 1.12 can report a near-stop command as successful while
            # rejecting it internally for braking-distance reasons.  Freeze
            # the same existing vehicle directly and restore car-following on
            # cleanup; never create a synthetic accident vehicle.
            self.traci.vehicle.setSpeed(vehicle_id, 0.0)
            runtime.forced_speed_vehicle_ids = (vehicle_id,)
        else:
            self.traci.vehicle.setStop(
                vehicle_id,
                target.edge_id,
                pos=stop_position,
                laneIndex=target.lane_index,
                duration=max(0.1, event.end_seconds - current_time),
            )
        runtime.vehicle_ids = (vehicle_id,)
        return True

    def _deactivate(self, runtime: _EventRuntime) -> None:
        event = runtime.event
        try:
            if isinstance(event, LaneClosureEvent):
                for lane_id in event.lane_ids:
                    self._closures.get(lane_id, set()).discard(event.event_id)
                    self._speed_limits.get(lane_id, {}).pop(event.event_id, None)
                    self._recompute_lane(lane_id)
            elif isinstance(event, SpeedLimitEvent):
                for lane_id in event.lane_ids:
                    self._speed_limits.get(lane_id, {}).pop(event.event_id, None)
                    self._recompute_lane(lane_id)
            elif isinstance(event, QueueSpillbackEvent) and event.max_speed is not None:
                for lane_id in event.blocked_lane_ids:
                    self._speed_limits.get(lane_id, {}).pop(event.event_id, None)
                    self._recompute_lane(lane_id)
            elif isinstance(event, AccidentEvent) and event.max_speed is not None:
                self._speed_limits.get(event.lane_id, {}).pop(event.event_id, None)
                self._recompute_lane(event.lane_id)
            elif runtime.created_vehicle_ids:
                self._remove_vehicles(runtime.created_vehicle_ids)
            elif runtime.vehicle_ids:
                if runtime.forced_speed_vehicle_ids:
                    self._reset_vehicle_speeds(runtime.forced_speed_vehicle_ids)
                else:
                    self._resume_vehicles(runtime.vehicle_ids)
        except Exception as exc:
            runtime.error = str(exc)
            runtime.state = EventState.FAILED

    def _remove_vehicles(self, vehicle_ids) -> None:
        try:
            live = set(self.traci.vehicle.getIDList())
        except Exception:
            live = set()
        for vehicle_id in vehicle_ids:
            try:
                if vehicle_id in live:
                    self.traci.vehicle.remove(vehicle_id)
            except Exception:
                pass

    def _resume_vehicles(self, vehicle_ids) -> None:
        try:
            live = set(self.traci.vehicle.getIDList())
        except Exception:
            live = set()
        for vehicle_id in vehicle_ids:
            try:
                if vehicle_id in live:
                    self.traci.vehicle.resume(vehicle_id)
            except Exception:
                pass

    def _reset_vehicle_speeds(self, vehicle_ids) -> None:
        for vehicle_id in vehicle_ids:
            try:
                self.traci.vehicle.setSpeed(vehicle_id, -1)
            except Exception:
                pass

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
