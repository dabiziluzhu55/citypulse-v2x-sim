"""仿真应用服务：负责会话、模式映射、指标与算法清理"""

from __future__ import annotations

import logging
import threading
from queue import Empty
from typing import Any, Iterable
from urllib.parse import urljoin

from simulation.sumo import (
    AccidentEvent,
    LaneClosureEvent,
    SimulationConfig,
    SimulationManager,
    SpeedLimitEvent,
)
from simulation.sumo.events import DisturbanceEvent
from simulation.sumo.session import SimulationSnapshot

from ..controllers.registry import require_control_mode
from ..controllers.runtime import AlgorithmRuntimeStore
from ..core.config import Settings
from ..core.exceptions import AppError
from ..metrics.session_hub import SessionMetricsHub
from ..schemas.events import (
    AccidentRequest,
    EventRequest,
    LaneClosureRequest,
    SpeedLimitRequest,
)
from ..schemas.simulations import StartSimulationRequest
from .snapshot_serializer import SnapshotSerializer

logger = logging.getLogger(__name__)

TERMINAL_STATES = frozenset({"STOPPED", "COMPLETED", "FAILED"})
INTERNAL_ALGORITHM_PATH_PREFIX = "api/v1/internal/algorithm"


class SimulationService:

    def __init__(
        self,
        manager: SimulationManager,
        serializer: SnapshotSerializer,
        settings: Settings,
        algorithm_store: AlgorithmRuntimeStore,
        metrics_hub: SessionMetricsHub | None = None,
    ) -> None:
        self._manager = manager
        self._serializer = serializer
        self._settings = settings
        self._algorithm_store = algorithm_store
        self._metrics_hub = metrics_hub or SessionMetricsHub()
        self._metrics_threads: dict[str, threading.Thread] = {}
        self._session_modes: dict[str, str] = {}
        self._lock = threading.RLock()

    # ------------------------------------------------------------------
    # Catalog
    # ------------------------------------------------------------------

    def get_catalog_response(self):
        from .map_service import MapService

        catalog = self._manager.catalog()
        return MapService.serialize_catalog(
            catalog,
            self._settings.mvp_intersection_ids,
            control_modes=list(self._settings.enabled_control_modes()),
        )

    def start(self, request: StartSimulationRequest) -> tuple[str, SimulationSnapshot]:
        self._validate_request_against_catalog(request)
        enabled = self._settings.enabled_control_modes()
        if request.control_mode not in enabled:
            raise AppError(
                code="INVALID_CONTROL_MODE",
                message=(
                    f"Unsupported control_mode={request.control_mode!r}. "
                    f"Allowed: {list(enabled)}"
                ),
                status_code=422,
            )

        config = self._build_config(request)
        logger.info(
            "启动仿真: intersections=%s period=%s control_mode=%s kernel_mode=%s",
            request.intersection_ids,
            request.period,
            request.control_mode,
            config.control_mode,
        )
        session_id = self._manager.start(config)
        with self._lock:
            self._session_modes[session_id] = request.control_mode
        self._metrics_hub.start_session(session_id, request.control_mode)
        self._start_metrics_watcher(session_id)
        snapshot = self._manager.snapshot(session_id)
        return session_id, snapshot

    def snapshot(self, session_id: str) -> dict[str, Any]:
        return self.serialize_snapshot(self._manager.snapshot(session_id))

    def serialize_snapshot(self, snapshot: SimulationSnapshot) -> dict[str, Any]:
        payload = self._serializer.serialize(snapshot)
        evaluation = self.get_metrics(snapshot.session_id)
        if evaluation:
            metrics = dict(payload.get("metrics") or {})
            metrics["evaluation"] = evaluation
            for key in (
                "avg_waiting_time",
                "avg_queue_length",
                "throughput",
                "avg_travel_time",
                "fuel_consumption",
            ):
                if evaluation.get(key) is not None:
                    metrics[key] = evaluation[key]
            payload["metrics"] = metrics
            payload["evaluation"] = evaluation
        return payload

    def stop(self, session_id: str) -> SimulationSnapshot:
        logger.info("停止仿真: %s", session_id)
        self._manager.stop(session_id)
        snapshot = self._manager.snapshot(session_id)
        self._finalize_session(snapshot)
        return snapshot

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

    def get_metrics(self, session_id: str) -> dict[str, Any]:
        latency = self._algorithm_store.get_decision_latency_ms(session_id)
        finished_hint = None
        try:
            state = self._manager.snapshot(session_id).state
            finished_hint = state in TERMINAL_STATES
        except Exception:
            finished_hint = None

        payload = self._metrics_hub.get_metrics_payload(
            session_id,
            decision_latency_ms=latency,
            finished_hint=finished_hint,
        )
        if payload is not None:
            return payload

        # 尚无采集数据时返回空
        mode = self._session_modes.get(session_id, "fixed")
        return {
            "episode_id": session_id,
            "algorithm": mode,
            "avg_waiting_time": 0.0,
            "avg_travel_time": 0.0,
            "avg_queue_length": 0.0,
            "throughput": 0.0,
            "fuel_consumption": 0.0,
            "avg_decision_latency_ms": latency,
            "departed": 0,
            "arrived": 0,
            "finished": bool(finished_hint),
        }

    def shutdown_active_session(self) -> None:
        session_id = self.get_active_session_id()
        if session_id is not None:
            try:
                logger.info("Shutting down active simulation session: %s", session_id)
                self._manager.stop(session_id)
                try:
                    snapshot = self._manager.snapshot(session_id)
                    self._finalize_session(snapshot)
                except Exception:
                    self._algorithm_store.abort_episode(session_id)
                    self._metrics_hub.abort_without_snapshot(
                        session_id,
                        decision_latency_ms=self._algorithm_store.get_decision_latency_ms(
                            session_id
                        ),
                    )
            except Exception:
                logger.exception("Failed to stop active session %s during shutdown.", session_id)
        self._algorithm_store.clear_all()
        self._metrics_hub.clear_all()
        with self._lock:
            self._session_modes.clear()
            self._metrics_threads.clear()

    def get_active_session_id(self) -> str | None:
        session_id = getattr(self._manager, "_active_session_id", None)
        if not session_id:
            return None
        snapshot = self._manager.snapshot(session_id)
        if snapshot.state in TERMINAL_STATES:
            return None
        return session_id

    # ------------------------------------------------------------------
    # 指标后台订阅（与前端无关）
    # ------------------------------------------------------------------

    def _start_metrics_watcher(self, session_id: str) -> None:
        thread = threading.Thread(
            target=self._metrics_watch_loop,
            args=(session_id,),
            name=f"metrics-{session_id[:8]}",
            daemon=True,
        )
        with self._lock:
            self._metrics_threads[session_id] = thread
        thread.start()

    def _metrics_watch_loop(self, session_id: str) -> None:
        subscription = None
        try:
            subscription = self._manager.subscribe(session_id)
            while True:
                try:
                    snapshot = subscription.get(timeout=2.0)
                except Empty:
                    continue
                self._metrics_hub.observe(snapshot)
                if snapshot.state in TERMINAL_STATES:
                    self._finalize_session(snapshot)
                    break
        except Exception:
            logger.exception("metrics watcher failed for session %s", session_id)
            self._algorithm_store.abort_episode(session_id)
            self._metrics_hub.abort_without_snapshot(
                session_id,
                decision_latency_ms=self._algorithm_store.get_decision_latency_ms(session_id),
            )
        finally:
            if subscription is not None:
                subscription.close()
            with self._lock:
                self._metrics_threads.pop(session_id, None)

    def _finalize_session(self, snapshot: SimulationSnapshot) -> None:
        session_id = snapshot.session_id
        # 算法侧：若 /finish未到或失败，本地abort仍completed latency
        self._algorithm_store.abort_episode(session_id)
        latency = self._algorithm_store.get_decision_latency_ms(session_id)
        self._metrics_hub.finalize(snapshot, decision_latency_ms=latency)
        with self._lock:
            # 保留mode直至completed可被查询一段时间；finalize后mode可清
            self._session_modes.pop(session_id, None)

    # ------------------------------------------------------------------
    # 配置构建
    # ------------------------------------------------------------------

    def _build_config(self, request: StartSimulationRequest) -> SimulationConfig:
        origins = {
            intersection_id: tuple(origin_ids)
            for intersection_id, origin_ids in request.origins.items()
        }
        spec = require_control_mode(request.control_mode)
        algorithm_endpoint = ""
        if spec.needs_algorithm:
            assert spec.algorithm_name is not None
            algorithm_endpoint = urljoin(
                self._settings.algorithm_base_url.rstrip("/") + "/",
                f"{INTERNAL_ALGORITHM_PATH_PREFIX}/{spec.algorithm_name}",
            )

        return SimulationConfig(
            intersection_ids=tuple(request.intersection_ids),
            period=request.period,
            origins=origins,
            window_start_seconds=request.window_start_seconds,
            duration_seconds=request.duration_seconds,
            flow_multiplier=request.flow_multiplier,
            control_mode=spec.kernel_mode,
            algorithm_endpoint=algorithm_endpoint,
            algorithm_timeout=self._settings.algorithm_timeout,
            decision_interval=self._settings.decision_interval,
            seed=request.seed,
            step_length=request.step_length,
            gui=request.gui,
            realtime=request.realtime,
            snapshot_interval_seconds=request.snapshot_interval_seconds,
            initial_events=tuple(
                self._to_disturbance_event(item) for item in request.initial_events
            ),
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
