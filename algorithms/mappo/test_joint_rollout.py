from __future__ import annotations

from collections.abc import Sequence
from dataclasses import replace

import numpy as np
import pytest

from algorithms.mappo.features import CENTRALIZED_STATE_SCHEMA, CentralizedState
from algorithms.mappo.joint_rollout import (
    JointExecutionAlignedRollout,
    JointPendingTransition,
    JointTransition,
)
from algorithms.mappo.rollout import ActionAlignmentError, PendingTransition, Transition
from algorithms.mappo.reward import REWARD_MIN, REWARD_MAX


NUM_AGENTS = 2
JOINT_STEP_ID = 3
DECISION_TIME_S = 15.0
WINDOW_END_S = 20.0
POLICY_GENERATION = 7


def _state(
    offset: float = 0.0,
    *,
    schema: str = CENTRALIZED_STATE_SCHEMA,
    agent_mask: np.ndarray | None = None,
) -> CentralizedState:
    return CentralizedState(
        observations=np.array(
            [[offset, 1.0], [offset + 2.0, 3.0]], dtype=np.float32
        ),
        agent_mask=(
            np.array([True, True]) if agent_mask is None else agent_mask
        ),
        intersection_ids=("demo_0", "demo_1"),
        schema=schema,
    )


def _state_with_observations(observations: np.ndarray) -> CentralizedState:
    row_count = observations.shape[0]
    return CentralizedState(
        observations=observations,
        agent_mask=np.ones(row_count, dtype=np.bool_),
        intersection_ids=tuple(f"demo_{index}" for index in range(row_count)),
        schema=CENTRALIZED_STATE_SCHEMA,
    )


def _pending(
    agent_index: int,
    *,
    global_state: CentralizedState | None = None,
    value: float = 0.5,
    decision_time_s: float = DECISION_TIME_S,
    policy_generation: int = POLICY_GENERATION,
    requested_phase: int | None = None,
) -> PendingTransition:
    phase = agent_index + 2 if requested_phase is None else requested_phase
    return PendingTransition(
        local_obs=np.array(
            [agent_index + 0.1, agent_index + 0.2], dtype=np.float32
        ),
        phase_features=np.array(
            [[1.0, 0.0], [0.0, 1.0]], dtype=np.float32
        ),
        action_mask=np.array([True, True]),
        global_state=_state() if global_state is None else global_state,
        agent_index=agent_index,
        action=agent_index,
        requested_phase=phase,
        log_prob=-0.25 - agent_index,
        value=value,
        decision_time_s=decision_time_s,
        policy_generation=policy_generation,
    )


def _joint_pending(
    agent_pendings: Sequence[PendingTransition] | None = None,
) -> JointPendingTransition:
    state = _state()
    pendings = (
        (_pending(0, global_state=state), _pending(1, global_state=state))
        if agent_pendings is None
        else tuple(agent_pendings)
    )
    return JointPendingTransition(
        joint_step_id=JOINT_STEP_ID,
        agent_pendings=pendings,
    )


def _next_local_observations() -> tuple[np.ndarray, np.ndarray]:
    return (
        np.array([10.1, 10.2], dtype=np.float32),
        np.array([11.1, 11.2], dtype=np.float32),
    )


def _complete(
    rollout: JointExecutionAlignedRollout,
    *,
    next_values: tuple[float, ...] = (0.75, 0.75),
    terminated: bool = False,
    truncated: bool = False,
):
    return rollout.complete(
        next_local_observations=_next_local_observations(),
        next_global_state=_state(10.0),
        next_values=next_values,
        team_reward=-0.25,
        team_raw_reward=-0.4,
        window_end_s=WINDOW_END_S,
        terminated=terminated,
        truncated=truncated,
    )


def _begin_and_confirm_all(
    rollout: JointExecutionAlignedRollout,
) -> None:
    rollout.begin(_joint_pending())
    rollout.confirm_applied(agent_index=0, phase=2, time_s=18.0)
    rollout.confirm_applied(agent_index=1, phase=3, time_s=19.0)


@pytest.mark.parametrize(
    ("agent_pendings", "message"),
    [
        ((_pending(0),), "missing"),
        ((_pending(0), _pending(0)), "duplicate"),
        ((_pending(1), _pending(0)), "fixed agent-index order"),
    ],
)
def test_begin_rejects_incomplete_duplicate_or_out_of_order_agents(
    agent_pendings: tuple[PendingTransition, ...], message: str
) -> None:
    rollout = JointExecutionAlignedRollout(num_agents=NUM_AGENTS)

    with pytest.raises(ValueError, match=message):
        rollout.begin(_joint_pending(agent_pendings))

    assert rollout.pending is None
    assert rollout.invalid is False


def test_begin_rejects_differing_decision_times_atomically() -> None:
    shared_state = _state()
    joint_pending = _joint_pending(
        (
            _pending(0, global_state=shared_state),
            _pending(
                1,
                global_state=shared_state,
                decision_time_s=DECISION_TIME_S + 1.0,
            ),
        )
    )
    rollout = JointExecutionAlignedRollout(num_agents=NUM_AGENTS)

    with pytest.raises(ValueError, match="decision time"):
        rollout.begin(joint_pending)

    assert rollout.pending is None


def test_begin_rejects_differing_global_states_atomically() -> None:
    rollout = JointExecutionAlignedRollout(num_agents=NUM_AGENTS)
    joint_pending = _joint_pending(
        (_pending(0, global_state=_state()), _pending(1, global_state=_state(1.0)))
    )

    with pytest.raises(ValueError, match="global state"):
        rollout.begin(joint_pending)

    assert rollout.pending is None


def test_begin_rejects_differing_policy_generations_atomically() -> None:
    shared_state = _state()
    rollout = JointExecutionAlignedRollout(num_agents=NUM_AGENTS)
    joint_pending = _joint_pending(
        (
            _pending(0, global_state=shared_state),
            _pending(
                1,
                global_state=shared_state,
                policy_generation=POLICY_GENERATION + 1,
            ),
        )
    )

    with pytest.raises(ValueError, match="policy generation"):
        rollout.begin(joint_pending)

    assert rollout.pending is None


def test_begin_prevalidates_every_child_before_mutating_any_child() -> None:
    shared_state = _state()
    malformed_second = replace(
        _pending(1, global_state=shared_state),
        phase_features=np.array([["bad"]], dtype=object),
    )
    rollout = JointExecutionAlignedRollout(num_agents=NUM_AGENTS)

    with pytest.raises(ValueError, match="phase features"):
        rollout.begin(
            _joint_pending(
                (
                    _pending(0, global_state=shared_state),
                    malformed_second,
                )
            )
        )

    assert rollout.pending is None
    assert rollout.invalid is False

    rollout.begin(_joint_pending())
    assert rollout.pending is not None


@pytest.mark.parametrize(
    "invalid_state",
    [
        _state_with_observations(np.ones((3, 2), dtype=np.float32)),
        _state_with_observations(np.ones((2, 3), dtype=np.float32)),
        _state(schema="unexpected_schema"),
        _state(agent_mask=np.array([True, False], dtype=np.bool_)),
    ],
    ids=(
        "row-count",
        "local-observation-width",
        "schema-label",
        "partial-agent-mask",
    ),
)
def test_begin_rejects_global_state_outside_complete_configured_schema(
    invalid_state: CentralizedState,
) -> None:
    rollout = JointExecutionAlignedRollout(num_agents=NUM_AGENTS)

    with pytest.raises(ValueError, match="global state"):
        rollout.begin(
            _joint_pending(
                (
                    _pending(0, global_state=invalid_state),
                    _pending(1, global_state=invalid_state),
                )
            )
        )

    assert rollout.pending is None
    assert rollout.invalid is False


@pytest.mark.parametrize(
    "invalid_next_state",
    [
        _state_with_observations(np.ones((2, 3), dtype=np.float32)),
        _state(10.0, schema="unexpected_schema"),
        _state(
            10.0,
            agent_mask=np.array([True, False], dtype=np.bool_),
        ),
    ],
    ids=("local-observation-width", "schema-label", "partial-agent-mask"),
)
def test_complete_rejects_successor_outside_configured_state_schema(
    invalid_next_state: CentralizedState,
) -> None:
    rollout = JointExecutionAlignedRollout(num_agents=NUM_AGENTS)
    _begin_and_confirm_all(rollout)

    with pytest.raises(ValueError, match="next global state"):
        rollout.complete(
            next_local_observations=_next_local_observations(),
            next_global_state=invalid_next_state,
            next_values=(0.75, 0.75),
            team_reward=-0.25,
            team_raw_reward=-0.4,
            window_end_s=WINDOW_END_S,
        )

    assert rollout.pending is not None
    assert rollout.invalid is False


def test_shared_value_begin_rejects_differing_current_values() -> None:
    shared_state = _state()
    rollout = JointExecutionAlignedRollout(
        num_agents=NUM_AGENTS, require_shared_values=True
    )
    joint_pending = _joint_pending(
        (
            _pending(0, global_state=shared_state, value=0.5),
            _pending(1, global_state=shared_state, value=0.6),
        )
    )

    with pytest.raises(ValueError, match="shared current value"):
        rollout.begin(joint_pending)

    assert rollout.pending is None


def test_local_value_mode_permits_distinct_owner_local_current_and_next_values() -> None:
    shared_state = _state()
    rollout = JointExecutionAlignedRollout(
        num_agents=NUM_AGENTS, require_shared_values=False
    )
    rollout.begin(
        _joint_pending(
            (
                _pending(0, global_state=shared_state, value=0.5),
                _pending(1, global_state=shared_state, value=0.6),
            )
        )
    )
    rollout.confirm_applied(agent_index=0, phase=2, time_s=18.0)
    rollout.confirm_applied(agent_index=1, phase=3, time_s=19.0)

    joint = _complete(rollout, next_values=(0.75, 0.85))

    assert tuple(item.value for item in joint.agent_transitions) == (0.5, 0.6)
    assert tuple(item.next_value for item in joint.agent_transitions) == (
        0.75,
        0.85,
    )


def test_confirm_applied_delegates_exact_requested_phase_validation() -> None:
    rollout = JointExecutionAlignedRollout(num_agents=NUM_AGENTS)
    rollout.begin(_joint_pending())

    with pytest.raises(
        ActionAlignmentError, match=r"requested phase 3.*applied phase 2"
    ):
        rollout.confirm_applied(agent_index=1, phase=2, time_s=18.0)

    assert rollout.invalid is True
    assert "agent 1" in (rollout.invalid_reason or "")
    assert rollout.pending is None


def test_alignment_failure_invalidates_the_whole_joint_rollout() -> None:
    rollout = JointExecutionAlignedRollout(num_agents=NUM_AGENTS)
    rollout.begin(_joint_pending())
    rollout.confirm_applied(agent_index=0, phase=2, time_s=18.0)

    with pytest.raises(ActionAlignmentError):
        rollout.confirm_applied(agent_index=1, phase=99, time_s=19.0)

    assert rollout.invalid is True
    assert rollout.pending is None
    with pytest.raises(RuntimeError, match="invalid"):
        rollout.confirm_applied(agent_index=0, phase=2, time_s=20.0)
    with pytest.raises(RuntimeError, match="invalid"):
        _complete(rollout)


def test_complete_requires_every_requested_phase_to_be_confirmed() -> None:
    rollout = JointExecutionAlignedRollout(num_agents=NUM_AGENTS)
    rollout.begin(_joint_pending())
    rollout.confirm_applied(agent_index=0, phase=2, time_s=18.0)

    with pytest.raises(RuntimeError, match="agent 1.*not been confirmed"):
        _complete(rollout)

    assert rollout.pending is not None
    assert rollout.invalid is False


def test_complete_rejects_missing_next_local_observation() -> None:
    rollout = JointExecutionAlignedRollout(num_agents=NUM_AGENTS)
    _begin_and_confirm_all(rollout)

    with pytest.raises(ValueError, match="next local observation"):
        rollout.complete(
            next_local_observations=(_next_local_observations()[0],),
            next_global_state=_state(10.0),
            next_values=(0.75, 0.75),
            team_reward=-0.25,
            team_raw_reward=-0.4,
            window_end_s=WINDOW_END_S,
        )

    assert rollout.pending is not None
    assert rollout.invalid is False


def test_complete_validation_failure_does_not_partially_consume_joint_step() -> None:
    rollout = JointExecutionAlignedRollout(num_agents=NUM_AGENTS)
    _begin_and_confirm_all(rollout)
    malformed_observations = (
        _next_local_observations()[0],
        np.array([1.0, 2.0, 3.0], dtype=np.float32),
    )

    with pytest.raises(ValueError, match="next local observation shape"):
        rollout.complete(
            next_local_observations=malformed_observations,
            next_global_state=_state(10.0),
            next_values=(0.75, 0.75),
            team_reward=-0.25,
            team_raw_reward=-0.4,
            window_end_s=WINDOW_END_S,
        )

    assert rollout.pending is not None
    assert rollout.invalid is False
    joint = _complete(rollout)
    assert len(joint.agent_transitions) == NUM_AGENTS


def test_shared_value_complete_rejects_differing_next_values_atomically() -> None:
    rollout = JointExecutionAlignedRollout(
        num_agents=NUM_AGENTS, require_shared_values=True
    )
    _begin_and_confirm_all(rollout)

    with pytest.raises(ValueError, match="shared next value"):
        _complete(rollout, next_values=(0.75, 0.85))

    assert rollout.pending is not None
    assert rollout.invalid is False


def test_complete_broadcasts_literal_team_reward_and_shared_global_values() -> None:
    rollout = JointExecutionAlignedRollout(
        num_agents=NUM_AGENTS, require_shared_values=True
    )
    _begin_and_confirm_all(rollout)
    next_state = _state(10.0)

    joint = rollout.complete(
        next_local_observations=_next_local_observations(),
        next_global_state=next_state,
        next_values=(0.75, 0.75),
        team_reward=-0.25,
        team_raw_reward=-0.4,
        window_end_s=WINDOW_END_S,
        terminated=False,
        truncated=False,
    )

    assert joint.joint_step_id == JOINT_STEP_ID
    assert joint.team_reward == -0.25
    assert joint.team_raw_reward == -0.4
    assert joint.window_start_s == DECISION_TIME_S
    assert joint.window_end_s == WINDOW_END_S
    assert joint.policy_generation == POLICY_GENERATION
    assert len(joint.agent_transitions) == NUM_AGENTS
    assert tuple(item.agent_index for item in joint.agent_transitions) == (0, 1)
    assert tuple(item.requested_phase for item in joint.agent_transitions) == (
        2,
        3,
    )
    assert tuple(item.applied_phase for item in joint.agent_transitions) == (
        2,
        3,
    )
    assert tuple(item.reward for item in joint.agent_transitions) == (
        -0.25,
        -0.25,
    )
    assert tuple(item.value for item in joint.agent_transitions) == (0.5, 0.5)
    assert tuple(item.next_value for item in joint.agent_transitions) == (
        0.75,
        0.75,
    )
    assert joint.team_value == 0.5
    assert joint.next_team_value == 0.75
    assert all(
        item.global_state is joint.global_state
        for item in joint.agent_transitions
    )
    assert all(
        item.next_global_state is joint.next_global_state
        for item in joint.agent_transitions
    )
    assert joint.next_global_state is not next_state
    assert joint.next_global_state.schema == CENTRALIZED_STATE_SCHEMA
    np.testing.assert_array_equal(
        joint.next_global_state.observations, next_state.observations
    )
    assert not np.shares_memory(
        joint.next_global_state.observations, next_state.observations
    )
    assert next_state.observations.flags.writeable is True
    assert rollout.pending is None


@pytest.mark.parametrize(
    ("terminated", "truncated"),
    [(False, False), (True, False), (False, True)],
)
def test_joint_done_flags_are_broadcast_uniformly(
    terminated: bool, truncated: bool
) -> None:
    rollout = JointExecutionAlignedRollout(num_agents=NUM_AGENTS)
    _begin_and_confirm_all(rollout)

    joint = _complete(
        rollout, terminated=terminated, truncated=truncated
    )

    assert joint.terminated is terminated
    assert joint.truncated is truncated
    assert tuple(item.terminated for item in joint.agent_transitions) == (
        terminated,
        terminated,
    )
    assert tuple(item.truncated for item in joint.agent_transitions) == (
        truncated,
        truncated,
    )


def test_joint_done_flags_are_mutually_exclusive() -> None:
    rollout = JointExecutionAlignedRollout(num_agents=NUM_AGENTS)
    _begin_and_confirm_all(rollout)

    with pytest.raises(ValueError, match="both terminated and truncated"):
        _complete(rollout, terminated=True, truncated=True)

    assert rollout.pending is not None
    assert rollout.invalid is False


def _valid_joint() -> JointTransition:
    rollout = JointExecutionAlignedRollout(num_agents=NUM_AGENTS)
    _begin_and_confirm_all(rollout)
    return _complete(rollout)


@pytest.mark.parametrize(
    "mutation",
    [
        lambda joint: replace(joint, values=()),
        lambda joint: replace(joint, values=(0.5,)),
        lambda joint: replace(joint, agent_transitions=()),
        lambda joint: replace(joint, values=(0.5, 0.6)),
        lambda joint: replace(
            joint,
            window_start_s=20.0,
            window_end_s=19.0,
        ),
        lambda joint: replace(joint, terminated=True, truncated=True),
        lambda joint: replace(
            joint,
            agent_transitions=(
                replace(joint.agent_transitions[0], reward=99.0),
                joint.agent_transitions[1],
            ),
        ),
        lambda joint: replace(
            joint,
            agent_transitions=(
                replace(
                    joint.agent_transitions[0],
                    policy_generation=joint.policy_generation + 1,
                ),
                joint.agent_transitions[1],
            ),
        ),
    ],
    ids=(
        "empty-values",
        "value-count",
        "empty-children",
        "unequal-shared-values",
        "reversed-window",
        "both-done",
        "child-reward",
        "child-generation",
    ),
)
def test_direct_joint_transition_construction_cannot_bypass_invariants(
    mutation,
) -> None:
    joint = _valid_joint()

    with pytest.raises(ValueError):
        mutation(joint)


def test_published_joint_arrays_are_deeply_immutable() -> None:
    joint = _valid_joint()
    published_arrays = (
        joint.global_state.observations,
        joint.global_state.agent_mask,
        joint.next_global_state.observations,
        joint.next_global_state.agent_mask,
        joint.agent_transitions[0].local_obs,
        joint.agent_transitions[0].phase_features,
        joint.agent_transitions[0].action_mask,
        joint.agent_transitions[0].next_local_obs,
    )

    for array in published_arrays:
        with pytest.raises(ValueError, match="read-only"):
            array.flat[0] = array.flat[0]


def _minimal_transition(agent_index: int, reward: float = 0.0) -> Transition:
    return Transition(
        local_obs=np.zeros(2, dtype=np.float32),
        phase_features=np.zeros((3, 2), dtype=np.float32),
        action_mask=np.ones(3, dtype=bool),
        global_state=_state(),
        agent_index=agent_index,
        action=0,
        requested_phase=0,
        applied_phase=0,
        log_prob=0.0,
        value=1.0,
        reward=reward,
        decision_time_s=0.0,
        applied_time_s=0.0,
        policy_generation=0,
        next_local_obs=np.zeros(2, dtype=np.float32),
        next_global_state=_state(10.0),
        next_value=1.0,
        terminated=False,
        truncated=False,
    )


def test_joint_transition_carries_local_rewards():
    raw = (1.0, -5.0)
    clipped = (1.0, -3.0)
    team = float(np.clip(np.mean(raw), REWARD_MIN, REWARD_MAX))
    jt = JointTransition(
        joint_step_id=0,
        global_state=_state(),
        next_global_state=_state(10.0),
        values=(1.0, 1.0),
        next_values=(1.0, 1.0),
        team_reward=team,
        team_raw_reward=team,
                raw_local_rewards=raw,
                local_rewards=clipped,
        window_start_s=0.0,
        window_end_s=10.0,
        terminated=False,
        truncated=False,
        policy_generation=0,
        agent_transitions=(
            _minimal_transition(0, reward=team),
            _minimal_transition(1, reward=team),
        ),
        require_shared_values=False,
        team_value_mode="mean_of_values",
    )
    assert jt.local_rewards == clipped
    assert abs(jt.team_value - np.mean((1.0, 1.0))) < 1e-7


def test_local_rewards_must_match_team_reward():
    with pytest.raises(ValueError):
        JointTransition(
            joint_step_id=0,
            global_state=_state(),
            next_global_state=_state(10.0),
            values=(1.0, 1.0),
            next_values=(1.0, 1.0),
            team_reward=0.0,
            team_raw_reward=0.0,
            raw_local_rewards=(1.0, 1.0),
            local_rewards=(1.0, 1.0),
            window_start_s=0.0,
            window_end_s=10.0,
            terminated=False,
            truncated=False,
            policy_generation=0,
            agent_transitions=(
                _minimal_transition(0),
                _minimal_transition(1),
            ),
            require_shared_values=False,
        )
