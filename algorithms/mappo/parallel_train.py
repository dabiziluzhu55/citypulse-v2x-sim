from __future__ import annotations

import json
from collections import Counter
from copy import deepcopy
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Iterable, Mapping, Sequence

import numpy as np
import torch

from algorithms.mappo.config import (
    JOINT_STEP_SCHEMA,
    MAPPOConfig,
    REWARD_SCOPE_SHARED_TEAM,
    TEAM_REWARD_SCHEMA,
)
from algorithms.mappo.features import CentralizedState
from algorithms.mappo.joint_rollout import JointTransition
from algorithms.mappo.rollout import Transition, compute_gae
from algorithms.mappo.trainer import PPOBatch
from traffic_control.common.environment_contract import (
    EnvironmentContractError,
    build_environment_contract,
    validate_balanced_period_batch,
    validate_checkpoint_environment,
    validate_contract_integrity,
)
from traffic_control.ippo.identity import IDENTITY_SLOT_IDS, identity_slots_for


class BatchValidationError(RuntimeError):
    pass


@dataclass(frozen=True)
class WorkerRollout:
    seed: int
    status: str
    policy_generation: int
    policy_digest: str
    config_signature: str
    local_observation_schema: str
    centralized_state_schema: str
    transitions: tuple[Any, ...]
    pending_count: int
    invalid_reason: str | None
    error: str | None
    dropped_pending: int = 0
    action_diagnostics: Mapping[str, object] | None = None
    reward_diagnostics: Mapping[str, object] | None = None
    reward_scope: str = REWARD_SCOPE_SHARED_TEAM
    team_reward_schema: str = TEAM_REWARD_SCHEMA
    joint_step_schema: str = JOINT_STEP_SCHEMA
    period: str | None = None
    metadata: Mapping[str, Any] | None = None


def build_ppo_batch(
    workers: Iterable[WorkerRollout],
    *,
    config: MAPPOConfig,
) -> PPOBatch:
    """Merge workers into one cooperative PPO batch."""

    worker_values = tuple(workers)
    if not worker_values:
        raise ValueError("cannot build PPO batch from no workers")
    return _pack_ppo_batch(
        *_build_shared_team_rows(worker_values, config), config=config
    )


def _build_shared_team_rows(
    workers: tuple[WorkerRollout, ...], config: MAPPOConfig
) -> tuple[list[Transition], list[float], list[float], list[int]]:
    ordered_transitions: list[Transition] = []
    ordered_advantages: list[float] = []
    ordered_returns: list[float] = []
    joint_step_indices: list[int] = []
    action_dimension: int | None = None
    next_global_joint_index = 0
    num_agents = len(config.intersection_ids)

    for worker in workers:
        _validate_worker_objective(worker, config)
        timeline = validate_joint_timeline(worker.transitions, config)
        for joint in timeline:
            if joint.policy_generation != worker.policy_generation:
                raise BatchValidationError(
                    f"worker {worker.seed} contains mixed policy generations"
                )

        if config.requires_shared_values:
            advantages, returns = compute_gae(
                rewards=np.asarray(
                    [joint.team_reward for joint in timeline],
                    dtype=np.float32,
                ),
                values=np.asarray(
                    [joint.team_value for joint in timeline],
                    dtype=np.float32,
                ),
                next_values=np.asarray(
                    [joint.next_team_value for joint in timeline],
                    dtype=np.float32,
                ),
                terminated=np.asarray(
                    [joint.terminated for joint in timeline],
                    dtype=np.bool_,
                ),
                truncated=np.asarray(
                    [joint.truncated for joint in timeline],
                    dtype=np.bool_,
                ),
                gamma=config.gamma,
                gae_lambda=config.gae_lambda,
            )
            advantages_by_owner = tuple(
                advantages for _ in range(num_agents)
            )
            returns_by_owner = tuple(returns for _ in range(num_agents))
        else:
            advantages_by_owner_values: list[np.ndarray] = []
            returns_by_owner_values: list[np.ndarray] = []
            for owner in range(num_agents):
                advantages, returns = compute_gae(
                    rewards=np.asarray(
                        [joint.team_reward for joint in timeline],
                        dtype=np.float32,
                    ),
                    values=np.asarray(
                        [joint.values[owner] for joint in timeline],
                        dtype=np.float32,
                    ),
                    next_values=np.asarray(
                        [joint.next_values[owner] for joint in timeline],
                        dtype=np.float32,
                    ),
                    terminated=np.asarray(
                        [joint.terminated for joint in timeline],
                        dtype=np.bool_,
                    ),
                    truncated=np.asarray(
                        [joint.truncated for joint in timeline],
                        dtype=np.bool_,
                    ),
                    gamma=config.gamma,
                    gae_lambda=config.gae_lambda,
                )
                advantages_by_owner_values.append(advantages)
                returns_by_owner_values.append(returns)
            advantages_by_owner = tuple(advantages_by_owner_values)
            returns_by_owner = tuple(returns_by_owner_values)

        for timeline_index, joint in enumerate(timeline):
            for owner, transition in enumerate(joint.agent_transitions):
                action_dimension = _validate_flat_transition(
                    transition,
                    worker=worker,
                    config=config,
                    action_dimension=action_dimension,
                )
                ordered_transitions.append(transition)
                ordered_advantages.append(
                    float(advantages_by_owner[owner][timeline_index])
                )
                ordered_returns.append(
                    float(returns_by_owner[owner][timeline_index])
                )
                joint_step_indices.append(next_global_joint_index)
            next_global_joint_index += 1
    return (
        ordered_transitions,
        ordered_advantages,
        ordered_returns,
        joint_step_indices,
    )


def _pad_global_to_identity_slots(
    global_state: CentralizedState, config: MAPPOConfig
) -> tuple[np.ndarray, np.ndarray]:
    """Pad an active-subset global state to the canonical 20 identity slots.

    The joint rollout keeps active-subset states (rows sized by the
    controlled intersections); every cooperative critic is a 20-slot model
    from the fixed identity contract.  Inactive slots are zero-filled and
    masked out, matching the controller-side ``_padded_global`` behaviour.
    For the full 20-intersection scope this is the identity mapping.
    """
    observations = np.asarray(
        global_state.observations, dtype=np.float32
    )
    agent_mask = np.asarray(global_state.agent_mask, dtype=np.bool_)
    num_slots = len(IDENTITY_SLOT_IDS)
    if (
        observations.shape == (num_slots, config.obs_dim)
        and agent_mask.shape == (num_slots,)
    ):
        return observations, agent_mask
    slots = np.asarray(
        identity_slots_for(config.intersection_ids), dtype=np.int64
    )
    padded_observations = np.zeros(
        (num_slots, config.obs_dim), dtype=np.float32
    )
    padded_mask = np.zeros(num_slots, dtype=np.bool_)
    padded_observations[slots] = observations
    padded_mask[slots] = agent_mask
    return padded_observations, padded_mask


def _pack_ppo_batch(
    ordered_transitions: list[Transition],
    ordered_advantages: list[float],
    ordered_returns: list[float],
    joint_step_indices: list[int],
    *,
    config: MAPPOConfig,
) -> PPOBatch:
    padded_global = [
        _pad_global_to_identity_slots(item.global_state, config)
        for item in ordered_transitions
    ]
    return PPOBatch(
        local_obs=torch.from_numpy(
            np.stack(
                [item.local_obs for item in ordered_transitions]
            ).astype(np.float32, copy=True)
        ),
        phase_features=torch.from_numpy(
            np.stack(
                [item.phase_features for item in ordered_transitions]
            ).astype(np.float32, copy=True)
        ),
        action_mask=torch.from_numpy(
            np.stack(
                [item.action_mask for item in ordered_transitions]
            ).astype(np.bool_, copy=True)
        ),
        global_obs=torch.from_numpy(
            np.stack([padded for padded, _mask in padded_global]).astype(
                np.float32, copy=True
            )
        ),
        agent_mask=torch.from_numpy(
            np.stack([mask for _padded, mask in padded_global]).astype(
                np.bool_, copy=True
            )
        ),
        agent_index=torch.tensor(
            [item.agent_index for item in ordered_transitions], dtype=torch.long
        ),
        actions=torch.tensor(
            [item.action for item in ordered_transitions], dtype=torch.long
        ),
        old_log_probs=torch.tensor(
            [item.log_prob for item in ordered_transitions], dtype=torch.float32
        ),
        old_values=torch.tensor(
            [item.value for item in ordered_transitions],
            dtype=torch.float32,
        ),
        advantages=torch.tensor(ordered_advantages, dtype=torch.float32),
        returns=torch.tensor(ordered_returns, dtype=torch.float32),
        joint_step_index=torch.tensor(joint_step_indices, dtype=torch.long),
    )


def validate_joint_timeline(
    joint_transitions: Iterable[Any], config: MAPPOConfig
) -> tuple[JointTransition, ...]:
    """Deep-validate one worker's atomic cooperative trajectory."""

    raw_timeline = tuple(joint_transitions)
    if not raw_timeline:
        raise BatchValidationError("joint timeline contains no transitions")
    if config.reward_scope != REWARD_SCOPE_SHARED_TEAM:
        raise BatchValidationError(
            "joint timeline requires shared_team reward scope"
        )

    canonical: list[JointTransition] = []
    expected_shared_values = config.requires_shared_values
    for position, raw_joint in enumerate(raw_timeline):
        if not isinstance(raw_joint, JointTransition):
            raise BatchValidationError(
                f"joint step {position} must be a JointTransition"
            )
        try:
            _validate_raw_joint_envelope(
                raw_joint, config=config, position=position
            )
        except BatchValidationError:
            raise
        except (TypeError, ValueError, AttributeError) as error:
            raise BatchValidationError(
                f"joint step {position} raw payload is invalid: {error}"
            ) from error
        try:
            joint = JointTransition(
                joint_step_id=raw_joint.joint_step_id,
                global_state=raw_joint.global_state,
                next_global_state=raw_joint.next_global_state,
                values=raw_joint.values,
                next_values=raw_joint.next_values,
                team_reward=raw_joint.team_reward,
                team_raw_reward=raw_joint.team_raw_reward,
                window_start_s=raw_joint.window_start_s,
                window_end_s=raw_joint.window_end_s,
                terminated=raw_joint.terminated,
                truncated=raw_joint.truncated,
                policy_generation=raw_joint.policy_generation,
                agent_transitions=raw_joint.agent_transitions,
                require_shared_values=raw_joint.require_shared_values,
                raw_local_rewards=raw_joint.raw_local_rewards,
                local_rewards=raw_joint.local_rewards,
                team_value_mode=raw_joint.team_value_mode,
                state_schema=raw_joint.state_schema,
            )
        except (TypeError, ValueError, RuntimeError) as error:
            raise BatchValidationError(
                f"joint step {position} payload is invalid: {error}"
            ) from error

        if joint.joint_step_id != position:
            raise BatchValidationError(
                "joint step id sequence must be contiguous from zero: "
                f"position {position} has id {joint.joint_step_id}"
            )
        if joint.require_shared_values is not expected_shared_values:
            expected = "shared" if expected_shared_values else "owner-local"
            raise BatchValidationError(
                f"joint step {position} value scope must be {expected}"
            )
        window_duration = joint.window_end_s - joint.window_start_s
        is_final = position == len(raw_timeline) - 1
        is_done = joint.terminated or joint.truncated
        if window_duration <= 0.0:
            raise BatchValidationError(
                f"joint step {position} window duration must be positive"
            )
        if is_final:
            if not is_done:
                raise BatchValidationError(
                    "final joint step must be terminal or truncated"
                )
            if window_duration > config.action_interval_s + 1e-9:
                raise BatchValidationError(
                    f"final joint step window duration {window_duration} "
                    f"exceeds {config.action_interval_s} seconds"
                )
        elif not np.isclose(
            window_duration,
            config.action_interval_s,
            rtol=0.0,
            atol=1e-9,
        ):
            raise BatchValidationError(
                f"joint step {position} window duration {window_duration} "
                f"does not match {config.action_interval_s} seconds"
            )
        _validate_joint_state(
            joint.global_state,
            config=config,
            label=f"joint step {position} state",
        )
        _validate_joint_state(
            joint.next_global_state,
            config=config,
            label=f"joint step {position} next state",
        )
        for owner, child in enumerate(joint.agent_transitions):
            if not np.array_equal(
                child.local_obs, joint.global_state.observations[owner]
            ):
                raise BatchValidationError(
                    f"joint step {position} owner {owner} local state does "
                    "not match its global-state row"
                )
            if not np.array_equal(
                child.next_local_obs,
                joint.next_global_state.observations[owner],
            ):
                raise BatchValidationError(
                    f"joint step {position} owner {owner} next local state "
                    "does not match its next-global-state row"
                )
            if (
                child.phase_features.shape[1] != config.phase_feature_dim
                or child.phase_features.shape[0] > config.max_action_dim
            ):
                raise BatchValidationError(
                    f"joint step {position} owner {owner} candidate action "
                    "schema mismatch"
                )
        canonical.append(joint)

    generation = canonical[0].policy_generation
    for position, joint in enumerate(canonical):
        if joint.policy_generation != generation:
            raise BatchValidationError(
                "joint timeline contains mixed policy generations"
            )
        if position == 0:
            continue
        previous = canonical[position - 1]
        if previous.terminated or previous.truncated:
            raise BatchValidationError(
                "joint timeline continues after a done step"
            )
        if previous.window_end_s != joint.window_start_s:
            raise BatchValidationError(
                "joint timeline window continuity mismatch between steps "
                f"{position - 1} and {position}"
            )
        if not _centralized_states_equal(
            previous.next_global_state, joint.global_state
        ):
            raise BatchValidationError(
                "joint timeline state continuity mismatch between steps "
                f"{position - 1} and {position}"
            )
        if previous.next_values != joint.values:
            raise BatchValidationError(
                "joint timeline value continuity mismatch between steps "
                f"{position - 1} and {position}"
            )
    return tuple(canonical)


def _validate_raw_joint_envelope(
    joint: JointTransition, *, config: MAPPOConfig, position: int
) -> None:
    """Reject damaged wire payloads before any canonical copies are made."""

    _validate_joint_state(
        joint.global_state,
        config=config,
        label=f"joint step {position} state",
    )
    _validate_joint_state(
        joint.next_global_state,
        config=config,
        label=f"joint step {position} next state",
    )
    children = tuple(joint.agent_transitions)
    num_agents = len(config.intersection_ids)
    if len(children) != num_agents:
        raise BatchValidationError(
            f"joint step {position} missing or unexpected agent owners"
        )
    if not all(isinstance(child, Transition) for child in children):
        raise BatchValidationError(
            f"joint step {position} children must be Transition values"
        )
    owners = tuple(int(child.agent_index) for child in children)
    if len(set(owners)) != len(owners):
        raise BatchValidationError(
            f"joint step {position} has duplicate agent owner"
        )
    expected_owners = tuple(range(num_agents))
    if set(owners) != set(expected_owners):
        raise BatchValidationError(
            f"joint step {position} has missing or unexpected agent owner"
        )
    if owners != expected_owners:
        raise BatchValidationError(
            f"joint step {position} agent owners are not in fixed order"
        )
    values = tuple(joint.values)
    next_values = tuple(joint.next_values)
    if len(values) != num_agents or len(next_values) != num_agents:
        raise BatchValidationError(
            f"joint step {position} value count does not match agent count"
        )

    for owner, child in enumerate(children):
        if child.value != values[owner]:
            raise BatchValidationError(
                f"joint step {position} owner {owner} current value mismatch"
            )
        if child.next_value != next_values[owner]:
            raise BatchValidationError(
                f"joint step {position} owner {owner} next value mismatch"
            )
        if child.reward != joint.team_reward:
            raise BatchValidationError(
                f"joint step {position} owner {owner} reward mismatch"
            )
        if (
            child.terminated is not joint.terminated
            or child.truncated is not joint.truncated
        ):
            raise BatchValidationError(
                f"joint step {position} owner {owner} done flag mismatch"
            )
        if child.policy_generation != joint.policy_generation:
            raise BatchValidationError(
                f"joint step {position} owner {owner} generation mismatch"
            )
        if child.decision_time_s != joint.window_start_s:
            raise BatchValidationError(
                f"joint step {position} owner {owner} decision/window mismatch"
            )
        if not (
            joint.window_start_s
            <= child.applied_time_s
            <= joint.window_end_s
        ):
            raise BatchValidationError(
                f"joint step {position} owner {owner} applied time is "
                "outside its window"
            )
        if child.requested_phase != child.applied_phase:
            raise BatchValidationError(
                f"joint step {position} owner {owner} requested/applied "
                "phase mismatch"
            )
        if not _centralized_states_equal(
            child.global_state, joint.global_state
        ):
            raise BatchValidationError(
                f"joint step {position} owner {owner} state mismatch"
            )
        if not _centralized_states_equal(
            child.next_global_state, joint.next_global_state
        ):
            raise BatchValidationError(
                f"joint step {position} owner {owner} next state mismatch"
            )
        if not np.array_equal(
            child.local_obs, joint.global_state.observations[owner]
        ):
            raise BatchValidationError(
                f"joint step {position} owner {owner} local state row mismatch"
            )
        if not np.array_equal(
            child.next_local_obs,
            joint.next_global_state.observations[owner],
        ):
            raise BatchValidationError(
                f"joint step {position} owner {owner} next local state row "
                "mismatch"
            )


def _validate_joint_state(
    state: CentralizedState, *, config: MAPPOConfig, label: str
) -> None:
    if not isinstance(state, CentralizedState):
        raise BatchValidationError(f"{label} is not a CentralizedState")
    expected_shape = (len(config.intersection_ids), config.obs_dim)
    observations = np.asarray(state.observations)
    mask = np.asarray(state.agent_mask)
    if state.schema != config.centralized_state_schema:
        raise BatchValidationError(f"{label} schema mismatch")
    if state.intersection_ids != config.intersection_ids:
        raise BatchValidationError(f"{label} intersection order mismatch")
    if observations.shape != expected_shape:
        raise BatchValidationError(f"{label} observation shape mismatch")
    if mask.shape != (len(config.intersection_ids),):
        raise BatchValidationError(f"{label} agent mask shape mismatch")
    if mask.dtype != np.bool_:
        raise BatchValidationError(f"{label} agent mask must be boolean")
    if not bool(mask.all()):
        raise BatchValidationError(f"{label} is missing a configured agent")
    if not np.isfinite(observations).all():
        raise BatchValidationError(f"{label} contains non-finite observations")


def _centralized_states_equal(
    left: CentralizedState, right: CentralizedState
) -> bool:
    if not isinstance(left, CentralizedState) or not isinstance(
        right, CentralizedState
    ):
        return False
    return (
        left.schema == right.schema
        and left.intersection_ids == right.intersection_ids
        and np.array_equal(left.observations, right.observations)
        and np.array_equal(left.agent_mask, right.agent_mask)
    )


def _validate_worker_objective(
    worker: WorkerRollout, config: MAPPOConfig
) -> None:
    prefix = f"worker {worker.seed}"
    if worker.reward_scope != config.reward_scope:
        raise BatchValidationError(f"{prefix} reward scope mismatch")
    if worker.team_reward_schema != config.team_reward_schema:
        raise BatchValidationError(f"{prefix} team reward schema mismatch")
    if worker.joint_step_schema != config.joint_step_schema:
        raise BatchValidationError(f"{prefix} joint step schema mismatch")
    if (
        config.reward_scope == REWARD_SCOPE_SHARED_TEAM
        and int(worker.dropped_pending) != 0
    ):
        raise BatchValidationError(
            f"{prefix} dropped {worker.dropped_pending} pending joint actions"
        )


def _validate_flat_transition(
    transition: Transition,
    *,
    worker: WorkerRollout,
    config: MAPPOConfig,
    action_dimension: int | None,
) -> int:
    if transition.policy_generation != worker.policy_generation:
        raise ValueError(
            f"worker {worker.seed} contains mixed policy generations"
        )
    if transition.local_obs.shape != (config.obs_dim,):
        raise ValueError("transition local observation schema mismatch")
    if transition.next_local_obs.shape != (config.obs_dim,):
        raise ValueError("transition next local observation schema mismatch")
    if (
        transition.phase_features.ndim != 2
        or transition.phase_features.shape[1] != config.phase_feature_dim
        or transition.action_mask.shape
        != transition.phase_features.shape[:1]
    ):
        raise ValueError("transition candidate action schema mismatch")
    transition_action_dimension = int(transition.phase_features.shape[0])
    if action_dimension is not None and transition_action_dimension != action_dimension:
        raise ValueError("transition action dimensions differ")
    for state in (transition.global_state, transition.next_global_state):
        if (
            state.intersection_ids != config.intersection_ids
            or state.schema != config.centralized_state_schema
            or state.observations.shape
            != (len(config.intersection_ids), config.obs_dim)
            or state.agent_mask.shape != (len(config.intersection_ids),)
        ):
            raise ValueError("transition centralized state schema mismatch")
    if not 0 <= transition.agent_index < len(config.intersection_ids):
        raise ValueError("transition owner agent is out of range")
    return transition_action_dimension


def _worker_period(worker: WorkerRollout) -> str:
    period = str(worker.period or "").strip()
    if not period:
        raise BatchValidationError(
            f"worker {worker.seed} is missing its scheduled period"
        )
    if worker.status == "ok":
        if not isinstance(worker.metadata, Mapping):
            raise BatchValidationError(
                f"worker {worker.seed} is missing successful rollout metadata"
            )
        metadata_period = str(worker.metadata.get("period", "")).strip()
        if metadata_period != period:
            raise BatchValidationError(
                f"worker {worker.seed} metadata period mismatch: "
                f"scheduled={period!r}, metadata={metadata_period!r}"
            )
    return period


def _validate_worker_environment_batch(
    workers: tuple[WorkerRollout, ...],
    *,
    expected_periods: Iterable[str],
    policy_spec: Mapping[str, Any],
    expected_environment_contract: Mapping[str, Any] | None = None,
) -> tuple[dict[str, Any], dict[str, dict[str, Any]], dict[str, int]]:
    scheduled = tuple(str(period) for period in expected_periods)
    if len(scheduled) != len(workers):
        raise BatchValidationError(
            "expected periods must contain one value per worker"
        )
    try:
        period_counts = validate_balanced_period_batch(scheduled)
    except EnvironmentContractError as error:
        raise BatchValidationError(str(error)) from error

    metadata_by_period: dict[str, dict[str, Any]] = {}
    for worker, expected_period in zip(workers, scheduled, strict=True):
        actual_period = _worker_period(worker)
        if actual_period != expected_period:
            raise BatchValidationError(
                f"worker {worker.seed} scheduled period mismatch: "
                f"expected={expected_period!r}, actual={actual_period!r}"
            )
        if not isinstance(worker.metadata, Mapping):
            raise BatchValidationError(
                f"worker {worker.seed} is missing successful rollout metadata"
            )
        metadata_by_period.setdefault(
            actual_period, deepcopy(dict(worker.metadata))
        )

    try:
        batch_contract = build_environment_contract(
            metadata_by_period,
            policy_spec=policy_spec,
        )
        if expected_environment_contract is None:
            environment_contract = batch_contract
        else:
            validate_contract_integrity(expected_environment_contract)
            environment_contract = deepcopy(dict(expected_environment_contract))
        for worker in workers:
            assert isinstance(worker.metadata, Mapping)
            validate_checkpoint_environment(
                environment_contract,
                worker.metadata,
                policy_spec=policy_spec,
            )
    except (EnvironmentContractError, TypeError, ValueError) as error:
        raise BatchValidationError(
            f"worker environment contract validation failed: {error}"
        ) from error

    return environment_contract, metadata_by_period, period_counts


def validate_worker_batch(
    results: Iterable[WorkerRollout],
    *,
    expected_generation: int,
    expected_policy_digest: str,
    expected_seeds: Iterable[int],
    expected_config_signature: str,
    expected_local_observation_schema: str,
    expected_centralized_state_schema: str,
    expected_reward_scope: str | None = None,
    expected_team_reward_schema: str | None = None,
    expected_joint_step_schema: str | None = None,
    expected_periods: Iterable[str] | None = None,
    policy_spec: Mapping[str, Any] | None = None,
    expected_environment_contract: Mapping[str, Any] | None = None,
) -> tuple[WorkerRollout, ...]:
    workers = tuple(results)
    required_seeds = tuple(int(seed) for seed in expected_seeds)
    if len(required_seeds) != len(set(required_seeds)):
        raise BatchValidationError("expected worker seeds must be unique")
    counts = Counter(int(worker.seed) for worker in workers)
    duplicates = sorted(seed for seed, count in counts.items() if count > 1)
    if duplicates:
        raise BatchValidationError(
            f"duplicate worker seed: {duplicates[0]}"
        )
    actual_seeds = set(counts)
    required_set = set(required_seeds)
    missing = sorted(required_set - actual_seeds)
    unexpected = sorted(actual_seeds - required_set)
    if missing:
        raise BatchValidationError(
            "missing seeds: " + ", ".join(str(seed) for seed in missing)
        )
    if unexpected:
        raise BatchValidationError(
            "unexpected seeds: "
            + ", ".join(str(seed) for seed in unexpected)
        )

    by_seed = {int(worker.seed): worker for worker in workers}
    ordered = tuple(by_seed[seed] for seed in required_seeds)
    for worker in ordered:
        prefix = f"worker {worker.seed}"
        _worker_period(worker)
        if worker.status != "ok":
            detail = worker.error or worker.status
            raise BatchValidationError(f"{prefix} failed: {detail}")
        if worker.invalid_reason:
            raise BatchValidationError(
                f"{prefix} rollout is invalid: {worker.invalid_reason}"
            )
        if int(worker.pending_count) != 0:
            raise BatchValidationError(
                f"{prefix} has {worker.pending_count} unresolved pending actions"
            )
        if not worker.transitions:
            raise BatchValidationError(f"{prefix} produced no transitions")
        if int(worker.policy_generation) != int(expected_generation):
            raise BatchValidationError(
                f"{prefix} policy generation {worker.policy_generation} "
                f"does not match {expected_generation}"
            )
        if worker.policy_digest != expected_policy_digest:
            raise BatchValidationError(f"{prefix} policy digest mismatch")
        if worker.config_signature != expected_config_signature:
            raise BatchValidationError(f"{prefix} frozen configuration mismatch")
        if (
            worker.local_observation_schema
            != expected_local_observation_schema
        ):
            raise BatchValidationError(
                f"{prefix} local observation schema mismatch"
            )
        if (
            worker.centralized_state_schema
            != expected_centralized_state_schema
        ):
            raise BatchValidationError(
                f"{prefix} centralized state schema mismatch"
            )
        if (
            expected_reward_scope is not None
            and worker.reward_scope != expected_reward_scope
        ):
            raise BatchValidationError(f"{prefix} reward scope mismatch")
        if (
            expected_team_reward_schema is not None
            and worker.team_reward_schema != expected_team_reward_schema
        ):
            raise BatchValidationError(
                f"{prefix} team reward schema mismatch"
            )
        if (
            expected_joint_step_schema is not None
            and worker.joint_step_schema != expected_joint_step_schema
        ):
            raise BatchValidationError(f"{prefix} joint step schema mismatch")
        shared_expected = (
            expected_reward_scope == REWARD_SCOPE_SHARED_TEAM
            or (
                expected_reward_scope is None
                and worker.reward_scope == REWARD_SCOPE_SHARED_TEAM
            )
        )
        if shared_expected and int(worker.dropped_pending) != 0:
            raise BatchValidationError(
                f"{prefix} dropped {worker.dropped_pending} pending joint actions"
            )
    if expected_periods is not None:
        if policy_spec is None:
            raise BatchValidationError(
                "joint worker validation requires a policy specification"
            )
        _validate_worker_environment_batch(
            ordered,
            expected_periods=expected_periods,
            policy_spec=policy_spec,
            expected_environment_contract=expected_environment_contract,
        )
    return ordered


class CentralUpdateCoordinator:
    """Owns the only learner update and advances generation after success."""

    def __init__(
        self,
        *,
        trainer: Any,
        policy_generation: int,
        policy_digest: str,
        config_signature: str,
        local_observation_schema: str,
        centralized_state_schema: str,
        digest_provider,
        batch_builder,
        reward_scope: str | None = None,
        team_reward_schema: str | None = None,
        joint_step_schema: str | None = None,
        policy_spec: Mapping[str, Any] | None = None,
        environment_contract: Mapping[str, Any] | None = None,
    ) -> None:
        self.trainer = trainer
        self.policy_generation = int(policy_generation)
        self.policy_digest = str(policy_digest)
        self.config_signature = str(config_signature)
        self.local_observation_schema = str(local_observation_schema)
        self.centralized_state_schema = str(centralized_state_schema)
        self.reward_scope = (
            None if reward_scope is None else str(reward_scope)
        )
        self.team_reward_schema = (
            None if team_reward_schema is None else str(team_reward_schema)
        )
        self.joint_step_schema = (
            None if joint_step_schema is None else str(joint_step_schema)
        )
        self.policy_spec = (
            None if policy_spec is None else deepcopy(dict(policy_spec))
        )
        self.environment_contract = (
            None
            if environment_contract is None
            else deepcopy(dict(environment_contract))
        )
        if self.environment_contract is not None:
            if self.policy_spec is None:
                raise ValueError("environment contract requires a policy specification")
            validate_contract_integrity(self.environment_contract)
        self.metadata_by_period: dict[str, dict[str, Any]] = {}
        self._digest_provider = digest_provider
        self._batch_builder = batch_builder

    def update_from_workers(
        self,
        results: Iterable[WorkerRollout],
        *,
        expected_seeds: Iterable[int],
        expected_periods: Iterable[str] | None = None,
    ) -> dict[str, object]:
        period_values = (
            None
            if expected_periods is None
            else tuple(str(period) for period in expected_periods)
        )
        workers = validate_worker_batch(
            results,
            expected_generation=self.policy_generation,
            expected_policy_digest=self.policy_digest,
            expected_seeds=expected_seeds,
            expected_config_signature=self.config_signature,
            expected_local_observation_schema=self.local_observation_schema,
            expected_centralized_state_schema=self.centralized_state_schema,
            expected_reward_scope=self.reward_scope,
            expected_team_reward_schema=self.team_reward_schema,
            expected_joint_step_schema=self.joint_step_schema,
            expected_periods=period_values,
            policy_spec=self.policy_spec,
            expected_environment_contract=self.environment_contract,
        )

        candidate_contract = None
        candidate_metadata: dict[str, dict[str, Any]] = {}
        period_counts: dict[str, int] | None = None
        if period_values is not None:
            assert self.policy_spec is not None
            (
                candidate_contract,
                candidate_metadata,
                period_counts,
            ) = _validate_worker_environment_batch(
                workers,
                expected_periods=period_values,
                policy_spec=self.policy_spec,
                expected_environment_contract=self.environment_contract,
            )

        batch = self._batch_builder(workers)
        diagnostics: dict[str, object] = dict(self.trainer.update(batch))
        new_digest = str(self._digest_provider())
        self.policy_generation += 1
        self.policy_digest = new_digest
        if candidate_contract is not None:
            self.environment_contract = candidate_contract
            self.metadata_by_period.update(deepcopy(candidate_metadata))
        diagnostics["policy_generation"] = self.policy_generation
        if period_counts is not None:
            diagnostics["period_counts"] = period_counts
        return diagnostics
