"""算法运行时仓库：仅管理算法控制器与决策延迟，不绑定公共交通指标"""

from __future__ import annotations

import logging
import threading
import time
from collections import OrderedDict
from dataclasses import dataclass, field
from typing import Any, Optional

from .registry import create_controller, is_supported_algorithm

logger = logging.getLogger(__name__)

MAX_COMPLETED_RESULTS = 50


@dataclass
class _ActiveEpisode:
    algorithm: str
    episode_id: str
    controller: Any
    latency_samples: list[float] = field(default_factory=list)
    created_at: float = field(default_factory=time.time)


@dataclass(frozen=True)
class CompletedAlgorithmResult:
    episode_id: str
    algorithm: str
    avg_decision_latency_ms: float
    finished_at: float


class AlgorithmRuntimeStore:
    """线程安全的算法会话表（SUMO worker线程会同步POST/step）"""

    def __init__(self, *, max_completed: int = MAX_COMPLETED_RESULTS) -> None:
        self._lock = threading.RLock()
        self._active: dict[str, _ActiveEpisode] = {}
        self._completed: OrderedDict[str, CompletedAlgorithmResult] = OrderedDict()
        self._max_completed = max_completed

    def initialize_episode(self, algorithm: str, body: dict[str, Any]) -> dict[str, Any]:
        if not is_supported_algorithm(algorithm):
            raise ValueError(f"Unsupported algorithm: {algorithm}")
        episode_id = str(body.get("episode_id", ""))
        if not episode_id:
            raise ValueError("episode_id is required")

        controller = create_controller(algorithm, body)
        episode = _ActiveEpisode(
            algorithm=algorithm,
            episode_id=episode_id,
            controller=controller,
        )
        with self._lock:
            # 同 id 重复 initialize：覆盖旧活动会话
            self._active[episode_id] = episode
        logger.info("算法会话初始化: algorithm=%s episode=%s", algorithm, episode_id)
        return {
            "protocol_version": "2.0",
            "episode_id": episode_id,
            "ready": True,
        }

    def step_episode(self, algorithm: str, body: dict[str, Any]) -> dict[str, Any]:
        episode_id = str(body.get("episode_id", ""))
        step_id = body.get("step_id")
        if step_id is None:
            raise ValueError("step_id is required")

        with self._lock:
            episode = self._active.get(episode_id)
            if episode is None or episode.algorithm != algorithm:
                raise KeyError(f"Episode not initialized: {episode_id}")

            t0 = time.perf_counter()
            actions = episode.controller.compute_actions(body)
            episode.latency_samples.append((time.perf_counter() - t0) * 1000.0)

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

    def finish_episode(self, algorithm: str, body: dict[str, Any]) -> dict[str, Any]:
        """幂等结束：释放控制器，写入有界 completed 结果。"""
        episode_id = str(body.get("episode_id", ""))
        with self._lock:
            episode = self._active.get(episode_id)
            if episode is None:
                # 已完成过则仍返回 ok
                completed = self._completed.get(episode_id)
                if completed is not None:
                    return {"ok": True, "already_finished": True}
                logger.warning(
                    "finish 时找不到活动 episode=%s algorithm=%s",
                    episode_id,
                    algorithm,
                )
                return {"ok": True}
            if episode.algorithm != algorithm:
                logger.warning(
                    "finish algorithm 不匹配: episode=%s expected=%s got=%s",
                    episode_id,
                    episode.algorithm,
                    algorithm,
                )
                return {"ok": True}

            latency = _avg(episode.latency_samples)
            self._active.pop(episode_id, None)
            # 显式释放控制器引用
            episode.controller = None
            self._store_completed(
                CompletedAlgorithmResult(
                    episode_id=episode_id,
                    algorithm=algorithm,
                    avg_decision_latency_ms=latency,
                    finished_at=time.time(),
                )
            )
            logger.info(
                "算法会话结束: algorithm=%s episode=%s latency_ms=%.3f",
                algorithm,
                episode_id,
                latency,
            )
        return {"ok": True}

    def abort_episode(self, episode_id: str) -> None:
        """仿真停止/失败/关闭时本地清理；若仍活动则写入 completed。"""
        with self._lock:
            episode = self._active.pop(episode_id, None)
            if episode is None:
                return
            latency = _avg(episode.latency_samples)
            episode.controller = None
            self._store_completed(
                CompletedAlgorithmResult(
                    episode_id=episode_id,
                    algorithm=episode.algorithm,
                    avg_decision_latency_ms=latency,
                    finished_at=time.time(),
                )
            )
            logger.info("算法会话中止清理: episode=%s", episode_id)

    def get_decision_latency_ms(self, episode_id: str) -> float:
        with self._lock:
            episode = self._active.get(episode_id)
            if episode is not None:
                return _avg(episode.latency_samples)
            completed = self._completed.get(episode_id)
            if completed is not None:
                return completed.avg_decision_latency_ms
            return 0.0

    def get_active_metrics(self, episode_id: str) -> Optional[dict[str, Any]]:
        with self._lock:
            episode = self._active.get(episode_id)
            if episode is None:
                return None
            return {
                "episode_id": episode_id,
                "algorithm": episode.algorithm,
                "avg_decision_latency_ms": _avg(episode.latency_samples),
                "finished": False,
            }

    def get_completed_metrics(self, episode_id: str) -> Optional[dict[str, Any]]:
        with self._lock:
            completed = self._completed.get(episode_id)
            if completed is None:
                return None
            return {
                "episode_id": completed.episode_id,
                "algorithm": completed.algorithm,
                "avg_decision_latency_ms": completed.avg_decision_latency_ms,
                "finished": True,
            }

    def clear_all(self) -> None:
        with self._lock:
            for episode in self._active.values():
                episode.controller = None
            self._active.clear()
            self._completed.clear()

    # 兼容旧内部协议路由命名
    def initialize(self, algorithm: str, body: dict[str, Any]) -> dict[str, Any]:
        return self.initialize_episode(algorithm, body)

    def step(self, algorithm: str, body: dict[str, Any]) -> dict[str, Any]:
        return self.step_episode(algorithm, body)

    def finish(self, algorithm: str, body: dict[str, Any]) -> dict[str, Any]:
        return self.finish_episode(algorithm, body)

    def _store_completed(self, result: CompletedAlgorithmResult) -> None:
        if result.episode_id in self._completed:
            self._completed.move_to_end(result.episode_id)
        self._completed[result.episode_id] = result
        while len(self._completed) > self._max_completed:
            self._completed.popitem(last=False)


def _avg(samples: list[float]) -> float:
    if not samples:
        return 0.0
    return sum(samples) / len(samples)
