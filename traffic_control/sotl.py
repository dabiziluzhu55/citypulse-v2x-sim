"""SOTL适应信号控制方法"""

from __future__ import annotations

import logging
from typing import Any, Optional

logger = logging.getLogger(__name__)

# 初始参数值（vehicle-seconds/meters/vehicles）
DEFAULT_THRESHOLD = 30.0
DEFAULT_OMEGA = 25.0
DEFAULT_MU = 3
DEFAULT_MINIMUM_GREEN = 5.0
DEFAULT_DECISION_INTERVAL = 5.0

_TRANSITION_STAGES = frozenset({"YELLOW", "CLEARANCE"})


# SOTL算法的对外入口，仿真每步调用compute_actions方法，传入observation，返回actions
class SOTLController:
    """SOTL：非当前相位维护请求积分κ，阈值触发切换，并保护接近停止线的车队"""

    def __init__(self, metadata: dict[str, Any]) -> None:
        self._metadata = metadata
        self._episode_id: str = str(metadata.get("episode_id", ""))
        self._decision_interval = float(
            metadata.get("decision_interval", DEFAULT_DECISION_INTERVAL)
        )
        self._minimum_green = float(
            metadata.get("minimum_green", DEFAULT_MINIMUM_GREEN)
        )
        self._threshold = float(metadata.get("sotl_threshold", DEFAULT_THRESHOLD))
        self._omega = float(metadata.get("sotl_omega", DEFAULT_OMEGA))
        self._mu = int(metadata.get("sotl_mu", DEFAULT_MU))

        self._ix: dict[str, _SOTLIndex] = {}
        for iid, i_meta in metadata.get("intersections", {}).items():
            self._ix[iid] = _build_sotl_index(i_meta)

        self._state: dict[str, _IntersectionRuntime] = {
            iid: _IntersectionRuntime(phase_order=ix.phase_order)
            for iid, ix in self._ix.items()
        }

        logger.info(
            "SOTLController 初始化: episode=%s 路口=%d threshold=%.1f omega=%.1fm mu=%d min_green=%.1fs",
            self._episode_id,
            len(self._ix),
            self._threshold,
            self._omega,
            self._mu,
            self._minimum_green,
        )

    def compute_actions(self, observation: dict[str, Any]) -> dict[str, Optional[int]]:
        actions: dict[str, Optional[int]] = {}
        obs_intersections: dict[str, Any] = observation.get("intersections", {})
        vehicles: dict[str, Any] = observation.get("vehicles", {})

        for iid, ix in self._ix.items():
            i_obs = obs_intersections.get(iid)
            if i_obs is None:
                continue
            runtime = self._state[iid]
            actions[iid] = _sotl_decide(
                ix,
                runtime,
                i_obs,
                vehicles,
                decision_interval=self._decision_interval,
                minimum_green=self._minimum_green,
                threshold=self._threshold,
                omega=self._omega,
                mu=self._mu,
            )
        return actions


class _SOTLIndex:
    __slots__ = (
        "intersection_id",
        "phase_order",
        "phase_incoming_lanes",
        "lane_lengths",
    )

    def __init__(self) -> None:
        self.intersection_id: str = ""
        self.phase_order: list[int] = []
        self.phase_incoming_lanes: dict[int, list[str]] = {}
        self.lane_lengths: dict[str, float] = {}


class _IntersectionRuntime:
    __slots__ = ("phase_order", "kappa", "last_stage")

    def __init__(self, *, phase_order: list[int]) -> None:
        self.phase_order = list(phase_order)
        self.kappa: dict[int, float] = {phase: 0.0 for phase in phase_order}
        self.last_stage: str | None = None


def _build_sotl_index(i_meta: dict[str, Any]) -> _SOTLIndex:
    ix = _SOTLIndex()
    ix.intersection_id = str(i_meta["intersection_id"])
    ix.phase_order = [int(p) for p in i_meta.get("phase_order", [])]

    incoming_lanes = set(i_meta.get("incoming_lanes", []))
    lane_meta = i_meta.get("lanes", {})
    for lane_id, lane_info in lane_meta.items():
        if isinstance(lane_info, dict):
            length = lane_info.get("length_m", lane_info.get("length"))
            if length is not None:
                ix.lane_lengths[str(lane_id)] = float(length)

    connections = {c["connection_id"]: c for c in i_meta.get("connections", [])}
    for phase_key, phase_info in i_meta.get("phases", {}).items():
        pid = int(phase_key)
        priorities: dict[str, str] = phase_info.get("connection_priorities", {})
        lanes: list[str] = []
        for conn_id in priorities:
            conn = connections.get(conn_id)
            if not conn:
                continue
            from_lane = str(conn["from_lane"])
            if incoming_lanes and from_lane not in incoming_lanes:
                continue
            lanes.append(from_lane)
        ix.phase_incoming_lanes[pid] = sorted(set(lanes))
    return ix


def _sotl_decide(
    ix: _SOTLIndex,
    runtime: _IntersectionRuntime,
    i_obs: dict[str, Any],
    vehicles: dict[str, Any],
    *,
    decision_interval: float,
    minimum_green: float,
    threshold: float,
    omega: float,
    mu: int,
) -> Optional[int]:
    stage = str(i_obs.get("stage", "GREEN"))
    pending_phase = i_obs.get("pending_phase")
    current_phase = int(i_obs.get("current_phase", ix.phase_order[0]))
    stage_elapsed = float(i_obs.get("stage_elapsed", 0.0))
    lanes_obs: dict[str, Any] = i_obs.get("lanes", {})

    # 如果当前阶段是过渡阶段或存在待切换相位，则不进行切换
    if stage in _TRANSITION_STAGES or pending_phase is not None:
        runtime.last_stage = stage
        return None
    # 如果当前阶段不是绿色阶段，则不进行切换
    if stage != "GREEN":
        runtime.last_stage = stage
        return None
    # 刚变绿的相位，积分清零
    if runtime.last_stage in _TRANSITION_STAGES or runtime.last_stage is None:
        runtime.kappa[current_phase] = 0.0
    runtime.last_stage = stage

    # 计算积分K
    # 积分K=车辆数*时间间隔
    dt = decision_interval
    for phase_id in ix.phase_order:
        if phase_id == current_phase:
            continue
        vehicle_count = _phase_incoming_vehicle_count(
            phase_id,
            ix.phase_incoming_lanes,
            lanes_obs,
        )
        runtime.kappa[phase_id] += vehicle_count * dt
    # 如果当前阶段绿灯时间小于最小绿灯时间，则不进行切换
    if stage_elapsed + 1e-9 < minimum_green:
        return None
    # 如果所有相位的积分K都小于阈值，则不进行切换
    if not any(runtime.kappa[p] >= threshold for p in ix.phase_order):
        return None

    # 选择目标相位
    max_kappa = max(runtime.kappa[p] for p in ix.phase_order)
    tied = [p for p in ix.phase_order if runtime.kappa[p] == max_kappa]
    target_phase = _pick_cyclic_after_current(
        ix.phase_order,
        current_phase,
        tied,
    )
    # 如果目标相位与当前相位相同，则不进行切换
    if target_phase == current_phase:
        return None
    # 车队保护，少于mu的车辆接近停止线，不进行切换
    if _platoon_blocks_switch(
        ix,
        current_phase,
        lanes_obs,
        vehicles,
        omega=omega,
        mu=mu,
    ):
        return None

    return target_phase


def _phase_incoming_vehicle_count(
    phase_id: int,
    phase_incoming_lanes: dict[int, list[str]],
    lanes_obs: dict[str, Any],
) -> int:
    total = 0
    for lane_id in phase_incoming_lanes.get(phase_id, []):
        lane_obs = lanes_obs.get(lane_id)
        if lane_obs:
            total += int(lane_obs.get("vehicle_count", 0))
    return total


def _pick_cyclic_after_current(
    phase_order: list[int],
    current_phase: int,
    candidates: list[int],
) -> int:
    if not candidates:
        return current_phase
    if len(candidates) == 1:
        return candidates[0]

    start = phase_order.index(current_phase)
    rotated = phase_order[start + 1 :] + phase_order[: start + 1]
    candidate_set = set(candidates)
    for phase_id in rotated:
        if phase_id in candidate_set:
            return phase_id
    return candidates[0]


def _platoon_blocks_switch(
    ix: _SOTLIndex,
    current_phase: int,
    lanes_obs: dict[str, Any],
    vehicles: dict[str, Any],
    *,
    omega: float,
    mu: int,
) -> bool:
    green_lanes = [
        lane_id
        for lane_id in ix.phase_incoming_lanes.get(current_phase, [])
        if lane_id in ix.lane_lengths
    ]
    if not green_lanes or not vehicles:
        return False

    near_stop_line = 0
    for vehicle in vehicles.values():
        if not isinstance(vehicle, dict):
            continue
        lane_id, lane_position = _vehicle_lane_position(vehicle)
        if lane_id not in green_lanes:
            continue
        lane_length = ix.lane_lengths.get(lane_id)
        if lane_length is None:
            continue
        distance_to_stop = lane_length - float(lane_position)
        if 0.0 <= distance_to_stop <= omega:
            near_stop_line += 1

    return 1 <= near_stop_line <= mu


def _vehicle_lane_position(vehicle: dict[str, Any]) -> tuple[str | None, float]:
    location = vehicle.get("location")
    if isinstance(location, dict):
        lane_id = location.get("lane_id")
        position = location.get("lane_position_m", location.get("lane_position"))
        if lane_id is not None and position is not None:
            return str(lane_id), float(position)

    lane_id = vehicle.get("lane_id")
    position = vehicle.get("lane_position_m", vehicle.get("lane_position"))
    if lane_id is not None and position is not None:
        return str(lane_id), float(position)
    return None, 0.0


# ---------------------------------------------------------------------------
# Protocol 2.0 local module API (SUMO worker in-process)
# ---------------------------------------------------------------------------

from traffic_control.protocol import (
    finish_response,
    initialize_response,
    signals_from_phase_map,
    step_response,
)

_controller: SOTLController | None = None


def initialize(payload: dict) -> dict:
    global _controller
    _controller = SOTLController(payload)
    return initialize_response(episode_id=str(payload.get("episode_id", "")))


def step(payload: dict) -> dict:
    if _controller is None:
        raise RuntimeError("SOTL is not initialized.")
    actions = _controller.compute_actions(payload)
    return step_response(
        episode_id=str(payload["episode_id"]),
        step_id=payload["step_id"],
        signals=signals_from_phase_map(actions),
    )


def finish(payload: dict) -> dict:
    global _controller
    already = _controller is None
    _controller = None
    return finish_response(already_finished=already)
