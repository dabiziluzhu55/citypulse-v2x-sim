"""Shared live-evaluation runtime for in-process algorithm controllers."""

from __future__ import annotations

import threading
from typing import Any, Dict, Optional

from .collector import EvalResult, HttpMetricsCollector


_lock = threading.RLock()
_collector: Optional[HttpMetricsCollector] = None
_episode_id: Optional[str] = None
_observer_active = False
_last_result: Optional[EvalResult] = None
_last_result_episode_id: Optional[str] = None


def _require_active_episode(payload: Dict[str, Any]) -> str:
    episode_id = str(payload.get("episode_id", ""))
    if _collector is None or _episode_id != episode_id:
        raise RuntimeError(
            f"Evaluation callback episode {episode_id!r} does not match "
            f"active episode {_episode_id!r}."
        )
    return episode_id


def start(algorithm: str, metadata: Dict[str, Any]) -> None:
    """Start one episode. Local controllers already support one episode at a time."""

    global _collector, _episode_id, _observer_active, _last_result
    global _last_result_episode_id
    with _lock:
        _collector = HttpMetricsCollector(algorithm=algorithm)
        _collector.on_initialize(metadata)
        _episode_id = str(metadata.get("episode_id", ""))
        _observer_active = False
        _last_result = None
        _last_result_episode_id = None


def enable_high_frequency_observer(metadata: Dict[str, Any]) -> None:
    """Let observer frames replace sparse decision frames for traffic metrics."""

    global _observer_active
    episode_id = str(metadata.get("episode_id", ""))
    with _lock:
        if _collector is None or _episode_id != episode_id:
            start("", metadata)
        _observer_active = True


def observe_decision(payload: Dict[str, Any]) -> None:
    with _lock:
        _require_active_episode(payload)
        if _collector is not None and not _observer_active:
            _collector.on_step(payload)


def observe_frame(payload: Dict[str, Any]) -> None:
    with _lock:
        _require_active_episode(payload)
        _collector.on_step(payload)


def record_latency(milliseconds: float, *, episode_id: str) -> None:
    with _lock:
        _require_active_episode({"episode_id": episode_id})
        _collector.record_latency(milliseconds)


def finish(summary: Dict[str, Any]) -> Optional[EvalResult]:
    global _collector, _episode_id, _observer_active, _last_result
    global _last_result_episode_id
    with _lock:
        if _collector is None:
            summary_episode_id = str(summary.get("episode_id", ""))
            if summary_episode_id != _last_result_episode_id:
                raise RuntimeError(
                    f"Evaluation finish episode {summary_episode_id!r} has no "
                    "active or completed collector."
                )
            return _last_result
        completed_episode_id = _require_active_episode(summary)
        _collector.on_finish(summary)
        _last_result = _collector.result()
        _last_result_episode_id = completed_episode_id
        _collector = None
        _episode_id = None
        _observer_active = False
        return _last_result


def last_result(episode_id: Optional[str] = None) -> Optional[EvalResult]:
    with _lock:
        if episode_id is not None and str(episode_id) != _last_result_episode_id:
            return None
        return _last_result
