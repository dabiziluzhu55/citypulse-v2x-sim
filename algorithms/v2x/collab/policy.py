# algorithms/v2x/collab/policy.py
"""CloudRulePolicy：信号规则族 C（排队/到达基线 + INTENT 前视）+ 引导阈值触发。

本文件在 Task 7 实现 propose_signal；Task 8 追加 propose_guidance 与状态字典。
"""
from __future__ import annotations

import math
from dataclasses import dataclass
from typing import Mapping

from algorithms.config.scenario_presets import ResolvedScenarioScope

from .proposals import (
    CollabConfig,
    FreshnessConfig,
    GuidanceDecisionStatus,
    GuidanceEmissionMode,
    GuidancePolicyConfig,
    LastEmittedGuidanceState,
    SignalDecisionStatus,
    SignalPolicyConfig,
    SignalProposal,
    VehicleGuidanceProposal,
)
from .snapshot import (
    ConnectedVehicleState,
    EdgeSnapshot,
    IntersectionStaticContext,
)

_TRANSITION_STAGES = frozenset({"YELLOW", "CLEARANCE"})


def _clamp(value: float, low: float, high: float) -> float:
    return max(low, min(high, value))


# ---- RSI 车辆引导（spec §3）----

GUIDANCE_FUNNEL_STAGES = (
    "connected_seen", "fresh_bsm", "next_signal_known", "next_signal_managed",
    "distance_known", "in_horizon_candidates", "raw_proposals",
    "threshold_passed", "dedup_passed", "cooldown_passed", "published",
)


@dataclass(frozen=True, slots=True)
class GuidanceOutcome:
    """单辆车引导决策结果：proposal 为 None 表示候选筛选未通过。"""
    proposal: VehicleGuidanceProposal | None
    funnel_stage: str          # 最高到达的漏斗阶段（§5.1）
    filter_reason: str | None  # 未 published 时的统计原因（§3.6）
    would_pass_threshold: bool = True   # FULL 模式诊断：若按 THRESHOLD 是否可达发射
    would_be_duplicate: bool | None = None
    would_be_in_cooldown: bool | None = None




class CloudRulePolicy:
    def __init__(self, config: CollabConfig) -> None:
        self._config = config
        self._last_emitted: dict[str, LastEmittedGuidanceState] = {}

    # ---------- 信号规则（spec §2） ----------
    def propose_signal(
        self,
        *,
        intersection_id: str,
        snapshot: EdgeSnapshot | None,
        static_context: IntersectionStaticContext | None,
        now: float,
        frame_id: str,
        config: CollabConfig,
    ) -> SignalProposal:
        policy = config.signal_policy
        freshness = config.freshness
        if snapshot is None or static_context is None:
            return self._signal_result(
                intersection_id, SignalDecisionStatus.MISSING_INPUT, None, None,
                None, {}, "missing_map_or_snapshot", 0.0, now, policy, frame_id,
                (), ())
        spat_age = snapshot.last_delivery_at.get("SPaT")
        if snapshot.phase is None:
            return self._signal_result(
                intersection_id, SignalDecisionStatus.MISSING_INPUT, None, None,
                None, {}, "missing_spat", 0.0, now, policy, frame_id,
                snapshot.source_message_ids, snapshot.source_frame_ids)
        if spat_age is None or now - spat_age > freshness.spat_s:
            return self._signal_result(
                intersection_id, SignalDecisionStatus.STALE_INPUT, None, None,
                None, {}, "spat_stale", 0.0, now, policy, frame_id,
                snapshot.source_message_ids, snapshot.source_frame_ids)
        if snapshot.stage in _TRANSITION_STAGES:
            return self._signal_result(
                intersection_id, SignalDecisionStatus.SUPPRESSED_TRANSITION,
                None, None, None, {}, "stage_transition", 0.0, now, policy,
                frame_id, snapshot.source_message_ids, snapshot.source_frame_ids)
        current_action = static_context.phase_to_action.get(snapshot.phase)
        if current_action is None:
            return self._signal_result(
                intersection_id, SignalDecisionStatus.INVALID_PROPOSAL,
                None, None, None, {}, "phase_to_action_missing", 0.0, now,
                policy, frame_id, snapshot.source_message_ids,
                snapshot.source_frame_ids)
        scores = self._score_actions(
            snapshot, static_context, now, policy, freshness)
        if any(not math.isfinite(value) for value in scores.values()):
            return self._signal_result(
                intersection_id, SignalDecisionStatus.INVALID_PROPOSAL,
                None, None, None, scores, "non_finite_score", 0.0, now,
                policy, frame_id, snapshot.source_message_ids,
                snapshot.source_frame_ids)
        best = max(
            static_context.valid_actions,
            key=lambda action: (
                scores[action],
                1 if action == current_action else 0,
                -action,
            ),
        )
        if scores[best] <= policy.demand_epsilon:
            return self._signal_result(
                intersection_id, SignalDecisionStatus.NO_DEMAND,
                current_action, current_action, current_action, scores,
                "no_demand", 0.0, now, policy, frame_id,
                snapshot.source_message_ids, snapshot.source_frame_ids)
        if best == current_action:
            return self._signal_result(
                intersection_id, SignalDecisionStatus.KEEP_CURRENT,
                current_action, current_action, current_action, scores,
                "keep_current", 0.0, now, policy, frame_id,
                snapshot.source_message_ids, snapshot.source_frame_ids)
        if snapshot.stage_elapsed_s is None:
            return self._signal_result(
                intersection_id, SignalDecisionStatus.SUPPRESSED_MIN_GREEN,
                best, current_action, current_action, scores,
                "stage_elapsed_unknown", 0.0, now, policy, frame_id,
                snapshot.source_message_ids, snapshot.source_frame_ids)
        if snapshot.stage_elapsed_s < policy.min_green_s:
            return self._signal_result(
                intersection_id, SignalDecisionStatus.SUPPRESSED_MIN_GREEN,
                best, current_action, current_action, scores, "min_green",
                0.0, now, policy, frame_id, snapshot.source_message_ids,
                snapshot.source_frame_ids)
        margin = scores[best] - scores[current_action]
        if margin < policy.switch_score_margin:
            return self._signal_result(
                intersection_id, SignalDecisionStatus.SUPPRESSED_SWITCH_MARGIN,
                best, current_action, current_action, scores,
                "switch_margin", 0.0, now, policy, frame_id,
                snapshot.source_message_ids, snapshot.source_frame_ids)
        confidence = self._signal_confidence(
            snapshot, static_context, now, policy, freshness,
            best, scores, margin)
        return self._signal_result(
            intersection_id, SignalDecisionStatus.PROPOSED,
            best, best, current_action, scores, "switch", confidence, now,
            policy, frame_id, snapshot.source_message_ids,
            snapshot.source_frame_ids)

    def _score_actions(
        self,
        snapshot: EdgeSnapshot,
        static_context: IntersectionStaticContext,
        now: float,
        policy: SignalPolicyConfig,
        freshness: FreshnessConfig,
    ) -> dict[int, float]:
        lane_states = {
            lane_id: lane
            for approach in snapshot.approaches.values()
            for lane_id, lane in approach.lane_states.items()
        }
        scores: dict[int, float] = {}
        for action in static_context.valid_actions:
            served = set(static_context.action_to_movements.get(action, ()))
            served_lanes = static_context.action_to_lanes.get(action, ())
            if served_lanes:
                relevant_lanes = list(served_lanes)
            else:
                # 无 connection_priorities 时回退 movement 匹配（旧 MAP 兼容）
                relevant_lanes = [
                    lane_id for lane_id, movements in
                    static_context.lane_to_movements.items()
                    if served & set(movements)
                ]
            queued = sum(
                lane_states[lane_id].queue_estimate
                for lane_id in relevant_lanes if lane_id in lane_states)
            arrivals = sum(
                lane_states[lane_id].arrivals_since_last_snapshot
                for lane_id in relevant_lanes if lane_id in lane_states)
            forward = self._forward_demand(
                snapshot, served, served_lanes, now, policy, freshness)
            scores[action] = (
                policy.queue_weight * queued
                + policy.arrival_weight * arrivals
                + policy.forward_weight * forward
            )
        return scores

    def _forward_demand(
        self,
        snapshot: EdgeSnapshot,
        served_movements: set[str],
        served_lanes: tuple[str, ...],
        now: float,
        policy: SignalPolicyConfig,
        freshness: FreshnessConfig,
    ) -> float:
        total = 0.0
        served_lane_set = frozenset(served_lanes)
        for vehicle in snapshot.connected_vehicles.values():
            if now - vehicle.bsm_delivered_at > freshness.bsm_s:
                continue
            if vehicle.intent_delivered_at is None:
                continue
            if now - vehicle.intent_delivered_at > freshness.intent_s:
                continue
            if vehicle.turn_intent not in served_movements:
                continue
            if served_lane_set and vehicle.lane_id not in served_lane_set:
                continue
            eta = vehicle.estimated_arrival_s
            if eta is None or not (0.0 <= eta <= policy.forward_horizon_s):
                continue
            total += vehicle.turn_confidence * math.exp(
                -eta / policy.forward_decay_s)
        return total

    def _signal_confidence(
        self,
        snapshot: EdgeSnapshot,
        static_context: IntersectionStaticContext,
        now: float,
        policy: SignalPolicyConfig,
        freshness: FreshnessConfig,
        best: int,
        scores: Mapping[int, float],
        margin: float,
    ) -> float:
        lane_states = {
            lane_id: lane
            for approach in snapshot.approaches.values()
            for lane_id, lane in approach.lane_states.items()
        }
        served = set(static_context.action_to_movements.get(best, ()))
        served_lanes = static_context.action_to_lanes.get(best, ())
        if served_lanes:
            relevant_lanes = list(served_lanes)
        else:
            relevant_lanes = [
                lane_id for lane_id, movements in
                static_context.lane_to_movements.items()
                if served & set(movements)
            ]
        queue_quality = 1.0 if any(
            lane_id in lane_states for lane_id in relevant_lanes) else 0.0
        arrival_quality = queue_quality
        fresh_connected = sum(
            1 for v in snapshot.connected_vehicles.values()
            if now - v.bsm_delivered_at <= freshness.bsm_s)
        fresh_intent = sum(
            1 for v in snapshot.connected_vehicles.values()
            if now - v.bsm_delivered_at <= freshness.bsm_s
            and v.intent_delivered_at is not None
            and now - v.intent_delivered_at <= freshness.intent_s)
        forward_quality = (
            fresh_intent / fresh_connected if fresh_connected > 0 else 0.0)
        weight_sum = (
            policy.queue_weight + policy.arrival_weight + policy.forward_weight)
        input_quality = (
            policy.queue_weight * queue_quality
            + policy.arrival_weight * arrival_quality
            + policy.forward_weight * forward_quality
        ) / weight_sum
        if len(static_context.valid_actions) == 1:
            margin_confidence = 1.0
        else:
            margin_confidence = _clamp(
                margin / max(abs(scores[best]), policy.score_epsilon), 0.0, 1.0)
        return margin_confidence * input_quality

    def _signal_result(
        self,
        intersection_id: str,
        status: SignalDecisionStatus,
        candidate_action: int | None,
        proposed_action: int | None,
        current_action: int | None,
        scores: Mapping[int, float],
        reason: str,
        confidence: float,
        now: float,
        policy: SignalPolicyConfig,
        frame_id: str,
        source_message_ids: tuple[str, ...],
        source_frame_ids: tuple[str, ...],
    ) -> SignalProposal:
        return SignalProposal(
            intersection_id=intersection_id,
            status=status,
            candidate_action=candidate_action,
            proposed_action=proposed_action,
            current_action=current_action,
            action_scores=dict(scores),
            reason=reason,
            confidence=confidence,
            valid_from=now,
            valid_until=now + policy.proposal_ttl_s,
            needs_transition=(
                proposed_action is not None
                and current_action is not None
                and proposed_action != current_action
            ),
            decision_frame_id=frame_id,
            source_message_ids=source_message_ids,
            source_frame_ids=source_frame_ids,
        )


    # ---------- 引导：候选筛选 ----------
    def _candidate_stage(
        self, vehicle: ConnectedVehicleState, now: float,
        freshness: FreshnessConfig, scope: ResolvedScenarioScope,
        horizon_m: float,
    ) -> tuple[str, str | None]:
        """返回 (最高到达漏斗阶段, 过滤原因)。"""
        if now - vehicle.bsm_delivered_at > freshness.bsm_s:
            return "connected_seen", "stale_bsm"
        if vehicle.next_signal_intersection_id is None:
            return "fresh_bsm", "next_signal_unknown"
        if vehicle.next_signal_intersection_id not in scope.managed_ids:
            return "next_signal_known", "next_signal_not_managed"
        if vehicle.distance_to_signal_m is None:
            return "next_signal_managed", "distance_unknown"
        if vehicle.distance_to_signal_m > horizon_m:
            return "distance_known", "not_in_guidance_horizon"
        return "in_horizon_candidates", None

    def _resolve_movement(
        self, vehicle: ConnectedVehicleState, snapshot: EdgeSnapshot,
        now: float, freshness: FreshnessConfig,
        static_context: IntersectionStaticContext,
    ) -> str | None:
        if (vehicle.intent_delivered_at is not None
                and now - vehicle.intent_delivered_at <= freshness.intent_s
                and vehicle.turn_intent):
            return vehicle.turn_intent
        lane_movements = static_context.lane_to_movements.get(
            vehicle.lane_id or "", ())
        if len(lane_movements) == 1:
            return lane_movements[0]
        return None

    # ---------- 引导：速度分量（§3.2，两阶段：raw 生成） ----------
    def _speed_decision(
        self, vehicle: ConnectedVehicleState, snapshot: EdgeSnapshot,
        static_context: IntersectionStaticContext, now: float,
        policy: GuidancePolicyConfig, freshness: FreshnessConfig,
    ) -> tuple[GuidanceDecisionStatus, float | None, str]:
        movement = self._resolve_movement(
            vehicle, snapshot, now, freshness, static_context)
        if movement is None:
            return GuidanceDecisionStatus.MISSING_INPUT, None, "movement_unknown"
        spat_delivered_at = snapshot.last_delivery_at.get("SPaT")
        if snapshot.phase is None or spat_delivered_at is None:
            return GuidanceDecisionStatus.MISSING_INPUT, None, "missing_spat"
        if now - spat_delivered_at > freshness.spat_s:
            return GuidanceDecisionStatus.STALE_INPUT, None, "spat_stale"
        stage = snapshot.stage or ""
        if stage != "GREEN":
            # v1 不预测下一服务相位：非绿灯阶段不生成减速赶绿建议
            return GuidanceDecisionStatus.NO_ACTION_NEEDED, None, "stage_not_green"
        action = static_context.phase_to_action.get(snapshot.phase)
        served = set(static_context.action_to_movements.get(action, ())) \
            if action is not None else set()
        if movement not in served:
            return (GuidanceDecisionStatus.MISSING_INPUT, None,
                    "next_served_green_unknown")
        if vehicle.speed_mps < policy.min_guidance_speed_mps:
            return GuidanceDecisionStatus.NO_ACTION_NEEDED, None, "vehicle_too_slow"
        remaining = snapshot.remaining_time_s
        if remaining is None:
            return (GuidanceDecisionStatus.NO_ACTION_NEEDED, None,
                    "insufficient_green_remaining")
        available = remaining - policy.green_clearance_buffer_s
        if available <= 0.0:
            return (GuidanceDecisionStatus.NO_ACTION_NEEDED, None,
                    "insufficient_green_remaining")
        distance = vehicle.distance_to_signal_m or 0.0
        eta_now = distance / max(vehicle.speed_mps, 1e-6)
        if eta_now <= available:
            return GuidanceDecisionStatus.NO_ACTION_NEEDED, None, "can_pass_within_green"
        raw_target = distance / available
        lane_speed_limit = static_context.lane_speed_limit_mps.get(
            vehicle.lane_id or "")
        v_eff_max = policy.v_max_mps
        if lane_speed_limit is not None:
            v_eff_max = min(v_eff_max, lane_speed_limit)
        lower = max(policy.v_min_mps,
                    policy.speed_scale_low * vehicle.speed_mps)
        upper = min(v_eff_max, policy.speed_scale_high * vehicle.speed_mps)
        if lower > upper:
            return (GuidanceDecisionStatus.INVALID_PROPOSAL, None,
                    "empty_speed_interval")
        if raw_target > upper:
            return (GuidanceDecisionStatus.NO_ACTION_NEEDED, None,
                    "cannot_catch_green_within_limits")
        target = max(raw_target, lower)
        return GuidanceDecisionStatus.PROPOSED, target, "speed_catchup"

    # ---------- 引导：车道分量（§3.3，严格校验 + advisory） ----------
    def _lane_has_fresh_support(
        self, snapshot: EdgeSnapshot, lane_id: str, now: float,
        freshness: FreshnessConfig,
    ) -> bool:
        for vehicle in snapshot.connected_vehicles.values():
            if vehicle.lane_id == lane_id and \
                    now - vehicle.bsm_delivered_at <= freshness.bsm_s:
                return True
        rsm_delivered_at = snapshot.last_delivery_at.get("RSM")
        if rsm_delivered_at is None or now - rsm_delivered_at > freshness.rsm_s:
            return False
        for approach in snapshot.approaches.values():
            lane = approach.lane_states.get(lane_id)
            if lane is not None and lane.observed_count > 0:
                return True
        return False

    def _queue_estimate(self, snapshot: EdgeSnapshot, lane_id: str) -> float:
        for approach in snapshot.approaches.values():
            lane = approach.lane_states.get(lane_id)
            if lane is not None:
                return lane.queue_estimate
        return 0.0

    def _lane_decision(
        self, vehicle: ConnectedVehicleState, snapshot: EdgeSnapshot,
        static_context: IntersectionStaticContext, now: float,
        policy: GuidancePolicyConfig, freshness: FreshnessConfig,
    ) -> tuple[GuidanceDecisionStatus, str | None, int | None, str]:
        movement = self._resolve_movement(
            vehicle, snapshot, now, freshness, static_context)
        if movement is None:
            return (GuidanceDecisionStatus.MISSING_INPUT, None, None,
                    "movement_unknown")
        current_lane = vehicle.lane_id
        if current_lane is None or current_lane not in static_context.lane_to_approach:
            return (GuidanceDecisionStatus.MISSING_INPUT, None, None,
                    "lane_unknown")
        if (vehicle.distance_to_signal_m is None
                or vehicle.distance_to_signal_m < policy.lane_change_min_distance_m):
            return (GuidanceDecisionStatus.NO_ACTION_NEEDED, None, None,
                    "lane_change_too_close")
        current_edge = static_context.lane_to_edge.get(current_lane)
        current_index = static_context.lane_to_index.get(current_lane)
        if current_edge is None or current_index is None:
            return (GuidanceDecisionStatus.MISSING_INPUT, None, None,
                    "lane_adjacency_unknown")
        if not self._lane_has_fresh_support(snapshot, current_lane, now, freshness):
            return (GuidanceDecisionStatus.MISSING_INPUT, None, None,
                    "lane_queue_missing")
        best: tuple[float, str, int] | None = None
        for lane_id, index in static_context.lane_to_index.items():
            if lane_id == current_lane:
                continue
            if static_context.lane_to_edge.get(lane_id) != current_edge:
                continue
            if abs(index - current_index) != 1:
                continue
            if movement not in static_context.lane_to_movements.get(lane_id, ()):
                continue
            if not self._lane_has_fresh_support(snapshot, lane_id, now, freshness):
                return (GuidanceDecisionStatus.STALE_INPUT, None, None,
                        "lane_queue_stale")
            benefit = (self._queue_estimate(snapshot, current_lane)
                       - self._queue_estimate(snapshot, lane_id))
            if benefit >= policy.lane_queue_margin:
                if best is None or benefit > best[0]:
                    best = (benefit, lane_id, index)
        if best is None:
            return (GuidanceDecisionStatus.NO_ACTION_NEEDED, None, None,
                    "no_better_adjacent_lane")
        return GuidanceDecisionStatus.PROPOSED, best[1], best[2], "lane_queue_benefit"

    # ---------- 引导：状态汇总与发射判定（§3.4/§3.5） ----------
    @staticmethod
    def _merge_guidance_status(
        speed_status: GuidanceDecisionStatus,
        lane_status: GuidanceDecisionStatus,
    ) -> GuidanceDecisionStatus:
        if (speed_status is GuidanceDecisionStatus.PROPOSED
                or lane_status is GuidanceDecisionStatus.PROPOSED):
            return GuidanceDecisionStatus.PROPOSED
        priority = (
            GuidanceDecisionStatus.STALE_INPUT,
            GuidanceDecisionStatus.MISSING_INPUT,
            GuidanceDecisionStatus.INVALID_PROPOSAL,
            GuidanceDecisionStatus.SUPPRESSED_COOLDOWN,
            GuidanceDecisionStatus.SUPPRESSED_DUPLICATE,
            GuidanceDecisionStatus.SUPPRESSED_THRESHOLD,
            GuidanceDecisionStatus.NO_ACTION_NEEDED,
        )
        for status in priority:
            if speed_status is status or lane_status is status:
                return status
        return GuidanceDecisionStatus.NO_ACTION_NEEDED

    @staticmethod
    def _filter_reason(
        speed_status: GuidanceDecisionStatus, speed_reason: str,
        lane_status: GuidanceDecisionStatus, lane_reason: str,
    ) -> str:
        reasons = {
            GuidanceDecisionStatus.STALE_INPUT: ("stale_input", speed_reason),
            GuidanceDecisionStatus.MISSING_INPUT: ("missing_input", speed_reason),
            GuidanceDecisionStatus.INVALID_PROPOSAL: ("invalid_proposal", speed_reason),
            GuidanceDecisionStatus.SUPPRESSED_THRESHOLD: ("speed_below_trigger", speed_reason),
            GuidanceDecisionStatus.SUPPRESSED_DUPLICATE: ("duplicate_guidance", speed_reason),
            GuidanceDecisionStatus.SUPPRESSED_COOLDOWN: ("cooldown_active", speed_reason),
        }
        for status, (default, detail) in reasons.items():
            if speed_status is status or lane_status is status:
                return default
        return "no_action_needed"

    def _build_guidance_proposal(
        self, *, vehicle: ConnectedVehicleState, snapshot: EdgeSnapshot,
        now: float, frame_id: str, policy: GuidancePolicyConfig,
        overall: GuidanceDecisionStatus,
        speed_status: GuidanceDecisionStatus, target_speed_mps: float | None,
        lane_status: GuidanceDecisionStatus, target_lane_id: str | None,
        target_lane_index: int | None, reason: str,
    ) -> VehicleGuidanceProposal:
        if overall is GuidanceDecisionStatus.PROPOSED:
            has_speed = target_speed_mps is not None
            has_lane = target_lane_id is not None
            guidance_type = ("combined" if has_speed and has_lane
                             else "speed" if has_speed else "lane")
        else:
            guidance_type = None
        source_message_ids = tuple(dict.fromkeys(
            tuple(vehicle.source_message_ids) + tuple(snapshot.source_message_ids)))
        return VehicleGuidanceProposal(
            vehicle_id=vehicle.vehicle_id,
            status=overall,
            speed_status=speed_status,
            lane_status=lane_status,
            current_speed_mps=vehicle.speed_mps,
            target_speed_mps=target_speed_mps,
            current_lane_id=vehicle.lane_id,
            target_lane_id=target_lane_id,
            target_lane_index=target_lane_index,
            guidance_type=guidance_type,
            reason=reason,
            confidence=None,
            valid_from=now,
            valid_until=now + policy.guidance_ttl_s,
            source_message_ids=source_message_ids,
            source_frame_ids=snapshot.source_frame_ids,
        )

    def _should_resend(
        self, last: LastEmittedGuidanceState, target_speed_mps: float | None,
        target_lane_id: str | None, reason: str, now: float,
        policy: GuidancePolicyConfig,
    ) -> bool:
        if last.target_lane_id != target_lane_id:
            return True
        if (target_speed_mps is None) != (last.target_speed_mps is None):
            return True
        if target_speed_mps is not None and last.target_speed_mps is not None:
            if abs(target_speed_mps - last.target_speed_mps) >= policy.speed_resend_delta_mps:
                return True
        if reason != last.reason:
            return True
        if now >= last.valid_until:
            return True
        return False

    def missing_signal_proposal(
        self, *, intersection_id: str, now: float, frame_id: str,
        reason: str = "missing_map",
    ) -> SignalProposal:
        """engine 在 MAP 缺失时构造 MISSING_INPUT 信号建议（spec §2 状态表）。"""
        return SignalProposal(
            intersection_id=intersection_id,
            status=SignalDecisionStatus.MISSING_INPUT,
            candidate_action=None, proposed_action=None, current_action=None,
            action_scores={}, reason=reason, confidence=0.0,
            valid_from=now,
            valid_until=now + self._config.signal_policy.proposal_ttl_s,
            needs_transition=False, decision_frame_id=frame_id,
            source_message_ids=(), source_frame_ids=())

    def propose_guidance(
        self, *, vehicle: ConnectedVehicleState, snapshot: EdgeSnapshot,
        static_context: IntersectionStaticContext, now: float, frame_id: str,
        config: CollabConfig, scope: ResolvedScenarioScope,
        last_emitted: LastEmittedGuidanceState | None,
    ) -> GuidanceOutcome:
        policy = config.guidance_policy
        if config.guidance_mode is GuidanceEmissionMode.DISABLED:
            return GuidanceOutcome(None, "connected_seen", "guidance_disabled")
        stage, filter_reason = self._candidate_stage(
            vehicle, now, config.freshness, scope, policy.guidance_horizon_m)
        if stage != "in_horizon_candidates":
            return GuidanceOutcome(None, stage, filter_reason)
        speed_status, target_speed_mps, speed_reason = self._speed_decision(
            vehicle, snapshot, static_context, now, policy, config.freshness)
        lane_status, target_lane_id, target_lane_index, lane_reason = \
            self._lane_decision(vehicle, snapshot, static_context, now,
                                policy, config.freshness)
        # 速度分量阈值（THRESHOLD 生效；FULL 仅诊断）
        speed_below_trigger = (
            speed_status is GuidanceDecisionStatus.PROPOSED
            and abs((target_speed_mps or 0.0) - vehicle.speed_mps)
            < policy.speed_trigger_delta_mps)
        if (config.guidance_mode is GuidanceEmissionMode.THRESHOLD
                and speed_below_trigger):
            speed_status = GuidanceDecisionStatus.SUPPRESSED_THRESHOLD
            speed_reason = "speed_below_trigger"
        overall = self._merge_guidance_status(speed_status, lane_status)
        if overall is not GuidanceDecisionStatus.PROPOSED:
            reason = self._filter_reason(speed_status, speed_reason,
                                         lane_status, lane_reason)
            proposal = self._build_guidance_proposal(
                vehicle=vehicle, snapshot=snapshot, now=now, frame_id=frame_id,
                policy=policy, overall=overall,
                speed_status=speed_status, target_speed_mps=target_speed_mps,
                lane_status=lane_status, target_lane_id=target_lane_id,
                target_lane_index=target_lane_index, reason=reason)
            return GuidanceOutcome(proposal, "raw_proposals", reason,
                                   would_pass_threshold=False,
                                   would_be_duplicate=None,
                                   would_be_in_cooldown=None)
        funnel_stage = "raw_proposals"
        reason = self._pick_guidance_reason(speed_reason, lane_reason)
        # 去重/冷却判定（THRESHOLD 生效；FULL 按 THRESHOLD 规则诊断计算）
        would_duplicate = False
        would_cooldown = False
        if last_emitted is not None and not self._should_resend(
                last_emitted, target_speed_mps, target_lane_id, reason,
                now, policy):
            would_duplicate = True
        elif last_emitted is not None and \
                now < last_emitted.emitted_at + policy.minimum_resend_interval_s:
            would_cooldown = True
        if config.guidance_mode is GuidanceEmissionMode.FULL:
            # FULL 绕过阈值/去重/冷却发布，但按 THRESHOLD 规则计算诊断三值
            sim_speed_status = (
                GuidanceDecisionStatus.SUPPRESSED_THRESHOLD
                if speed_below_trigger else speed_status)
            sim_overall = self._merge_guidance_status(
                sim_speed_status, lane_status)
            would_pass_threshold = (
                sim_overall is GuidanceDecisionStatus.PROPOSED)
            if would_pass_threshold:
                return GuidanceOutcome(
                    self._build_guidance_proposal(
                        vehicle=vehicle, snapshot=snapshot, now=now,
                        frame_id=frame_id, policy=policy,
                        overall=GuidanceDecisionStatus.PROPOSED,
                        speed_status=speed_status,
                        target_speed_mps=target_speed_mps,
                        lane_status=lane_status, target_lane_id=target_lane_id,
                        target_lane_index=target_lane_index, reason=reason),
                    "published", None,
                    would_pass_threshold=True,
                    would_be_duplicate=would_duplicate,
                    would_be_in_cooldown=would_cooldown)
            return GuidanceOutcome(
                self._build_guidance_proposal(
                    vehicle=vehicle, snapshot=snapshot, now=now,
                    frame_id=frame_id, policy=policy,
                    overall=GuidanceDecisionStatus.PROPOSED,
                    speed_status=speed_status,
                    target_speed_mps=target_speed_mps,
                    lane_status=lane_status, target_lane_id=target_lane_id,
                    target_lane_index=target_lane_index, reason=reason),
                "published", None,
                would_pass_threshold=False,
                would_be_duplicate=None,
                would_be_in_cooldown=None)
        # THRESHOLD 模式
        funnel_stage = "threshold_passed"
        if would_duplicate:
            overall = GuidanceDecisionStatus.SUPPRESSED_DUPLICATE
            return GuidanceOutcome(
                self._build_guidance_proposal(
                    vehicle=vehicle, snapshot=snapshot, now=now,
                    frame_id=frame_id, policy=policy, overall=overall,
                    speed_status=speed_status, target_speed_mps=target_speed_mps,
                    lane_status=lane_status, target_lane_id=target_lane_id,
                    target_lane_index=target_lane_index,
                    reason="duplicate_guidance"),
                "threshold_passed", "duplicate_guidance",
                would_pass_threshold=True,
                would_be_duplicate=True,
                would_be_in_cooldown=False)
        funnel_stage = "dedup_passed"
        if would_cooldown:
            overall = GuidanceDecisionStatus.SUPPRESSED_COOLDOWN
            return GuidanceOutcome(
                self._build_guidance_proposal(
                    vehicle=vehicle, snapshot=snapshot, now=now,
                    frame_id=frame_id, policy=policy, overall=overall,
                    speed_status=speed_status, target_speed_mps=target_speed_mps,
                    lane_status=lane_status, target_lane_id=target_lane_id,
                    target_lane_index=target_lane_index,
                    reason="cooldown_active"),
                "dedup_passed", "cooldown_active",
                would_pass_threshold=True,
                would_be_duplicate=False,
                would_be_in_cooldown=True)
        funnel_stage = "cooldown_passed"
        proposal = self._build_guidance_proposal(
            vehicle=vehicle, snapshot=snapshot, now=now, frame_id=frame_id,
            policy=policy, overall=GuidanceDecisionStatus.PROPOSED,
            speed_status=speed_status, target_speed_mps=target_speed_mps,
            lane_status=lane_status, target_lane_id=target_lane_id,
            target_lane_index=target_lane_index, reason=reason)
        return GuidanceOutcome(proposal, "published", None,
                               would_pass_threshold=True,
                               would_be_duplicate=False,
                               would_be_in_cooldown=False)

    @staticmethod
    def _pick_guidance_reason(speed_reason: str, lane_reason: str) -> str:
        if speed_reason == "speed_catchup":
            return speed_reason
        if lane_reason == "lane_queue_benefit":
            return lane_reason
        return speed_reason if speed_reason != "no_action_needed" else lane_reason
