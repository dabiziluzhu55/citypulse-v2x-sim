from __future__ import annotations

import json
from dataclasses import replace

import pytest

import numpy as np
import torch

from algorithms.mappo.config import (
    JOINT_STEP_SCHEMA,
    REWARD_SCOPE_SHARED_TEAM,
    TEAM_REWARD_SCHEMA,
)
from algorithms.mappo.features import CENTRALIZED_STATE_SCHEMA
from algorithms.mappo.parallel_train import (
    BatchValidationError,
    _pack_ppo_batch,
    build_ppo_batch,
    CentralUpdateCoordinator,
    WorkerRollout,
    validate_worker_batch,
)


from algorithms.mappo.config import MAPPOConfig
from algorithms.mappo.joint_rollout import JointTransition
from algorithms.mappo.rollout import Transition

LOCAL_SCHEMA = "ippo_v8_local_obs_v1"
CONFIG_SIGNATURE = "frozen-v8-config"


def _worker(
    seed: int,
    *,
    status: str = "ok",
    generation: int = 0,
    digest: str = "policy-abc",
) -> WorkerRollout:
    return WorkerRollout(
        seed=seed,
        status=status,
        policy_generation=generation,
        policy_digest=digest,
        config_signature=CONFIG_SIGNATURE,
        local_observation_schema=LOCAL_SCHEMA,
        centralized_state_schema=CENTRALIZED_STATE_SCHEMA,
        transitions=(f"sample-{seed}",),
        pending_count=0,
        invalid_reason=None,
        error=None if status == "ok" else "SUMO failed",
    )


def _validate(results: list[WorkerRollout]) -> tuple[WorkerRollout, ...]:
    return validate_worker_batch(
        results,
        expected_generation=0,
        expected_policy_digest="policy-abc",
        expected_seeds=(93001, 93002),
        expected_config_signature=CONFIG_SIGNATURE,
        expected_local_observation_schema=LOCAL_SCHEMA,
        expected_centralized_state_schema=CENTRALIZED_STATE_SCHEMA,
    )


def _shared_worker(seed: int, **changes: object) -> WorkerRollout:
    worker = replace(
        _worker(seed),
        reward_scope=REWARD_SCOPE_SHARED_TEAM,
        team_reward_schema=TEAM_REWARD_SCHEMA,
        joint_step_schema=JOINT_STEP_SCHEMA,
    )
    return replace(worker, **changes)


def _validate_shared(
    results: list[WorkerRollout],
) -> tuple[WorkerRollout, ...]:
    return validate_worker_batch(
        results,
        expected_generation=0,
        expected_policy_digest="policy-abc",
        expected_seeds=(93001, 93002),
        expected_config_signature=CONFIG_SIGNATURE,
        expected_local_observation_schema=LOCAL_SCHEMA,
        expected_centralized_state_schema=CENTRALIZED_STATE_SCHEMA,
        expected_reward_scope=REWARD_SCOPE_SHARED_TEAM,
        expected_team_reward_schema=TEAM_REWARD_SCHEMA,
        expected_joint_step_schema=JOINT_STEP_SCHEMA,
    )


def test_generation_mismatch_invalidates_complete_batch() -> None:
    results = [_worker(93001), _worker(93002, generation=1)]

    with pytest.raises(BatchValidationError, match="policy generation"):
        _validate(results)


def test_policy_digest_mismatch_invalidates_complete_batch() -> None:
    results = [_worker(93001), _worker(93002, digest="policy-new")]

    with pytest.raises(BatchValidationError, match="policy digest"):
        _validate(results)


def test_failed_worker_invalidates_complete_batch() -> None:
    results = [_worker(93001), _worker(93002, status="error")]

    with pytest.raises(BatchValidationError, match="worker 93002"):
        _validate(results)


def test_missing_worker_seed_invalidates_complete_batch() -> None:
    with pytest.raises(BatchValidationError, match="missing seeds: 93002"):
        _validate([_worker(93001)])


def test_duplicate_worker_seed_invalidates_complete_batch() -> None:
    with pytest.raises(BatchValidationError, match="duplicate worker seed: 93001"):
        _validate([_worker(93001), _worker(93001), _worker(93002)])


def test_empty_worker_rollout_invalidates_complete_batch() -> None:
    results = [_worker(93001), replace(_worker(93002), transitions=())]

    with pytest.raises(BatchValidationError, match="worker 93002.*no transitions"):
        _validate(results)


def test_unresolved_pending_action_invalidates_complete_batch() -> None:
    results = [_worker(93001), replace(_worker(93002), pending_count=1)]

    with pytest.raises(BatchValidationError, match="worker 93002.*pending"):
        _validate(results)


def test_schema_mismatch_invalidates_complete_batch() -> None:
    results = [
        _worker(93001),
        replace(_worker(93002), centralized_state_schema="wrong-schema"),
    ]

    with pytest.raises(BatchValidationError, match="centralized state schema"):
        _validate(results)


@pytest.mark.parametrize(
    ("field", "wrong_value", "message"),
    [
        ("reward_scope", "local", "reward scope"),
        ("team_reward_schema", "wrong-team", "team reward schema"),
        ("joint_step_schema", "wrong-joint", "joint step schema"),
    ],
)
def test_shared_worker_objective_schema_mismatch_invalidates_complete_batch(
    field: str, wrong_value: str, message: str
) -> None:
    workers = [_shared_worker(93001), _shared_worker(93002)]
    workers[1] = replace(workers[1], **{field: wrong_value})

    with pytest.raises(BatchValidationError, match=message):
        _validate_shared(workers)


def test_shared_worker_dropped_pending_invalidates_complete_batch() -> None:
    workers = [
        _shared_worker(93001),
        _shared_worker(93002, dropped_pending=1),
    ]

    with pytest.raises(BatchValidationError, match="worker 93002.*dropped"):
        _validate_shared(workers)


def test_valid_shared_batch_returns_expected_seed_order() -> None:
    ordered = _validate_shared(
        [_shared_worker(93002), _shared_worker(93001)]
    )

    assert tuple(worker.seed for worker in ordered) == (93001, 93002)


def test_valid_batch_is_returned_in_expected_seed_order() -> None:
    ordered = _validate([_worker(93002), _worker(93001)])

    assert tuple(worker.seed for worker in ordered) == (93001, 93002)
    assert tuple(worker.transitions[0] for worker in ordered) == (
        "sample-93001",
        "sample-93002",
    )


class _RecordingTrainer:
    def __init__(self) -> None:
        self.batches: list[tuple[str, ...]] = []

    def update(self, batch: tuple[str, ...]) -> dict[str, float]:
        self.batches.append(batch)
        return {"actor_loss": 1.25}


def _coordinator(trainer: _RecordingTrainer) -> CentralUpdateCoordinator:
    return CentralUpdateCoordinator(
        trainer=trainer,
        policy_generation=0,
        policy_digest="policy-abc",
        config_signature=CONFIG_SIGNATURE,
        local_observation_schema=LOCAL_SCHEMA,
        centralized_state_schema=CENTRALIZED_STATE_SCHEMA,
        digest_provider=lambda: "policy-after-update",
        batch_builder=lambda workers: tuple(
            transition
            for worker in workers
            for transition in worker.transitions
        ),
    )


def _shared_coordinator(
    trainer: _RecordingTrainer,
) -> CentralUpdateCoordinator:
    return CentralUpdateCoordinator(
        trainer=trainer,
        policy_generation=0,
        policy_digest="policy-abc",
        config_signature=CONFIG_SIGNATURE,
        local_observation_schema=LOCAL_SCHEMA,
        centralized_state_schema=CENTRALIZED_STATE_SCHEMA,
        reward_scope=REWARD_SCOPE_SHARED_TEAM,
        team_reward_schema=TEAM_REWARD_SCHEMA,
        joint_step_schema=JOINT_STEP_SCHEMA,
        digest_provider=lambda: "shared-policy-after-update",
        batch_builder=lambda workers: tuple(
            transition
            for worker in workers
            for transition in worker.transitions
        ),
    )


def test_invalid_batch_does_not_call_learner_or_advance_generation() -> None:
    trainer = _RecordingTrainer()
    coordinator = _coordinator(trainer)

    with pytest.raises(BatchValidationError):
        coordinator.update_from_workers(
            [_worker(93001), _worker(93002, status="error")],
            expected_seeds=(93001, 93002),
        )

    assert trainer.batches == []
    assert coordinator.policy_generation == 0
    assert coordinator.policy_digest == "policy-abc"


def test_valid_batch_calls_central_learner_once_and_advances_generation() -> None:
    trainer = _RecordingTrainer()
    coordinator = _coordinator(trainer)

    diagnostics = coordinator.update_from_workers(
        [_worker(93002), _worker(93001)],
        expected_seeds=(93001, 93002),
    )

    assert trainer.batches == [("sample-93001", "sample-93002")]
    assert coordinator.policy_generation == 1
    assert coordinator.policy_digest == "policy-after-update"
    assert diagnostics == {"actor_loss": 1.25, "policy_generation": 1}


@pytest.mark.parametrize(
    "changes",
    [
        {"reward_scope": "local"},
        {"team_reward_schema": "wrong-team"},
        {"joint_step_schema": "wrong-joint"},
        {"dropped_pending": 1},
    ],
)
def test_invalid_shared_batch_does_not_reach_central_learner(
    changes: dict[str, object],
) -> None:
    trainer = _RecordingTrainer()
    coordinator = _shared_coordinator(trainer)
    workers = [_shared_worker(93001), _shared_worker(93002, **changes)]

    with pytest.raises(BatchValidationError):
        coordinator.update_from_workers(
            workers, expected_seeds=(93001, 93002)
        )

    assert trainer.batches == []
    assert coordinator.policy_generation == 0
    assert coordinator.policy_digest == "policy-abc"


def test_valid_shared_batch_reaches_central_learner_once() -> None:
    trainer = _RecordingTrainer()
    coordinator = _shared_coordinator(trainer)

    diagnostics = coordinator.update_from_workers(
        [_shared_worker(93002), _shared_worker(93001)],
        expected_seeds=(93001, 93002),
    )

    assert trainer.batches == [("sample-93001", "sample-93002")]
    assert coordinator.policy_generation == 1
    assert coordinator.policy_digest == "shared-policy-after-update"
    assert diagnostics == {"actor_loss": 1.25, "policy_generation": 1}


def _mini_state(offset: float = 0.0):
    from algorithms.mappo.features import CENTRALIZED_STATE_SCHEMA, CentralizedState
    return CentralizedState(
        observations=np.array(
            [[offset, 1.0], [offset + 2.0, 3.0]], dtype=np.float32
        ),
        agent_mask=np.array([True, True]),
        intersection_ids=("demo_1", "demo_2"),
        schema=CENTRALIZED_STATE_SCHEMA,
    )


def _mini_transition(
    agent_index: int, *, value: float = 1.0, reward: float = 0.0
) -> Transition:
    return Transition(
        local_obs=np.zeros(2, dtype=np.float32),
        phase_features=np.zeros((1, 3), dtype=np.float32),
        action_mask=np.ones(1, dtype=bool),
        global_state=_mini_state(),
        agent_index=agent_index,
        action=0,
        requested_phase=0,
        applied_phase=0,
        log_prob=0.0,
        value=value,
        reward=reward,
        decision_time_s=0.0,
        applied_time_s=0.0,
        policy_generation=0,
        next_local_obs=np.zeros(2, dtype=np.float32),
        next_global_state=_mini_state(10.0),
        next_value=value,
        terminated=False,
        truncated=False,
    )


def _mini_joint(
    joint_id: int,
    local_rewards: tuple[float, float],
    values: tuple[float, float] = (1.0, 2.0),
) -> JointTransition:
    team = float(np.clip(np.mean(local_rewards), -3.0, 1.0))
    clipped = tuple(float(np.clip(v, -3.0, 1.0)) for v in local_rewards)
    return JointTransition(
        joint_step_id=joint_id,
        global_state=_mini_state(),
        next_global_state=_mini_state(10.0),
        values=values,
        next_values=values,
        team_reward=team,
        team_raw_reward=float(np.mean(local_rewards)),
        window_start_s=0.0,
        window_end_s=5.0,
        terminated=False,
        truncated=False,
        policy_generation=0,
        agent_transitions=(
            _mini_transition(0, value=values[0], reward=team),
            _mini_transition(1, value=values[1], reward=team),
        ),
        require_shared_values=False,
        raw_local_rewards=local_rewards,
        local_rewards=clipped,
        team_value_mode="mean_of_values",
    )


def test_pack_ppo_batch_builds_expected_batch() -> None:
    timeline = [_mini_joint(0, (1.0, -0.5)), _mini_joint(1, (0.5, 0.25))]
    transitions = [t for joint in timeline for t in joint.agent_transitions]
    batch = _pack_ppo_batch(
        ordered_transitions=transitions,
        ordered_advantages=[1.0, 1.0, 2.0, 2.0],
        ordered_returns=[1.0, 1.0, 2.0, 2.0],
        joint_step_indices=[0, 0, 1, 1],
    )
    assert batch.advantages.shape == (4,)
    assert batch.returns.shape == (4,)
    assert batch.old_values.shape == (4,)
    assert torch.equal(
        batch.joint_step_index, torch.tensor([0, 0, 1, 1], dtype=torch.long)
    )


def _shared_state(offset: float = 0.0):
    from algorithms.mappo.features import CentralizedState
    return CentralizedState(
        observations=np.full((2, 132), offset, dtype=np.float32),
        agent_mask=np.array([True, True]),
        intersection_ids=("demo_1", "demo_2"),
        schema=CENTRALIZED_STATE_SCHEMA,
    )


def _shared_transition(
    agent_index: int,
    *,
    value: float = 1.5,
    reward: float = 0.0,
    decision_time_s: float = 0.0,
    applied_time_s: float = 0.0,
    terminated: bool = False,
    state_offset: float = 0.0,
    next_state_offset: float = 0.0,
) -> Transition:
    return Transition(
        local_obs=np.full(132, state_offset, dtype=np.float32),
        phase_features=np.zeros((1, 11), dtype=np.float32),
        action_mask=np.ones(1, dtype=bool),
        global_state=_shared_state(state_offset),
        agent_index=agent_index,
        action=0,
        requested_phase=0,
        applied_phase=0,
        log_prob=0.0,
        value=value,
        reward=reward,
        decision_time_s=decision_time_s,
        applied_time_s=applied_time_s,
        policy_generation=0,
        next_local_obs=np.full(132, next_state_offset, dtype=np.float32),
        next_global_state=_shared_state(next_state_offset),
        next_value=value,
        terminated=terminated,
        truncated=False,
    )


def _shared_joint(
    joint_id: int,
    local_rewards: tuple[float, float],
    *,
    state_offset: float,
    next_state_offset: float,
    terminated: bool = False,
) -> JointTransition:
    team = float(np.clip(np.mean(local_rewards), -3.0, 1.0))
    clipped = tuple(float(np.clip(v, -3.0, 1.0)) for v in local_rewards)
    return JointTransition(
        joint_step_id=joint_id,
        global_state=_shared_state(state_offset),
        next_global_state=_shared_state(next_state_offset),
        values=(1.5, 1.5),
        next_values=(1.5, 1.5),
        team_reward=team,
        team_raw_reward=float(np.mean(local_rewards)),
        window_start_s=state_offset,
        window_end_s=next_state_offset,
        terminated=terminated,
        truncated=False,
        policy_generation=0,
        agent_transitions=(
            _shared_transition(
                0,
                reward=team,
                decision_time_s=state_offset,
                applied_time_s=state_offset + 0.1,
                terminated=terminated,
                state_offset=state_offset,
                next_state_offset=next_state_offset,
            ),
            _shared_transition(
                1,
                reward=team,
                decision_time_s=state_offset,
                applied_time_s=state_offset + 0.1,
                terminated=terminated,
                state_offset=state_offset,
                next_state_offset=next_state_offset,
            ),
        ),
        require_shared_values=True,
        raw_local_rewards=local_rewards,
        local_rewards=clipped,
        team_value_mode="scalar",
    )


def _shared_timeline() -> list[JointTransition]:
    return [
        _shared_joint(
            0,
            (1.0, -0.5),
            state_offset=0.0,
            next_state_offset=15.0,
        ),
        _shared_joint(
            1,
            (0.5, 0.25),
            state_offset=15.0,
            next_state_offset=30.0,
            terminated=True,
        ),
    ]


def _shared_timeline_worker(
    timeline: list[JointTransition], *, seed: int = 0
) -> WorkerRollout:
    return WorkerRollout(
        seed=seed,
        status="complete",
        policy_generation=0,
        policy_digest="policy-abc",
        config_signature=CONFIG_SIGNATURE,
        local_observation_schema=LOCAL_SCHEMA,
        centralized_state_schema=CENTRALIZED_STATE_SCHEMA,
        transitions=timeline,
        pending_count=0,
        invalid_reason=None,
        error=None,
        dropped_pending=0,
        reward_scope=REWARD_SCOPE_SHARED_TEAM,
        team_reward_schema=TEAM_REWARD_SCHEMA,
        joint_step_schema=JOINT_STEP_SCHEMA,
    )


def test_build_shared_team_batch_end_to_end() -> None:
    config = MAPPOConfig(("demo_1", "demo_2"))
    batch = build_ppo_batch(
        [_shared_timeline_worker(_shared_timeline())], config=config
    )
    assert batch.advantages.shape == (4,)
    assert torch.equal(
        batch.joint_step_index, torch.tensor([0, 0, 1, 1], dtype=torch.long)
    )
    assert torch.allclose(
        batch.old_values, torch.full((4,), 1.5, dtype=torch.float32)
    )
    assert torch.allclose(
        batch.advantages[:2], batch.advantages[:2][0].expand(2)
    )
    assert torch.allclose(
        batch.advantages[2:], batch.advantages[2].expand(2)
    )
    assert torch.allclose(
        batch.returns[:2], batch.returns[:2][0].expand(2)
    )
    assert torch.allclose(
        batch.returns[2:], batch.returns[2].expand(2)
    )
