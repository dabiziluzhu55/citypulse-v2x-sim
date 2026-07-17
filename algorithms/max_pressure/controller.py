"""
Max Pressure 信号控制 —— 纯计算，不依赖 SUMO / TraCI。

相位压力 = Σ (上游车道.halting_count − 下游车道.halting_count)
对相位的每一个 connection 求和，选压力最大的相位放行。

参考：
  Varaiya, P. (2013). "Max pressure control of a network of signalized intersections."
  Transportation Research Part C, 36, 177-195.
"""

from __future__ import annotations

import logging
from typing import Any, Dict, List, Mapping, Optional

logger = logging.getLogger(__name__)


class MaxPressureController:
    """Max Pressure 算法控制器。

    每个 episode 实例化一次（在 /initialize 时），之后每次 /step 调用
    ``compute_actions()`` 做无状态决策——控制器仅保存 /initialize 的静态元数据。
    """

    # ------------------------------------------------------------------
    # 构造
    # ------------------------------------------------------------------

    def __init__(self, metadata: Dict[str, Any]) -> None:
        """解析 /initialize 载荷并建立索引。

        Parameters
        ----------
        metadata : dict
            ``POST /initialize`` 的完整 JSON。
            必须包含 ``"intersections"``，含每个路口 phases、connections、phase_order 等。
        """
        self._metadata = metadata
        self._episode_id: str = metadata.get("episode_id", "")
        self._decision_interval: float = float(metadata.get("decision_interval", 5.0))

        # ── 预建每个路口的索引，O(1) 查询 ──
        self._ix: Dict[str, _IntersectionIndex] = {}
        for iid, i_meta in metadata.get("intersections", {}).items():
            self._ix[iid] = _build_intersection_index(i_meta)

        logger.info(
            "MaxPressureController 初始化完成: episode=%s 路口数=%d 决策间隔=%.1fs",
            self._episode_id,
            len(self._ix),
            self._decision_interval,
        )

    # ------------------------------------------------------------------
    # 公共接口
    # ------------------------------------------------------------------

    def compute_actions(
        self,
        observation: Dict[str, Any],
        *,
        tie_keep_current: bool = True,
    ) -> Dict[str, Optional[int]]:
        """返回 ``{路口id: 目标相位id}`` 的一次决策结果。

        Parameters
        ----------
        observation : dict
            ``POST /step`` 的完整 JSON。
        tie_keep_current : bool
            两个相位压力相等时，``True`` 保留当前相位，``False`` 取 phase_order 第一个。

        Returns
        -------
        dict
            intersection_id → phase_id 映射。未出现在 observation 中的路口会被跳过。
        """
        actions: Dict[str, Optional[int]] = {}
        obs_intersections: Dict[str, Any] = observation.get("intersections", {})

        for iid, ix in self._ix.items():
            i_obs = obs_intersections.get(iid)
            if i_obs is None:
                logger.warning("路口 %s 未出现在 /step 观测中，跳过", iid)
                continue
            actions[iid] = _best_phase(ix, i_obs, tie_keep_current=tie_keep_current)

        return actions


# ======================================================================
# 内部辅助
# ======================================================================


class _IntersectionIndex:
    """单个路口的预解析静态数据。"""

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
        self.phase_order: List[int] = []
        self.phases: Dict[int, Dict[str, Any]] = {}
        # phase_id → [(from_lane, to_lane), ...]
        self.phase_connections: Dict[int, List[tuple[str, str]]] = {}
        self.connection_map: Dict[str, Dict[str, Any]] = {}
        self.incoming_lanes: List[str] = []
        self.outgoing_lanes: List[str] = []


def _build_intersection_index(i_meta: Dict[str, Any]) -> _IntersectionIndex:
    """从 /initialize 元数据预计算 车道→connection→相位 的查找结构。"""
    ix = _IntersectionIndex()
    ix.intersection_id = i_meta["intersection_id"]
    ix.phase_order = [int(p) for p in i_meta.get("phase_order", [])]
    ix.incoming_lanes = list(i_meta.get("incoming_lanes", []))
    ix.outgoing_lanes = list(i_meta.get("outgoing_lanes", []))

    # 索引 phases（JSON key 是字符串，转为 int）
    raw_phases: Dict[str, Any] = i_meta.get("phases", {})
    for phase_key, phase_info in raw_phases.items():
        pid = int(phase_key)
        ix.phases[pid] = phase_info

    # 索引 connections
    for conn in i_meta.get("connections", []):
        ix.connection_map[conn["connection_id"]] = conn

    # 预计算每个相位的 (上游车道, 下游车道) 列表
    for pid in ix.phase_order:
        phase_info = ix.phases.get(pid)
        if phase_info is None:
            logger.warning("相位 %d 在 phase_order 中但不在 phases 字典里", pid)
            ix.phase_connections[pid] = []
            continue

        priorities: Dict[str, str] = phase_info.get("connection_priorities", {})
        lane_pairs: List[tuple[str, str]] = []
        for conn_id in priorities:
            conn = ix.connection_map.get(conn_id)
            if conn is None:
                logger.warning(
                    "路口 %s 相位 %d 引用了不存在的 connection %s",
                    ix.intersection_id,
                    pid,
                    conn_id,
                )
                continue
            lane_pairs.append((conn["from_lane"], conn["to_lane"]))
        ix.phase_connections[pid] = lane_pairs

        logger.debug(
            "相位 %d (%s): %d 个 connection → %d 个车道对",
            pid,
            phase_info.get("name", ""),
            len(priorities),
            len(lane_pairs),
        )

    return ix


def _best_phase(
    ix: _IntersectionIndex,
    i_obs: Dict[str, Any],
    *,
    tie_keep_current: bool = True,
) -> Optional[int]:
    """为一个路口选出压力最高的相位。"""
    lanes_obs: Dict[str, Any] = i_obs.get("lanes", {})
    current_phase: int = i_obs.get("current_phase", ix.phase_order[0])

    best_phase: Optional[int] = None
    best_pressure: float = -float("inf")

    for pid in ix.phase_order:
        pressure = _compute_phase_pressure(
            ix.phase_connections.get(pid, []), lanes_obs
        )

        if pressure > best_pressure + 1e-9:
            best_pressure = pressure
            best_phase = pid
        elif tie_keep_current and abs(pressure - best_pressure) < 1e-9:
            # 平局时优先当前相位（减少不必要的切换）
            if pid == current_phase:
                best_phase = pid

    # 安全兜底：所有相位压力都是 −∞（如无车道数据）→ 保持当前相位
    if best_phase is None:
        logger.warning(
            "路口 %s 无法计算有效压力，回退到当前相位 %d",
            ix.intersection_id,
            current_phase,
        )
        return current_phase

    return best_phase


def _compute_phase_pressure(
    lane_pairs: List[tuple[str, str]],
    lanes_obs: Dict[str, Any],
) -> float:
    """对一个相位的所有车道对求和 (上游 halting − 下游 halting)。"""
    total = 0.0
    for from_lane, to_lane in lane_pairs:
        from_obs = lanes_obs.get(from_lane)
        to_obs = lanes_obs.get(to_lane)
        if from_obs is None or to_obs is None:
            # 车道不在观测中（如空车道被省略），视为 0
            continue
        upstream = float(from_obs.get("halting_count", 0))
        downstream = float(to_obs.get("halting_count", 0))
        total += upstream - downstream
    return total
