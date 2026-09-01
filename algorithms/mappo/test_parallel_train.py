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
from traffic_control.common.environment_contract import JOINT_PERIODS
from algorithms.mappo.parallel_train import (
    BatchValidationError,
    _pack_ppo_batch,
    _pad_global_to_identity_slots,
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


JOINT_POLICY_SPEC = {
    "algorithm_family": "mappo_cooperative_ppo",
    "identity_slots": [f"demo_{index}" for index in range(1, 21)],
    "obs_dim": 132,
    "phase_feature_dim": 11,
    "max_action_dim": 4,
    "saturation_flow_per_lane": 0.5,
}


def _environment_metadata(period: str, *, phase_count: int = 2) -> dict:
    intersection_id = "demo_4"
    return {
        "protocol_version": "2.0",
        "period": period,
        "episode_id": f"episode-{period}",
        "seed": 100,
        "decision_interval": 5.0,
        "minimum_green": 5.0,
        "intersections": {
            intersection_id: {
                "intersection_id": intersection_id,
                "incoming_lanes": ["in_0"],
                "outgoing_lanes": ["out_0"],
                "lanes": {
                    "in_0": {
                        "edge_id": "in",
                        "lane_index": 0,
                        "role": "incoming",
                        "length_m": 100.0,
                        "speed_limit_mps": 13.9,
                    },
                    "out_0": {
                        "edge_id": "out",
                        "lane_index": 0,
                        "role": "outgoing",
                        "length_m": 100.0,
                        "speed_limit_mps": 13.9,
                    },
                },
                "connections": [
                    {"connection_id": "c0", "from_lane": "in_0", "to_lane": "out_0"}
                ],
                "direct_neighbors": [],
                "phase_order": list(range(phase_count)),
                "phases": {
                    str(index): {
                        "phase_id": index,
                        "name": f"{period}-{index}",
                        "movement": "through" if index % 2 == 0 else "left",
                        "approaches": ["west"],
                        "green_seconds": 20.0 + index,
                        "yellow_seconds": 3.0,
                        "clearance_seconds": 2.0,
                        "connection_priorities": {"c0": "protected"},
                    }
                    for index in range(phase_count)
                },
            }
        },
    }


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
        period="off_peak",
        metadata={"period": "off_peak"},
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


def _joint_workers(count: int) -> tuple[list[WorkerRollout], tuple[int, ...], tuple[str, ...]]:
    seeds = tuple(range(94000, 94000 + count))
    periods = tuple(JOINT_PERIODS[index % len(JOINT_PERIODS)] for index in range(count))
    phase_counts = {
        "morning_peak": 4,
        "off_peak": 3,
        "evening_peak": 4,
    }
    workers = [
        replace(
            _worker(seed),
            period=period,
            metadata=_environment_metadata(period, phase_count=phase_counts[period]),
        )
        for seed, period in zip(seeds, periods, strict=True)
    ]
    return workers, seeds, periods


def _validate_joint_workers(
    workers: list[WorkerRollout],
    seeds: tuple[int, ...],
    periods: tuple[str, ...],
) -> tuple[WorkerRollout, ...]:
    return validate_worker_batch(
        workers,
        expected_generation=0,
        expected_policy_digest="policy-abc",
        expected_seeds=seeds,
        expected_config_signature=CONFIG_SIGNATURE,
        expected_local_observation_schema=LOCAL_SCHEMA,
        expected_centralized_state_schema=CENTRALIZED_STATE_SCHEMA,
        expected_periods=periods,
        policy_spec=JOINT_POLICY_SPEC,
    )


def test_successful_worker_requires_period_and_metadata() -> None:
    worker = replace(_worker(93001), period=None, metadata=None)

    with pytest.raises(BatchValidationError, match="period"):
        _validate([worker, _worker(93002)])


def test_failed_worker_can_omit_metadata_but_requires_scheduled_period() -> None:
    failed = replace(
        _worker(93002, status="error"),
        period="off_peak",
        metadata=None,
    )

    with pytest.raises(BatchValidationError, match="worker 93002 failed"):
        _validate([_worker(93001), failed])

    missing_period = replace(failed, period=None)
    with pytest.raises(BatchValidationError, match="scheduled period"):
        _validate([_worker(93001), missing_period])


@pytest.mark.parametrize("worker_count", [6, 9, 12])
def test_joint_worker_batch_accepts_strictly_balanced_periods(worker_count: int) -> None:
    workers, seeds, periods = _joint_workers(worker_count)

    ordered = _validate_joint_workers(workers, seeds, periods)

    assert tuple(worker.period for worker in ordered) == periods


def test_joint_worker_batch_rejects_unbalanced_periods() -> None:
    workers, seeds, periods = _joint_workers(6)
    workers[-1] = replace(
        workers[-1],
        period="morning_peak",
        metadata=_environment_metadata("morning_peak", phase_count=4),
    )
    scheduled = (*periods[:-1], "morning_peak")

    with pytest.raises(BatchValidationError, match="balanced"):
        _validate_joint_workers(workers, seeds, scheduled)


def test_joint_worker_batch_rejects_scheduled_metadata_period_mismatch() -> None:
    workers, seeds, periods = _joint_workers(6)
    workers[1] = replace(
        workers[1],
        metadata=_environment_metadata("morning_peak", phase_count=4),
    )

    with pytest.raises(BatchValidationError, match="metadata period mismatch"):
        _validate_joint_workers(workers, seeds, periods)


def test_demo_4_four_three_four_phase_programs_share_one_policy_space() -> None:
    workers, seeds, periods = _joint_workers(6)

    assert _validate_joint_workers(workers, seeds, periods)


def test_environment_mismatch_does_not_build_or_update() -> None:
    workers, seeds, periods = _joint_workers(6)
    changed = dict(workers[-1].metadata)
    changed_intersections = dict(changed["intersections"])
    changed_demo = dict(changed_intersections["demo_4"])
    changed_connections = [dict(value) for value in changed_demo["connections"]]
    changed_connections[0]["to_lane"] = "in_0"
    changed_demo["connections"] = changed_connections
    changed_intersections["demo_4"] = changed_demo
    changed["intersections"] = changed_intersections
    workers[-1] = replace(workers[-1], metadata=changed)

    trainer = _RecordingTrainer()
    built: list[tuple[WorkerRollout, ...]] = []
    coordinator = CentralUpdateCoordinator(
        trainer=trainer,
        policy_generation=0,
        policy_digest="policy-abc",
        config_signature=CONFIG_SIGNATURE,
        local_observation_schema=LOCAL_SCHEMA,
        centralized_state_schema=CENTRALIZED_STATE_SCHEMA,
        digest_provider=lambda: "updated",
        batch_builder=lambda values: built.append(tuple(values)) or ("batch",),
        policy_spec=JOINT_POLICY_SPEC,
    )

    with pytest.raises(BatchValidationError, match="policy space"):
        coordinator.update_from_workers(
            workers,
            expected_seeds=seeds,
            expected_periods=periods,
        )

    assert built == []
    assert trainer.batches == []
    assert coordinator.policy_generation == 0


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
        observations=np.full((2, 132), offset, dtype=np.float32),
        agent_mask=np.array([True, True]),
        intersection_ids=("demo_1", "demo_2"),
        schema=CENTRALIZED_STATE_SCHEMA,
    )


def _mini_transition(
    agent_index: int, *, value: float = 1.0, reward: float = 0.0
) -> Transition:
    return Transition(
        local_obs=np.zeros(132, dtype=np.float32),
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
        next_local_obs=np.zeros(132, dtype=np.float32),
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
        config=MAPPOConfig(("demo_1", "demo_2")),
    )
    assert batch.advantages.shape == (4,)
    assert batch.returns.shape == (4,)
    assert batch.old_values.shape == (4,)
    assert torch.equal(
        batch.joint_step_index, torch.tensor([0, 0, 1, 1], dtype=torch.long)
    )


def _shared_state(
    offset: float = 0.0,
    intersection_ids: tuple[str, ...] = ("demo_1", "demo_2"),
):
    from algorithms.mappo.features import CentralizedState
    return CentralizedState(
        observations=np.full(
            (len(intersection_ids), 132), offset, dtype=np.float32
        ),
        agent_mask=np.ones(len(intersection_ids), dtype=bool),
        intersection_ids=intersection_ids,
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
    intersection_ids: tuple[str, ...] = ("demo_1", "demo_2"),
) -> Transition:
    return Transition(
        local_obs=np.full(132, state_offset, dtype=np.float32),
        phase_features=np.zeros((1, 11), dtype=np.float32),
        action_mask=np.ones(1, dtype=bool),
        global_state=_shared_state(state_offset, intersection_ids),
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
        next_global_state=_shared_state(next_state_offset, intersection_ids),
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
    intersection_ids: tuple[str, ...] = ("demo_1", "demo_2"),
) -> JointTransition:
    team = float(np.clip(np.mean(local_rewards), -3.0, 1.0))
    clipped = tuple(float(np.clip(v, -3.0, 1.0)) for v in local_rewards)
    return JointTransition(
        joint_step_id=joint_id,
        global_state=_shared_state(state_offset, intersection_ids),
        next_global_state=_shared_state(next_state_offset, intersection_ids),
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
                intersection_ids=intersection_ids,
            ),
            _shared_transition(
                1,
                reward=team,
                decision_time_s=state_offset,
                applied_time_s=state_offset + 0.1,
                terminated=terminated,
                state_offset=state_offset,
                next_state_offset=next_state_offset,
                intersection_ids=intersection_ids,
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


def test_pad_global_to_identity_slots_maps_subset_to_canonical_slots() -> None:
    config = MAPPOConfig(("demo_3", "demo_5"))
    state = _shared_state(offset=7.0, intersection_ids=("demo_3", "demo_5"))
    observations, agent_mask = _pad_global_to_identity_slots(
        state, config
    )

    assert observations.shape == (20, 132)
    assert agent_mask.shape == (20,)
    assert agent_mask.dtype == np.bool_
    assert agent_mask.tolist() == [
        False, False, True, False, True, *([False] * 15)
    ]
    assert np.array_equal(observations[2], np.full(132, 7.0, dtype=np.float32))
    assert np.array_equal(observations[4], np.full(132, 7.0, dtype=np.float32))
    assert np.array_equal(
        observations[0], np.zeros(132, dtype=np.float32)
    )
    assert np.array_equal(
        observations[19], np.zeros(132, dtype=np.float32)
    )


def test_pad_global_to_identity_slots_passthrough_for_full_scope() -> None:
    config = MAPPOConfig(tuple(f"demo_{i}" for i in range(1, 21)))
    full = np.arange(20 * 132, dtype=np.float32).reshape(20, 132)
    mask = np.arange(20) % 2 == 0
    state = _shared_state(offset=1.0)
    observations, agent_mask = _pad_global_to_identity_slots(
        replace(state, observations=full, agent_mask=mask), config
    )
    assert np.array_equal(observations, full)
    assert np.array_equal(agent_mask, mask)


def test_build_ppo_batch_pads_subset_global_state_to_twenty_slots() -> None:
    config = MAPPOConfig(("demo_3", "demo_5"))
    timeline = [
        _shared_joint(
            0,
            (1.0, -0.5),
            state_offset=5.0,
            next_state_offset=20.0,
            intersection_ids=("demo_3", "demo_5"),
        ),
        _shared_joint(
            1,
            (0.5, 0.25),
            state_offset=20.0,
            next_state_offset=35.0,
            terminated=True,
            intersection_ids=("demo_3", "demo_5"),
        ),
    ]
    batch = build_ppo_batch(
        [_shared_timeline_worker(timeline)], config=config
    )

    assert batch.global_obs.shape == (4, 20, 132)
    assert batch.agent_mask.shape == (4, 20)
    assert batch.agent_mask.dtype == torch.bool
    expected_mask = torch.zeros((4, 20), dtype=torch.bool)
    expected_mask[:, 2] = True
    expected_mask[:, 4] = True
    assert torch.equal(batch.agent_mask, expected_mask)
    assert torch.allclose(
        batch.global_obs[:, 2],
        torch.tensor([5.0, 5.0, 20.0, 20.0], dtype=torch.float32).unsqueeze(
            -1
        ).expand(4, 132),
    )
    assert torch.allclose(
        batch.global_obs[:, 4],
        torch.tensor([5.0, 5.0, 20.0, 20.0], dtype=torch.float32).unsqueeze(
            -1
        ).expand(4, 132),
    )
    assert torch.equal(
        batch.global_obs[:, 2], batch.global_obs[:, 4]
    )
    assert torch.allclose(
        batch.global_obs[:, 0], torch.zeros((4, 132), dtype=torch.float32)
    )
    assert torch.allclose(
        batch.global_obs[:, 19], torch.zeros((4, 132), dtype=torch.float32)
    )
