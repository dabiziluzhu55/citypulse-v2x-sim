from __future__ import annotations

import json
from collections import Counter
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Iterable, Mapping, Sequence

import numpy as np
import torch

from algorithms.mappo.config import (
    COOPERATIVE_M1_MODEL_VERSION,
    JOINT_STEP_SCHEMA,
    MAPPOConfig,
    REWARD_SCOPE_LOCAL,
    REWARD_SCOPE_SHARED_TEAM,
    TEAM_REWARD_SCHEMA,
)
from algorithms.mappo.features import CentralizedState
from algorithms.mappo.joint_rollout import JointTransition
from algorithms.mappo.rollout import Transition, compute_gae
from algorithms.mappo.trainer import PPOBatch


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
    reward_scope: str = REWARD_SCOPE_LOCAL
    team_reward_schema: str = TEAM_REWARD_SCHEMA
    joint_step_schema: str = JOINT_STEP_SCHEMA


def build_ppo_batch(
    workers: Iterable[WorkerRollout],
    *,
    config: MAPPOConfig,
    adjacency: np.ndarray | None = None,
) -> PPOBatch:
    """Merge workers without mixing legacy and cooperative objectives."""

    worker_values = tuple(workers)
    if not worker_values:
        raise ValueError("cannot build PPO batch from no workers")
    if config.model_version == COOPERATIVE_M1_MODEL_VERSION:
        return _build_m1_batch(worker_values, config, adjacency=adjacency)
    if config.reward_scope == REWARD_SCOPE_SHARED_TEAM:
        flattened = _build_shared_team_rows(worker_values, config)
    else:
        flattened = _build_legacy_local_rows(worker_values, config)
    return _pack_ppo_batch(*flattened)


def _build_legacy_local_rows(
    workers: tuple[WorkerRollout, ...], config: MAPPOConfig
) -> tuple[list[Transition], list[float], list[float], list[int]]:
    ordered_transitions: list[Transition] = []
    ordered_advantages: list[float] = []
    ordered_returns: list[float] = []
    joint_step_indices: list[int] = []
    action_dimension: int | None = None

    for worker in workers:
        _validate_worker_objective(worker, config)
        transitions = tuple(worker.transitions)
        if not transitions:
            raise ValueError(f"worker {worker.seed} produced no transitions")
        by_agent: dict[int, list[tuple[int, Transition]]] = {}
        for position, raw_transition in enumerate(transitions):
            if not isinstance(raw_transition, Transition):
                raise TypeError("worker transitions must be Transition values")
            transition = raw_transition
            action_dimension = _validate_flat_transition(
                transition,
                worker=worker,
                config=config,
                action_dimension=action_dimension,
            )
            by_agent.setdefault(transition.agent_index, []).append(
                (position, transition)
            )

        advantages_by_position: dict[int, float] = {}
        returns_by_position: dict[int, float] = {}
        for agent_transitions in by_agent.values():
            agent_transitions.sort(
                key=lambda item: (item[1].decision_time_s, item[0])
            )
            trajectory = [item[1] for item in agent_transitions]
            advantages, returns = _compute_transition_gae(trajectory, config)
            for offset, (position, _) in enumerate(agent_transitions):
                advantages_by_position[position] = float(advantages[offset])
                returns_by_position[position] = float(returns[offset])

        first_index = len(joint_step_indices)
        ordered_transitions.extend(transitions)
        ordered_advantages.extend(
            advantages_by_position[position]
            for position in range(len(transitions))
        )
        ordered_returns.extend(
            returns_by_position[position]
            for position in range(len(transitions))
        )
        joint_step_indices.extend(
            range(first_index, first_index + len(transitions))
        )
    return (
        ordered_transitions,
        ordered_advantages,
        ordered_returns,
        joint_step_indices,
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


def _compute_transition_gae(
    trajectory: list[Transition], config: MAPPOConfig
) -> tuple[np.ndarray, np.ndarray]:
    return compute_gae(
        rewards=np.asarray(
            [item.reward for item in trajectory], dtype=np.float32
        ),
        values=np.asarray(
            [item.value for item in trajectory], dtype=np.float32
        ),
        next_values=np.asarray(
            [item.next_value for item in trajectory], dtype=np.float32
        ),
        terminated=np.asarray(
            [item.terminated for item in trajectory], dtype=np.bool_
        ),
        truncated=np.asarray(
            [item.truncated for item in trajectory], dtype=np.bool_
        ),
        gamma=config.gamma,
        gae_lambda=config.gae_lambda,
    )


def _pack_ppo_batch(
    ordered_transitions: list[Transition],
    ordered_advantages: list[float],
    ordered_returns: list[float],
    joint_step_indices: list[int],
    component_advantages: dict[str, list[float]] | None = None,
    old_values: list[float] | None = None,
) -> PPOBatch:
    component_tensors = None
    if component_advantages is not None:
        component_tensors = {
            key: torch.tensor(values, dtype=torch.float32)
            for key, values in component_advantages.items()
        }
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
            np.stack(
                [item.global_state.observations for item in ordered_transitions]
            ).astype(np.float32, copy=True)
        ),
        agent_mask=torch.from_numpy(
            np.stack(
                [item.global_state.agent_mask for item in ordered_transitions]
            ).astype(np.bool_, copy=True)
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
            (
                [item.value for item in ordered_transitions]
                if old_values is None
                else old_values
            ),
            dtype=torch.float32,
        ),
        advantages=torch.tensor(ordered_advantages, dtype=torch.float32),
        returns=torch.tensor(ordered_returns, dtype=torch.float32),
        joint_step_index=torch.tensor(joint_step_indices, dtype=torch.long),
        component_advantages=component_tensors,
    )


def _build_m1_components(
    timeline: list[JointTransition],
    config: MAPPOConfig,
    adjacency: np.ndarray,
) -> dict[str, list[float]]:
    """M1-A/B：raw local/nbr/team 组件（未标准化），按 owner-row 顺序展开。

    关键：local 与 team 的 GAE 都必须在整条轨迹上递推（compute_gae 输入为
    全时间序列），不能对每个 joint 单独调用 compute_gae（那只是单步 delta）。
    """
    num_agents = len(config.intersection_ids)
    if timeline:
        timeline = sorted(timeline, key=lambda j: j.joint_step_id)
    terminated = np.asarray([j.terminated for j in timeline], dtype=np.bool_)
    truncated = np.asarray([j.truncated for j in timeline], dtype=np.bool_)

    # --- local 组件：per-owner GAE（reward=local_rewards, value=values[owner]）---
    local_by_joint: list[np.ndarray] = []  # 每 joint 一个 (A,) 向量
    for owner in range(num_agents):
        rewards = np.asarray(
            [j.local_rewards[owner] for j in timeline], dtype=np.float32
        )
        values = np.asarray(
            [j.values[owner] for j in timeline], dtype=np.float32
        )
        next_values = np.asarray(
            [j.next_values[owner] for j in timeline], dtype=np.float32
        )
        advs, _ = compute_gae(
            rewards=rewards,
            values=values,
            next_values=next_values,
            terminated=terminated,
            truncated=truncated,
            gamma=config.gamma,
            gae_lambda=config.gae_lambda,
        )
        if len(local_by_joint) == 0:
            local_by_joint = [
                np.zeros(num_agents, dtype=np.float32) for _ in timeline
            ]
        for t, value in enumerate(advs):
            local_by_joint[t][owner] = float(value)

    # --- neighbor 组件：raw local 的邻域均值（M 对称、M_ii=0、孤立=0）---
    adjacency = np.asarray(adjacency, dtype=np.float32)
    counts = adjacency.sum(axis=1).clip(min=1)
    neighbor_by_joint = [
        (advs @ adjacency) / counts for advs in local_by_joint
    ]

    # --- team 组件：GAE(reward=team_reward, value=mean(values)) ---
    team_advs, _ = compute_gae(
        rewards=np.asarray(
            [j.team_reward for j in timeline], dtype=np.float32
        ),
        values=np.asarray(
            [float(np.mean(j.values)) for j in timeline], dtype=np.float32
        ),
        next_values=np.asarray(
            [float(np.mean(j.next_values)) for j in timeline],
            dtype=np.float32,
        ),
        terminated=terminated,
        truncated=truncated,
        gamma=config.gamma,
        gae_lambda=config.gae_lambda,
    )

    local_raw: list[float] = []
    neighbor_raw: list[float] = []
    team_raw: list[float] = []
    for t, joint in enumerate(timeline):
        local_raw.extend(float(v) for v in local_by_joint[t])
        neighbor_raw.extend(float(v) for v in neighbor_by_joint[t])
        team_raw.extend(float(team_advs[t]) for _ in range(num_agents))
    return {"local": local_raw, "neighbor": neighbor_raw, "team": team_raw}


def _build_m1_0_scalar_rows(
    timeline: list[JointTransition],
    config: MAPPOConfig,
) -> dict[str, list[float]]:
    """M1-0：scalar team GAE（reward=team_reward, value=mean(values)）并广播。

    与 _build_shared_team_rows 的 requires_shared_values=True 分支等价，
    只是 team value 用 mean-of-values；M1-0 正式训练走共享分支，本函数供
    统一 component 字典的入口/测试使用。
    """
    num_agents = len(config.intersection_ids)
    if timeline:
        timeline = sorted(timeline, key=lambda j: j.joint_step_id)
    terminated = np.asarray([j.terminated for j in timeline], dtype=np.bool_)
    truncated = np.asarray([j.truncated for j in timeline], dtype=np.bool_)
    team_advs, _ = compute_gae(
        rewards=np.asarray(
            [j.team_reward for j in timeline], dtype=np.float32
        ),
        values=np.asarray(
            [float(np.mean(j.values)) for j in timeline], dtype=np.float32
        ),
        next_values=np.asarray(
            [float(np.mean(j.next_values)) for j in timeline],
            dtype=np.float32,
        ),
        terminated=terminated,
        truncated=truncated,
        gamma=config.gamma,
        gae_lambda=config.gae_lambda,
    )
    team_raw: list[float] = []
    for value in team_advs:
        team_raw.extend(float(value) for _ in range(num_agents))
    return {"local": team_raw, "neighbor": team_raw, "team": team_raw}


def load_intersection_adjacency_matrix(
    path: str | Path, intersection_ids: Sequence[str]
) -> np.ndarray:
    """Load {edges: {id: [neighbor ids]}} into a symmetric 0/1 matrix."""

    data = json.loads(Path(path).read_text(encoding="utf-8"))
    edges = data.get("edges")
    if not isinstance(edges, Mapping):
        raise BatchValidationError("adjacency file must contain an edges map")
    ids = tuple(str(value) for value in intersection_ids)
    matrix = np.zeros((len(ids), len(ids)), dtype=np.float32)
    for index, node in enumerate(ids):
        neighbors = edges.get(node, ())
        if isinstance(neighbors, (str, bytes)) or not isinstance(
            neighbors, Iterable
        ):
            raise BatchValidationError(
                f"adjacency edges for {node} must be a list"
            )
        for neighbor in neighbors:
            neighbor = str(neighbor)
            if neighbor not in ids:
                continue
            column = ids.index(neighbor)
            matrix[index, column] = 1.0
            matrix[column, index] = 1.0
    return matrix


def _build_m1_batch(
    workers: tuple[WorkerRollout, ...],
    config: MAPPOConfig,
    *,
    adjacency: np.ndarray | None = None,
) -> PPOBatch:
    """Assemble M1 batches.

    m1_0_scalar: scalar team GAE (mean-of-values) broadcast, no component
      advantages; old_values are joint means so the m1_0_scalar joint
      validation holds.
    per_agent: raw local/neighbor/team components are handed to the trainer
      mix_advantages; ordered advantages/returns use the local component
      rows (returns = local_adv + value[owner]).
    """
    ordered_transitions: list[Transition] = []
    ordered_advantages: list[float] = []
    ordered_returns: list[float] = []
    joint_step_indices: list[int] = []
    component_advantages: dict[str, list[float]] | None = None
    action_dimension: int | None = None
    next_global_joint_index = 0
    num_agents = len(config.intersection_ids)

    if config.m1_target_mode == "m1_0_scalar":
        timelines: list[tuple[JointTransition, ...]] = []
        for worker in workers:
            _validate_worker_objective(worker, config)
            timeline = validate_joint_timeline(worker.transitions, config)
            timelines.append(timeline)
            comps = _build_m1_0_scalar_rows(timeline, config)
            for timeline_index, joint in enumerate(timeline):
                team_advantage = comps["team"][timeline_index * num_agents]
                team_return = team_advantage + float(np.mean(joint.values))
                for owner, transition in enumerate(joint.agent_transitions):
                    action_dimension = _validate_flat_transition(
                        transition,
                        worker=worker,
                        config=config,
                        action_dimension=action_dimension,
                    )
                    ordered_transitions.append(transition)
                    ordered_advantages.append(team_advantage)
                    ordered_returns.append(team_return)
                    joint_step_indices.append(next_global_joint_index)
                next_global_joint_index += 1
        old_values = [
            float(np.mean(joint.values))
            for timeline in timelines
            for joint in timeline
            for _ in joint.agent_transitions
        ]
        return _pack_ppo_batch(
            ordered_transitions,
            ordered_advantages,
            ordered_returns,
            joint_step_indices,
            old_values=old_values,
        )

    if adjacency is None:
        if config.m1_adjacency_path is None:
            raise BatchValidationError(
                "M1 per-agent arms require an adjacency matrix"
            )
        adjacency = load_intersection_adjacency_matrix(
            config.m1_adjacency_path, config.intersection_ids
        )
    component_advantages = {"local": [], "neighbor": [], "team": []}
    for worker in workers:
        _validate_worker_objective(worker, config)
        timeline = validate_joint_timeline(worker.transitions, config)
        comps = _build_m1_components(timeline, config, adjacency)
        for key in component_advantages:
            component_advantages[key].extend(comps[key])
        for timeline_index, joint in enumerate(timeline):
            for owner, transition in enumerate(joint.agent_transitions):
                action_dimension = _validate_flat_transition(
                    transition,
                    worker=worker,
                    config=config,
                    action_dimension=action_dimension,
                )
                row = timeline_index * num_agents + owner
                local_advantage = comps["local"][row]
                ordered_transitions.append(transition)
                ordered_advantages.append(local_advantage)
                ordered_returns.append(
                    local_advantage + float(joint.values[owner])
                )
                joint_step_indices.append(next_global_joint_index)
            next_global_joint_index += 1
    return _pack_ppo_batch(
        ordered_transitions,
        ordered_advantages,
        ordered_returns,
        joint_step_indices,
        component_advantages=component_advantages,
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
        self._digest_provider = digest_provider
        self._batch_builder = batch_builder

    def update_from_workers(
        self,
        results: Iterable[WorkerRollout],
        *,
        expected_seeds: Iterable[int],
    ) -> dict[str, float | int]:
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
        )
        batch = self._batch_builder(workers)
        diagnostics = dict(self.trainer.update(batch))
        new_digest = str(self._digest_provider())
        self.policy_generation += 1
        self.policy_digest = new_digest
        diagnostics["policy_generation"] = self.policy_generation
        return diagnostics
