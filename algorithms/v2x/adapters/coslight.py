# algorithms/v2x/adapters/coslight.py
"""coslight shadow-mode 接入：env 开关 + 惰性导入，不改决策逻辑。"""
from __future__ import annotations

import os
from typing import Any, Mapping, Optional

from config.scenario_presets import (
    ALL_DEMO_INTERSECTION_IDS, ResolvedScenarioScope,
)
from ..config import V2XConfig
from ..hub import V2XHub
from ..logger import JSONLSink
from ..protocol import (
    build_bsm_draft, build_intent_draft, build_spat_draft,
    build_rsm_draft, build_rsi_draft,
)
from ..entities import VehicleCapability, resolve_v2x_enabled, build_rsu_covered_lanes, RSU
from ..coverage import is_in_rsu_coverage
from ..derive import (
    derive_turn_intent, derive_lane_change_intent, derive_estimated_arrival_s,
)
from ..messages import MessageDraft
from ..collab.aggregator import EdgeAggregator
from ..collab.arbiter import ActionArbiter
from ..collab.engine import CollabDecisionEngine
from ..collab.policy import CloudRulePolicy
from ..collab.proposals import CollabConfig, DecisionMode, GuidanceEmissionMode
from ..collab.records import CompositeSink, InMemoryRecordCollector
from ..collab.state import CloudStateStore

# 非机动车（无法通信，仅由 RSU 的 RSM 感知上报）
NON_MOTOR_CLASSES = frozenset({"bicycle", "pedestrian"})


def _is_motor(vehicle_class: str) -> bool:
    return vehicle_class not in NON_MOTOR_CLASSES


_bridge: Optional["CoslightV2XBridge"] = None
_last_collab_summary: Optional[dict] = None


def _env_collab_enabled() -> bool:
    return os.environ.get("COSLIGHT_V2X_COLLAB", "") == "1"


def _env_collab_config() -> CollabConfig:
    mode = DecisionMode(os.environ.get("COSLIGHT_V2X_COLLAB_MODE", "shadow"))
    guidance = GuidanceEmissionMode(
        os.environ.get("COSLIGHT_V2X_GUIDANCE_MODE", "threshold"))
    return CollabConfig(decision_mode=mode, guidance_mode=guidance)


def _env_scope() -> ResolvedScenarioScope:
    source = os.environ.get("COSLIGHT_V2X_SCOPE_SOURCE", "default")
    preset_id = os.environ.get("COSLIGHT_V2X_SCOPE_PRESET_ID") or None
    raw_ids = os.environ.get("COSLIGHT_V2X_SCOPE_MANAGED_IDS", "")
    managed = tuple(i for i in raw_ids.split(",") if i) if raw_ids         else ALL_DEMO_INTERSECTION_IDS
    return ResolvedScenarioScope(source=source, preset_id=preset_id,
                                 managed_ids=managed)


class CoslightV2XBridge:
    def __init__(self, log_path: Optional[str] = None,
                 config: Optional[V2XConfig] = None,
                 run_id: str = "coslight") -> None:
        self.config = config or V2XConfig()
        self.run_id = run_id
        self._collab_enabled = _env_collab_enabled()
        if self._collab_enabled:
            self._collector = InMemoryRecordCollector()
            jsonl_sink = JSONLSink(log_path) if log_path else None
            self._hub = V2XHub(
                config=self.config,
                sink=CompositeSink(required=[self._collector],
                                   optional=[jsonl_sink]))
        else:
            self._collector = None
            self._hub = V2XHub(
                config=self.config,
                sink=JSONLSink(log_path) if log_path else None)
        self._engine: Optional[CollabDecisionEngine] = None
        self._scope: Optional[ResolvedScenarioScope] = None
        self._collab_summary: Optional[dict] = None
        self._capabilities: dict[str, VehicleCapability] = {}
        self._vehicle_class_by_type: dict[str, str] = {}
        self._init_payload: Optional[Mapping[str, Any]] = None
        self._rsu_ids: set[str] = set()

    def on_initialize(self, payload: Mapping[str, Any], *, run_id: str,
                      episode_id: str, scenario: Optional[Mapping[str, Any]] = None,
                      initial_sim_time: float = 0.0) -> None:
        self._init_payload = payload
        vehicle_types = payload.get("vehicle_types") or {}
        for type_id, meta in vehicle_types.items():
            self._vehicle_class_by_type[str(type_id)] = str(
                meta.get("vehicle_class") or meta.get("profile_id") or "unknown")
        hub = self._hub
        if self._collab_enabled:
            catalog = frozenset((payload.get("intersections") or {}).keys())
            scope = _env_scope()
            unknown = [iid for iid in scope.managed_ids if iid not in catalog]
            if unknown:
                raise ValueError(
                    f"scope managed_ids not in initialize catalog: {sorted(unknown)}")
            collab_config = _env_collab_config()
            self._scope = scope
            aggregator = EdgeAggregator(managed_ids=scope.managed_ids)
            store = CloudStateStore(aggregator, collab_config.freshness)
            policy = CloudRulePolicy(collab_config)
            arbiter = ActionArbiter(
                collab_config.decision_mode)  # ACTIVE → ActiveModeUnavailableError
            self._engine = CollabDecisionEngine(
                hub=self._hub, aggregator=aggregator, store=store,
                policy=policy, arbiter=arbiter,
                collector=self._collector, config=collab_config,
                scope=scope, run_id=run_id, episode_id=episode_id,
                registered_ids=tuple(sorted(catalog)))
        hub.ingest_initialize(
            payload, run_id=run_id, episode_id=episode_id,
            scenario=scenario, initial_sim_time=initial_sim_time)
        self._rsu_ids = set((payload.get("intersections") or {}).keys())

    def on_step(self, payload: Mapping[str, Any],
                actions: Mapping[str, Any]) -> Optional[Mapping[str, Any]]:
        sim_time = float(payload.get("simulation_time", 0.0))
        hub = self._hub
        frame = hub.ingest_step(payload)
        # 兼容控制器返回两种形状：{"actions": {...}} 或扁平 {...}
        raw_actions = actions
        if isinstance(actions, Mapping) and isinstance(actions.get("actions"), Mapping):
            raw_actions = actions["actions"]
        # --- 上行消息 ---
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
            if _is_motor(vclass):
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
        # SPaT（每路口）
        for inter_id, state in intersections.items():
            phases_meta = (init_intersections.get(inter_id) or {}).get("phases") or {}
            spat = build_spat_draft(inter_id, state, phases_meta, sim_time=sim_time)
            if hub.should_send(hub._episode_id or "", inter_id, "SPaT", sim_time):
                hub.publish(spat, frame_id=frame.frame_id)
                hub.mark_sent(hub._episode_id or "", inter_id, "SPaT", sim_time)
        # RSM（非网联车/非机动车，按 RSU 批量；eligible 为全部非网联，observed 为实际覆盖）
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
                rsu = hub_rsu(hub, rid)
                if _rsu_covers(rsu, lane_id, pos, ns_id,
                               self.config.detection_radius_m):
                    hub._rsm_observed.add(str(vid))
                    rsu_objects[rid].append({
                        "object_id": str(vid), "object_class": cap.vehicle_class,
                        "position": pos, "speed_mps": (raw.get("motion") or {}).get("speed_mps"),
                        "lane_id": lane_id, "confidence": 1.0})
                    break
        for rid, objects in rsu_objects.items():
            if not objects:
                continue
            rsm = build_rsm_draft(rid, objects, sim_time=sim_time)
            if hub.should_send(hub._episode_id or "", rid, "RSM", sim_time):
                hub.publish(rsm, frame_id=frame.frame_id)
                hub.mark_sent(hub._episode_id or "", rid, "RSM", sim_time)
        # --- 下行影子记录（collab 拥有 RSI 发射权；signal control 仍走 hub）---
        if self._engine is not None:
            result = self._engine.tick(
                frame=frame, baseline_actions=raw_actions)
            # 不把 actions.vehicles 转 RSI，避免重复发射（§3.5/§8.2）
            actions_for_hub = {
                key: value for key, value in result.protocol_actions.items()
                if key != "vehicles"}
            hub.ingest_actions(actions_for_hub, frame=frame)
            return result.protocol_actions  # shadow == baseline；供未来 active 替换
        hub.ingest_actions(raw_actions, frame=frame)
        return None

    def on_finish(self, final_sim_time: float) -> dict:
        network_summary = self._hub.finish_episode(
            final_sim_time, drain_pending=True)
        if self._engine is not None:
            catalog = tuple(sorted(
                (self._init_payload or {}).get("intersections") or {}))
            collab = self._engine.finalize_episode(
                episode_id=self._hub._episode_id or "",
                registered_ids=catalog)
            merged = dict(network_summary)
            merged["collab"] = collab["collab"]
            merged["scope"] = collab["scope"]
            self._collab_summary = merged
            return merged
        return network_summary

    def close(self) -> None:
        if self._engine is not None:
            self._engine.close()   # 不抢关共享 sink（spec §1.3）
        self._hub.close()


def _rsu_covers(rsu: RSU, lane_id: Optional[str],
                position: Optional[tuple[float, float]],
                next_signal_intersection_id: Optional[str],
                detection_radius_m: Optional[float]) -> bool:
    """RSU 覆盖判定：车道/半径；RSU 无任何覆盖配置时用 next_signal 路口兜底。"""
    if is_in_rsu_coverage(lane_id, position, rsu, detection_radius_m):
        return True
    if not rsu.covered_lane_ids and rsu.position is None:
        return next_signal_intersection_id == rsu.rsu_id
    return False


def hub_rsu(hub: V2XHub, rsu_id: str) -> RSU:
    """构造 RSU 实体（covered lanes 由初始化 MAP 车道推导 + 配置 extra）。"""
    init = getattr(hub, "_coverage_config", None)
    extra = frozenset((init.extra_covered_lane_ids if init else {}).get(rsu_id, ()))
    covered = build_rsu_covered_lanes({}, {rsu_id: extra})
    position = None
    if init is not None and rsu_id in init.positions:
        position = init.positions[rsu_id]
    return RSU(rsu_id=rsu_id, covered_lane_ids=covered[rsu_id], position=position)


def reset_bridge() -> None:
    global _bridge, _last_collab_summary
    if _bridge is not None:
        try:
            _bridge.close()
        except Exception:
            pass
    _bridge = None
    _last_collab_summary = None


def _ensure_bridge() -> Optional[CoslightV2XBridge]:
    global _bridge
    log_path = os.environ.get("COSLIGHT_V2X_LOG")
    if not log_path and not _env_collab_enabled():
        return None
    if _bridge is None:
        _bridge = CoslightV2XBridge(
            log_path=log_path,
            run_id=os.environ.get("COSLIGHT_V2X_RUN_ID", "coslight"))
    return _bridge


def bridge_initialize(payload: Mapping[str, Any]) -> None:
    bridge = _ensure_bridge()
    if bridge is None:
        return
    episode_id = str(payload.get("episode_id") or "episode")
    bridge.on_initialize(payload, run_id=bridge.run_id, episode_id=episode_id,
                         scenario={"source": "coslight"})


def bridge_step(payload: Mapping[str, Any],
                actions: Mapping[str, Any]) -> None:
    bridge = _ensure_bridge()
    if bridge is None:
        return
    bridge.on_step(payload, actions)


def bridge_finish(payload: Mapping[str, Any]) -> None:
    global _last_collab_summary
    bridge = _ensure_bridge()
    if bridge is None:
        return
    if isinstance(payload, Mapping):
        final_sim_time = float(payload.get("simulation_time", 0.0))
    else:
        final_sim_time = float(payload)
    summary = bridge.on_finish(final_sim_time)
    reset_bridge()
    # reset 会清空 _last_collab_summary；这里在 reset 之后保存，保证可查询最近一次 episode
    _last_collab_summary = summary


def last_collab_summary() -> Optional[dict]:
    """最近一次 episode 的合并 summary（network + collab + scope）；无则 None。"""
    return _last_collab_summary
