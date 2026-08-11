# algorithms/v2x/adapters/coslight_learned.py
"""learned VRC 桥：无规则引擎，SPaT 扩展特征 + 投递集 policy tensors。

与 SHADOW 桥（coslight.py）共享生命周期（on_initialize/on_step/on_finish）
与 hub 投递语义，但不导入 collab 规则引擎。SPaT 携带 Task 0.3 扩展字段
（vrc_local_features=8 / vrc_derived_features=6 / vrc_valid），桥消费这些
字段构造 policy_tensors 的 message_features（14 维消息内容，age/delay 独立）。

模式（冻结语义）：
- ``mode="real"``（默认）：TTL 生效；age = now - delivered_at；delay =
  delivered_at - sim_time（生成时刻），均按 COSLIGHT_VRC_TTL（默认 10.0）归一。
- ``mode="ideal"``：age 恒 0、delay 恒 0、TTL 不失效（配合零延迟满渗透配置）。
- ``mode="nocollab"`` / ``config=None``：不启动 hub；policy_tensors 返回
  全 False mask 与零张量。
"""
from __future__ import annotations

import os
from typing import Any, Mapping, Optional, Sequence

import numpy as np

from ..config import V2XConfig
from ..coverage import is_in_rsu_coverage
from ..derive import (
    derive_estimated_arrival_s,
    derive_lane_change_intent,
    derive_turn_intent,
)
from ..entities import (
    RSU,
    VehicleCapability,
    build_rsu_covered_lanes,
    resolve_v2x_enabled,
)
from ..hub import FrameContext, V2XHub
from ..logger import JSONLSink
from ..messages import V2XMessage
from ..protocol import (
    VRC_DERIVED_FEATURE_DIM,
    VRC_LOCAL_FEATURE_DIM,
    build_bsm_draft,
    build_intent_draft,
    build_rsm_draft,
    build_spat_draft,
)

# 消息内容维度 = 本地(8) + 派生(6)，与 coslight.MSG_FEATURE_DIM=14 对齐；
# age/delay 作为独立张量由 msg_encoder 拼接。
MSG_FEATURE_DIM = VRC_LOCAL_FEATURE_DIM + VRC_DERIVED_FEATURE_DIM

LOCAL_FEATURE_DIM = VRC_LOCAL_FEATURE_DIM
DERIVED_FEATURE_DIM = VRC_DERIVED_FEATURE_DIM

# 本地特征布局（冻结）：[green, stage_elapsed, vehicle_count, halting_count,
# occupancy, queue_length_m, mean_speed, waiting_time]
LOCAL_GREEN_IDX = 0
LOCAL_STAGE_ELAPSED_IDX = 1
LOCAL_VEHICLE_COUNT_IDX = 2
LOCAL_HALTING_COUNT_IDX = 3
LOCAL_OCCUPANCY_IDX = 4
LOCAL_QUEUE_LENGTH_M_IDX = 5
LOCAL_MEAN_SPEED_IDX = 6
LOCAL_WAITING_TIME_IDX = 7

# 车辆派生特征布局（冻结）：[connected_count, lane_count, queue_count,
# occupancy_ratio, mean_speed, mean_waiting_time]；connected_count=0 → invalid
DERIVED_CONNECTED_COUNT_IDX = 0
DERIVED_LANE_COUNT_IDX = 1
DERIVED_QUEUE_COUNT_IDX = 2
DERIVED_OCCUPANCY_IDX = 3
DERIVED_MEAN_SPEED_IDX = 4
DERIVED_WAITING_TIME_IDX = 5

TTL_ENV = "COSLIGHT_VRC_TTL"
TTL_DEFAULT = 10.0
HALTING_SPEED_EPSILON_MPS = 0.1

NON_MOTOR_CLASSES = frozenset({"bicycle", "pedestrian"})


def _as_float(value: Any, default: float = 0.0) -> float:
    try:
        if value is None:
            return default
        return float(value)
    except (TypeError, ValueError):
        return default


def _mean(values: Sequence[float]) -> float:
    return sum(values) / len(values) if values else 0.0


def _read_ttl() -> float:
    raw = os.environ.get(TTL_ENV)
    if raw is None:
        return TTL_DEFAULT
    try:
        return float(raw)
    except (TypeError, ValueError):
        return TTL_DEFAULT


def _extract_local_features(intersection: Mapping[str, Any]) -> list[float]:
    """8 维本地特征：相位 one-hot（GREEN 指示）+ stage_elapsed + 6 项进口车道汇总。

    车道汇总口径：vehicle_count/halting_count/queue_length_m/waiting_time 取和；
    occupancy/mean_speed 取车道均值。
    """
    stage = str(intersection.get("stage") or "")
    green = 1.0 if stage == "GREEN" else 0.0
    elapsed = _as_float(intersection.get("stage_elapsed"))
    lanes = intersection.get("lanes") or {}
    if not isinstance(lanes, Mapping):
        lanes = {}
    items = [lane for lane in lanes.values() if isinstance(lane, Mapping)]
    vehicle_count = sum(_as_float(lane.get("vehicle_count")) for lane in items)
    halting_count = sum(_as_float(lane.get("halting_count")) for lane in items)
    occupancy = _mean([_as_float(lane.get("occupancy")) for lane in items])
    queue_length_m = sum(_as_float(lane.get("queue_length_m")) for lane in items)
    mean_speed = _mean([_as_float(lane.get("mean_speed")) for lane in items])
    waiting_time = sum(_as_float(lane.get("waiting_time")) for lane in items)
    return [green, elapsed, vehicle_count, halting_count, occupancy,
            queue_length_m, mean_speed, waiting_time]


def _vehicle_waiting_time(raw: Mapping[str, Any]) -> float:
    value = raw.get("waiting_time")
    if value is None:
        traffic = raw.get("traffic") or {}
        value = traffic.get("waiting_time_s", traffic.get("time_loss_s"))
    return _as_float(value)


def _derive_connected_features(
    intersection_id: str,
    vehicles: Mapping[str, Mapping[str, Any]],
    capabilities: Mapping[str, VehicleCapability],
) -> tuple[list[float], bool]:
    """6 维派生特征：仅统计 next_signal.intersection_id==j 的已连接车辆。"""
    connected: list[Mapping[str, Any]] = []
    for vid, raw in vehicles.items():
        cap = capabilities.get(str(vid))
        if cap is None or not cap.v2x_enabled:
            continue
        ns = (raw.get("next_signal") or {}).get("intersection_id")
        if ns != intersection_id:
            continue
        connected.append(raw)
    count = len(connected)
    if count == 0:
        return [0.0] * VRC_DERIVED_FEATURE_DIM, False
    lane_ids: set[str] = set()
    queue_count = 0
    speed_sum = 0.0
    waiting_sum = 0.0
    for raw in connected:
        lane = (raw.get("location") or {}).get("lane_id")
        if lane is not None:
            lane_ids.add(str(lane))
        speed = _as_float((raw.get("motion") or {}).get("speed_mps"))
        speed_sum += speed
        if speed <= HALTING_SPEED_EPSILON_MPS:
            queue_count += 1
        waiting_sum += _vehicle_waiting_time(raw)
    return [
        float(count),
        float(len(lane_ids)),
        float(queue_count),
        queue_count / count,
        speed_sum / count,
        waiting_sum / count,
    ], True


def _rsu_covers(rsu: RSU, lane_id: Optional[str],
                position: Optional[tuple[float, float]],
                next_signal_intersection_id: Optional[str],
                detection_radius_m: Optional[float]) -> bool:
    if is_in_rsu_coverage(lane_id, position, rsu, detection_radius_m):
        return True
    if not rsu.covered_lane_ids and rsu.position is None:
        return next_signal_intersection_id == rsu.rsu_id
    return False


def _hub_rsu(hub: V2XHub, rsu_id: str) -> RSU:
    init = getattr(hub, "_coverage_config", None)
    extra = frozenset((init.extra_covered_lane_ids if init else {}).get(rsu_id, ()))
    covered = build_rsu_covered_lanes({}, {rsu_id: extra})
    position = None
    if init is not None and rsu_id in init.positions:
        position = init.positions[rsu_id]
    return RSU(rsu_id=rsu_id, covered_lane_ids=covered[rsu_id], position=position)


class LearnedV2XBridge:
    """learned VRC 桥：投递 SPaT → candidate/message tensors（无 batch 维）。"""

    def __init__(self, log_path: Optional[str] = None,
                 config: Optional[V2XConfig] = None,
                 run_id: str = "coslight_learned",
                 mode: Optional[str] = None) -> None:
        if mode is None:
            mode = "nocollab" if config is None else "real"
        if mode not in ("ideal", "real", "nocollab"):
            raise ValueError(f"unknown VRC mode: {mode!r}")
        self.run_id = run_id
        self.ttl = _read_ttl()
        self._mode = mode
        self._init_payload: Optional[Mapping[str, Any]] = None
        self._vehicle_class_by_type: dict[str, str] = {}
        self._capabilities: dict[str, VehicleCapability] = {}
        self._rsu_ids: set[str] = set()
        self._delivered_spat: dict[str, V2XMessage] = {}
        if mode == "nocollab":
            # 冻结语义：nocollab → config=None → 不启动 hub
            self.config = None
            self._hub = None
        else:
            if config is None:
                raise ValueError(
                    f"VRC mode {mode!r} requires a V2XConfig (nocollab uses config=None)")
            self.config = config
            self._hub = V2XHub(
                config=config,
                sink=JSONLSink(log_path) if log_path else None)
            self._hub.subscribe("SPaT", self._on_spat_delivered)

    # ---------- 生命周期（与 SHADOW 桥一致）----------
    def on_initialize(self, payload: Mapping[str, Any], *, run_id: str,
                      episode_id: str, scenario: Optional[Mapping[str, Any]] = None,
                      initial_sim_time: float = 0.0) -> None:
        self._init_payload = payload
        vehicle_types = payload.get("vehicle_types") or {}
        self._vehicle_class_by_type = {
            str(type_id): str(meta.get("vehicle_class") or meta.get("profile_id")
                             or "unknown")
            for type_id, meta in vehicle_types.items()
        }
        self._capabilities = {}
        self._delivered_spat = {}
        self._rsu_ids = set((payload.get("intersections") or {}).keys())
        if self._hub is None:
            return
        self._hub.ingest_initialize(
            payload, run_id=run_id, episode_id=episode_id,
            scenario=scenario, initial_sim_time=initial_sim_time)

    def pre_step(self, payload: Mapping[str, Any]) -> Optional[FrameContext]:
        """决策前 ingest：advance 投递到期消息并发布本帧草稿，返回最新 step frame。

        返回的 frame 必须原样传给 post_step（ingest_actions 要求最新 step frame）。
        修复调用顺序：controller 先 pre_step 再决策，使 policy_tensors 能看到
        决策时刻已投递的最新 SPaT（age≈0.49TTL），而不是 10s 旧消息（0.99TTL）。
        """
        if self._hub is None:
            return None
        sim_time = float(payload.get("simulation_time", 0.0))
        hub = self._hub
        frame = hub.ingest_step(payload)
        intersections = payload.get("intersections") or {}
        vehicles = payload.get("vehicles") or {}
        init_intersections = (self._init_payload or {}).get("intersections") or {}
        # 网联/非网联注册
        for vid, raw in vehicles.items():
            type_id = raw.get("type_id")
            vclass = self._vehicle_class_by_type.get(str(type_id), "unknown")
            v2x = resolve_v2x_enabled(
                vehicle_id=str(vid), vehicle_class=vclass,
                explicit=None, type_v2x=None, config=self.config)
            self._capabilities[str(vid)] = VehicleCapability(vclass, v2x)
            hub._entity_info[str(vid)] = {
                "type_id": str(type_id), "vehicle_class": vclass, "v2x_enabled": v2x}
            if vclass not in NON_MOTOR_CLASSES:
                hub._motor_ids.add(str(vid))
                if v2x:
                    hub._connected_motor_ids.add(str(vid))
        # BSM/INTENT（网联车）
        for vid, raw in vehicles.items():
            cap = self._capabilities[str(vid)]
            if not cap.v2x_enabled:
                continue
            bsm = build_bsm_draft(str(vid), dict(raw, _sim_time=sim_time))
            if hub.should_send(hub._episode_id or "", str(vid), "BSM", sim_time):
                hub.publish(bsm, frame_id=frame.frame_id)
                hub.mark_sent(hub._episode_id or "", str(vid), "BSM", sim_time)
            turn, tconf = derive_turn_intent(raw, init_intersections)
            lc, lconf = derive_lane_change_intent(raw, init_intersections)
            arrival, aconf = derive_estimated_arrival_s(raw)
            intent = build_intent_draft(
                str(vid), raw, sim_time=sim_time, turn=turn, lane_change=lc,
                arrival=arrival, turn_conf=tconf, lane_change_conf=lconf,
                arrival_conf=aconf, origin="derived")
            if hub.should_send(hub._episode_id or "", str(vid), "INTENT", sim_time):
                hub.publish(intent, frame_id=frame.frame_id)
                hub.mark_sent(hub._episode_id or "", str(vid), "INTENT", sim_time)
        # SPaT（每路口，携带 VRC 扩展特征）
        for inter_id, state in intersections.items():
            local = _extract_local_features(state)
            derived, valid = _derive_connected_features(
                inter_id, vehicles, self._capabilities)
            phases_meta = (init_intersections.get(inter_id) or {}).get("phases") or {}
            spat = build_spat_draft(
                inter_id, state, phases_meta, sim_time=sim_time,
                vrc_local_features=local, vrc_derived_features=derived,
                vrc_valid=valid)
            if hub.should_send(hub._episode_id or "", inter_id, "SPaT", sim_time):
                hub.publish(spat, frame_id=frame.frame_id)
                hub.mark_sent(hub._episode_id or "", inter_id, "SPaT", sim_time)
        # RSM（非网联车，按 RSU 批量）
        rsu_objects: dict[str, list[dict]] = {rid: [] for rid in self._rsu_ids}
        for vid, raw in vehicles.items():
            cap = self._capabilities[str(vid)]
            if cap.v2x_enabled:
                continue
            hub._rsm_eligible.add(str(vid))
            lane_id = (raw.get("location") or {}).get("lane_id")
            position = raw.get("position")
            pos = (position.get("x_m"), position.get("y_m")) if position else None
            ns_id = (raw.get("next_signal") or {}).get("intersection_id")
            for rid in self._rsu_ids:
                rsu = _hub_rsu(hub, rid)
                if _rsu_covers(rsu, lane_id, pos, ns_id,
                               self.config.detection_radius_m):
                    hub._rsm_observed.add(str(vid))
                    rsu_objects[rid].append({
                        "object_id": str(vid), "object_class": cap.vehicle_class,
                        "position": pos,
                        "speed_mps": (raw.get("motion") or {}).get("speed_mps"),
                        "lane_id": lane_id, "confidence": 1.0})
                    break
        for rid, objects in rsu_objects.items():
            if not objects:
                continue
            rsm = build_rsm_draft(rid, objects, sim_time=sim_time)
            if hub.should_send(hub._episode_id or "", rid, "RSM", sim_time):
                hub.publish(rsm, frame_id=frame.frame_id)
                hub.mark_sent(hub._episode_id or "", rid, "RSM", sim_time)
        return frame

    def post_step(self, payload: Mapping[str, Any],
                  actions: Mapping[str, Any],
                  frame: Optional[FrameContext] = None) -> None:
        """决策后下行影子：把 actions 消费到 pre_step 返回的最新 frame。"""
        if self._hub is None:
            return None
        raw_actions = actions
        if isinstance(actions, Mapping) and isinstance(actions.get("actions"), Mapping):
            raw_actions = actions["actions"]
        self._hub.ingest_actions(raw_actions, frame=frame)
        return None

    def on_step(self, payload: Mapping[str, Any],
                actions: Mapping[str, Any]) -> Optional[Mapping[str, Any]]:
        """兼容入口：pre_step + post_step（既有测试与调用保持可用）。"""
        frame = self.pre_step(payload)
        self.post_step(payload, actions, frame)
        return None

    def on_finish(self, final_sim_time: float) -> dict:
        if self._hub is None:
            return {}
        return self._hub.finish_episode(final_sim_time, drain_pending=True)

    def close(self) -> None:
        if self._hub is not None:
            self._hub.close()

    # ---------- 投递订阅 ----------
    def _on_spat_delivered(self, message: V2XMessage) -> None:
        # hub 投递时以 replace(p.message, delivered_at=p.due) 调用订阅者
        self._delivered_spat[message.source_id] = message

    # ---------- policy tensors（无 batch 维：[a, n, ...]）----------
    def policy_tensors(self, simulation_time: float,
                       intersection_ids: Sequence[str]) -> dict[str, Any]:
        agents = len(intersection_ids)
        candidate_mask = np.zeros((agents, agents), dtype=bool)
        message_features = np.zeros(
            (agents, agents, MSG_FEATURE_DIM), dtype=np.float32)
        message_age = np.zeros((agents, agents), dtype=np.float32)
        message_delay = np.zeros((agents, agents), dtype=np.float32)
        if self._mode == "nocollab":
            return {
                "candidate_mask": candidate_mask,
                "message_features": message_features,
                "message_age": message_age,
                "message_delay": message_delay,
            }
        order = list(intersection_ids)
        for j, source_id in enumerate(order):
            message = self._delivered_spat.get(source_id)
            if message is None or message.delivered_at is None:
                continue
            payload = message.payload
            local = payload.get("vrc_local_features")
            derived = payload.get("vrc_derived_features")
            well_formed = (
                isinstance(local, (list, tuple)) and len(local) == VRC_LOCAL_FEATURE_DIM
                and isinstance(derived, (list, tuple))
                and len(derived) == VRC_DERIVED_FEATURE_DIM)
            if well_formed:
                feats = [float(v) for v in (*local, *derived)]
                for i in range(agents):
                    if i != j:
                        message_features[i, j] = feats
            valid = well_formed and bool(payload.get("vrc_valid", False))
            if not valid:
                continue
            delivered_at = message.delivered_at
            if self._mode == "ideal":
                fresh = True
                age_value = 0.0
                delay_value = 0.0
            else:
                ttl = self.ttl
                raw_age = simulation_time - delivered_at
                fresh = ttl > 0.0 and raw_age < ttl
                age_value = float(np.clip(raw_age / ttl, 0.0, 1.0)) if ttl > 0.0 else 0.0
                delay_value = float(np.clip(
                    (delivered_at - message.sim_time) / ttl, 0.0, 1.0)) if ttl > 0.0 else 0.0
            for i in range(agents):
                if i == j:
                    continue
                # age/delay 描述最近投递消息本身（过期仍可见，便于区分
                # age_norm=0.99 vs 1.0）；candidate_mask 才按 fresh 过滤
                message_age[i, j] = age_value
                message_delay[i, j] = delay_value
                if fresh:
                    candidate_mask[i, j] = True
        return {
            "candidate_mask": candidate_mask,
            "message_features": message_features,
            "message_age": message_age,
            "message_delay": message_delay,
        }
