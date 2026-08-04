# algorithms/v2x/collab/policy.py
"""CloudRulePolicy：信号规则族 C（排队/到达基线 + INTENT 前视）+ 引导阈值触发。

本文件在 Task 7 实现 propose_signal；Task 8 追加 propose_guidance 与状态字典。
"""
from __future__ import annotations

import math
from typing import Mapping

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
