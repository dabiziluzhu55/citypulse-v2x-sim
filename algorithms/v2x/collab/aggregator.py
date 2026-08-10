# algorithms/v2x/collab/aggregator.py
"""EdgeAggregator：订阅 Hub 已投递消息，维护车辆缓存并构建不可变 EdgeSnapshot。

语义（spec §1.4/§1.2）：
- 只接收 managed 路口消息；BSM 按 next_signal 更新车辆所属路口并迁移；
- connected_vehicles 只含 v2x_enabled=True 的网联车（由适配层保证只发网联 BSM）；
- 新鲜度锚点使用 message.sim_time（发送时刻，保守含网络延迟；阈值 10s 量级下可忽略）。
"""
from __future__ import annotations

from typing import Mapping

from algorithms.v2x.messages import V2XMessage

from .snapshot import (
    ApproachState,
    ConnectedVehicleState,
    EdgeSnapshot,
    IntersectionStaticContext,
    LaneState,
    build_static_context,
)

_STOP_SPEED_MPS = 0.5


class _Vehicle:
    __slots__ = (
        "vehicle_id", "lane_id", "approach_id", "speed_mps", "acceleration_mps2",
        "next_signal_intersection_id", "distance_to_signal_m",
        "turn_intent", "turn_confidence", "lane_change_intent",
        "estimated_arrival_s", "bsm_delivered_at", "intent_delivered_at",
        "message_ids",
    )

    def __init__(self, vehicle_id: str) -> None:
        self.vehicle_id = vehicle_id
        self.lane_id: str | None = None
        self.approach_id: str | None = None
        self.speed_mps = 0.0
        self.acceleration_mps2: float | None = None
        self.next_signal_intersection_id: str | None = None
        self.distance_to_signal_m: float | None = None
        self.turn_intent: str | None = None
        self.turn_confidence = 0.0
        self.lane_change_intent: int | None = None
        self.estimated_arrival_s: float | None = None
        self.bsm_delivered_at = 0.0
        self.intent_delivered_at: float | None = None
        self.message_ids: list[str] = []

    def to_state(self) -> ConnectedVehicleState:
        return ConnectedVehicleState(
            vehicle_id=self.vehicle_id,
            lane_id=self.lane_id,
            approach_id=self.approach_id,
            speed_mps=self.speed_mps,
            acceleration_mps2=self.acceleration_mps2,
            next_signal_intersection_id=self.next_signal_intersection_id,
            distance_to_signal_m=self.distance_to_signal_m,
            turn_intent=self.turn_intent,
            turn_confidence=self.turn_confidence,
            lane_change_intent=self.lane_change_intent,
            estimated_arrival_s=self.estimated_arrival_s,
            bsm_delivered_at=self.bsm_delivered_at,
            intent_delivered_at=self.intent_delivered_at,
            source_message_ids=tuple(self.message_ids),
        )


class EdgeAggregator:
    def __init__(self, managed_ids: tuple[str, ...]) -> None:
        self._managed = frozenset(managed_ids)
        self._static: dict[str, IntersectionStaticContext] = {}
        self._spat: dict[str, dict] = {}
        self._rsm_speeds: dict[str, dict[str, list[float]]] = {}
        self._vehicles: dict[str, _Vehicle] = {}
        self._by_intersection: dict[str, dict[str, _Vehicle]] = {
            iid: {} for iid in self._managed}
        self._prev_lane_vehicle_ids: dict[str, dict[str, set[str]]] = {}
        self._last_delivery_at: dict[str, float | None] = {}
        self._last_message_id: dict[str, str] = {}
        self._last_frame_id: dict[str, str] = {}

    # ---------- 订阅入口（Hub 同步回调） ----------
    def on_message(self, message: V2XMessage) -> None:
        message_type = message.message_type
        self._last_delivery_at[message_type] = message.sim_time
        self._last_message_id[message_type] = message.message_id
        self._last_frame_id[message_type] = message.frame_id
        if message_type == "MAP":
            self._on_map(message)
        elif message_type == "SPaT":
            self._on_spat(message)
        elif message_type == "BSM":
            self._on_bsm(message)
        elif message_type == "INTENT":
            self._on_intent(message)
        elif message_type == "RSM":
            self._on_rsm(message)

    def _on_map(self, message: V2XMessage) -> None:
        intersection_id = str(message.payload["intersection_id"])
        if intersection_id not in self._managed:
            return
        try:
            self._static[intersection_id] = build_static_context(message)
        except ValueError:
            # 缺 phase_order 等畸形 MAP：不建立静态上下文 → 策略 MISSING_INPUT
            self._static.pop(intersection_id, None)

    def _on_spat(self, message: V2XMessage) -> None:
        intersection_id = str(message.payload["intersection_id"])
        if intersection_id not in self._managed:
            return
        self._spat[intersection_id] = {
            "payload": dict(message.payload),
            "delivered_at": message.sim_time,
            "message_id": message.message_id,
            "frame_id": message.frame_id,
        }

    def _on_bsm(self, message: V2XMessage) -> None:
        payload = message.payload
        vehicle_id = str(payload["vehicle_id"])
        vehicle = self._vehicles.get(vehicle_id)
        if vehicle is None:
            vehicle = _Vehicle(vehicle_id)
            self._vehicles[vehicle_id] = vehicle
        location = payload.get("location") or {}
        motion = payload.get("motion") or {}
        ns = payload.get("next_signal") or {}
        new_ns = ns.get("intersection_id")
        if new_ns is not None:
            new_ns = str(new_ns)
        old_ns = vehicle.next_signal_intersection_id
        if old_ns != new_ns:
            if old_ns is not None and old_ns in self._by_intersection:
                self._by_intersection[old_ns].pop(vehicle_id, None)
            if new_ns is not None and new_ns in self._by_intersection:
                self._by_intersection[new_ns][vehicle_id] = vehicle
        vehicle.next_signal_intersection_id = new_ns
        vehicle.lane_id = location.get("lane_id")
        vehicle.approach_id = location.get("approach_id")
        vehicle.speed_mps = float(motion.get("speed_mps") or 0.0)
        vehicle.acceleration_mps2 = motion.get("acceleration_mps2")
        vehicle.distance_to_signal_m = ns.get("distance_m")
        vehicle.bsm_delivered_at = message.sim_time
        vehicle.message_ids.append(message.message_id)

    def _on_intent(self, message: V2XMessage) -> None:
        payload = message.payload
        vehicle_id = str(payload["vehicle_id"])
        vehicle = self._vehicles.get(vehicle_id)
        if vehicle is None:
            vehicle = _Vehicle(vehicle_id)
            self._vehicles[vehicle_id] = vehicle
        vehicle.turn_intent = payload.get("turn_intent")
        vehicle.turn_confidence = float(payload.get("turn_confidence") or 0.0)
        vehicle.lane_change_intent = payload.get("lane_change_intent")
        vehicle.estimated_arrival_s = payload.get("estimated_arrival_s")
        vehicle.intent_delivered_at = message.sim_time
        vehicle.message_ids.append(message.message_id)

    def _on_rsm(self, message: V2XMessage) -> None:
        rsu_id = str(message.payload.get("rsu_id") or message.source_id)
        if rsu_id not in self._managed:
            return
        by_lane: dict[str, list[float]] = {}
        for obj in message.payload.get("objects") or []:
            lane_id = obj.get("lane_id")
            if lane_id is None:
                continue
            speed = obj.get("speed_mps")
            if speed is None:
                continue
            by_lane.setdefault(str(lane_id), []).append(float(speed))
        self._rsm_speeds[rsu_id] = by_lane

    # ---------- 快照构建 ----------
    def snapshot(self, intersection_id: str, now: float) -> EdgeSnapshot | None:
        if intersection_id not in self._managed:
            return None
        # MAP 未投递 = 静态上下文缺失 = 不产快照（策略 MISSING_INPUT）
        if intersection_id not in self._static:
            return None
        spat = self._spat.get(intersection_id)
        static = self._static.get(intersection_id)
        lane_states: dict[str, LaneState] = {}
        if static is not None:
            lane_to_approach = static.lane_to_approach
        else:
            lane_to_approach = {}
        prev_lanes = self._prev_lane_vehicle_ids.get(intersection_id, {})
        current_lanes: dict[str, set[str]] = {}
        connected_vehicles: dict[str, ConnectedVehicleState] = {}
        for vehicle_id, vehicle in self._by_intersection.get(intersection_id, {}).items():
            connected_vehicles[vehicle_id] = vehicle.to_state()
            lane_id = vehicle.lane_id
            if lane_id is None:
                continue
            current_lanes.setdefault(lane_id, set()).add(vehicle_id)
        for lane_id, vehicle_ids in current_lanes.items():
            prev = prev_lanes.get(lane_id, set())
            arrivals = len(vehicle_ids - prev)
            stopped = sum(
                1 for vid in vehicle_ids
                if self._vehicles[vid].speed_mps <= _STOP_SPEED_MPS)
            rsm_speeds = self._rsm_speeds.get(intersection_id, {}).get(lane_id, [])
            observed = len(rsm_speeds)
            stopped += sum(1 for s in rsm_speeds if s <= _STOP_SPEED_MPS)
            lane_states[lane_id] = LaneState(
                lane_id=lane_id,
                connected_count=len(vehicle_ids),
                observed_count=observed,
                stopped_count=stopped,
                queue_estimate=float(stopped),
                arrivals_since_last_snapshot=arrivals,
            )
        # RSM-only 车道（无网联 BSM）：仍进入快照，供排队估计/车道建议使用
        for lane_id, speeds in self._rsm_speeds.get(intersection_id, {}).items():
            if lane_id in lane_states:
                continue
            observed = len(speeds)
            stopped = sum(1 for s in speeds if s <= _STOP_SPEED_MPS)
            lane_states[lane_id] = LaneState(
                lane_id=lane_id,
                connected_count=0,
                observed_count=observed,
                stopped_count=stopped,
                queue_estimate=float(stopped),
                arrivals_since_last_snapshot=0,
            )
        approach_ids: dict[str, list[str]] = {}
        for lane_id in lane_states:
            approach_ids.setdefault(
                lane_to_approach.get(lane_id, lane_id), []).append(lane_id)
        approaches: dict[str, ApproachState] = {}
        for approach_id, lane_ids in approach_ids.items():
            ordered = tuple(sorted(lane_ids))
            approaches[approach_id] = ApproachState(
                approach_id=approach_id,
                incoming_lane_ids=ordered,
                lane_states={lid: lane_states[lid] for lid in ordered},
                downstream_vehicle_count=None,
                downstream_queue_estimate=None,
                turn_intent_counts={},
                arrival_etas_s=(),
            )
        phase: int | None = None
        stage: str | None = None
        stage_elapsed: float | None = None
        remaining: float | None = None
        if spat is not None:
            payload = spat["payload"]
            phase = payload.get("current_phase")
            stage = payload.get("stage")
            stage_elapsed = payload.get("stage_elapsed")
            remaining = payload.get("remaining_time_s")
        source_types = sorted(
            t for t in ("BSM", "INTENT", "SPaT", "MAP", "RSM")
            if t in self._last_message_id)
        return EdgeSnapshot(
            intersection_id=intersection_id,
            sim_time=now,
            phase=phase,
            stage=stage,
            stage_elapsed_s=stage_elapsed,
            remaining_time_s=remaining,
            approaches=approaches,
            connected_vehicles=connected_vehicles,
            last_delivery_at={
                t: self._last_delivery_at.get(t) for t in source_types},
            source_message_ids=tuple(
                self._last_message_id[t] for t in source_types),
            source_frame_ids=tuple(
                self._last_frame_id[t] for t in source_types),
        )

    def after_snapshot(self, intersection_id: str) -> None:
        """每帧构建快照后调用：更新跨帧 arrivals 基线（不随快照对象复制）。"""
        if intersection_id not in self._managed:
            return
        current: dict[str, set[str]] = {}
        for vehicle in self._by_intersection.get(intersection_id, {}).values():
            if vehicle.lane_id is not None:
                current.setdefault(vehicle.lane_id, set()).add(vehicle.vehicle_id)
        self._prev_lane_vehicle_ids[intersection_id] = current

    def static_context(self, intersection_id: str) -> IntersectionStaticContext | None:
        return self._static.get(intersection_id)

    def managed_ids(self) -> frozenset[str]:
        return self._managed

    def reset_episode(self) -> None:
        self._static = {}
        self._spat = {}
        self._rsm_speeds = {}
        self._vehicles = {}
        self._by_intersection = {iid: {} for iid in self._managed}
        self._prev_lane_vehicle_ids = {}
        self._last_delivery_at = {}
        self._last_message_id = {}
        self._last_frame_id = {}
