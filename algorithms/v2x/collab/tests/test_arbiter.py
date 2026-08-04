import pytest

from algorithms.v2x.collab.arbiter import (
    ActionArbiter, ActiveModeUnavailableError, validate_signal_proposal,
)
from algorithms.v2x.collab.proposals import (
    DecisionMode, DecisionSource, SignalDecisionStatus, SignalProposal,
)


def _proposal(status=SignalDecisionStatus.PROPOSED, proposed=2, current=1,
              frame_id="ep1:step:000001", intersection_id="i1",
              valid_until=10.0):
    return SignalProposal(
        intersection_id=intersection_id,
        status=status,
        candidate_action=2 if status is SignalDecisionStatus.PROPOSED else None,
        proposed_action=proposed,
        current_action=current,
        action_scores={1: 1.0, 2: 3.0},
        reason="test", confidence=0.8,
        valid_from=0.0, valid_until=valid_until,
        needs_transition=True, decision_frame_id=frame_id,
        source_message_ids=("m1",), source_frame_ids=("ep1:step:000001",),
    )


def test_validate_passes_for_valid_proposal():
    result = validate_signal_proposal(
        _proposal(), run_id="run1", frame_id="ep1:step:000001",
        intersection_id="i1", now=5.0, current_action=1,
        in_transition=False, valid_actions=(1, 2))
    assert result.passed
    assert result.would_select_cloud
    assert result.would_select_action == 2
    assert result.failure_reason is None


@pytest.mark.parametrize("kw,reason", [
    ({"frame_id": "ep1:step:000002"}, "stale_decision_frame"),
    ({"now": 11.0}, "outside_validity_window"),
    ({"current_action": 2}, "current_action_mismatch"),
    ({"in_transition": True}, "in_transition"),
    ({"valid_actions": (1,)}, "proposed_action_not_valid"),
])
def test_validate_failures(kw, reason):
    base = dict(run_id="run1", frame_id="ep1:step:000001",
                intersection_id="i1", now=5.0, current_action=1,
                in_transition=False, valid_actions=(1, 2))
    base.update(kw)
    result = validate_signal_proposal(_proposal(), **base)
    assert not result.passed
    assert result.failure_reason == reason
    assert not result.would_select_cloud


def test_validate_rejects_non_selectable_status():
    proposal = _proposal(status=SignalDecisionStatus.MISSING_INPUT, proposed=None)
    result = validate_signal_proposal(
        proposal, run_id="run1", frame_id="ep1:step:000001",
        intersection_id="i1", now=5.0, current_action=1,
        in_transition=False, valid_actions=(1, 2))
    assert not result.passed
    assert result.failure_reason == "status_not_selectable"


def test_shadow_arbiter_always_selects_baseline():
    arbiter = ActionArbiter(DecisionMode.SHADOW)
    result = arbiter.arbitrate(
        proposal=_proposal(), baseline_action=1, run_id="run1",
        frame_id="ep1:step:000001", intersection_id="i1", now=5.0,
        in_transition=False, valid_actions=(1, 2))
    assert result.selected_action == 1
    assert result.decision_source is DecisionSource.BASELINE
    assert result.selection_status == "selected_baseline_shadow"
    assert result.validation.passed


def test_off_arbiter_short_circuits():
    arbiter = ActionArbiter(DecisionMode.OFF)
    result = arbiter.arbitrate(
        proposal=None, baseline_action=1, run_id="run1",
        frame_id="ep1:step:000001", intersection_id="i1", now=5.0,
        in_transition=False, valid_actions=(1, 2))
    assert result.selected_action == 1
    assert result.validation is None


def test_active_mode_unavailable_at_construction():
    with pytest.raises(ActiveModeUnavailableError):
        ActionArbiter(DecisionMode.ACTIVE)
