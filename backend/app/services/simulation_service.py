"""基于SimulationManager的仿真会话"""

from __future__ import annotations

import logging
from typing import Iterable

from simulation.sumo import (
    AccidentEvent,
    LaneClosureEvent,
    SimulationConfig,
    SimulationManager,
    SpeedLimitEvent,
)
from simulation.sumo.events import DisturbanceEvent
from simulation.sumo.session import SimulationSnapshot

from ..core.config import Settings
from ..core.exceptions import AppError
from ..schemas.events import (
    AccidentRequest,
    EventRequest,
    LaneClosureRequest,
    SpeedLimitRequest,
)
from ..schemas.simulations import StartSimulationRequest
from .snapshot_serializer import SnapshotSerializer

logger = logging.getLogger(__name__)

TERMINAL_STATES = {"STOPPED", "COMPLETED", "FAILED"}


class SimulationService:
    def __init__(
        self,
        manager: SimulationManager,
        serializer: SnapshotSerializer,
        settings: Settings,
    ) -> None:
        self._manager = manager
        self._serializer = serializer
        self._settings = settings

    def get_catalog_response(self):
        from .map_service import MapService

        catalog = self._manager.catalog()
        return MapService.serialize_catalog(catalog, self._settings.mvp_intersection_ids)

    def start(self, request: StartSimulationRequest) -> tuple[str, SimulationSnapshot]:
        self._validate_request_against_catalog(request)
        config = self._build_config(request)
        logger.info(
            "Starting simulation for intersections=%s period=%s duration=%s",
            request.intersection_ids,
            request.period,
            request.duration_seconds,
        )
        session_id = self._manager.start(config)
        snapshot = self._manager.snapshot(session_id)
        logger.info("Created simulation session: %s state=%s", session_id, snapshot.state)
        return session_id, snapshot

    def snapshot(self, session_id: str) -> dict:
        return self.serialize_snapshot(self._manager.snapshot(session_id))

    def serialize_snapshot(self, snapshot: SimulationSnapshot) -> dict:
        return self._serializer.serialize(snapshot)

    def stop(self, session_id: str) -> SimulationSnapshot:
        logger.info("Stopping simulation session: %s", session_id)
        self._manager.stop(session_id)
        return self._manager.snapshot(session_id)

    def add_event(self, session_id: str, request: EventRequest) -> str:
        event = self._to_disturbance_event(request)
        self._validate_event_lanes(event)
        event_id = self._manager.add_event(session_id, event)
        logger.info("Added event %s to session %s", event_id, session_id)
        return event_id

    def cancel_event(self, session_id: str, event_id: str) -> None:
        logger.info("Cancelling event %s on session %s", event_id, session_id)
        self._manager.cancel_event(session_id, event_id)

    def subscribe(self, session_id: str):
        return self._manager.subscribe(session_id)

    def shutdown_active_session(self) -> None:
        session_id = self.get_active_session_id()
        if session_id is None:
            return
        try:
            logger.info("Shutting down active simulation session: %s", session_id)
            self._manager.stop(session_id)
        except Exception:
            logger.exception("Failed to stop active session %s during shutdown.", session_id)

    def get_active_session_id(self) -> str | None:
        session_id = getattr(self._manager, "_active_session_id", None)
        if not session_id:
            return None
        snapshot = self._manager.snapshot(session_id)
        if snapshot.state in TERMINAL_STATES:
            return None
        return session_id

    def _build_config(self, request: StartSimulationRequest) -> SimulationConfig:
        origins = {
            intersection_id: tuple(origin_ids)
            for intersection_id, origin_ids in request.origins.items()
        }
        return SimulationConfig(
            intersection_ids=tuple(request.intersection_ids),
            period=request.period,
            origins=origins,
            window_start_seconds=request.window_start_seconds,
            duration_seconds=request.duration_seconds,
            flow_multiplier=request.flow_multiplier,
            control_mode=request.control_mode,
            seed=request.seed,
            step_length=request.step_length,
            gui=request.gui,
            realtime=request.realtime,
            snapshot_interval_seconds=request.snapshot_interval_seconds,
            initial_events=tuple(self._to_disturbance_event(item) for item in request.initial_events),
        )

    def _validate_request_against_catalog(self, request: StartSimulationRequest) -> None:
        catalog = self._manager.catalog()
        intersection = catalog.intersections.get(self._settings.default_intersection_id)
        if intersection is None:
            raise AppError(
                code="UNKNOWN_INTERSECTION",
                message=f"Catalog does not contain {self._settings.default_intersection_id}.",
                status_code=422,
            )
        if request.period not in intersection.periods:
            raise AppError(
                code="INVALID_PERIOD",
                message=f"Period {request.period!r} is not supported for demo_2.",
                status_code=422,
            )

        valid_origins = {origin.origin_id for origin in intersection.origins}
        for intersection_id, origin_ids in request.origins.items():
            if intersection_id not in catalog.intersections:
                raise AppError(
                    code="INVALID_ORIGIN",
                    message=f"Unknown intersection in origins: {intersection_id}",
                    status_code=422,
                )
            unknown = set(origin_ids) - valid_origins
            if unknown:
                raise AppError(
                    code="INVALID_ORIGIN",
                    message=f"Unknown origin IDs for demo_2: {sorted(unknown)}",
                    status_code=422,
                )

        for event in request.initial_events:
            self._validate_event_lanes(self._to_disturbance_event(event))

    def _validate_event_lanes(self, event: DisturbanceEvent) -> None:
        catalog = self._manager.catalog()
        lane_ids = {
            lane.lane_id
            for intersection_id in self._settings.mvp_intersection_ids
            for lane in catalog.intersections[intersection_id].lanes
        }
        target_lanes = self._event_lane_ids(event)
        unknown = set(target_lanes) - lane_ids
        if unknown:
            raise AppError(
                code="INVALID_LANE",
                message=f"Unknown lane IDs: {sorted(unknown)}",
                status_code=422,
            )

    @staticmethod
    def _event_lane_ids(event: DisturbanceEvent) -> Iterable[str]:
        if isinstance(event, AccidentEvent):
            return (event.lane_id,)
        return event.lane_ids

    @staticmethod
    def _to_disturbance_event(request: EventRequest) -> DisturbanceEvent:
        if isinstance(request, LaneClosureRequest):
            return LaneClosureEvent(
                event_id=request.event_id,
                start_seconds=request.start_seconds,
                end_seconds=request.end_seconds,
                lane_ids=tuple(request.lane_ids),
            )
        if isinstance(request, SpeedLimitRequest):
            return SpeedLimitEvent(
                event_id=request.event_id,
                start_seconds=request.start_seconds,
                end_seconds=request.end_seconds,
                lane_ids=tuple(request.lane_ids),
                max_speed=request.max_speed,
            )
        if isinstance(request, AccidentRequest):
            return AccidentEvent(
                event_id=request.event_id,
                start_seconds=request.start_seconds,
                end_seconds=request.end_seconds,
                lane_id=request.lane_id,
                position_ratio=request.position_ratio,
            )
        raise AppError(
            code="INVALID_EVENT",
            message="Unsupported event type.",
            status_code=422,
        )
