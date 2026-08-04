# algorithms/v2x/collab/arbiter.py
"""ActionArbiter + validate_signal_proposal（spec §4）。"""
from __future__ import annotations

import math
from dataclasses import dataclass
from typing import Mapping, Optional, Sequence

from .proposals import (
    DecisionMode, DecisionSource, SignalDecisionStatus, SignalProposal,
)

CLOUD_SELECTABLE_STATUSES = frozenset({
    SignalDecisionStatus.PROPOSED,
    SignalDecisionStatus.KEEP_CURRENT,
    SignalDecisionStatus.NO_DEMAND,
    SignalDecisionStatus.SUPPRESSED_MIN_GREEN,
    SignalDecisionStatus.SUPPRESSED_SWITCH_MARGIN,
})

FALLBACK_STATUSES = frozenset({
    SignalDecisionStatus.MISSING_INPUT,
    SignalDecisionStatus.STALE_INPUT,
    SignalDecisionStatus.INVALID_PROPOSAL,
    SignalDecisionStatus.SUPPRESSED_TRANSITION,
})


class ActiveModeUnavailableError(RuntimeError):
    """v1 不支持 ACTIVE 决策模式（spec §4.1：选择即报错）。"""


@dataclass(frozen=True, slots=True)
class ProposalValidationResult:
    passed: bool
    would_select_cloud: bool
    would_select_action: Optional[int]
    failure_reason: Optional[str]


@dataclass(frozen=True, slots=True)
class ArbitrationResult:
    intersection_id: str
    baseline_action: Optional[int]
    selected_action: Optional[int]
    decision_source: DecisionSource
    selection_status: str  # selected_baseline_shadow / selected_cloud / selected_fallback
    proposal: Optional[SignalProposal]
    validation: Optional[ProposalValidationResult]


def validate_signal_proposal(
    proposal: SignalProposal, *,
    run_id: str, frame_id: str, intersection_id: str, now: float,
    current_action: Optional[int], in_transition: bool,
    valid_actions: Sequence[int],
) -> ProposalValidationResult:
    """SHADOW 与未来 ACTIVE 共用同一验证器（spec §4.3）。"""
    if proposal.intersection_id != intersection_id:
        return ProposalValidationResult(False, False, None, "intersection_mismatch")
    if proposal.decision_frame_id != frame_id:
        return ProposalValidationResult(False, False, None, "stale_decision_frame")
    if not (proposal.valid_from <= now < proposal.valid_until):
        return ProposalValidationResult(False, False, None, "outside_validity_window")
    if proposal.current_action != current_action:
        return ProposalValidationResult(False, False, None, "current_action_mismatch")
    if proposal.status not in CLOUD_SELECTABLE_STATUSES:
        return ProposalValidationResult(False, False, None, "status_not_selectable")
    if proposal.proposed_action is None or proposal.proposed_action not in valid_actions:
        return ProposalValidationResult(False, False, None, "proposed_action_not_valid")
    if in_transition:
        return ProposalValidationResult(False, False, None, "in_transition")
    values = [proposal.valid_from, proposal.valid_until, proposal.confidence]
    values.extend(proposal.action_scores.values())
    if any(v is not None and (not math.isfinite(float(v))) for v in values):
        return ProposalValidationResult(False, False, None, "non_finite_values")
    return ProposalValidationResult(True, True, proposal.proposed_action, None)


class ActionArbiter:
    def __init__(self, mode: DecisionMode) -> None:
        if mode is DecisionMode.ACTIVE:
            raise ActiveModeUnavailableError(
                "ACTIVE decision mode is unavailable in v1 (spec §4.1); "
                "use --v2x-collab-mode shadow|off")
        self.mode = mode

    def arbitrate(
        self, *, proposal: Optional[SignalProposal], baseline_action: Optional[int],
        run_id: str, frame_id: str, intersection_id: str, now: float,
        in_transition: bool, valid_actions: Sequence[int],
    ) -> ArbitrationResult:
        if self.mode is DecisionMode.OFF:
            return ArbitrationResult(
                intersection_id=intersection_id, baseline_action=baseline_action,
                selected_action=baseline_action,
                decision_source=DecisionSource.BASELINE,
                selection_status="selected_baseline_shadow",
                proposal=proposal, validation=None)
        # SHADOW（默认）：建议照常生成/记录，但 applied == baseline
        validation = None
        if proposal is not None:
            validation = validate_signal_proposal(
                proposal, run_id=run_id, frame_id=frame_id,
                intersection_id=intersection_id, now=now,
                current_action=baseline_action, in_transition=in_transition,
                valid_actions=valid_actions)
        return ArbitrationResult(
            intersection_id=intersection_id, baseline_action=baseline_action,
            selected_action=baseline_action,
            decision_source=DecisionSource.BASELINE,
            selection_status="selected_baseline_shadow",
            proposal=proposal, validation=validation)

    def reset_episode(self) -> None:
        # v1 仲裁器无 episode 级动态状态；保留钩子以兼容多 episode
        return None
