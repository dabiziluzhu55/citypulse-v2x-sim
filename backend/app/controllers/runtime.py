"""算法运行时仓库：按episode绑定控制器与指标采集器"""

from __future__ import annotations

import logging
import threading
import time
from dataclasses import dataclass, field
from typing import Any, Optional

from ..metrics import EvalResult, MetricsCollector
from .max_pressure import MaxPressureController

logger = logging.getLogger(__name__)

SUPPORTED_ALGORITHMS = ("max_pressure",)


@dataclass
class AlgorithmEpisode:
    algorithm: str
    episode_id: str
    controller: MaxPressureController
    collector: MetricsCollector
    finished: bool = False
    last_result: Optional[EvalResult] = None
    created_at: float = field(default_factory=time.time)


class AlgorithmRuntimeStore:
    """线程安全的算法会话表（SUMO worker 线程会同步 POST /step）"""

    def __init__(self) -> None:
        self._lock = threading.RLock()
        self._episodes: dict[str, AlgorithmEpisode] = {}
        self._last_by_algorithm: dict[str, EvalResult] = {}

    def initialize(self, algorithm: str, body: dict[str, Any]) -> dict[str, Any]:
        if algorithm not in SUPPORTED_ALGORITHMS:
            raise ValueError(f"Unsupported algorithm: {algorithm}")
        episode_id = str(body.get("episode_id", ""))
        if not episode_id:
            raise ValueError("episode_id is required")

        controller = MaxPressureController(body)
        collector = MetricsCollector(algorithm=algorithm)
        collector.on_initialize(body)
        episode = AlgorithmEpisode(
            algorithm=algorithm,
            episode_id=episode_id,
            controller=controller,
            collector=collector,
        )
        with self._lock:
            self._episodes[episode_id] = episode
        logger.info("算法会话初始化: algorithm=%s episode=%s", algorithm, episode_id)
        return {
            "protocol_version": "2.0",
            "episode_id": episode_id,
            "ready": True,
        }

    def step(self, algorithm: str, body: dict[str, Any]) -> dict[str, Any]:
        episode_id = str(body.get("episode_id", ""))
        step_id = body.get("step_id")
        if step_id is None:
            raise ValueError("step_id is required")

        with self._lock:
            episode = self._episodes.get(episode_id)
            if episode is None or episode.algorithm != algorithm:
                raise KeyError(f"Episode not initialized: {episode_id}")
            if episode.finished:
                raise RuntimeError(f"Episode already finished: {episode_id}")

            t0 = time.perf_counter()
            actions = episode.controller.compute_actions(body)
            episode.collector.record_latency((time.perf_counter() - t0) * 1000.0)
            episode.collector.on_step(body)
            episode.last_result = episode.collector.result(finished=False)

        signal_actions = {
            iid: {"target_phase": pid}
            for iid, pid in actions.items()
            if pid is not None
        }
        return {
            "protocol_version": "2.0",
            "episode_id": episode_id,
            "step_id": step_id,
            "actions": {"signals": signal_actions, "vehicles": {}},
        }

    def finish(self, algorithm: str, body: dict[str, Any]) -> dict[str, Any]:
        episode_id = str(body.get("episode_id", ""))
        with self._lock:
            episode = self._episodes.get(episode_id)
            if episode is None or episode.algorithm != algorithm:
                logger.warning("finish 时找不到 episode=%s algorithm=%s", episode_id, algorithm)
                return {"ok": True}
            episode.collector.on_finish(body)
            episode.last_result = episode.collector.result(finished=True)
            episode.finished = True
            self._last_by_algorithm[algorithm] = episode.last_result
            logger.info(
                "算法会话结束: algorithm=%s episode=%s metrics=%s",
                algorithm,
                episode_id,
                episode.last_result.to_dict(),
            )
        return {"ok": True}

    def get_metrics(self, episode_id: str) -> Optional[dict[str, Any]]:
        with self._lock:
            episode = self._episodes.get(episode_id)
            if episode is None:
                return None
            result = episode.last_result
            if result is None:
                result = episode.collector.result(finished=episode.finished)
            payload = result.to_frontend_metrics()
            payload["finished"] = episode.finished
            payload["episode_id"] = episode_id
            return payload

    def get_eval_for_snapshot(self, episode_id: str) -> Optional[dict[str, Any]]:
        metrics = self.get_metrics(episode_id)
        if metrics is None:
            return None
        return {
            "algorithm": metrics.get("algorithm", ""),
            "avg_waiting_time": metrics.get("avg_waiting_time", 0.0),
            "avg_travel_time": metrics.get("avg_travel_time", 0.0),
            "avg_queue_length": metrics.get("avg_queue_length", 0.0),
            "throughput": metrics.get("throughput", 0.0),
            "fuel_consumption": metrics.get("fuel_consumption", 0.0),
            "avg_decision_latency_ms": metrics.get("avg_decision_latency_ms", 0.0),
            "departed": metrics.get("departed", 0),
            "arrived": metrics.get("arrived", 0),
            "finished": metrics.get("finished", False),
        }

    def clear_episode(self, episode_id: str) -> None:
        with self._lock:
            self._episodes.pop(episode_id, None)
