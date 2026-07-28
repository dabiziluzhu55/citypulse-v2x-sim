"""
SOTL (Self-Organizing Traffic Lights) v2 —— 参照 Gershenson 原版 + Nonurt 实现重写。

与 v1 的关键差异：
  1. vehicle_count 替代 halting_count —— 不低估慢行车辆
  2. 动态最小相位保持时间 —— max(3, min(15, total_vehicles // 3))
  3. 直接选排队最多的相位 —— 不等"清空"
  4. 绿灯时长与排队量成正比 —— max(5, min(60, count * 2))

参考：
  Gershenson, C. (2005). "Self-Organizing Traffic Lights."
  Nonurt/SUMO-SOTL (GitHub)

最小绿灯、黄灯、全红过渡由仿真端负责。
"""

from __future__ import annotations

import logging
from typing import Any, Dict, List, Optional

logger = logging.getLogger(__name__)


class SOTLController:
    """SOTL v2 自适应信号控制器。

    每相位至少保持动态最小间隔，到期后切到排队（车辆数）最多的相位。
    """

    def __init__(self, metadata: Dict[str, Any]) -> None:
        self._metadata = metadata
        self._episode_id: str = metadata.get("episode_id", "")

        # ── 预建每个路口的索引 ──
        self._ix: Dict[str, _SOTLIndex] = {}
        for iid, i_meta in metadata.get("intersections", {}).items():
            self._ix[iid] = _build_sotl_index(i_meta)

        # ── 运行时状态：每个路口的上次切换时间 ──
        self._last_switch: Dict[str, float] = {}

        logger.info(
            "SOTLController v2 初始化完成: episode=%s 路口数=%d",
            self._episode_id,
            len(self._ix),
        )

    def compute_actions(self, observation: Dict[str, Any]) -> Dict[str, Optional[int]]:
        """返回 {路口id: 目标相位id}。"""
        actions: Dict[str, Optional[int]] = {}
        obs_intersections: Dict[str, Any] = observation.get("intersections", {})
        sim_time: float = float(observation.get("simulation_time", 0.0))

        for iid, ix in self._ix.items():
            i_obs = obs_intersections.get(iid)
            if i_obs is None:
                continue

            # 初始化 last_switch
            if iid not in self._last_switch:
                self._last_switch[iid] = sim_time

            actions[iid] = _sotl_decide_v2(
                ix,
                i_obs,
                sim_time,
                self._last_switch,
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
        self.phase_lanes: Dict[int, List[str]] = {}


def _build_sotl_index(i_meta: Dict[str, Any]) -> _SOTLIndex:
    """预计算每个相位的进口道列表。"""
    ix = _SOTLIndex()
    ix.intersection_id = i_meta["intersection_id"]
    ix.phase_order = [int(p) for p in i_meta.get("phase_order", [])]

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
        ix.phase_lanes[pid] = list(set(lanes))

    return ix


def _sotl_decide_v2(
    ix: _SOTLIndex,
    i_obs: Dict[str, Any],
    sim_time: float,
    last_switch: Dict[str, float],
) -> Optional[int]:
    """SOTL v2 决策：动态间隔 + 选车最多的相位。"""
    lanes_obs: Dict[str, Any] = i_obs.get("lanes", {})
    current_phase: int = i_obs.get("current_phase", ix.phase_order[0])

    # ── 1. 计算每相位车辆总数 ──
    phase_vehicles: Dict[int, float] = {}
    total_all_lanes: float = 0.0
    for pid in ix.phase_order:
        total = 0.0
        for lane_id in ix.phase_lanes.get(pid, []):
            obs = lanes_obs.get(lane_id)
            if obs:
                total += float(obs.get("vehicle_count", 0))
        phase_vehicles[pid] = total

    for lane_id in lanes_obs:
        total_all_lanes += float(lanes_obs[lane_id].get("vehicle_count", 0))

    # ── 2. 动态最小保持间隔 ──
    # interval = max(3, min(15, total_vehicles // 3))
    min_hold: float = max(3.0, min(15.0, total_all_lanes / 3.0))
    elapsed: float = sim_time - last_switch.get(ix.intersection_id, sim_time)

    if elapsed < min_hold:
        # 还没到最小保持时间，继续当前相位
        return current_phase

    # ── 3. 找车辆最多的相位 ──
    best_phase = max(phase_vehicles, key=lambda p: phase_vehicles[p])
    best_count = phase_vehicles[best_phase]

    # ── 4. 如果最优相位就是当前相位，更新 last_switch 继续保持 ──
    if best_phase == current_phase:
        # 延长当前相位：更新切换时间避免立即被切
        # 但如果车多，给更长的绿灯
        duration = max(5.0, min(60.0, best_count * 2.0))
        if elapsed < duration:
            return current_phase
        # 超时了也留它，除非有别的相位车明显更多
        second_best = sorted(phase_vehicles, key=lambda p: phase_vehicles[p], reverse=True)
        if len(second_best) >= 2:
            if phase_vehicles[second_best[1]] > best_count * 0.7:
                # 第二相位排队接近当前 → 切过去
                best_phase = second_best[1]

    # ── 5. 切换 ──
    if best_phase != current_phase:
        last_switch[ix.intersection_id] = sim_time
        logger.debug(
            "路口 %s: %d→%d (当前车数=%.0f 最优=%.0f 间隔=%.1fs)",
            ix.intersection_id,
            current_phase,
            best_phase,
            phase_vehicles.get(current_phase, 0),
            best_count,
            min_hold,
        )
        return best_phase

    return current_phase
