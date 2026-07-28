"""SOTL (Self-Organizing Traffic Lights) 信号控制：纯规则驱动自适应"""

from __future__ import annotations

import logging
from typing import Any, Optional

logger = logging.getLogger(__name__)


class SOTLController:
    DEFAULT_CLEAR_THRESHOLD = 3
    DEFAULT_MIN_QUEUE_DIFF = 2

    def __init__(self, metadata: dict[str, Any]) -> None:
        self._metadata = metadata
        self._episode_id: str = metadata.get("episode_id", "")
        self._clear_threshold = float(
            metadata.get("sotl_clear_threshold", self.DEFAULT_CLEAR_THRESHOLD)
        )
        self._min_queue_diff = float(
            metadata.get("sotl_min_queue_diff", self.DEFAULT_MIN_QUEUE_DIFF)
        )
        self._ix: dict[str, _SOTLIndex] = {}
        for iid, i_meta in metadata.get("intersections", {}).items():
            self._ix[iid] = _build_sotl_index(i_meta)
        logger.info(
            "SOTLController 初始化完成: episode=%s 路口数=%d 清空阈值=%.0f 最小差=%.0f",
            self._episode_id,
            len(self._ix),
            self._clear_threshold,
            self._min_queue_diff,
        )

    def compute_actions(self, observation: dict[str, Any]) -> dict[str, Optional[int]]:
        actions: dict[str, Optional[int]] = {}
        obs_intersections: dict[str, Any] = observation.get("intersections", {})
        for iid, ix in self._ix.items():
            i_obs = obs_intersections.get(iid)
            if i_obs is None:
                continue
            actions[iid] = _sotl_decide(
                ix,
                i_obs,
                self._clear_threshold,
                self._min_queue_diff,
            )
        return actions


class _SOTLIndex:
    __slots__ = ("intersection_id", "phase_order", "phase_lanes")

    def __init__(self) -> None:
        self.intersection_id: str = ""
        self.phase_order: list[int] = []
        self.phase_lanes: dict[int, list[str]] = {}


def _build_sotl_index(i_meta: dict[str, Any]) -> _SOTLIndex:
    ix = _SOTLIndex()
    ix.intersection_id = i_meta["intersection_id"]
    ix.phase_order = [int(p) for p in i_meta.get("phase_order", [])]
    connections = {c["connection_id"]: c for c in i_meta.get("connections", [])}
    for phase_key, phase_info in i_meta.get("phases", {}).items():
        pid = int(phase_key)
        priorities: dict[str, str] = phase_info.get("connection_priorities", {})
        lanes: list[str] = []
        for conn_id in priorities:
            conn = connections.get(conn_id)
            if conn:
                lanes.append(conn["from_lane"])
        ix.phase_lanes[pid] = list(set(lanes))
    return ix


def _sotl_decide(
    ix: _SOTLIndex,
    i_obs: dict[str, Any],
    clear_threshold: float,
    min_queue_diff: float,
) -> Optional[int]:
    lanes_obs: dict[str, Any] = i_obs.get("lanes", {})
    current_phase: int = i_obs.get("current_phase", ix.phase_order[0])

    phase_queue: dict[int, float] = {}
    for pid in ix.phase_order:
        total = 0.0
        for lane_id in ix.phase_lanes.get(pid, []):
            obs = lanes_obs.get(lane_id)
            if obs:
                total += float(obs.get("halting_count", 0))
        phase_queue[pid] = total

    current_queue = phase_queue.get(current_phase, 0.0)
    max_phase = max(phase_queue, key=lambda phase: phase_queue[phase])
    max_queue = phase_queue[max_phase]

    if max_phase != current_phase:
        if current_queue < clear_threshold:
            return max_phase
        if max_queue > current_queue + min_queue_diff:
            return max_phase
    return current_phase
