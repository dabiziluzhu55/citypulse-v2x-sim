"""
Max Pressure信号控制算法：根据上下游停车数选择压力最大的相位
相位压力=Σ(上游车道.halting_count−下游车道.halting_count)
"""

from __future__ import annotations

import logging
from typing import Any, Optional

logger = logging.getLogger(__name__)


class MaxPressureController:

    def __init__(self, metadata: dict[str, Any]) -> None:
        self._metadata = metadata
        self._episode_id: str = metadata.get("episode_id", "")
        self._decision_interval: float = float(metadata.get("decision_interval", 5.0))
        self._ix: dict[str, _IntersectionIndex] = {}
        for iid, i_meta in metadata.get("intersections", {}).items():
            self._ix[iid] = _build_intersection_index(i_meta)
        logger.info(
            "MaxPressureController 初始化完成: episode=%s 路口数=%d 决策间隔=%.1fs",
            self._episode_id,
            len(self._ix),
            self._decision_interval,
        )

    def compute_actions(
        self,
        observation: dict[str, Any],
        *,
        tie_keep_current: bool = True,
    ) -> dict[str, Optional[int]]:
        actions: dict[str, Optional[int]] = {}
        obs_intersections: dict[str, Any] = observation.get("intersections", {})
        for iid, ix in self._ix.items():
            i_obs = obs_intersections.get(iid)
            if i_obs is None:
                logger.warning("路口 %s 未出现在 /step 观测中，跳过", iid)
                continue
            actions[iid] = _best_phase(ix, i_obs, tie_keep_current=tie_keep_current)
        return actions


class _IntersectionIndex:
    __slots__ = (
        "intersection_id",
        "phase_order",
        "phases",
        "phase_connections",
        "connection_map",
        "incoming_lanes",
        "outgoing_lanes",
    )

    def __init__(self) -> None:
        self.intersection_id: str = ""
        self.phase_order: list[int] = []
        self.phases: dict[int, dict[str, Any]] = {}
        self.phase_connections: dict[int, list[tuple[str, str]]] = {}
        self.connection_map: dict[str, dict[str, Any]] = {}
        self.incoming_lanes: list[str] = []
        self.outgoing_lanes: list[str] = []


def _build_intersection_index(i_meta: dict[str, Any]) -> _IntersectionIndex:
    ix = _IntersectionIndex()
    ix.intersection_id = i_meta["intersection_id"]
    ix.phase_order = [int(p) for p in i_meta.get("phase_order", [])]
    ix.incoming_lanes = list(i_meta.get("incoming_lanes", []))
    ix.outgoing_lanes = list(i_meta.get("outgoing_lanes", []))

    for phase_key, phase_info in i_meta.get("phases", {}).items():
        ix.phases[int(phase_key)] = phase_info

    for conn in i_meta.get("connections", []):
        ix.connection_map[conn["connection_id"]] = conn

    for pid in ix.phase_order:
        phase_info = ix.phases.get(pid)
        if phase_info is None:
            ix.phase_connections[pid] = []
            continue
        priorities: dict[str, str] = phase_info.get("connection_priorities", {})
        lane_pairs: list[tuple[str, str]] = []
        for conn_id in priorities:
            conn = ix.connection_map.get(conn_id)
            if conn is None:
                continue
            lane_pairs.append((conn["from_lane"], conn["to_lane"]))
        ix.phase_connections[pid] = lane_pairs

    return ix


def _best_phase(
    ix: _IntersectionIndex,
    i_obs: dict[str, Any],
    *,
    tie_keep_current: bool = True,
) -> Optional[int]:
    lanes_obs: dict[str, Any] = i_obs.get("lanes", {})
    current_phase: int = i_obs.get("current_phase", ix.phase_order[0] if ix.phase_order else 0)

    best_phase: Optional[int] = None
    best_pressure: float = -float("inf")

    for pid in ix.phase_order:
        pressure = _compute_phase_pressure(ix.phase_connections.get(pid, []), lanes_obs)
        if pressure > best_pressure + 1e-9:
            best_pressure = pressure
            best_phase = pid
        elif tie_keep_current and abs(pressure - best_pressure) < 1e-9:
            if pid == current_phase:
                best_phase = pid

    if best_phase is None:
        return current_phase
    return best_phase


def _compute_phase_pressure(
    lane_pairs: list[tuple[str, str]],
    lanes_obs: dict[str, Any],
) -> float:
    total = 0.0
    for from_lane, to_lane in lane_pairs:
        from_obs = lanes_obs.get(from_lane)
        to_obs = lanes_obs.get(to_lane)
        if from_obs is None or to_obs is None:
            continue
        upstream = float(from_obs.get("halting_count", 0))
        downstream = float(to_obs.get("halting_count", 0))
        total += upstream - downstream
    return total
