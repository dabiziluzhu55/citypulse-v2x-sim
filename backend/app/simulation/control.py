"""仿真控制层：统一启动/停止/快照，把业务侧 control_mode 映射为 SUMO 内核配置。

- fixed：SimulationManager 固定配时
- max_pressure 等：映射为 algorithm 模式，回调本进程内部算法协议端点
对外始终走 /api/v1/simulations，不按算法拆分 REST 版本。
"""

from __future__ import annotations

import logging
from typing import Any
from urllib.parse import urljoin

from simulation.sumo import SimulationConfig, SimulationManager
from simulation.sumo.session import SimulationSnapshot

from ..controllers.runtime import SUPPORTED_ALGORITHMS, AlgorithmRuntimeStore
from ..core.config import Settings
from ..core.exceptions import AppError
from ..schemas.events import EventRequest
from ..schemas.simulations import StartSimulationRequest
from ..services.simulation_service import SimulationService, TERMINAL_STATES
from ..services.snapshot_serializer import SnapshotSerializer

logger = logging.getLogger(__name__)

# 业务侧管控模式 → 仿真内核模式（内核仅认 fixed / algorithm）
CONTROL_MODE_TO_KERNEL = {
    "fixed": "fixed",
    "max_pressure": "algorithm",
}

# SUMO worker 回调的内部算法协议前缀（非前端对外 API）
INTERNAL_ALGORITHM_PATH_PREFIX = "api/v1/internal/algorithm"


class SimulationControlService:
    """仿真控制服务：复用 SimulationService 的校验与事件能力，并注入算法评估指标。"""

    def __init__(
        self,
        manager: SimulationManager,
        serializer: SnapshotSerializer,
        settings: Settings,
        algorithm_store: AlgorithmRuntimeStore,
        legacy_service: SimulationService | None = None,
    ) -> None:
        self._manager = manager
        self._serializer = serializer
        self._settings = settings
        self._algorithm_store = algorithm_store
        self._legacy = legacy_service or SimulationService(manager, serializer, settings)

    def get_catalog_response(self):
        catalog = self._manager.catalog()
        from ..services.map_service import MapService

        return MapService.serialize_catalog(
            catalog,
            self._settings.mvp_intersection_ids,
            control_modes=list(self._settings.mvp_control_modes),
        )

    def start(self, request: StartSimulationRequest) -> tuple[str, SimulationSnapshot]:
        self._legacy._validate_request_against_catalog(request)
        control_mode = request.control_mode
        if control_mode not in self._settings.mvp_control_modes:
            raise AppError(
                code="INVALID_CONTROL_MODE",
                message=(
                    f"Unsupported control_mode={control_mode!r}. "
                    f"Allowed: {list(self._settings.mvp_control_modes)}"
                ),
                status_code=422,
            )

        config = self._build_config(request)
        logger.info(
            "启动仿真: intersections=%s period=%s control_mode=%s kernel_mode=%s",
            request.intersection_ids,
            request.period,
            control_mode,
            config.control_mode,
        )
        session_id = self._manager.start(config)
        snapshot = self._manager.snapshot(session_id)
        return session_id, snapshot

    def snapshot(self, session_id: str) -> dict[str, Any]:
        return self.serialize_snapshot(self._manager.snapshot(session_id))

    def serialize_snapshot(self, snapshot: SimulationSnapshot) -> dict[str, Any]:
        payload = self._serializer.serialize(snapshot)
        evaluation = self._algorithm_store.get_eval_for_snapshot(snapshot.session_id)
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
        return self._manager.snapshot(session_id)

    def add_event(self, session_id: str, request: EventRequest) -> str:
        return self._legacy.add_event(session_id, request)

    def cancel_event(self, session_id: str, event_id: str) -> None:
        self._legacy.cancel_event(session_id, event_id)

    def subscribe(self, session_id: str):
        return self._manager.subscribe(session_id)

    def get_metrics(self, session_id: str) -> dict[str, Any]:
        metrics = self._algorithm_store.get_metrics(session_id)
        if metrics is None:
            snap = self.snapshot(session_id)
            raw = snap.get("metrics") or {}
            active = float(raw.get("active_vehicles") or 0)
            total_waiting = float(raw.get("total_waiting_time") or 0)
            return {
                "algorithm": "fixed",
                "avg_waiting_time": round(total_waiting / active, 2) if active > 0 else 0.0,
                "avg_travel_time": 0.0,
                "avg_queue_length": float(raw.get("halting_vehicles") or 0),
                "throughput": float(raw.get("arrived_vehicles") or 0),
                "fuel_consumption": 0.0,
                "avg_decision_latency_ms": 0.0,
                "departed": int(raw.get("departed_vehicles") or 0),
                "arrived": int(raw.get("arrived_vehicles") or 0),
                "finished": snap.get("state") in TERMINAL_STATES,
                "episode_id": session_id,
            }
        return metrics

    def shutdown_active_session(self) -> None:
        self._legacy.shutdown_active_session()

    def _build_config(self, request: StartSimulationRequest) -> SimulationConfig:
        origins = {
            intersection_id: tuple(origin_ids)
            for intersection_id, origin_ids in request.origins.items()
        }
        business_mode = request.control_mode
        kernel_mode = CONTROL_MODE_TO_KERNEL.get(business_mode, "fixed")
        algorithm_endpoint = ""
        if kernel_mode == "algorithm":
            if business_mode not in SUPPORTED_ALGORITHMS:
                raise AppError(
                    code="INVALID_CONTROL_MODE",
                    message=f"Algorithm control_mode not implemented: {business_mode}",
                    status_code=422,
                )
            algorithm_endpoint = urljoin(
                self._settings.algorithm_base_url.rstrip("/") + "/",
                f"{INTERNAL_ALGORITHM_PATH_PREFIX}/{business_mode}",
            )

        return SimulationConfig(
            intersection_ids=tuple(request.intersection_ids),
            period=request.period,
            origins=origins,
            window_start_seconds=request.window_start_seconds,
            duration_seconds=request.duration_seconds,
            flow_multiplier=request.flow_multiplier,
            control_mode=kernel_mode,
            algorithm_endpoint=algorithm_endpoint,
            algorithm_timeout=self._settings.algorithm_timeout,
            decision_interval=self._settings.decision_interval,
            seed=request.seed,
            step_length=request.step_length,
            gui=request.gui,
            realtime=request.realtime,
            snapshot_interval_seconds=request.snapshot_interval_seconds,
            initial_events=tuple(
                self._legacy._to_disturbance_event(item) for item in request.initial_events
            ),
        )
