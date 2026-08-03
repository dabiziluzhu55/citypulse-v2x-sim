"""仿真应用服务：多会话并发、元数据持久化、指标 watcher 与生命周期管理。"""

from __future__ import annotations

import logging
import os
import threading
from datetime import datetime, timezone
from queue import Empty
from typing import Any, Iterable
from urllib.parse import urljoin

from simulation.sumo import (
    AccidentEvent,
    LaneClosureEvent,
    SimulationConfig,
    SpeedLimitEvent,
)
from simulation.sumo.events import DisturbanceEvent
from simulation.sumo.session import SimulationSnapshot, UnknownSessionError

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
from ..scenario.presets import list_scenario_presets, supported_intersection_ids
from ..scenario.resolver import ResolvedStartSimulation, resolve_start_simulation
from ..schemas.simulations import StartSimulationRequest
from .session_metadata import (
    SessionMetadata,
    SessionMetadataStore,
    create_session_metadata_store,
    new_lock_owner,
)
from .snapshot_serializer import SnapshotSerializer

logger = logging.getLogger(__name__)

TERMINAL_STATES = frozenset({"STOPPED", "COMPLETED", "FAILED"})
QUEUED_STATE = "QUEUED"
ACTIVE_COMMAND_STATES = frozenset({"STARTING", "RUNNING", "PAUSED", "STOPPING"})
INTERNAL_ALGORITHM_PATH_PREFIX = "api/v1/internal/algorithm"
METRICS_LOCK_TTL_SECONDS = 30


class SimulationService:
    def __init__(
        self,
        manager: Any,
        serializer: SnapshotSerializer,
        settings: Settings,
        algorithm_store: AlgorithmRuntimeStore,
        metrics_hub: SessionMetricsHub | None = None,
        metadata_store: SessionMetadataStore | None = None,
    ) -> None:
        self._manager = manager
        self._serializer = serializer
        self._settings = settings
        self._algorithm_store = algorithm_store
        self._metadata = metadata_store or create_session_metadata_store(
            mode=settings.normalized_manager_mode(),
            redis_url=settings.citypulse_redis_state_url,
            key_prefix=settings.backend_redis_key_prefix,
            terminal_ttl_seconds=settings.citypulse_session_ttl_seconds,
        )
        self._metrics_hub = metrics_hub or SessionMetricsHub(
            session_root=settings.session_root,
            traffic_manifest_path=settings.generated_dir
            / "manifests"
            / "traffic_manifest.json",
            metadata_store=self._metadata,
        )
        self._metrics_hub.configure_paths(
            session_root=settings.session_root,
            traffic_manifest_path=settings.generated_dir
            / "manifests"
            / "traffic_manifest.json",
        )
        self._metrics_hub.set_metadata_store(self._metadata)
        self._metrics_threads: dict[str, threading.Thread] = {}
        self._watcher_owners: dict[str, str] = {}
        self._watcher_stop: set[str] = set()
        self._lock = threading.RLock()

    # ------------------------------------------------------------------
    # Catalog / list
    # ------------------------------------------------------------------

    def get_catalog_response(self):
        from .map_service import MapService

        catalog = self._manager.catalog()
        return MapService.serialize_catalog(
            catalog,
            supported_intersection_ids(),
            control_modes=list(self._settings.enabled_control_modes()),
        )

    def list_sessions(
        self,
        *,
        state: str | None = None,
        offset: int = 0,
        limit: int = 50,
    ) -> dict[str, Any]:
        items, total = self._metadata.list_sessions(
            state=state, offset=offset, limit=limit
        )
        # 尽量用管理器快照刷新状态/进度（失败不阻塞列表）
        refreshed: list[dict[str, Any]] = []
        for meta in items:
            row = self._session_summary(meta)
            try:
                snap = self._manager.snapshot(meta.session_id)
                self._metadata.update(
                    meta.session_id,
                    state=snap.state,
                    progress=float(snap.progress),
                )
                row["state"] = snap.state
                row["progress"] = float(snap.progress)
                row["updated_at"] = _iso(time_now())
            except Exception:
                pass
            refreshed.append(row)
        return {
            "items": refreshed,
            "total": total,
            "offset": offset,
            "limit": limit,
        }

    def start(self, request: StartSimulationRequest) -> tuple[str, SimulationSnapshot]:
        catalog = self._manager.catalog()
        resolved = resolve_start_simulation(request, catalog)
        enabled = self._settings.enabled_control_modes()
        if resolved.control_mode not in enabled:
            raise AppError(
                code="INVALID_CONTROL_MODE",
                message=(
                    f"Unsupported control_mode={resolved.control_mode!r}. "
                    f"Allowed: {list(enabled)}"
                ),
                status_code=422,
            )

        config = self._build_config(resolved)
        logger.info(
            "启动仿真: mode=%s preset=%s intersections=%s period=%s control_mode=%s",
            self._settings.normalized_manager_mode(),
            resolved.scenario_preset_id,
            resolved.intersection_ids,
            resolved.period,
            resolved.control_mode,
        )
        session_id = self._manager.start(config)
        snapshot = self._manager.snapshot(session_id)
        self._metadata.upsert(
            session_id,
            control_mode=resolved.control_mode,
            scenario_preset_id=resolved.scenario_preset_id,
            state=snapshot.state,
            progress=float(snapshot.progress),
            metrics_status="collecting",
        )
        self._metrics_hub.start_session(session_id, resolved.control_mode)
        self._start_metrics_watcher(session_id)
        return session_id, snapshot

    def snapshot(self, session_id: str) -> dict[str, Any]:
        snap = self._manager.snapshot(session_id)
        self._sync_metadata_from_snapshot(snap)
        return self.serialize_snapshot(snap)

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
        self._sync_metadata_from_snapshot(snapshot)
        if snapshot.state in TERMINAL_STATES:
            self._finalize_session(snapshot)
        return snapshot

    def pause(self, session_id: str) -> SimulationSnapshot:
        self._reject_if_queued(session_id, "pause")
        logger.info("暂停仿真: %s", session_id)
        self._manager.pause(session_id)
        snapshot = self._manager.snapshot(session_id)
        self._sync_metadata_from_snapshot(snapshot)
        return snapshot

    def resume(self, session_id: str) -> SimulationSnapshot:
        self._reject_if_queued(session_id, "resume")
        logger.info("恢复仿真: %s", session_id)
        self._manager.resume(session_id)
        snapshot = self._manager.snapshot(session_id)
        self._sync_metadata_from_snapshot(snapshot)
        return snapshot

    def set_playback_speed(
        self, session_id: str, playback_speed: float
    ) -> SimulationSnapshot:
        self._reject_if_queued(session_id, "playback-speed")
        from ..core.playback import validate_playback_speed

        speed = validate_playback_speed(playback_speed)
        logger.info("设置仿真倍速: session=%s playback_speed=%s", session_id, speed)
        self._manager.set_playback_speed(session_id, speed)
        snapshot = self._manager.snapshot(session_id)
        self._sync_metadata_from_snapshot(snapshot)
        return snapshot

    def add_event(self, session_id: str, request: EventRequest) -> str:
        self._reject_if_queued(session_id, "add_event")
        event = self._to_disturbance_event(request)
        self._validate_event_lanes(session_id, event)
        event_id = self._manager.add_event(session_id, event)
        logger.info("Added event %s to session %s", event_id, session_id)
        return event_id

    def cancel_event(self, session_id: str, event_id: str) -> None:
        self._reject_if_queued(session_id, "cancel_event")
        logger.info("Cancelling event %s on session %s", event_id, session_id)
        self._manager.cancel_event(session_id, event_id)

    def subscribe(self, session_id: str):
        # 未知会话由 manager 抛 UnknownSessionError
        return self._manager.subscribe(session_id)

    def get_metrics(self, session_id: str) -> dict[str, Any]:
        latency = self._algorithm_store.get_decision_latency_ms(session_id)
        finished_hint = None
        try:
            state = self._manager.snapshot(session_id).state
            finished_hint = state in TERMINAL_STATES
        except Exception:
            meta = self._metadata.get(session_id)
            if meta is not None:
                finished_hint = meta.state in TERMINAL_STATES

        payload = self._metrics_hub.get_metrics_payload(
            session_id,
            decision_latency_ms=latency,
            finished_hint=finished_hint,
        )
        if payload is not None:
            return payload

        meta = self._metadata.get(session_id)
        mode = meta.control_mode if meta is not None else "fixed"
        return {
            "episode_id": session_id,
            "algorithm": mode,
            "avg_waiting_time": None,
            "avg_travel_time": None,
            "avg_queue_length": None,
            "throughput": None,
            "fuel_consumption": None,
            "avg_decision_latency_ms": latency,
            "departed": 0,
            "arrived": 0,
            "completion_rate": None,
            "metric_sources": {},
            "warnings": [],
            "finished": bool(finished_hint),
        }

    def recover_sessions(self) -> int:
        """后端重启后，为未完成会话重新建立指标 watcher。"""

        recovered = 0
        for meta in self._metadata.list_non_terminal():
            try:
                snap = self._manager.snapshot(meta.session_id)
            except UnknownSessionError:
                self._metadata.update(
                    meta.session_id,
                    state="FAILED",
                    metrics_status="aborted",
                )
                continue
            except Exception as exc:
                logger.warning(
                    "恢复会话 %s 时读取快照失败: %s", meta.session_id, exc
                )
                continue
            self._metadata.update(
                meta.session_id,
                state=snap.state,
                progress=float(snap.progress),
            )
            if snap.state in TERMINAL_STATES:
                self._metrics_hub.start_session(meta.session_id, meta.control_mode)
                self._finalize_session(snap)
                recovered += 1
                continue
            self._metrics_hub.start_session(meta.session_id, meta.control_mode)
            self._start_metrics_watcher(meta.session_id)
            recovered += 1
            logger.info(
                "已恢复会话 watcher: session=%s state=%s control_mode=%s",
                meta.session_id,
                snap.state,
                meta.control_mode,
            )
        return recovered

    def shutdown(self) -> None:
        """进程关闭钩子。

        local：停止本机活动会话。
        redis：只停止本地 watcher，不停止 SUMO worker 中的会话。
        """

        mode = self._settings.normalized_manager_mode()
        if mode == "local":
            for meta in list(self._metadata.list_non_terminal()):
                try:
                    logger.info(
                        "local 关闭：停止会话 %s (state=%s)",
                        meta.session_id,
                        meta.state,
                    )
                    self._manager.stop(meta.session_id)
                    snapshot = self._manager.snapshot(meta.session_id)
                    self._finalize_session(snapshot)
                except Exception:
                    logger.exception(
                        "Failed to stop local session %s during shutdown.",
                        meta.session_id,
                    )
                    self._algorithm_store.abort_episode(meta.session_id)
                    self._metrics_hub.abort_without_snapshot(
                        meta.session_id,
                        decision_latency_ms=self._algorithm_store.get_decision_latency_ms(
                            meta.session_id
                        ),
                    )
            self._algorithm_store.clear_all()
            self._metrics_hub.clear_all()
        else:
            logger.info(
                "redis 关闭：保留 SUMO worker 会话，仅停止本地指标 watcher"
            )
            self._stop_all_watchers()
        with self._lock:
            self._metrics_threads.clear()
            self._watcher_owners.clear()

    # 兼容旧调用名
    def shutdown_active_session(self) -> None:
        self.shutdown()

    def get_active_session_id(self) -> str | None:
        """兼容旧接口：返回任意一个非终态会话（不保证唯一）。"""

        active = self._metadata.list_non_terminal()
        return active[0].session_id if active else None

    # ------------------------------------------------------------------
    # 指标后台订阅
    # ------------------------------------------------------------------

    def _start_metrics_watcher(self, session_id: str) -> None:
        owner = new_lock_owner()
        if not self._metadata.try_acquire_metrics_lock(
            session_id, owner=owner, ttl_seconds=METRICS_LOCK_TTL_SECONDS
        ):
            logger.info(
                "会话 %s 已有指标 watcher，跳过本进程采集", session_id
            )
            return
        thread = threading.Thread(
            target=self._metrics_watch_loop,
            args=(session_id, owner),
            name=f"metrics-{session_id[:8]}",
            daemon=True,
        )
        with self._lock:
            self._watcher_stop.discard(session_id)
            self._metrics_threads[session_id] = thread
            self._watcher_owners[session_id] = owner
        thread.start()

    def _stop_all_watchers(self) -> None:
        with self._lock:
            session_ids = list(self._metrics_threads)
            owners = dict(self._watcher_owners)
            self._watcher_stop.update(session_ids)
        for session_id in session_ids:
            owner = owners.get(session_id)
            if owner:
                try:
                    self._metadata.release_metrics_lock(session_id, owner=owner)
                except Exception:
                    pass

    def _metrics_watch_loop(self, session_id: str, owner: str) -> None:
        subscription = None
        try:
            self._metadata.update(session_id, metrics_status="collecting")
            subscription = self._manager.subscribe(session_id)
            while True:
                with self._lock:
                    if session_id in self._watcher_stop:
                        break
                if not self._metadata.refresh_metrics_lock(
                    session_id, owner=owner, ttl_seconds=METRICS_LOCK_TTL_SECONDS
                ):
                    logger.warning(
                        "丢失会话 %s 的指标锁，停止本 watcher", session_id
                    )
                    break
                try:
                    snapshot = subscription.get(timeout=2.0)
                except Empty:
                    continue
                self._sync_metadata_from_snapshot(snapshot)
                # QUEUED/STARTING 也可能推送；采集器可安全忽略空车辆帧
                if snapshot.state not in {QUEUED_STATE}:
                    self._metrics_hub.observe(snapshot)
                if snapshot.state in TERMINAL_STATES:
                    self._finalize_session(snapshot)
                    break
        except Exception:
            logger.exception("metrics watcher failed for session %s", session_id)
            self._algorithm_store.abort_episode(session_id)
            self._metrics_hub.abort_without_snapshot(
                session_id,
                decision_latency_ms=self._algorithm_store.get_decision_latency_ms(
                    session_id
                ),
            )
            self._metadata.update(session_id, metrics_status="aborted")
        finally:
            if subscription is not None:
                subscription.close()
            try:
                self._metadata.release_metrics_lock(session_id, owner=owner)
            except Exception:
                pass
            with self._lock:
                self._metrics_threads.pop(session_id, None)
                self._watcher_owners.pop(session_id, None)
                self._watcher_stop.discard(session_id)

    def _finalize_session(self, snapshot: SimulationSnapshot) -> None:
        session_id = snapshot.session_id
        self._algorithm_store.abort_episode(session_id)
        latency = self._algorithm_store.get_decision_latency_ms(session_id)
        try:
            self._metrics_hub.finalize(snapshot, decision_latency_ms=latency)
        except Exception:
            logger.exception(
                "Finalize metrics failed for session %s; continuing without 500",
                session_id,
            )
            self._metrics_hub.abort_without_snapshot(
                session_id, decision_latency_ms=latency
            )
        self._sync_metadata_from_snapshot(snapshot)

    def _sync_metadata_from_snapshot(self, snapshot: SimulationSnapshot) -> None:
        meta = self._metadata.get(snapshot.session_id)
        if meta is None:
            return
        self._metadata.update(
            snapshot.session_id,
            state=snapshot.state,
            progress=float(snapshot.progress),
        )

    def _reject_if_queued(self, session_id: str, action: str) -> None:
        try:
            state = self._manager.snapshot(session_id).state
        except UnknownSessionError:
            raise
        if state == QUEUED_STATE:
            raise AppError(
                code="SESSION_QUEUED",
                message=(
                    f"Session {session_id} is QUEUED; only status query and stop "
                    f"are allowed before startup (rejected: {action})."
                ),
                status_code=409,
            )

    def _session_summary(self, meta: SessionMetadata) -> dict[str, Any]:
        return {
            "session_id": meta.session_id,
            "state": meta.state,
            "control_mode": meta.control_mode,
            "scenario_preset_id": meta.scenario_preset_id,
            "progress": meta.progress,
            "created_at": _iso(meta.created_at),
            "updated_at": _iso(meta.updated_at),
            "metrics_status": meta.metrics_status,
        }

    # ------------------------------------------------------------------
    # 配置构建
    # ------------------------------------------------------------------

    def _build_config(self, request: ResolvedStartSimulation) -> SimulationConfig:
        spec = require_control_mode(request.control_mode)
        algorithm_endpoint = ""
        if spec.needs_algorithm:
            assert spec.algorithm_name is not None
            algorithm_endpoint = urljoin(
                self._settings.algorithm_base_url.rstrip("/") + "/",
                f"{INTERNAL_ALGORITHM_PATH_PREFIX}/{spec.algorithm_name}",
            )

        return SimulationConfig(
            intersection_ids=request.intersection_ids,
            period=request.period,
            origins=request.origins,
            window_start_seconds=request.window_start_seconds,
            duration_seconds=request.duration_seconds,
            flow_multiplier=1.0,
            control_mode=spec.kernel_mode,
            algorithm_endpoint=algorithm_endpoint,
            algorithm_timeout=self._settings.algorithm_timeout,
            decision_interval=self._settings.decision_interval,
            seed=request.seed,
            step_length=request.step_length,
            gui=request.gui,
            realtime=request.realtime,
            snapshot_interval_seconds=request.snapshot_interval_seconds,
            playback_speed=request.playback_speed,
            initial_events=tuple(
                self._to_disturbance_event(item) for item in request.initial_events
            ),
        )

    def _validate_event_lanes(self, session_id: str, event: DisturbanceEvent) -> None:
        catalog = self._manager.catalog()
        meta = self._metadata.get(session_id)
        preset_id = meta.scenario_preset_id if meta is not None else None
        if preset_id is None:
            snapshot = self._manager.snapshot(session_id)
            if snapshot.state in TERMINAL_STATES:
                raise AppError(
                    code="SESSION_NOT_ACTIVE",
                    message=f"Session {session_id} is not active.",
                    status_code=400,
                )
            allowed_intersections = set(catalog.intersections)
        else:
            from ..scenario.presets import require_scenario_preset

            allowed_intersections = set(
                require_scenario_preset(preset_id).intersection_ids
            )

        lane_ids = {
            lane.lane_id
            for intersection_id in allowed_intersections
            if intersection_id in catalog.intersections
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


def time_now() -> float:
    import time

    return time.time()


def _iso(ts: float) -> str:
    return datetime.fromtimestamp(ts, tz=timezone.utc).isoformat()


def recommended_uvicorn_workers() -> int:
    return 1


def detect_uvicorn_worker_count() -> int | None:
    raw = os.environ.get("WEB_CONCURRENCY") or os.environ.get("UVICORN_WORKERS")
    if not raw:
        return None
    try:
        return int(raw)
    except ValueError:
        return None
