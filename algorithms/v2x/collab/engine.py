# algorithms/v2x/collab/engine.py
"""CollabDecisionEngine：一次决策帧编排（spec §1.2/§1.3/§4.2）。"""
from __future__ import annotations

import copy
from collections import Counter
from dataclasses import dataclass, field
from typing import Any, Mapping, Optional

from ..hub import FrameContext, V2XHub
from ..messages import MessageDraft
from .aggregator import EdgeAggregator
from .arbiter import ActionArbiter, ActiveModeUnavailableError
from .policy import GUIDANCE_FUNNEL_STAGES, CloudRulePolicy, GuidanceOutcome
from .proposals import (
    CollabConfig, DecisionMode, DecisionSource,
    GuidanceEmissionMode, LastEmittedGuidanceState, SignalProposal,
    VehicleGuidanceProposal,
)
from .records import (
    InMemoryRecordCollector, arbitration_record, cloud_proposal_record,
    collab_episode_end_record, collab_tick_stats_record, edge_snapshot_record,
)
from .snapshot import ConnectedVehicleState, EdgeSnapshot, IntersectionStaticContext
from .state import CloudIntersectionView, CloudStateStore
from .stats import build_collab_summary
from algorithms.config.scenario_presets import ResolvedScenarioScope


@dataclass(frozen=True, slots=True)
class CollabStatsDelta:
    baseline_slots: int = 0
    decision_records: int = 0
    status_counts: Mapping[str, int] = field(default_factory=dict)
    validation_counts: Mapping[str, int] = field(default_factory=dict)
    proposal_without_baseline: int = 0
    guidance_funnel: Mapping[str, int] = field(default_factory=dict)
    filter_reason_counts: Mapping[str, int] = field(default_factory=dict)
    emitted_rsi_count: int = 0


@dataclass(frozen=True, slots=True)
class CollabTickResult:
    protocol_actions: Mapping[str, Any]          # SHADOW = baseline 完整等价副本
    signal_sources: Mapping[str, DecisionSource]
    emitted_rsi_message_ids: tuple[str, ...]
    emitted_rsi_message_ids_by_intersection: Mapping[str, tuple[str, ...]]
    stats_delta: CollabStatsDelta
    frame_id: str
    sim_time: float


def _deep_copy_actions(actions: Mapping[str, Any]) -> dict:
    return copy.deepcopy(dict(actions))


def _action_of(spec: Any) -> Optional[int]:
    if isinstance(spec, Mapping):
        value = spec.get("target_phase")
        return int(value) if value is not None else None
    return int(spec) if spec is not None else None


def _rsi_draft(proposal: VehicleGuidanceProposal, *, sim_time: float) -> MessageDraft:
    # 直接构造 RSI 草稿（不经过 protocol.build_rsi_draft，以保留 combined 类型；
    # RSI REQUIRED_FIELDS 只要求键存在，None 值合法）
    return MessageDraft(
        "RSI", "cloud", proposal.vehicle_id, sim_time,
        {"vehicle_id": proposal.vehicle_id,
         "target_speed_mps": proposal.target_speed_mps,
         "target_lane_index": proposal.target_lane_index,
         "guidance_type": proposal.guidance_type},
    )


class CollabDecisionEngine:
    def __init__(
        self, *, hub: V2XHub, aggregator: EdgeAggregator,
        store: CloudStateStore, policy: CloudRulePolicy,
        arbiter: ActionArbiter, collector: InMemoryRecordCollector,
        config: CollabConfig, scope: ResolvedScenarioScope,
        run_id: str, episode_id: str, registered_ids: tuple[str, ...],
    ) -> None:
        if config.decision_mode is DecisionMode.ACTIVE:
            raise ActiveModeUnavailableError(
                "ACTIVE decision mode is unavailable in v1 (spec §4.1)")
        self._hub = hub
        self._aggregator = aggregator
        self._store = store
        self._policy = policy
        self._arbiter = arbiter
        self._collector = collector
        self._config = config
        self._scope = scope
        self._run_id = run_id
        self._episode_id = episode_id
        self._registered_ids = tuple(registered_ids)
        self._last_emitted: dict[str, LastEmittedGuidanceState] = {}
        self._closed = False
        # 订阅一次：引擎创建时注册，close() 后停止回调（不修改 hub 订阅 API）
        def _handler(message, _aggregator=aggregator, _engine=self):
            if not _engine._closed:
                _aggregator.on_message(message)
        for message_type in ("BSM", "INTENT", "SPaT", "MAP", "RSM"):
            hub.subscribe(message_type, _handler)

    # ---------- 决策帧 ----------
    def tick(self, *, frame: FrameContext,
             baseline_actions: Mapping[str, Any]) -> CollabTickResult:
        if self._closed:
            raise RuntimeError("engine closed")
        now = frame.sim_time
        if self._config.decision_mode is DecisionMode.OFF:
            return CollabTickResult(
                protocol_actions=_deep_copy_actions(baseline_actions),
                signal_sources={}, emitted_rsi_message_ids=(),
                emitted_rsi_message_ids_by_intersection={},
                stats_delta=CollabStatsDelta(),
                frame_id=frame.frame_id, sim_time=now)
        managed = tuple(self._scope.managed_ids)
        managed_set = frozenset(managed)
        baseline_signals = dict((baseline_actions.get("signals") or {}))
        # 先构建全部视图（每路口 1 次），再统一 after_snapshot 更新 arrivals 基线
        views: dict[str, tuple[Optional[CloudIntersectionView],
                               Optional[IntersectionStaticContext]]] = {}
        for iid in managed:
            views[iid] = (self._store.view(iid, now),
                          self._store.static_context(iid))
        # ---- 信号：决策 + 仲裁 + 记录 ----
        status_counts: Counter[str] = Counter()
        validation_counts: Counter[str] = Counter()
        failure_reason_counts: Counter[str] = Counter()
        signal_sources: dict[str, DecisionSource] = {}
        proposal_without_baseline = 0
        decision_records = 0
        baseline_slots = 0
        for iid in managed:
            if iid not in baseline_signals:
                continue
            baseline_slots += 1
            decision_records += 1
            baseline_action = _action_of(baseline_signals[iid])
            view, ctx = views[iid]
            if view is not None and ctx is not None:
                proposal = self._policy.propose_signal(
                    intersection_id=iid, snapshot=view.snapshot,
                    static_context=ctx, now=now,
                    frame_id=frame.frame_id, config=self._config)
            else:
                proposal = self._policy.missing_signal_proposal(
                    intersection_id=iid, now=now,
                    frame_id=frame.frame_id, reason="missing_map")
            status_counts[proposal.status.value] += 1
            in_transition = bool(
                view is not None and view.snapshot.stage in ("YELLOW", "CLEARANCE"))
            result = self._arbiter.arbitrate(
                proposal=proposal, baseline_action=baseline_action,
                run_id=self._run_id, frame_id=frame.frame_id,
                intersection_id=iid, now=now, in_transition=in_transition,
                valid_actions=ctx.valid_actions if ctx is not None else ())
            signal_sources[iid] = result.decision_source
            if result.validation is not None:
                validation_counts[
                    "passed" if result.validation.passed else "failed"] += 1
                if result.validation.failure_reason:
                    failure_reason_counts[result.validation.failure_reason] += 1
            if self._config.log_arbitration_mode == "all":
                self._collector.write(arbitration_record(
                    run_id=self._run_id, episode_id=frame.episode_id,
                    frame_id=frame.frame_id, intersection_id=iid,
                    sim_time=now, baseline_action=baseline_action,
                    candidate_action=proposal.candidate_action,
                    proposed_action=proposal.proposed_action,
                    selected_action=result.selected_action,
                    proposal_status=proposal.status.value,
                    validation_status=(
                        result.validation.failure_reason if result.validation
                        and not result.validation.passed else "passed"
                        if result.validation else None),
                    validation_failure_reason=(
                        result.validation.failure_reason
                        if result.validation else None),
                    decision_source=result.decision_source.value,
                    selection_status=result.selection_status,
                    confidence=proposal.confidence, reason=proposal.reason))
            self._collector.write(cloud_proposal_record(
                run_id=self._run_id, episode_id=frame.episode_id,
                frame_id=frame.frame_id, sim_time=now,
                proposal=proposal, proposal_type="signal"))
            if self._config.log_edge_snapshot and view is not None:
                self._collector.write(edge_snapshot_record(
                    run_id=self._run_id, episode_id=frame.episode_id,
                    frame_id=frame.frame_id, snapshot=view.snapshot))
        # ---- 引导：RSI（仅 PROPOSED → hub.publish；不进 actions.vehicles）----
        funnel: Counter[str] = Counter()
        filter_reason_counts: Counter[str] = Counter()
        emitted_ids: list[str] = []
        emitted_by_intersection: dict[str, list[str]] = {}
        diagnostics: Counter[str] = Counter()
        if self._config.guidance_mode is not GuidanceEmissionMode.DISABLED:
            for iid in managed:
                view, ctx = views[iid]
                if view is None or ctx is None:
                    continue
                for vehicle in view.snapshot.connected_vehicles.values():
                    outcome = self._policy.propose_guidance(
                        vehicle=vehicle, snapshot=view.snapshot,
                        static_context=ctx, now=now,
                        frame_id=frame.frame_id, config=self._config,
                        scope=self._scope,
                        last_emitted=self._last_emitted.get(vehicle.vehicle_id))
                    self._accumulate_funnel(funnel, outcome.funnel_stage)
                    if outcome.filter_reason is not None:
                        filter_reason_counts[outcome.filter_reason] += 1
                    diagnostics["would_pass_threshold"] += int(
                        outcome.would_pass_threshold)
                    if outcome.would_be_duplicate is not None:
                        diagnostics["would_be_duplicate"] += int(
                            outcome.would_be_duplicate)
                    if outcome.would_be_in_cooldown is not None:
                        diagnostics["would_be_in_cooldown"] += int(
                            outcome.would_be_in_cooldown)
                    proposal = outcome.proposal
                    emitted_message_id = None
                    if (proposal is not None
                            and outcome.funnel_stage == "published"
                            and vehicle.next_signal_intersection_id in managed_set):
                        # 发布前防御性双检（§3.6）
                        message = self._hub.publish(
                            _rsi_draft(proposal, sim_time=now),
                            frame_id=frame.frame_id,
                            correlation_id=frame.frame_id)
                        emitted_message_id = message.message_id
                        emitted_ids.append(message.message_id)
                        emitted_by_intersection.setdefault(
                            vehicle.next_signal_intersection_id, []
                        ).append(message.message_id)
                        self._last_emitted[vehicle.vehicle_id] = \
                            LastEmittedGuidanceState(
                                target_speed_mps=proposal.target_speed_mps,
                                target_lane_id=proposal.target_lane_id,
                                target_lane_index=proposal.target_lane_index,
                                emitted_at=now,
                                valid_until=proposal.valid_until,
                                reason=proposal.reason,
                                emitted_message_id=message.message_id)
                    if proposal is not None:
                        self._collector.write(cloud_proposal_record(
                            run_id=self._run_id, episode_id=frame.episode_id,
                            frame_id=frame.frame_id, sim_time=now,
                            proposal=proposal,
                            proposal_type="vehicle_guidance",
                            emitted_message_id=emitted_message_id,
                            next_signal_intersection_id=(
                                vehicle.next_signal_intersection_id)))
        # ---- 帧末更新 arrivals 基线 ----
        for iid in managed:
            if views[iid][0] is not None:
                self._aggregator.after_snapshot(iid)
        # ---- collab_tick_stats 原子写入（不可关闭）----
        guidance_funnel = {key: funnel.get(key, 0)
                           for key in GUIDANCE_FUNNEL_STAGES}
        if self._config.guidance_mode is GuidanceEmissionMode.FULL:
            guidance_funnel.update(dict(diagnostics))
        stats_delta = CollabStatsDelta(
            baseline_slots=baseline_slots,
            decision_records=decision_records,
            status_counts=dict(status_counts),
            validation_counts=dict(validation_counts),
            proposal_without_baseline=proposal_without_baseline,
            guidance_funnel=guidance_funnel,
            filter_reason_counts=dict(filter_reason_counts),
            emitted_rsi_count=len(emitted_ids))
        self._collector.write(collab_tick_stats_record(
            run_id=self._run_id, episode_id=frame.episode_id,
            frame_id=frame.frame_id, sim_time=now,
            baseline_slots=baseline_slots,
            decision_records=decision_records,
            status_counts=dict(status_counts),
            validation_counts=dict(validation_counts),
            proposal_without_baseline=proposal_without_baseline,
            guidance_funnel=guidance_funnel,
            filter_reason_counts=dict(filter_reason_counts)))
        return CollabTickResult(
            protocol_actions=_deep_copy_actions(baseline_actions),
            signal_sources=signal_sources,
            emitted_rsi_message_ids=tuple(emitted_ids),
            emitted_rsi_message_ids_by_intersection={
                k: tuple(v) for k, v in emitted_by_intersection.items()},
            stats_delta=stats_delta,
            frame_id=frame.frame_id, sim_time=now)

    @staticmethod
    def _accumulate_funnel(counter: Counter, funnel_stage: str) -> None:
        try:
            reached = GUIDANCE_FUNNEL_STAGES.index(funnel_stage)
        except ValueError:
            return
        for stage in GUIDANCE_FUNNEL_STAGES[: reached + 1]:
            counter[stage] += 1

    # ---------- 生命周期 ----------
    def finalize_episode(self, *, episode_id: str,
                         registered_ids: Optional[tuple[str, ...]] = None) -> dict:
        summary = build_collab_summary(
            records=self._collector.episode_records,
            config=self._config, scope=self._scope,
            registered_ids=registered_ids or self._registered_ids,
            hub=self._hub, run_id=self._run_id, episode_id=episode_id)
        self._collector.write(collab_episode_end_record(summary=summary))
        return summary

    def reset_episode(self, *, episode_id: str) -> None:
        self._aggregator.reset_episode()
        self._store.reset_episode()
        self._arbiter.reset_episode()
        self._last_emitted.clear()
        self._collector.reset_episode()

    def close(self) -> None:
        self._closed = True
        self._collector.flush()