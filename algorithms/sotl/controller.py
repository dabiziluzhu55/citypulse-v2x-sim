"""
SOTL (Self-Organizing Traffic Lights) 信号控制 —— 纯规则驱动。

参考：
  Gershenson, C. (2005). "Self-Organizing Traffic Lights."

策略：
  1. 当前相位 halting_count 总和 < 阈值 → 认为已清空
  2. 有其他相位 halting_count 更大 → 切换到排队最多的相位
  3. 否则保持当前相位

最小绿灯、黄灯、全红过渡由仿真端负责。
"""

from __future__ import annotations

import logging
from typing import Any, Dict, List, Optional

logger = logging.getLogger(__name__)


class SOTLController:
    """SOTL 自适应信号控制器。

    纯启发式规则：清空当前相位后切到排队最多的相位。
    """

    # 默认阈值：当前相位排队车辆数低于此值就考虑切换
    DEFAULT_CLEAR_THRESHOLD = 3
    # 最小切换排队差：另一相位至少多排这么多才切换（避免乒乓）
    DEFAULT_MIN_QUEUE_DIFF = 2

    def __init__(self, metadata: Dict[str, Any]) -> None:
        self._metadata = metadata
        self._episode_id: str = metadata.get("episode_id", "")

        # 参数：可从 metadata 中读取，或用默认值
        self._clear_threshold: float = float(
            metadata.get("sotl_clear_threshold", self.DEFAULT_CLEAR_THRESHOLD)
        )
        self._min_queue_diff: float = float(
            metadata.get("sotl_min_queue_diff", self.DEFAULT_MIN_QUEUE_DIFF)
        )

        # ── 预建每个路口的索引 ──
        self._ix: Dict[str, _SOTLIndex] = {}
        for iid, i_meta in metadata.get("intersections", {}).items():
            self._ix[iid] = _build_sotl_index(i_meta)

        logger.info(
            "SOTLController 初始化完成: episode=%s 路口数=%d "
            "清空阈值=%.0f 最小差=%.0f",
            self._episode_id,
            len(self._ix),
            self._clear_threshold,
            self._min_queue_diff,
        )

    def compute_actions(self, observation: Dict[str, Any]) -> Dict[str, Optional[int]]:
        """返回 {路口id: 目标相位id}。"""
        actions: Dict[str, Optional[int]] = {}
        obs_intersections: Dict[str, Any] = observation.get("intersections", {})

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


# ======================================================================
# 内部辅助
# ======================================================================


class _SOTLIndex:
    """单个路口预解析数据。"""

    __slots__ = ("intersection_id", "phase_order", "phase_lanes")

    def __init__(self) -> None:
        self.intersection_id: str = ""
        self.phase_order: List[int] = []
        # phase_id → 该相位对应的进口道 lane ID 列表
        self.phase_lanes: Dict[int, List[str]] = {}


def _build_sotl_index(i_meta: Dict[str, Any]) -> _SOTLIndex:
    """预计算每个相位的进口道列表。"""
    ix = _SOTLIndex()
    ix.intersection_id = i_meta["intersection_id"]
    ix.phase_order = [int(p) for p in i_meta.get("phase_order", [])]

    # 索引 connections：from_lane 就是该相位的进口道
    connections = {c["connection_id"]: c for c in i_meta.get("connections", [])}
    raw_phases = i_meta.get("phases", {})

    for phase_key, phase_info in raw_phases.items():
        pid = int(phase_key)
        priorities: Dict[str, str] = phase_info.get("connection_priorities", {})
        lanes: List[str] = []
        for conn_id in priorities:
            conn = connections.get(conn_id)
            if conn:
                lanes.append(conn["from_lane"])
        ix.phase_lanes[pid] = list(set(lanes))  # 去重（同一车道可能出现在多个 connection）

        logger.debug(
            "相位 %d: 进口道 %s", pid, ix.phase_lanes[pid]
        )

    return ix


def _sotl_decide(
    ix: _SOTLIndex,
    i_obs: Dict[str, Any],
    clear_threshold: float,
    min_queue_diff: float,
) -> Optional[int]:
    """SOTL 决策：清空当前 → 选排队最多的相位。"""
    lanes_obs: Dict[str, Any] = i_obs.get("lanes", {})
    current_phase: int = i_obs.get("current_phase", ix.phase_order[0])

    # 计算每个相位的排队
    phase_queue: Dict[int, float] = {}
    for pid in ix.phase_order:
        total = 0.0
        for lane_id in ix.phase_lanes.get(pid, []):
            obs = lanes_obs.get(lane_id)
            if obs:
                total += float(obs.get("halting_count", 0))
        phase_queue[pid] = total

    current_queue = phase_queue.get(current_phase, 0.0)

    # 找排队最多的相位
    max_phase = max(phase_queue, key=lambda p: phase_queue[p])  # type: ignore[arg-type]
    max_queue = phase_queue[max_phase]

    # 决策
    if max_phase != current_phase:
        if current_queue < clear_threshold:
            # 当前相位已清空，切换到排队最多的
            logger.debug(
                "路口 %s: 当前相位 %d 已清空(%.0f<%.0f) → 切相位 %d(排队%.0f)",
                ix.intersection_id,
                current_phase,
                current_queue,
                clear_threshold,
                max_phase,
                max_queue,
            )
            return max_phase
        elif max_queue > current_queue + min_queue_diff:
            # 虽然当前没清空，但另一方向排队严重超标，强制切换
            logger.debug(
                "路口 %s: 相位 %d 排队 %.0f, 相位 %d 排队 %.0f → 差距超阈值",
                ix.intersection_id,
                current_phase,
                current_queue,
                max_phase,
                max_queue,
            )
            return max_phase

    # 保持当前相位
    return current_phase
