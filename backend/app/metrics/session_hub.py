"""会话交通指标：每个仿真session一份采集器"""

from __future__ import annotations

import logging
import threading
from collections import OrderedDict
from typing import Any

from simulation.sumo.session import SimulationSnapshot

from .collector import TrafficMetricsCollector
from .models import EvalResult

logger = logging.getLogger(__name__)

MAX_COMPLETED_RESULTS = 50
TERMINAL_STATES = frozenset({"STOPPED", "COMPLETED", "FAILED"})


class SessionMetricsHub:
    """与control_mode无关的公共交通指标存储"""

    def __init__(self, *, max_completed: int = MAX_COMPLETED_RESULTS) -> None:
        self._lock = threading.RLock()
        self._active: dict[str, TrafficMetricsCollector] = {}
        self._modes: dict[str, str] = {}
        self._completed: OrderedDict[str, EvalResult] = OrderedDict()
        self._max_completed = max_completed

    def start_session(self, session_id: str, control_mode: str) -> None:
        with self._lock:
            collector = TrafficMetricsCollector(algorithm=control_mode)
            collector.reset(algorithm=control_mode)
            self._active[session_id] = collector
            self._modes[session_id] = control_mode

    def observe(self, snapshot: SimulationSnapshot) -> None:
        with self._lock:
            collector = self._active.get(snapshot.session_id)
            if collector is None:
                return
            collector.observe_snapshot(snapshot)

    def finalize(self, snapshot: SimulationSnapshot, *, decision_latency_ms: float = 0.0) -> EvalResult:
        with self._lock:
            session_id = snapshot.session_id
            collector = self._active.get(session_id)
            mode = self._modes.get(session_id, "")
            if collector is None:
                # 已结算过则返回 completed
                existing = self._completed.get(session_id)
                if existing is not None:
                    return existing
                collector = TrafficMetricsCollector(algorithm=mode)
            result = collector.finalize_from_snapshot(snapshot)
            result.algorithm = mode or result.algorithm
            result.avg_decision_latency_ms = float(decision_latency_ms)
            self._active.pop(session_id, None)
            self._modes.pop(session_id, None)
            self._store_completed(session_id, result)
            return result

    def abort_without_snapshot(self, session_id: str, *, decision_latency_ms: float = 0.0) -> None:
        with self._lock:
            collector = self._active.pop(session_id, None)
            mode = self._modes.pop(session_id, "")
            if collector is None:
                return
            result = collector.result(finished=True, decision_latency_ms=decision_latency_ms)
            result.algorithm = mode or result.algorithm
            self._store_completed(session_id, result)

    def get_metrics_payload(
        self,
        session_id: str,
        *,
        decision_latency_ms: float = 0.0,
        finished_hint: bool | None = None,
    ) -> dict[str, Any] | None:
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
                payload["finished"] = bool(finished_hint) if finished_hint is not None else False
                return payload
            if session_id in self._completed:
                result = self._completed[session_id]
                # 合并最新延迟
                if decision_latency_ms and result.avg_decision_latency_ms <= 0:
                    result = EvalResult(
                        algorithm=result.algorithm,
                        avg_travel_time_s=result.avg_travel_time_s,
                        avg_waiting_time_s=result.avg_waiting_time_s,
                        avg_queue_length_veh=result.avg_queue_length_veh,
                        throughput_veh_per_h=result.throughput_veh_per_h,
                        avg_decision_latency_ms=decision_latency_ms,
                        fuel_intensity_L_per_100km=result.fuel_intensity_L_per_100km,
                        departed=result.departed,
                        arrived=result.arrived,
                    )
                    self._completed[session_id] = result
                payload = result.to_frontend_metrics()
                payload["episode_id"] = session_id
                payload["finished"] = True
                return payload
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
