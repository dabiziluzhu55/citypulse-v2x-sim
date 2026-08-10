"""会话交通指标：每个仿真session一份采集器，终态结果可持久化到元数据仓库"""

from __future__ import annotations

import logging
import threading
from collections import OrderedDict
from pathlib import Path
from typing import Any, Optional

from simulation.sumo.engine.session import SimulationSnapshot

from .collector import TrafficMetricsCollector
from .models import EvalResult
from .powertrain import load_fuel_meta_by_type

logger = logging.getLogger(__name__)

MAX_COMPLETED_RESULTS = 50
TERMINAL_STATES = frozenset({"STOPPED", "COMPLETED", "FAILED"})


class SessionMetricsHub:
    """与control_mode无关的公共交通指标存储"""

    def __init__(
        self,
        *,
        max_completed: int = MAX_COMPLETED_RESULTS,
        session_root: Path | None = None,
        traffic_manifest_path: Path | None = None,
        metadata_store: Any | None = None,
    ) -> None:
        self._lock = threading.RLock()
        self._active: dict[str, TrafficMetricsCollector] = {}
        self._modes: dict[str, str] = {}
        self._completed: OrderedDict[str, EvalResult] = OrderedDict()
        self._max_completed = max_completed
        self._session_root = session_root
        self._traffic_manifest_path = traffic_manifest_path
        self._metadata_store = metadata_store

    def configure_paths(
        self,
        *,
        session_root: Path | None = None,
        traffic_manifest_path: Path | None = None,
    ) -> None:
        with self._lock:
            if session_root is not None:
                self._session_root = session_root
            if traffic_manifest_path is not None:
                self._traffic_manifest_path = traffic_manifest_path

    def set_metadata_store(self, store: Any | None) -> None:
        with self._lock:
            self._metadata_store = store

    def start_session(self, session_id: str, control_mode: str) -> None:
        with self._lock:
            if session_id in self._active:
                return
            collector = TrafficMetricsCollector(algorithm=control_mode)
            collector.reset(algorithm=control_mode)
            session_dir = (
                self._session_root / session_id if self._session_root is not None else None
            )
            fuel_meta, warnings = load_fuel_meta_by_type(
                session_dir=session_dir,
                traffic_manifest_path=self._traffic_manifest_path,
            )
            if fuel_meta:
                collector.set_fuel_meta_by_type(fuel_meta)
            else:
                collector.extend_warnings(warnings)
            self._active[session_id] = collector
            self._modes[session_id] = control_mode

    def observe(self, snapshot: SimulationSnapshot) -> None:
        with self._lock:
            collector = self._active.get(snapshot.session_id)
            if collector is None:
                return
            collector.observe_snapshot(snapshot)

    def finalize(
        self,
        snapshot: SimulationSnapshot,
        *,
        decision_latency_ms: Optional[float] = None,
    ) -> EvalResult:
        with self._lock:
            session_id = snapshot.session_id
            collector = self._active.get(session_id)
            mode = self._modes.get(session_id, "")
            if collector is None:
                existing = self._completed.get(session_id)
                if existing is not None:
                    return existing
                persisted = self._load_persisted(session_id)
                if persisted is not None:
                    return persisted
                collector = TrafficMetricsCollector(algorithm=mode)
                session_dir = (
                    self._session_root / session_id
                    if self._session_root is not None
                    else None
                )
                fuel_meta, warnings = load_fuel_meta_by_type(
                    session_dir=session_dir,
                    traffic_manifest_path=self._traffic_manifest_path,
                )
                if fuel_meta:
                    collector.set_fuel_meta_by_type(fuel_meta)
                else:
                    collector.extend_warnings(warnings)

            tripinfo_path = None
            if self._session_root is not None:
                tripinfo_path = self._session_root / session_id / "tripinfo.xml"

            result = collector.finalize_from_snapshot(
                snapshot,
                decision_latency_ms=decision_latency_ms,
                tripinfo_path=tripinfo_path,
            )
            result.algorithm = mode or result.algorithm
            self._active.pop(session_id, None)
            self._modes.pop(session_id, None)
            self._store_completed(session_id, result)
            return result

    def abort_without_snapshot(
        self,
        session_id: str,
        *,
        decision_latency_ms: Optional[float] = None,
    ) -> None:
        with self._lock:
            collector = self._active.pop(session_id, None)
            mode = self._modes.pop(session_id, "")
            if collector is None:
                return
            result = collector.result(
                finished=True,
                decision_latency_ms=decision_latency_ms,
            )
            result.algorithm = mode or result.algorithm
            self._store_completed(session_id, result)

    def has_final_metrics(self, session_id: str) -> bool:
        """指标是否已终态结算（含TripInfo回填）并离开active采集器"""

        with self._lock:
            if session_id in self._completed:
                return True
            if session_id in self._active:
                return False
            persisted = self._load_persisted_payload(session_id)
            return bool(persisted and persisted.get("finished"))

    def get_metrics_payload(
        self,
        session_id: str,
        *,
        decision_latency_ms: Optional[float] = None,
        finished_hint: bool | None = None,
    ) -> dict[str, Any] | None:
        """返回前端指标载荷

        finished仅在指标已finalize（_completed或持久化）时为True。
        finished_hint保留兼容调用方，但不再把active采集器上的临时指标标成终态。
        """

        _ = finished_hint
        with self._lock:
            if session_id in self._active:
                collector = self._active[session_id]
                mode = self._modes.get(session_id, "")
                result = collector.result(
                    finished=False,
                    decision_latency_ms=decision_latency_ms,
                )
                result.algorithm = mode or result.algorithm
                payload = result.to_frontend_metrics()
                payload["episode_id"] = session_id
                # 采集器仍在active时不得因仿真终态把临时指标标成finished
                # finished仅在finalize/_completed/持久化路径为true
                payload["finished"] = False
                return payload
            if session_id in self._completed:
                result = self._completed[session_id]
                if (
                    decision_latency_ms is not None
                    and result.avg_decision_latency_ms is None
                ):
                    result.avg_decision_latency_ms = float(decision_latency_ms)
                    result.metric_sources[
                        "avg_decision_latency_ms"
                    ] = "algorithm_perf_counter"
                    result.warnings = [
                        w
                        for w in result.warnings
                        if "决策延迟不可用" not in w and "决策耗时样本" not in w
                    ]
                    self._completed[session_id] = result
                    self._persist(session_id, result)
                payload = result.to_frontend_metrics()
                payload["episode_id"] = session_id
                payload["finished"] = True
                return payload

            persisted = self._load_persisted_payload(session_id)
            if persisted is not None:
                # 已持久化的终态指标始终finished=true
                persisted = dict(persisted)
                persisted["finished"] = True
                return persisted
            return None

    def clear_all(self) -> None:
        with self._lock:
            self._active.clear()
            self._modes.clear()
            self._completed.clear()

    def _store_completed(self, session_id: str, result: EvalResult) -> None:
        if session_id in self._completed:
            self._completed.move_to_end(session_id)
        self._completed[session_id] = result
        while len(self._completed) > self._max_completed:
            self._completed.popitem(last=False)
        self._persist(session_id, result)

    def _persist(self, session_id: str, result: EvalResult) -> None:
        store = self._metadata_store
        if store is None:
            return
        try:
            payload = result.to_frontend_metrics()
            payload["episode_id"] = session_id
            payload["finished"] = True
            store.save_metrics(session_id, payload)
        except Exception:
            logger.exception("Persist metrics failed for session %s", session_id)

    def _load_persisted(self, session_id: str) -> EvalResult | None:
        payload = self._load_persisted_payload(session_id)
        if payload is None:
            return None
        fuel = payload.get("fuel_intensity_L_per_100km", payload.get("fuel_consumption"))
        hard_events = payload.get("hard_braking_events")
        return EvalResult(
            algorithm=str(payload.get("algorithm", "")),
            avg_travel_time_s=payload.get("avg_travel_time"),
            avg_waiting_time_s=payload.get("avg_waiting_time"),
            avg_queue_length_veh=payload.get("avg_queue_length"),
            throughput_veh_per_h=payload.get("throughput"),
            avg_decision_latency_ms=payload.get("avg_decision_latency_ms"),
            fuel_intensity_L_per_100km=fuel,
            hard_braking_events=(
                None if hard_events is None else int(hard_events)
            ),
            hard_braking_rate=payload.get("hard_braking_rate"),
            departed=int(payload.get("departed", 0) or 0),
            arrived=int(payload.get("arrived", 0) or 0),
            completion_rate=payload.get("completion_rate"),
            metric_sources=dict(payload.get("metric_sources") or {}),
            warnings=list(payload.get("warnings") or []),
        )

    def _load_persisted_payload(self, session_id: str) -> dict[str, Any] | None:
        store = self._metadata_store
        if store is None:
            return None
        try:
            return store.get_metrics(session_id)
        except Exception:
            logger.exception("Load persisted metrics failed for session %s", session_id)
            return None
