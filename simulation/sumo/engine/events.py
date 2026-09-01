"""Scheduled SUMO disturbances with overlap-safe lane restoration."""

from __future__ import annotations

from dataclasses import asdict, dataclass
from enum import Enum
from typing import Mapping


DEFAULT_ACTIVITY_VEHICLE_TYPE_ID = "citypulse_event_passenger"

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
    role: str = ""
    successor_edge_ids: tuple[str, ...] = ()


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


@dataclass(frozen=True)
class _LaneBaseline:
    allowed: tuple[str, ...]
    disallowed: tuple[str, ...]
    max_speed: float


@dataclass(frozen=True)
class _ActivityRoute:
    route_id: str
    depart_position: float
    arrival_position: float


@dataclass
class _ActivityRuntime:
    routes: tuple[_ActivityRoute, ...]
    unreachable_route_count: int
    next_vehicle_index: int = 0
    spawned_vehicle_count: int = 0


@dataclass
class _EventRuntime:
    event: DisturbanceEvent
    state: EventState = EventState.SCHEDULED
    error: str | None = None
    vehicle_id: str | None = None
    activity: _ActivityRuntime | None = None


def _overlaps(first: DisturbanceEvent, second: DisturbanceEvent) -> bool:
    return first.start_seconds < second.end_seconds and second.start_seconds < first.end_seconds


def _event_lanes(event: DisturbanceEvent) -> tuple[str, ...]:
    if isinstance(event, AccidentEvent):
        return (event.lane_id,)
    if isinstance(event, MajorEventOpeningEvent):
        return (event.venue_lane_id, *event.source_lane_ids)
    if isinstance(event, MajorEventClosingEvent):
        return (event.venue_lane_id, *event.destination_lane_ids)
    return event.lane_ids


def _is_activity_event(event: DisturbanceEvent) -> bool:
    return isinstance(event, (MajorEventOpeningEvent, MajorEventClosingEvent))


def _sanitized_id(value: str) -> str:
    return "".join(character if character.isalnum() or character in "_.-" else "_" for character in value)


class DisturbanceScheduler:
    def __init__(
        self,
        traci,
        lane_targets: Mapping[str, LaneTarget],
        duration_seconds: float,
        *,
        upstream_extensions: Mapping[str, str] | None = None,
        downstream_extensions: Mapping[str, str] | None = None,
    ) -> None:
        self.traci = traci
        self.lane_targets = dict(lane_targets)
        self.duration_seconds = float(duration_seconds)
        self.upstream_extensions = dict(upstream_extensions or {})
        self.downstream_extensions = dict(downstream_extensions or {})
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
            if runtime.state == EventState.ACTIVE and _is_activity_event(runtime.event):
                self._best_effort_tick_activity(runtime, now)
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
                if runtime.state == EventState.ACTIVE and _is_activity_event(runtime.event):
                    self._best_effort_tick_activity(runtime, now)

    def snapshots(self) -> tuple[EventSnapshot, ...]:
        values = []
        for event_id in sorted(self._events):
            runtime = self._events[event_id]
            raw = asdict(runtime.event)
            for key in ("event_id", "event_type", "start_seconds", "end_seconds"):
                raw.pop(key, None)
            if _is_activity_event(runtime.event):
                activity = runtime.activity
                raw.update(
                    {
                        "spawned_vehicle_count": (
                            activity.spawned_vehicle_count if activity else 0
                        ),
                        "planned_vehicle_count": runtime.event.vehicle_count,
                        "reachable_route_count": len(activity.routes) if activity else 0,
                        "unreachable_route_count": (
                            activity.unreachable_route_count if activity else 0
                        ),
                    }
                )
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
        if isinstance(event, (MajorEventOpeningEvent, MajorEventClosingEvent)):
            self._validate_activity_event(event, lanes)
        elif not lanes or len(lanes) != len(set(lanes)):
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

    def _validate_activity_event(
        self,
        event: MajorEventOpeningEvent | MajorEventClosingEvent,
        lanes: tuple[str, ...],
    ) -> None:
        if event.vehicle_count <= 0:
            raise EventValidationError("Activity vehicle_count must be positive.")
        if not event.vehicle_type_id:
            raise EventValidationError("Activity vehicle_type_id cannot be empty.")
        if not event.venue_lane_id:
            raise EventValidationError("Activity venue_lane_id cannot be empty.")
        explicit_lanes = (
            event.source_lane_ids
            if isinstance(event, MajorEventOpeningEvent)
            else event.destination_lane_ids
        )
        if len(explicit_lanes) != len(set(explicit_lanes)):
            raise EventValidationError("Activity endpoint lanes must be unique.")
        if event.venue_lane_id in set(explicit_lanes):
            raise EventValidationError("Activity endpoint lanes cannot include the venue lane.")
        if len(lanes) != len(set(lanes)):
            raise EventValidationError("Event lanes must be non-empty and unique.")

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
            elif isinstance(event, AccidentEvent):
                self._activate_accident(runtime, event, current_time)
            elif _is_activity_event(event):
                self._activate_activity(runtime, event, current_time)
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

    def _activate_activity(
        self,
        runtime: _EventRuntime,
        event: MajorEventOpeningEvent | MajorEventClosingEvent,
        current_time: float,
    ) -> None:
        route_specs = []
        unreachable = 0
        route_ids: dict[str, int] = {}
        for source_lane_id, destination_lane_id in self._activity_lane_pairs(event):
            source = self.lane_targets[source_lane_id]
            destination = self.lane_targets[destination_lane_id]
            source_edge_id = self._activity_endpoint_edge(source)
            destination_edge_id = self._activity_endpoint_edge(destination)
            route_edges = self._activity_route_edges(
                source_edge_id,
                destination_edge_id,
                event.vehicle_type_id,
                current_time,
            )
            if not route_edges:
                unreachable += 1
                continue
            base_route_id = (
                f"event_route_{event.event_id}_"
                f"{_sanitized_id(source_edge_id)}_{_sanitized_id(destination_edge_id)}"
            )
            route_ids[base_route_id] = route_ids.get(base_route_id, 0) + 1
            route_id = (
                base_route_id
                if route_ids[base_route_id] == 1
                else f"{base_route_id}_{route_ids[base_route_id]}"
            )
            route_specs.append(
                (
                    route_id,
                    tuple(route_edges),
                    self._activity_edge_midpoint(source_edge_id),
                    self._activity_edge_midpoint(destination_edge_id),
                )
            )
        if not route_specs:
            raise EventValidationError("Activity event has no reachable routes.")
        if self._activity_has_explicit_endpoints(event) and unreachable:
            raise EventValidationError("Activity event has unreachable explicit routes.")
        routes = []
        for route_id, route_edges, depart_position, arrival_position in route_specs:
            self.traci.route.add(route_id, list(route_edges))
            routes.append(
                _ActivityRoute(
                    route_id=route_id,
                    depart_position=depart_position,
                    arrival_position=arrival_position,
                )
            )
        runtime.activity = _ActivityRuntime(
            routes=tuple(routes),
            unreachable_route_count=unreachable,
        )

    def _activity_lane_pairs(
        self,
        event: MajorEventOpeningEvent | MajorEventClosingEvent,
    ) -> tuple[tuple[str, str], ...]:
        if isinstance(event, MajorEventOpeningEvent):
            sources = event.source_lane_ids or self._default_activity_lanes(
                {"incoming", "both", ""},
                event.venue_lane_id,
            )
            return tuple((source, event.venue_lane_id) for source in sources)
        destinations = event.destination_lane_ids or self._default_activity_lanes(
            {"outgoing", "both", ""},
            event.venue_lane_id,
        )
        return tuple((event.venue_lane_id, destination) for destination in destinations)

    def _default_activity_lanes(
        self,
        roles: set[str],
        venue_lane_id: str,
    ) -> tuple[str, ...]:
        venue_edge_id = self.lane_targets[venue_lane_id].edge_id
        return tuple(
            lane_id
            for lane_id, target in sorted(self.lane_targets.items())
            if target.edge_id != venue_edge_id and target.role in roles
        )

    def _activity_has_explicit_endpoints(
        self,
        event: MajorEventOpeningEvent | MajorEventClosingEvent,
    ) -> bool:
        if isinstance(event, MajorEventOpeningEvent):
            return bool(event.source_lane_ids)
        return bool(event.destination_lane_ids)

    def _activity_endpoint_edge(self, target: LaneTarget) -> str:
        if target.role == "incoming":
            return self.upstream_extensions.get(target.edge_id, target.edge_id)
        if target.role == "outgoing":
            return self.downstream_extensions.get(target.edge_id, target.edge_id)
        upstream = self.upstream_extensions.get(target.edge_id)
        downstream = self.downstream_extensions.get(target.edge_id)
        if upstream is not None and downstream is None:
            return upstream
        if downstream is not None and upstream is None:
            return downstream
        return target.edge_id

    def _activity_edge_midpoint(self, edge_id: str) -> float:
        lane_count = int(self.traci.edge.getLaneNumber(edge_id))
        lengths = [
            float(self.traci.lane.getLength(f"{edge_id}_{lane_index}"))
            for lane_index in range(lane_count)
        ]
        positive_lengths = [length for length in lengths if length > 0]
        if not positive_lengths:
            raise EventValidationError(
                f"Activity endpoint edge {edge_id!r} has no positive lane length."
            )
        return min(positive_lengths) * 0.5

    def _activity_route_edges(
        self,
        source_edge_id: str,
        destination_edge_id: str,
        vehicle_type_id: str,
        current_time: float,
    ) -> tuple[str, ...]:
        if source_edge_id == destination_edge_id:
            return (source_edge_id,)
        route = self.traci.simulation.findRoute(
            source_edge_id,
            destination_edge_id,
            vType=vehicle_type_id,
            depart=current_time,
        )
        edges = tuple(str(edge) for edge in getattr(route, "edges", ()))
        return edges

    def _tick_activity(self, runtime: _EventRuntime, current_time: float) -> None:
        activity = runtime.activity
        event = runtime.event
        if activity is None or not _is_activity_event(event):
            return
        interval = (event.end_seconds - event.start_seconds) / event.vehicle_count
        while activity.next_vehicle_index < event.vehicle_count:
            vehicle_index = activity.next_vehicle_index
            depart_time = event.start_seconds + (vehicle_index + 0.5) * interval
            if depart_time > current_time + 1e-9:
                break
            route = activity.routes[vehicle_index % len(activity.routes)]
            vehicle_id = f"event_vehicle_{event.event_id}_{vehicle_index + 1:06d}"
            self.traci.vehicle.add(
                vehicle_id,
                route.route_id,
                typeID=event.vehicle_type_id,
                depart="now",
                departLane="best",
                departPos=f"{route.depart_position:g}",
                departSpeed="max",
                arrivalPos=f"{route.arrival_position:g}",
            )
            activity.next_vehicle_index += 1
            activity.spawned_vehicle_count += 1

    def _best_effort_tick_activity(
        self,
        runtime: _EventRuntime,
        current_time: float,
    ) -> None:
        try:
            self._tick_activity(runtime, current_time)
        except Exception as exc:
            runtime.error = str(exc)
            runtime.state = EventState.FAILED

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
