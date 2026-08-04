from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass, replace
import math
import operator

import numpy as np

from algorithms.mappo.features import CENTRALIZED_STATE_SCHEMA, CentralizedState
from algorithms.mappo.reward import REWARD_MAX, REWARD_MIN
from algorithms.mappo.rollout import (
    ActionAlignmentError,
    ExecutionAlignedRollout,
    PendingTransition,
    Transition,
)


@dataclass(frozen=True)
class JointPendingTransition:
    joint_step_id: int
    agent_pendings: tuple[PendingTransition, ...]

    def __post_init__(self) -> None:
        object.__setattr__(self, "agent_pendings", tuple(self.agent_pendings))


@dataclass(frozen=True)
class JointTransition:
    joint_step_id: int
    global_state: CentralizedState
    next_global_state: CentralizedState
    values: tuple[float, ...]
    next_values: tuple[float, ...]
    team_reward: float
    team_raw_reward: float
    window_start_s: float
    window_end_s: float
    terminated: bool
    truncated: bool
    policy_generation: int
    agent_transitions: tuple[Transition, ...]
    require_shared_values: bool
    state_schema: str = CENTRALIZED_STATE_SCHEMA
    raw_local_rewards: tuple[float, ...] = ()
    local_rewards: tuple[float, ...] = ()
    team_value_mode: str = "scalar"

    def __post_init__(self) -> None:
        transitions = tuple(self.agent_transitions)
        if not transitions:
            raise ValueError("joint transition requires at least one agent")
        num_agents = len(transitions)
        if not all(isinstance(item, Transition) for item in transitions):
            raise ValueError("agent transitions must be Transition values")

        joint_step_id = _nonnegative_index(self.joint_step_id, "joint_step_id")
        generation = _nonnegative_index(
            self.policy_generation, "policy generation"
        )
        schema = str(self.state_schema)
        if not schema:
            raise ValueError("state schema must be non-empty")

        _validate_state(
            self.global_state,
            "global state",
            expected_schema=schema,
            expected_num_agents=num_agents,
        )
        obs_dim = int(np.asarray(self.global_state.observations).shape[1])
        _validate_state(
            self.next_global_state,
            "next global state",
            expected_schema=schema,
            expected_num_agents=num_agents,
            expected_obs_dim=obs_dim,
        )
        if not _same_state_schema(self.global_state, self.next_global_state):
            raise ValueError(
                "next global state schema does not match current global state"
            )

        values = tuple(
            _finite_float(value, "current value") for value in self.values
        )
        next_values = tuple(
            _finite_float(value, "next value") for value in self.next_values
        )
        if len(values) != num_agents:
            raise ValueError("current value count must equal agent count")
        if len(next_values) != num_agents:
            raise ValueError("next value count must equal agent count")

        shared_values = _boolean_flag(
            self.require_shared_values, "require_shared_values"
        )
        if shared_values and any(value != values[0] for value in values[1:]):
            raise ValueError(
                "shared current value must be identical for every agent"
            )
        if shared_values and any(
            value != next_values[0] for value in next_values[1:]
        ):
            raise ValueError(
                "shared next value must be identical for every agent"
            )

        reward = _finite_float(self.team_reward, "team reward")
        raw_reward = _finite_float(self.team_raw_reward, "team raw reward")
        raw_local_rewards = tuple(self.raw_local_rewards)
        local_rewards = tuple(self.local_rewards)
        if raw_local_rewards and len(raw_local_rewards) != num_agents:
            raise ValueError("raw_local_rewards must match agent count")
        if local_rewards and len(local_rewards) != num_agents:
            raise ValueError("local_rewards must match agent count")
        if raw_local_rewards:
            if not all(
                np.isfinite(float(value)) for value in raw_local_rewards
            ):
                raise ValueError("raw local rewards must be finite")
            expected_team = float(
                np.clip(np.mean(raw_local_rewards), REWARD_MIN, REWARD_MAX)
            )
            if abs(expected_team - float(reward)) > 1e-7:
                raise ValueError(
                    "team_reward must equal clip(mean(raw_local_rewards))"
                )
        if local_rewards:
            expected_clipped = tuple(
                float(np.clip(float(value), REWARD_MIN, REWARD_MAX))
                for value in raw_local_rewards
            )
            if any(
                abs(a - b) > 1e-7
                for a, b in zip(local_rewards, expected_clipped)
            ):
                raise ValueError(
                    "local_rewards must equal clip(raw_local_rewards)"
                )
        team_value_mode = str(self.team_value_mode)
        if team_value_mode not in {"scalar", "mean_of_values"}:
            raise ValueError(
                "team_value_mode must be scalar or mean_of_values"
            )
        start_time = _finite_float(self.window_start_s, "window start")
        end_time = _finite_float(self.window_end_s, "window end")
        if end_time < start_time:
            raise ValueError("window end must not precede window start")
        terminal_flag = _boolean_flag(self.terminated, "terminated")
        truncation_flag = _boolean_flag(self.truncated, "truncated")
        if terminal_flag and truncation_flag:
            raise ValueError(
                "a joint transition cannot be both terminated and truncated"
            )

        current_state = _immutable_state_copy(self.global_state)
        successor_state = _immutable_state_copy(self.next_global_state)
        canonical_transitions = tuple(
            _canonicalize_completed_transition(
                transition,
                agent_index=index,
                obs_dim=obs_dim,
                current_state=current_state,
                successor_state=successor_state,
                value=values[index],
                next_value=next_values[index],
                team_reward=reward,
                window_start_s=start_time,
                window_end_s=end_time,
                policy_generation=generation,
                terminated=terminal_flag,
                truncated=truncation_flag,
            )
            for index, transition in enumerate(transitions)
        )

        object.__setattr__(self, "joint_step_id", joint_step_id)
        object.__setattr__(self, "global_state", current_state)
        object.__setattr__(self, "next_global_state", successor_state)
        object.__setattr__(self, "values", values)
        object.__setattr__(self, "next_values", next_values)
        object.__setattr__(self, "team_reward", reward)
        object.__setattr__(self, "team_raw_reward", raw_reward)
        object.__setattr__(self, "window_start_s", start_time)
        object.__setattr__(self, "window_end_s", end_time)
        object.__setattr__(self, "terminated", terminal_flag)
        object.__setattr__(self, "truncated", truncation_flag)
        object.__setattr__(self, "policy_generation", generation)
        object.__setattr__(self, "agent_transitions", canonical_transitions)
        object.__setattr__(self, "require_shared_values", shared_values)
        object.__setattr__(self, "state_schema", schema)
        object.__setattr__(self, "raw_local_rewards", raw_local_rewards)
        object.__setattr__(self, "local_rewards", local_rewards)
        object.__setattr__(self, "team_value_mode", team_value_mode)

    @property
    def team_value(self) -> float:
        if self.team_value_mode == "mean_of_values":
            return float(np.mean(self.values))
        if not self.require_shared_values:
            raise RuntimeError(
                "team_value is available only in shared value mode"
            )
        return self.values[0]

    @property
    def next_team_value(self) -> float:
        if self.team_value_mode == "mean_of_values":
            return float(np.mean(self.next_values))
        if not self.require_shared_values:
            raise RuntimeError(
                "next_team_value is available only in shared value mode"
            )
        return self.next_values[0]


class JointExecutionAlignedRollout:
    def __init__(
        self,
        num_agents: int,
        *,
        require_shared_values: bool = True,
        expected_state_schema: str = CENTRALIZED_STATE_SCHEMA,
    ) -> None:
        try:
            count = operator.index(num_agents)
        except TypeError as error:
            raise TypeError("num_agents must be an integer") from error
        if count <= 0:
            raise ValueError("num_agents must be positive")
        schema = str(expected_state_schema)
        if not schema:
            raise ValueError("expected_state_schema must be non-empty")
        self._num_agents = count
        self._require_shared_values = _boolean_flag(
            require_shared_values, "require_shared_values"
        )
        self._expected_state_schema = schema
        self._children = tuple(
            ExecutionAlignedRollout() for _ in range(self._num_agents)
        )
        self._pending: JointPendingTransition | None = None
        self._confirmed = [False] * self._num_agents
        self._applied_times: list[float | None] = [None] * self._num_agents
        self._invalid_reason: str | None = None

    @property
    def pending(self) -> JointPendingTransition | None:
        return self._pending

    @property
    def invalid(self) -> bool:
        return self._invalid_reason is not None

    @property
    def invalid_reason(self) -> str | None:
        return self._invalid_reason

    def begin(self, pending: JointPendingTransition) -> None:
        if self.invalid:
            raise RuntimeError("joint rollout is invalid")
        if self._pending is not None:
            raise RuntimeError("a joint transition is already pending")
        if not isinstance(pending, JointPendingTransition):
            raise TypeError("pending must be a JointPendingTransition")

        canonical = self._validate_pending(pending)
        if any(child.pending is not None or child.invalid for child in self._children):
            raise RuntimeError("joint child rollout state is inconsistent")

        for child, agent_pending in zip(
            self._children, canonical.agent_pendings, strict=True
        ):
            child.begin(agent_pending)
        self._pending = canonical
        self._confirmed = [False] * self._num_agents
        self._applied_times = [None] * self._num_agents

    def confirm_applied(
        self, agent_index: int, phase: int, time_s: float
    ) -> None:
        if self.invalid:
            raise RuntimeError("joint rollout is invalid")
        if self._pending is None:
            raise RuntimeError("no joint transition is pending")
        index = self._validate_agent_index(agent_index)
        applied_time = _finite_float(time_s, "applied time")
        window_start = float(
            self._pending.agent_pendings[0].decision_time_s
        )
        if applied_time < window_start:
            raise ValueError("applied time must not precede window start")

        try:
            self._children[index].confirm_applied(
                applied_phase=phase, simulation_time_s=applied_time
            )
        except ActionAlignmentError as error:
            self._invalidate(index, error)
            raise

        if not self._confirmed[index]:
            self._confirmed[index] = True
            self._applied_times[index] = applied_time

    def complete(
        self,
        next_local_observations: Sequence[np.ndarray],
        next_global_state: CentralizedState,
        next_values: Sequence[float],
        team_reward: float,
        team_raw_reward: float,
        window_end_s: float,
        *,
        terminated: bool = False,
        truncated: bool = False,
    ) -> JointTransition:
        if self.invalid:
            raise RuntimeError("joint rollout is invalid")
        if self._pending is None:
            raise RuntimeError("no joint transition is pending")
        for index, confirmed in enumerate(self._confirmed):
            if not confirmed:
                raise RuntimeError(
                    f"agent {index} requested phase application has not "
                    "been confirmed"
                )

        prepared = self._validate_completion(
            next_local_observations=next_local_observations,
            next_global_state=next_global_state,
            next_values=next_values,
            team_reward=team_reward,
            team_raw_reward=team_raw_reward,
            window_end_s=window_end_s,
            terminated=terminated,
            truncated=truncated,
        )
        (
            successors,
            successor_state,
            successor_values,
            reward,
            raw_reward,
            end_time,
            terminal_flag,
            truncation_flag,
        ) = prepared

        pending = self._pending
        for child in self._children:
            child.add_reward(reward)
        completed = tuple(
            child.complete(
                next_local_obs=successor,
                next_global_state=successor_state,
                next_value=successor_value,
                terminated=terminal_flag,
                truncated=truncation_flag,
            )
            for child, successor, successor_value in zip(
                self._children, successors, successor_values, strict=True
            )
        )
        self._validate_completed_tuple(
            completed,
            pending=pending,
            next_global_state=successor_state,
            next_values=successor_values,
            team_reward=reward,
            terminated=terminal_flag,
            truncated=truncation_flag,
        )

        values = tuple(float(item.value) for item in pending.agent_pendings)
        joint = JointTransition(
            joint_step_id=int(pending.joint_step_id),
            global_state=pending.agent_pendings[0].global_state,
            next_global_state=successor_state,
            values=values,
            next_values=successor_values,
            team_reward=reward,
            team_raw_reward=raw_reward,
            window_start_s=float(pending.agent_pendings[0].decision_time_s),
            window_end_s=end_time,
            terminated=terminal_flag,
            truncated=truncation_flag,
            policy_generation=int(
                pending.agent_pendings[0].policy_generation
            ),
            agent_transitions=completed,
            require_shared_values=self._require_shared_values,
            state_schema=self._expected_state_schema,
        )
        self._pending = None
        self._confirmed = [False] * self._num_agents
        self._applied_times = [None] * self._num_agents
        return joint

    def _validate_pending(
        self, pending: JointPendingTransition
    ) -> JointPendingTransition:
        try:
            joint_step_id = operator.index(pending.joint_step_id)
        except TypeError as error:
            raise TypeError("joint_step_id must be an integer") from error
        if joint_step_id < 0:
            raise ValueError("joint_step_id must be non-negative")

        agent_pendings = tuple(pending.agent_pendings)
        if len(agent_pendings) < self._num_agents:
            raise ValueError(
                "missing agent pending transitions: expected "
                f"{self._num_agents}, received {len(agent_pendings)}"
            )
        if len(agent_pendings) > self._num_agents:
            raise ValueError(
                "too many agent pending transitions: expected "
                f"{self._num_agents}, received {len(agent_pendings)}"
            )
        if not all(isinstance(item, PendingTransition) for item in agent_pendings):
            raise TypeError("all agent pendings must be PendingTransition values")

        try:
            indices = tuple(
                operator.index(item.agent_index) for item in agent_pendings
            )
        except TypeError as error:
            raise TypeError("every agent index must be an integer") from error
        if len(set(indices)) != len(indices):
            raise ValueError("duplicate agent indices in joint transition")
        expected_indices = tuple(range(self._num_agents))
        if set(indices) != set(expected_indices):
            raise ValueError(
                "missing or unexpected agent indices in joint transition"
            )
        if indices != expected_indices:
            raise ValueError(
                "agent pendings must use fixed agent-index order "
                f"{expected_indices}"
            )

        current_state = agent_pendings[0].global_state
        _validate_state(
            current_state,
            "global state",
            expected_schema=self._expected_state_schema,
            expected_num_agents=self._num_agents,
        )
        obs_dim = int(np.asarray(current_state.observations).shape[1])
        for item in agent_pendings:
            if np.asarray(item.local_obs).shape != (obs_dim,):
                raise ValueError(
                    "global state observation width must match every local "
                    "observation"
                )
        for item in agent_pendings[1:]:
            _validate_state(
                item.global_state,
                "global state",
                expected_schema=self._expected_state_schema,
                expected_num_agents=self._num_agents,
                expected_obs_dim=obs_dim,
            )
            if not _states_equal(current_state, item.global_state):
                raise ValueError("all agent global states must be identical")

        canonical_state = _immutable_state_copy(current_state)
        canonical_pendings = tuple(
            _canonicalize_pending_transition(
                item,
                expected_agent_index=index,
                obs_dim=obs_dim,
                global_state=canonical_state,
            )
            for index, item in enumerate(agent_pendings)
        )
        decision_times = tuple(
            float(item.decision_time_s) for item in canonical_pendings
        )
        if any(value != decision_times[0] for value in decision_times[1:]):
            raise ValueError("all agent decision times must be identical")
        generations = tuple(
            int(item.policy_generation) for item in canonical_pendings
        )
        if any(value != generations[0] for value in generations[1:]):
            raise ValueError("all agent policy generations must be identical")
        values = tuple(float(item.value) for item in canonical_pendings)
        if self._require_shared_values and any(
            value != values[0] for value in values[1:]
        ):
            raise ValueError(
                "shared current value must be identical for every agent"
            )

        return JointPendingTransition(
            joint_step_id=joint_step_id,
            agent_pendings=canonical_pendings,
        )

    def _validate_completion(
        self,
        *,
        next_local_observations: Sequence[np.ndarray],
        next_global_state: CentralizedState,
        next_values: Sequence[float],
        team_reward: float,
        team_raw_reward: float,
        window_end_s: float,
        terminated: bool,
        truncated: bool,
    ) -> tuple[
        tuple[np.ndarray, ...],
        CentralizedState,
        tuple[float, ...],
        float,
        float,
        float,
        bool,
        bool,
    ]:
        assert self._pending is not None
        terminal_flag = _boolean_flag(terminated, "terminated")
        truncation_flag = _boolean_flag(truncated, "truncated")
        if terminal_flag and truncation_flag:
            raise ValueError(
                "a joint transition cannot be both terminated and truncated"
            )

        successors = tuple(next_local_observations)
        if len(successors) != self._num_agents:
            raise ValueError(
                "next local observation count must equal num_agents"
            )
        copied_successors: list[np.ndarray] = []
        for index, (successor, agent_pending) in enumerate(
            zip(successors, self._pending.agent_pendings, strict=True)
        ):
            copied = _readonly_float_array(
                successor,
                f"next local observation for agent {index}",
                ndim=1,
            )
            if copied.shape != agent_pending.local_obs.shape:
                raise ValueError(
                    "next local observation shape does not match current "
                    f"for agent {index}"
                )
            copied_successors.append(copied)

        values = tuple(next_values)
        if len(values) != self._num_agents:
            raise ValueError("next value count must equal num_agents")
        successor_values = tuple(
            _finite_float(value, "next value") for value in values
        )
        if self._require_shared_values and any(
            value != successor_values[0]
            for value in successor_values[1:]
        ):
            raise ValueError(
                "shared next value must be identical for every agent"
            )

        reward = _finite_float(team_reward, "team reward")
        raw_reward = _finite_float(team_raw_reward, "team raw reward")
        end_time = _finite_float(window_end_s, "window end")
        start_time = float(
            self._pending.agent_pendings[0].decision_time_s
        )
        if end_time < start_time:
            raise ValueError("window end must not precede window start")
        applied_times = tuple(
            time for time in self._applied_times if time is not None
        )
        if len(applied_times) != self._num_agents:
            raise RuntimeError("joint applied-time state is incomplete")
        if any(end_time < time for time in applied_times):
            raise ValueError("window end must not precede an applied time")

        current_state = self._pending.agent_pendings[0].global_state
        obs_dim = int(current_state.observations.shape[1])
        _validate_state(
            next_global_state,
            "next global state",
            expected_schema=self._expected_state_schema,
            expected_num_agents=self._num_agents,
            expected_obs_dim=obs_dim,
        )
        if not _same_state_schema(current_state, next_global_state):
            raise ValueError(
                "next global state schema does not match current global state"
            )
        successor_state = _immutable_state_copy(next_global_state)

        return (
            tuple(copied_successors),
            successor_state,
            successor_values,
            reward,
            raw_reward,
            end_time,
            terminal_flag,
            truncation_flag,
        )

    def _validate_completed_tuple(
        self,
        completed: tuple[Transition, ...],
        *,
        pending: JointPendingTransition,
        next_global_state: CentralizedState,
        next_values: tuple[float, ...],
        team_reward: float,
        terminated: bool,
        truncated: bool,
    ) -> None:
        if len(completed) != self._num_agents:
            raise RuntimeError("joint completion produced an incomplete tuple")
        for index, (transition, agent_pending, next_value) in enumerate(
            zip(
                completed,
                pending.agent_pendings,
                next_values,
                strict=True,
            )
        ):
            if transition.agent_index != index:
                raise RuntimeError("joint completion changed fixed agent order")
            if transition.value != float(agent_pending.value):
                raise RuntimeError("joint completion changed a current value")
            if transition.next_value != next_value:
                raise RuntimeError("joint completion changed a next value")
            if transition.reward != team_reward:
                raise RuntimeError("joint completion did not broadcast team reward")
            if transition.global_state is not pending.agent_pendings[0].global_state:
                raise RuntimeError("joint completion did not share global state")
            if transition.next_global_state is not next_global_state:
                raise RuntimeError(
                    "joint completion did not share next global state"
                )
            if transition.terminated is not terminated:
                raise RuntimeError("joint completion changed terminal flags")
            if transition.truncated is not truncated:
                raise RuntimeError("joint completion changed truncation flags")

    def _validate_agent_index(self, agent_index: int) -> int:
        try:
            index = operator.index(agent_index)
        except TypeError as error:
            raise TypeError("agent_index must be an integer") from error
        if not 0 <= index < self._num_agents:
            raise ValueError(
                f"agent_index must be between 0 and {self._num_agents - 1}"
            )
        return index

    def _invalidate(self, agent_index: int, error: Exception) -> None:
        self._invalid_reason = f"agent {agent_index}: {error}"
        for child in self._children:
            if not child.invalid and child.pending is not None:
                child.discard_pending()
        self._pending = None
        self._confirmed = [False] * self._num_agents
        self._applied_times = [None] * self._num_agents


def _finite_float(value: object, name: str) -> float:
    try:
        result = float(value)
    except (TypeError, ValueError) as error:
        raise ValueError(f"{name} must be numeric") from error
    if not math.isfinite(result):
        raise ValueError(f"{name} must be finite")
    return result


def _boolean_flag(value: object, name: str) -> bool:
    if not isinstance(value, (bool, np.bool_)):
        raise ValueError(f"{name} must be a boolean")
    return bool(value)


def _nonnegative_index(value: object, name: str) -> int:
    if isinstance(value, (bool, np.bool_)):
        raise ValueError(f"{name} must be an integer")
    try:
        result = operator.index(value)
    except TypeError as error:
        raise ValueError(f"{name} must be an integer") from error
    if result < 0:
        raise ValueError(f"{name} must be non-negative")
    return result


def _readonly_float_array(
    value: object, name: str, *, ndim: int
) -> np.ndarray:
    try:
        array = np.asarray(value, dtype=np.float32)
    except (TypeError, ValueError) as error:
        raise ValueError(f"{name} must be numeric") from error
    if array.ndim != ndim:
        raise ValueError(f"{name} must be {ndim}-dimensional")
    if not np.isfinite(array).all():
        raise ValueError(f"{name} must contain only finite values")
    copied = np.array(array, dtype=np.float32, copy=True)
    copied.setflags(write=False)
    return copied


def _readonly_bool_array(
    value: object, name: str, *, ndim: int
) -> np.ndarray:
    array = np.asarray(value)
    if array.dtype != np.bool_:
        raise ValueError(f"{name} must have boolean dtype")
    if array.ndim != ndim:
        raise ValueError(f"{name} must be {ndim}-dimensional")
    copied = np.array(array, dtype=np.bool_, copy=True)
    copied.setflags(write=False)
    return copied


def _canonical_action_payload(
    *,
    local_obs: object,
    phase_features: object,
    action_mask: object,
    action: object,
    requested_phase: object,
    obs_dim: int,
) -> tuple[np.ndarray, np.ndarray, np.ndarray, int, int]:
    local = _readonly_float_array(
        local_obs, "local observation", ndim=1
    )
    if local.shape != (obs_dim,):
        raise ValueError(
            f"local observation shape must be ({obs_dim},)"
        )
    phases = _readonly_float_array(
        phase_features, "phase features", ndim=2
    )
    if phases.shape[0] == 0 or phases.shape[1] == 0:
        raise ValueError("phase features must be non-empty")
    mask = _readonly_bool_array(action_mask, "action mask", ndim=1)
    if mask.shape[0] != phases.shape[0]:
        raise ValueError(
            "action mask length must match phase feature rows"
        )
    selected_action = _nonnegative_index(action, "action")
    if selected_action >= mask.shape[0]:
        raise ValueError("action must index the action mask")
    if not bool(mask[selected_action]):
        raise ValueError("selected action must be enabled by the action mask")
    requested = _nonnegative_index(requested_phase, "requested phase")
    return local, phases, mask, selected_action, requested


def _canonicalize_pending_transition(
    pending: PendingTransition,
    *,
    expected_agent_index: int,
    obs_dim: int,
    global_state: CentralizedState,
) -> PendingTransition:
    agent_index = _nonnegative_index(pending.agent_index, "agent index")
    if agent_index != expected_agent_index:
        raise ValueError("pending agent index does not match fixed order")
    local, phases, mask, action, requested = _canonical_action_payload(
        local_obs=pending.local_obs,
        phase_features=pending.phase_features,
        action_mask=pending.action_mask,
        action=pending.action,
        requested_phase=pending.requested_phase,
        obs_dim=obs_dim,
    )
    log_prob = _finite_float(pending.log_prob, "log probability")
    value = _finite_float(pending.value, "current value")
    decision_time = _finite_float(pending.decision_time_s, "decision time")
    if decision_time < 0.0:
        raise ValueError("decision time must be non-negative")
    generation = _nonnegative_index(
        pending.policy_generation, "policy generation"
    )
    return replace(
        pending,
        local_obs=local,
        phase_features=phases,
        action_mask=mask,
        global_state=global_state,
        agent_index=agent_index,
        action=action,
        requested_phase=requested,
        log_prob=log_prob,
        value=value,
        decision_time_s=decision_time,
        policy_generation=generation,
    )


def _canonicalize_completed_transition(
    transition: Transition,
    *,
    agent_index: int,
    obs_dim: int,
    current_state: CentralizedState,
    successor_state: CentralizedState,
    value: float,
    next_value: float,
    team_reward: float,
    window_start_s: float,
    window_end_s: float,
    policy_generation: int,
    terminated: bool,
    truncated: bool,
) -> Transition:
    actual_index = _nonnegative_index(transition.agent_index, "agent index")
    if actual_index != agent_index:
        raise ValueError("agent transitions must use fixed index order")
    local, phases, mask, action, requested = _canonical_action_payload(
        local_obs=transition.local_obs,
        phase_features=transition.phase_features,
        action_mask=transition.action_mask,
        action=transition.action,
        requested_phase=transition.requested_phase,
        obs_dim=obs_dim,
    )
    applied = _nonnegative_index(transition.applied_phase, "applied phase")
    if requested != applied:
        raise ValueError("requested phase must equal applied phase")
    successor = _readonly_float_array(
        transition.next_local_obs, "next local observation", ndim=1
    )
    if successor.shape != local.shape:
        raise ValueError(
            "next local observation shape does not match current"
        )

    log_prob = _finite_float(transition.log_prob, "log probability")
    child_value = _finite_float(transition.value, "current value")
    child_reward = _finite_float(transition.reward, "transition reward")
    child_next_value = _finite_float(transition.next_value, "next value")
    decision_time = _finite_float(
        transition.decision_time_s, "decision time"
    )
    applied_time = _finite_float(transition.applied_time_s, "applied time")
    generation = _nonnegative_index(
        transition.policy_generation, "policy generation"
    )
    terminal_flag = _boolean_flag(transition.terminated, "terminated")
    truncation_flag = _boolean_flag(transition.truncated, "truncated")

    if child_value != value:
        raise ValueError("child current value does not match joint values")
    if child_next_value != next_value:
        raise ValueError("child next value does not match joint next values")
    if child_reward != team_reward:
        raise ValueError("child reward does not match joint team reward")
    if generation != policy_generation:
        raise ValueError("child policy generation does not match joint")
    if decision_time != window_start_s:
        raise ValueError("child decision time does not match window start")
    if not window_start_s <= applied_time <= window_end_s:
        raise ValueError("child applied time lies outside the joint window")
    if terminal_flag != terminated or truncation_flag != truncated:
        raise ValueError("child done flags do not match joint done flags")
    if not _states_equal(transition.global_state, current_state):
        raise ValueError("child global state does not match joint state")
    if not _states_equal(transition.next_global_state, successor_state):
        raise ValueError("child next global state does not match joint state")

    return replace(
        transition,
        local_obs=local,
        phase_features=phases,
        action_mask=mask,
        global_state=current_state,
        agent_index=actual_index,
        action=action,
        requested_phase=requested,
        applied_phase=applied,
        log_prob=log_prob,
        value=child_value,
        reward=child_reward,
        decision_time_s=decision_time,
        applied_time_s=applied_time,
        policy_generation=generation,
        next_local_obs=successor,
        next_global_state=successor_state,
        next_value=child_next_value,
        terminated=terminal_flag,
        truncated=truncation_flag,
    )


def _validate_state(
    state: CentralizedState,
    name: str,
    *,
    expected_schema: str,
    expected_num_agents: int,
    expected_obs_dim: int | None = None,
) -> None:
    if not isinstance(state, CentralizedState):
        raise TypeError(f"{name} must be a CentralizedState")
    try:
        observations = np.asarray(state.observations, dtype=np.float32)
    except (TypeError, ValueError) as error:
        raise ValueError(f"{name} observations must be numeric") from error
    agent_mask = np.asarray(state.agent_mask)
    if state.schema != expected_schema:
        raise ValueError(
            f"{name} schema must be {expected_schema!r}"
        )
    if observations.ndim != 2:
        raise ValueError(f"{name} observations must be two-dimensional")
    if observations.shape[0] != expected_num_agents:
        raise ValueError(
            f"{name} must contain exactly {expected_num_agents} agent rows"
        )
    if observations.shape[1] <= 0:
        raise ValueError(f"{name} observation width must be positive")
    if (
        expected_obs_dim is not None
        and observations.shape[1] != expected_obs_dim
    ):
        raise ValueError(
            f"{name} observation width must be {expected_obs_dim}"
        )
    if not np.isfinite(observations).all():
        raise ValueError(f"{name} observations must be finite")
    if agent_mask.dtype != np.bool_:
        raise ValueError(f"{name} agent mask must have boolean dtype")
    if agent_mask.ndim != 1 or agent_mask.shape[0] != expected_num_agents:
        raise ValueError(f"{name} agent mask shape is invalid")
    if not bool(np.all(agent_mask)):
        raise ValueError(f"{name} requires every configured agent to be active")
    intersection_ids = tuple(state.intersection_ids)
    if len(intersection_ids) != expected_num_agents:
        raise ValueError(f"{name} intersection IDs do not match observations")
    if any(not isinstance(value, str) or not value for value in intersection_ids):
        raise ValueError(f"{name} intersection IDs must be non-empty strings")
    if len(set(intersection_ids)) != len(intersection_ids):
        raise ValueError(f"{name} intersection IDs must be unique")


def _immutable_state_copy(state: CentralizedState) -> CentralizedState:
    observations = np.array(state.observations, dtype=np.float32, copy=True)
    agent_mask = np.array(state.agent_mask, dtype=np.bool_, copy=True)
    observations.setflags(write=False)
    agent_mask.setflags(write=False)
    return CentralizedState(
        observations=observations,
        agent_mask=agent_mask,
        intersection_ids=tuple(state.intersection_ids),
        schema=str(state.schema),
    )


def _states_equal(left: CentralizedState, right: CentralizedState) -> bool:
    if not isinstance(left, CentralizedState) or not isinstance(
        right, CentralizedState
    ):
        return False
    return (
        left.schema == right.schema
        and np.array_equal(left.observations, right.observations)
        and np.array_equal(left.agent_mask, right.agent_mask)
        and tuple(left.intersection_ids) == tuple(right.intersection_ids)
    )


def _same_state_schema(
    current: CentralizedState, successor: CentralizedState
) -> bool:
    return (
        current.schema == successor.schema
        and np.asarray(current.observations).shape
        == np.asarray(successor.observations).shape
        and np.array_equal(current.agent_mask, successor.agent_mask)
        and tuple(current.intersection_ids)
        == tuple(successor.intersection_ids)
    )
