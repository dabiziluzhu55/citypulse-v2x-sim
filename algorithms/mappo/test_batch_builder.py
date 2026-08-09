from __future__ import annotations

from dataclasses import replace

import numpy as np
import pytest
import torch

import algorithms.mappo.parallel_train as parallel_train_module
from algorithms.mappo.config import (
    COOPERATIVE_MODEL_VERSION,
    MAPPOConfig,
    REWARD_SCOPE_SHARED_TEAM,
)
from algorithms.mappo.features import CentralizedState
from algorithms.mappo.joint_rollout import JointTransition
from algorithms.mappo.parallel_train import (
    BatchValidationError,
    WorkerRollout,
    build_ppo_batch,
    validate_joint_timeline,
)
from algorithms.mappo.rollout import Transition, compute_gae


COOPERATIVE_IDS = ("demo_1", "demo_2")


def _cooperative_config(
    *,
    critic_scope: str = "global",
    model_version: str = COOPERATIVE_MODEL_VERSION,
) -> MAPPOConfig:
    return MAPPOConfig(
        COOPERATIVE_IDS,
        critic_scope=critic_scope,
        model_version=model_version,
        reward_scope=REWARD_SCOPE_SHARED_TEAM,
        critic_target_scope="team_return",
    )


def _centralized_state(
    config: MAPPOConfig, *, marker: float
) -> CentralizedState:
    observations = np.zeros(
        (len(config.intersection_ids), config.obs_dim), dtype=np.float32
    )
    observations[:, 0] = marker
    observations[:, 1] = np.arange(
        len(config.intersection_ids), dtype=np.float32
    )
    return CentralizedState(
        observations=observations,
        agent_mask=np.ones(len(config.intersection_ids), dtype=np.bool_),
        intersection_ids=config.intersection_ids,
    )


def _joint_transition(
    config: MAPPOConfig,
    *,
    joint_step_id: int,
    state: CentralizedState,
    next_state: CentralizedState,
    values: tuple[float, float],
    next_values: tuple[float, float],
    team_reward: float,
    window_start_s: float,
    window_end_s: float,
    terminated: bool = False,
    truncated: bool = False,
) -> JointTransition:
    children = tuple(
        Transition(
            local_obs=np.array(
                state.observations[owner], dtype=np.float32, copy=True
            ),
            phase_features=np.full(
                (2, config.phase_feature_dim), owner, dtype=np.float32
            ),
            action_mask=np.ones(2, dtype=np.bool_),
            global_state=state,
            agent_index=owner,
            action=owner,
            requested_phase=owner,
            applied_phase=owner,
            log_prob=-0.5 - owner,
            value=values[owner],
            reward=team_reward,
            decision_time_s=window_start_s,
            applied_time_s=window_start_s,
            policy_generation=0,
            next_local_obs=np.array(
                next_state.observations[owner], dtype=np.float32, copy=True
            ),
            next_global_state=next_state,
            next_value=next_values[owner],
            terminated=terminated,
            truncated=truncated,
        )
        for owner in range(len(config.intersection_ids))
    )
    return JointTransition(
        joint_step_id=joint_step_id,
        global_state=state,
        next_global_state=next_state,
        values=values,
        next_values=next_values,
        team_reward=team_reward,
        team_raw_reward=team_reward,
        window_start_s=window_start_s,
        window_end_s=window_end_s,
        terminated=terminated,
        truncated=truncated,
        policy_generation=0,
        agent_transitions=children,
        require_shared_values=config.requires_shared_values,
    )


def _two_step_joint_timeline(
    config: MAPPOConfig,
    *,
    terminal_last: bool = False,
    literal_terminal_bootstrap: bool = False,
) -> tuple[JointTransition, JointTransition]:
    first_state = _centralized_state(config, marker=5.0)
    middle_state = _centralized_state(config, marker=20.0)
    final_state = _centralized_state(config, marker=35.0)
    if config.requires_shared_values:
        first_values = (0.2, 0.2)
        middle_values = (0.3, 0.3)
        final_values = (
            (999.0, 999.0)
            if literal_terminal_bootstrap
            else (0.4, 0.4)
        )
    else:
        first_values = (0.2, 0.5)
        middle_values = (0.3, 0.6)
        final_values = (0.4, 0.7)
    return (
        _joint_transition(
            config,
            joint_step_id=0,
            state=first_state,
            next_state=middle_state,
            values=first_values,
            next_values=middle_values,
            team_reward=1.0,
            window_start_s=5.0,
            window_end_s=20.0,
        ),
        _joint_transition(
            config,
            joint_step_id=1,
            state=middle_state,
            next_state=final_state,
            values=middle_values,
            next_values=final_values,
            team_reward=2.0,
            window_start_s=20.0,
            window_end_s=35.0,
            terminated=terminal_last,
            truncated=not terminal_last,
        ),
    )


def _timeline_with_final_window(
    config: MAPPOConfig,
    *,
    final_end_s: float,
    terminated: bool,
    truncated: bool,
) -> tuple[JointTransition, JointTransition]:
    first_state = _centralized_state(config, marker=5.0)
    middle_state = _centralized_state(config, marker=20.0)
    final_state = _centralized_state(config, marker=final_end_s)
    return (
        _joint_transition(
            config,
            joint_step_id=0,
            state=first_state,
            next_state=middle_state,
            values=(0.2, 0.2),
            next_values=(0.3, 0.3),
            team_reward=1.0,
            window_start_s=5.0,
            window_end_s=20.0,
        ),
        _joint_transition(
            config,
            joint_step_id=1,
            state=middle_state,
            next_state=final_state,
            values=(0.3, 0.3),
            next_values=(0.4, 0.4),
            team_reward=2.0,
            window_start_s=20.0,
            window_end_s=final_end_s,
            terminated=terminated,
            truncated=truncated,
        ),
    )


def _joint_worker(
    config: MAPPOConfig,
    transitions: tuple[JointTransition, ...],
    *,
    seed: int = 94001,
) -> WorkerRollout:
    return WorkerRollout(
        seed=seed,
        status="ok",
        policy_generation=0,
        policy_digest="cooperative-policy",
        config_signature="cooperative-config",
        local_observation_schema="local",
        centralized_state_schema=config.centralized_state_schema,
        transitions=transitions,
        pending_count=0,
        invalid_reason=None,
        error=None,
        dropped_pending=0,
        reward_scope=config.reward_scope,
        team_reward_schema=config.team_reward_schema,
        joint_step_schema=config.joint_step_schema,
    )


def _corrupt_child_fields(
    joint: JointTransition, owner: int, **changes: object
) -> JointTransition:
    """Simulate a damaged serialized child that bypassed construction checks."""

    children = list(joint.agent_transitions)
    children[owner] = replace(children[owner], **changes)
    object.__setattr__(joint, "agent_transitions", tuple(children))
    return joint


def _corrupt_owner_indices(
    joint: JointTransition, indices: tuple[int, int]
) -> JointTransition:
    """Simulate missing/duplicate owners in a damaged worker payload."""

    children = tuple(
        replace(child, agent_index=index)
        for child, index in zip(joint.agent_transitions, indices, strict=True)
    )
    object.__setattr__(joint, "agent_transitions", children)
    return joint


def _corrupt_joint_field(
    joint: JointTransition, field: str, value: object
) -> JointTransition:
    """Simulate a damaged serialized joint envelope."""

    object.__setattr__(joint, field, value)
    return joint


def _corrupt_joint_and_all_children(
    joint: JointTransition, **changes: object
) -> JointTransition:
    """Keep envelope/children internally equal while damaging the payload."""

    children = tuple(
        replace(child, **changes) for child in joint.agent_transitions
    )
    object.__setattr__(joint, "agent_transitions", children)
    for field, value in changes.items():
        object.__setattr__(joint, field, value)
    return joint


def test_build_ppo_batch_computes_gae_per_worker_trajectory() -> None:
    config = _cooperative_config(critic_scope="global")
    first = _timeline_with_final_window(
        config, final_end_s=35.0, terminated=False, truncated=True
    )
    second = (
        _joint_transition(
            config,
            joint_step_id=0,
            state=_centralized_state(config, marker=5.0),
            next_state=_centralized_state(config, marker=20.0),
            values=(0.7, 0.7),
            next_values=(999.0, 999.0),
            team_reward=10.0,
            window_start_s=5.0,
            window_end_s=20.0,
            terminated=True,
            truncated=False,
        ),
    )

    batch = build_ppo_batch(
        (
            _joint_worker(config, first, seed=93001),
            _joint_worker(config, second, seed=93002),
        ),
        config=config,
    )
    expected_first = compute_gae(
        rewards=np.array([1.0, 2.0], dtype=np.float32),
        values=np.array([0.2, 0.3], dtype=np.float32),
        next_values=np.array([0.3, 0.4], dtype=np.float32),
        terminated=np.array([False, False]),
        truncated=np.array([False, True]),
        gamma=config.gamma,
        gae_lambda=config.gae_lambda,
    )

    assert batch.batch_size == 6
    torch.testing.assert_close(
        batch.advantages[:4],
        torch.from_numpy(expected_first[0]).repeat_interleave(2),
    )
    torch.testing.assert_close(
        batch.returns[:4],
        torch.from_numpy(expected_first[1]).repeat_interleave(2),
    )
    # Second worker is a true terminal: advantage = 10 - 0.7 = 9.3, return = 10.
    assert batch.advantages[4:].tolist() == pytest.approx([9.3, 9.3])
    assert batch.returns[4:].tolist() == pytest.approx([10.0, 10.0])
    assert batch.local_obs[:, 0].tolist() == [5.0, 5.0, 20.0, 20.0, 5.0, 5.0]


def test_validate_joint_timeline_accepts_fixed_ids_windows_and_state_chain() -> None:
    config = _cooperative_config()
    timeline = _two_step_joint_timeline(config)

    validated = validate_joint_timeline(timeline, config)

    assert len(validated) == 2
    assert tuple(item.joint_step_id for item in validated) == (0, 1)
    assert tuple(item.window_start_s for item in validated) == (5.0, 20.0)
    assert tuple(item.window_end_s for item in validated) == (20.0, 35.0)


@pytest.mark.parametrize(
    ("indices", "message"),
    [
        ((2, 1), "missing|unexpected|owner|agent"),
        ((1, 1), "duplicate.*(owner|agent)|(owner|agent).*duplicate"),
    ],
)
def test_corrupted_owner_set_invalidates_whole_joint_worker(
    indices: tuple[int, int], message: str
) -> None:
    config = _cooperative_config()
    timeline = list(_two_step_joint_timeline(config))
    timeline[0] = _corrupt_owner_indices(timeline[0], indices)

    with pytest.raises(BatchValidationError, match=message):
        build_ppo_batch(
            (_joint_worker(config, tuple(timeline)),), config=config
        )


@pytest.mark.parametrize(
    ("damage", "message"),
    [
        ("state", "state"),
        ("reward", "reward"),
        ("done", "terminated|done"),
        ("generation", "generation"),
        ("window", "window|decision"),
    ],
)
def test_corrupted_child_payload_invalidates_whole_joint_worker(
    damage: str, message: str
) -> None:
    config = _cooperative_config()
    timeline = list(_two_step_joint_timeline(config))
    if damage == "state":
        timeline[0] = _corrupt_child_fields(
            timeline[0],
            1,
            global_state=_centralized_state(config, marker=123.0),
        )
    elif damage == "reward":
        timeline[0] = _corrupt_child_fields(timeline[0], 1, reward=-7.0)
    elif damage == "done":
        timeline[0] = _corrupt_child_fields(
            timeline[0], 1, terminated=True
        )
    elif damage == "generation":
        timeline[0] = _corrupt_child_fields(
            timeline[0], 1, policy_generation=1
        )
    else:
        timeline[0] = _corrupt_child_fields(
            timeline[0], 1, decision_time_s=6.0
        )

    with pytest.raises(BatchValidationError, match=message):
        build_ppo_batch(
            (_joint_worker(config, tuple(timeline)),), config=config
        )


def test_validate_joint_timeline_rejects_noncontiguous_joint_ids() -> None:
    config = _cooperative_config()
    first, second = _two_step_joint_timeline(config)
    second = _corrupt_joint_field(second, "joint_step_id", 2)

    with pytest.raises(BatchValidationError, match="joint.*(id|step)|sequence"):
        validate_joint_timeline((first, second), config)


def test_validate_joint_timeline_rejects_broken_window_continuity() -> None:
    config = _cooperative_config()
    first, second = _two_step_joint_timeline(config)
    shifted_second = _joint_transition(
        config,
        joint_step_id=1,
        state=second.global_state,
        next_state=second.next_global_state,
        values=(0.3, 0.3),
        next_values=(0.4, 0.4),
        team_reward=2.0,
        window_start_s=21.0,
        window_end_s=36.0,
        truncated=True,
    )

    with pytest.raises(BatchValidationError, match="window|timeline"):
        validate_joint_timeline((first, shifted_second), config)


def test_validate_joint_timeline_rejects_broken_state_continuity() -> None:
    config = _cooperative_config()
    first, second = _two_step_joint_timeline(config)
    wrong_successor = _centralized_state(config, marker=19.0)
    broken_first = _joint_transition(
        config,
        joint_step_id=0,
        state=first.global_state,
        next_state=wrong_successor,
        values=(0.2, 0.2),
        next_values=(0.3, 0.3),
        team_reward=1.0,
        window_start_s=5.0,
        window_end_s=20.0,
    )

    with pytest.raises(BatchValidationError, match="state.*continu|continu.*state"):
        validate_joint_timeline((broken_first, second), config)


def test_joint_generation_must_match_worker_generation() -> None:
    config = _cooperative_config()
    timeline = tuple(
        _corrupt_joint_and_all_children(
            joint, policy_generation=1
        )
        for joint in _two_step_joint_timeline(config)
    )

    with pytest.raises(BatchValidationError, match="generation"):
        build_ppo_batch((_joint_worker(config, timeline),), config=config)


def test_validate_joint_timeline_rejects_done_step_with_successor() -> None:
    config = _cooperative_config()
    first, second = _two_step_joint_timeline(config)
    first = _corrupt_joint_and_all_children(first, terminated=True)

    with pytest.raises(
        BatchValidationError, match="done|terminal|truncat|final"
    ):
        validate_joint_timeline((first, second), config)


def test_validate_joint_timeline_rejects_value_chain_mismatch() -> None:
    config = _cooperative_config()
    first, second = _two_step_joint_timeline(config)
    children = tuple(
        replace(child, next_value=0.8)
        for child in first.agent_transitions
    )
    object.__setattr__(first, "agent_transitions", children)
    object.__setattr__(first, "next_values", (0.8, 0.8))

    with pytest.raises(
        BatchValidationError, match="value|bootstrap|continu"
    ):
        validate_joint_timeline((first, second), config)


def test_validate_joint_timeline_rejects_non_action_interval_window() -> None:
    config = _cooperative_config()
    first_state = _centralized_state(config, marker=5.0)
    middle_state = _centralized_state(config, marker=21.0)
    final_state = _centralized_state(config, marker=36.0)
    first = _joint_transition(
        config,
        joint_step_id=0,
        state=first_state,
        next_state=middle_state,
        values=(0.2, 0.2),
        next_values=(0.3, 0.3),
        team_reward=1.0,
        window_start_s=5.0,
        window_end_s=21.0,
    )
    second = _joint_transition(
        config,
        joint_step_id=1,
        state=middle_state,
        next_state=final_state,
        values=(0.3, 0.3),
        next_values=(0.4, 0.4),
        team_reward=2.0,
        window_start_s=21.0,
        window_end_s=36.0,
        truncated=True,
    )

    with pytest.raises(
        BatchValidationError, match="duration|interval|15|window"
    ):
        validate_joint_timeline((first, second), config)


@pytest.mark.parametrize(
    ("terminated", "truncated"),
    ((True, False), (False, True)),
)
def test_validate_joint_timeline_accepts_short_final_done_window(
    terminated: bool, truncated: bool
) -> None:
    config = _cooperative_config()
    timeline = _timeline_with_final_window(
        config,
        final_end_s=30.0,
        terminated=terminated,
        truncated=truncated,
    )

    validated = validate_joint_timeline(timeline, config)

    assert validated[-1].window_end_s - validated[-1].window_start_s == 10.0
    assert validated[-1].terminated is terminated
    assert validated[-1].truncated is truncated


def test_validate_joint_timeline_rejects_overlong_final_done_window() -> None:
    config = _cooperative_config()
    timeline = _timeline_with_final_window(
        config,
        final_end_s=36.0,
        terminated=False,
        truncated=True,
    )

    with pytest.raises(
        BatchValidationError, match="duration|interval|15|window"
    ):
        validate_joint_timeline(timeline, config)


def test_validate_joint_timeline_rejects_final_step_without_done_flag() -> None:
    config = _cooperative_config()
    timeline = _timeline_with_final_window(
        config,
        final_end_s=35.0,
        terminated=False,
        truncated=False,
    )

    with pytest.raises(
        BatchValidationError, match="done|terminal|truncat|final"
    ):
        validate_joint_timeline(timeline, config)


def test_global_shared_gae_is_computed_once_then_repeated_in_owner_order() -> None:
    config = _cooperative_config(critic_scope="global")
    timeline = _two_step_joint_timeline(config)

    batch = build_ppo_batch((_joint_worker(config, timeline),), config=config)

    # Hand-derived with gamma=.99, lambda=.95:
    # delta=[1+.99*.3-.2, 2+.99*.4-.3] = [1.097, 2.096]
    # A=[1.097+.99*.95*2.096, 2.096] = [3.068288, 2.096]
    expected_advantages = torch.tensor(
        [3.068288, 3.068288, 2.096, 2.096], dtype=torch.float32
    )
    expected_returns = torch.tensor(
        [3.268288, 3.268288, 2.396, 2.396], dtype=torch.float32
    )
    torch.testing.assert_close(batch.advantages, expected_advantages)
    torch.testing.assert_close(batch.returns, expected_returns)
    assert batch.agent_index.tolist() == [0, 1, 0, 1]
    assert batch.old_values.tolist() == pytest.approx([0.2, 0.2, 0.3, 0.3])
    assert batch.joint_step_index.tolist() == [0, 0, 1, 1]


def test_global_shared_gae_calls_compute_gae_once_per_worker(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    config = _cooperative_config(critic_scope="global")
    timeline = _two_step_joint_timeline(config)
    calls: list[dict[str, np.ndarray]] = []
    original_compute_gae = parallel_train_module.compute_gae

    def recording_compute_gae(
        rewards: object,
        values: object,
        next_values: object,
        terminated: object,
        truncated: object,
        gamma: object,
        gae_lambda: object,
    ):
        calls.append(
            {
                "terminated": np.array(terminated, copy=True),
                "truncated": np.array(truncated, copy=True),
            }
        )
        return original_compute_gae(
            rewards=rewards,
            values=values,
            next_values=next_values,
            terminated=terminated,
            truncated=truncated,
            gamma=gamma,
            gae_lambda=gae_lambda,
        )

    monkeypatch.setattr(
        parallel_train_module, "compute_gae", recording_compute_gae
    )

    build_ppo_batch(
        (
            _joint_worker(config, timeline, seed=94001),
            _joint_worker(config, timeline, seed=94002),
        ),
        config=config,
    )

    assert len(calls) == 2
    for call in calls:
        assert call["terminated"].tolist() == [False, False]
        assert call["truncated"].tolist() == [False, True]


def test_true_terminal_ignores_literal_999_next_team_value() -> None:
    config = _cooperative_config(critic_scope="global")
    timeline = _two_step_joint_timeline(
        config, terminal_last=True, literal_terminal_bootstrap=True
    )

    batch = build_ppo_batch((_joint_worker(config, timeline),), config=config)

    # The terminal delta is 2-.3=1.7, irrespective of next_value=999.
    # The preceding A is 1.097+.99*.95*1.7=2.69585.
    torch.testing.assert_close(
        batch.advantages,
        torch.tensor([2.69585, 2.69585, 1.7, 1.7]),
    )
    torch.testing.assert_close(
        batch.returns,
        torch.tensor([2.89585, 2.89585, 2.0, 2.0]),
    )


def test_time_limit_truncation_bootstraps_next_team_value() -> None:
    config = _cooperative_config(critic_scope="global")
    timeline = _two_step_joint_timeline(config)

    batch = build_ppo_batch((_joint_worker(config, timeline),), config=config)

    # Final truncated step uses .4 bootstrap but ends the trace:
    # A_1=2+.99*.4-.3=2.096 and R_1=2.396.
    assert batch.advantages[-2:].tolist() == pytest.approx([2.096, 2.096])
    assert batch.returns[-2:].tolist() == pytest.approx([2.396, 2.396])


def test_local_cooperative_ippo_uses_team_reward_with_owner_local_gae() -> None:
    config = _cooperative_config(critic_scope="local")
    timeline = _two_step_joint_timeline(config)

    batch = build_ppo_batch((_joint_worker(config, timeline),), config=config)

    # Owner 0 uses V=[.2,.3], next=[.3,.4]: A=[3.068288,2.096].
    # Owner 1 uses V=[.5,.6], next=[.6,.7]: A=[3.0624665,2.093].
    # Both sequences use the identical team rewards [1,2].
    torch.testing.assert_close(
        batch.advantages,
        torch.tensor([3.068288, 3.0624665, 2.096, 2.093]),
    )
    torch.testing.assert_close(
        batch.returns,
        torch.tensor([3.268288, 3.5624665, 2.396, 2.693]),
    )
    assert batch.agent_index.tolist() == [0, 1, 0, 1]
    assert batch.joint_step_index.tolist() == [0, 0, 1, 1]


def test_local_cooperative_gae_calls_compute_gae_once_per_owner(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    config = _cooperative_config(critic_scope="local")
    timeline = _two_step_joint_timeline(config)
    calls = 0
    original_compute_gae = parallel_train_module.compute_gae

    def recording_compute_gae(*args: object, **kwargs: object):
        nonlocal calls
        calls += 1
        return original_compute_gae(*args, **kwargs)

    monkeypatch.setattr(
        parallel_train_module, "compute_gae", recording_compute_gae
    )

    build_ppo_batch((_joint_worker(config, timeline),), config=config)

    assert calls == len(config.intersection_ids)
