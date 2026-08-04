from __future__ import annotations

import numpy as np
import pytest

from algorithms.mappo.features import CentralizedState
from algorithms.mappo.rollout import (
    ActionAlignmentError,
    ExecutionAlignedRollout,
    PendingTransition,
    compute_gae,
)


def _state(offset: float = 0.0) -> CentralizedState:
    return CentralizedState(
        observations=np.array(
            [[offset, 1.0], [offset + 2.0, 3.0]], dtype=np.float32
        ),
        agent_mask=np.array([True, True]),
        intersection_ids=("demo_1", "demo_2"),
    )


def _pending(requested_phase: int = 2) -> PendingTransition:
    return PendingTransition(
        local_obs=np.array([0.1, 0.2], dtype=np.float32),
        phase_features=np.array(
            [[1.0, 0.0], [0.0, 1.0]], dtype=np.float32
        ),
        action_mask=np.array([True, True]),
        global_state=_state(),
        agent_index=0,
        action=1,
        requested_phase=requested_phase,
        log_prob=-0.25,
        value=0.5,
        decision_time_s=15.0,
        policy_generation=3,
    )


def test_transition_cannot_complete_before_requested_phase_is_applied() -> None:
    rollout = ExecutionAlignedRollout()
    rollout.begin(_pending(requested_phase=2))

    with pytest.raises(RuntimeError, match="has not been confirmed"):
        rollout.complete(
            next_local_obs=np.zeros(2, dtype=np.float32),
            next_global_state=_state(10.0),
            next_value=0.75,
        )


def test_mismatched_applied_phase_invalidates_rollout() -> None:
    rollout = ExecutionAlignedRollout()
    rollout.begin(_pending(requested_phase=2))

    with pytest.raises(
        ActionAlignmentError, match=r"requested phase 2.*applied phase 1"
    ):
        rollout.confirm_applied(applied_phase=1, simulation_time_s=20.0)

    assert rollout.invalid is True
    assert rollout.pending is None


def test_confirmed_action_retains_clearance_interval_reward() -> None:
    rollout = ExecutionAlignedRollout()
    rollout.begin(_pending(requested_phase=2))
    rollout.add_reward(-1.0)
    rollout.confirm_applied(applied_phase=2, simulation_time_s=20.0)
    rollout.add_reward(0.25)

    transition = rollout.complete(
        next_local_obs=np.array([0.3, 0.4], dtype=np.float32),
        next_global_state=_state(10.0),
        next_value=0.75,
        truncated=True,
    )

    assert transition.reward == pytest.approx(-0.75)
    assert transition.requested_phase == 2
    assert transition.applied_phase == 2
    assert transition.applied_time_s == 20.0
    assert transition.policy_generation == 3
    assert transition.terminated is False
    assert transition.truncated is True
    assert rollout.pending is None


def test_rollout_rejects_reward_after_alignment_failure() -> None:
    rollout = ExecutionAlignedRollout()
    rollout.begin(_pending(requested_phase=2))
    with pytest.raises(ActionAlignmentError):
        rollout.confirm_applied(applied_phase=1, simulation_time_s=20.0)

    with pytest.raises(RuntimeError, match="invalid"):
        rollout.add_reward(1.0)


def test_true_terminal_does_not_bootstrap() -> None:
    advantages, returns = compute_gae(
        rewards=np.array([1.0], dtype=np.float32),
        values=np.array([0.25], dtype=np.float32),
        next_values=np.array([100.0], dtype=np.float32),
        terminated=np.array([True]),
        truncated=np.array([False]),
        gamma=0.99,
        gae_lambda=0.95,
    )

    np.testing.assert_allclose(advantages, [0.75], rtol=0, atol=1e-6)
    np.testing.assert_allclose(returns, [1.0], rtol=0, atol=1e-6)


def test_time_limit_truncation_bootstraps_next_value() -> None:
    advantages, returns = compute_gae(
        rewards=np.array([1.0], dtype=np.float32),
        values=np.array([0.25], dtype=np.float32),
        next_values=np.array([2.0], dtype=np.float32),
        terminated=np.array([False]),
        truncated=np.array([True]),
        gamma=0.99,
        gae_lambda=0.95,
    )

    np.testing.assert_allclose(advantages, [2.73], rtol=0, atol=1e-6)
    np.testing.assert_allclose(returns, [2.98], rtol=0, atol=1e-6)


def test_gae_does_not_cross_a_truncation_boundary() -> None:
    advantages, returns = compute_gae(
        rewards=np.array([1.0, 2.0], dtype=np.float32),
        values=np.array([0.5, 0.25], dtype=np.float32),
        next_values=np.array([0.25, 3.0], dtype=np.float32),
        terminated=np.array([False, False]),
        truncated=np.array([False, True]),
        gamma=1.0,
        gae_lambda=1.0,
    )

    np.testing.assert_allclose(advantages, [5.5, 4.75], rtol=0, atol=1e-6)
    np.testing.assert_allclose(returns, [6.0, 5.0], rtol=0, atol=1e-6)


def test_gae_rejects_transition_marked_terminal_and_truncated() -> None:
    with pytest.raises(ValueError, match="both terminated and truncated"):
        compute_gae(
            rewards=np.array([1.0], dtype=np.float32),
            values=np.array([0.0], dtype=np.float32),
            next_values=np.array([0.0], dtype=np.float32),
            terminated=np.array([True]),
            truncated=np.array([True]),
            gamma=0.99,
            gae_lambda=0.95,
        )
