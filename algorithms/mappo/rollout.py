from __future__ import annotations

from dataclasses import dataclass
import math

import numpy as np

from algorithms.mappo.features import CentralizedState


class ActionAlignmentError(RuntimeError):
    pass


@dataclass(frozen=True)
class PendingTransition:
    local_obs: np.ndarray
    phase_features: np.ndarray
    action_mask: np.ndarray
    global_state: CentralizedState
    agent_index: int
    action: int
    requested_phase: int
    log_prob: float
    value: float
    decision_time_s: float
    policy_generation: int


@dataclass(frozen=True)
class Transition:
    local_obs: np.ndarray
    phase_features: np.ndarray
    action_mask: np.ndarray
    global_state: CentralizedState
    agent_index: int
    action: int
    requested_phase: int
    applied_phase: int
    log_prob: float
    value: float
    reward: float
    decision_time_s: float
    applied_time_s: float
    policy_generation: int
    next_local_obs: np.ndarray
    next_global_state: CentralizedState
    next_value: float
    terminated: bool
    truncated: bool


class ExecutionAlignedRollout:
    def __init__(self) -> None:
        self._pending: PendingTransition | None = None
        self._reward = 0.0
        self._applied_phase: int | None = None
        self._applied_time_s: float | None = None
        self._invalid_reason: str | None = None

    @property
    def pending(self) -> PendingTransition | None:
        return self._pending

    @property
    def invalid(self) -> bool:
        return self._invalid_reason is not None

    @property
    def invalid_reason(self) -> str | None:
        return self._invalid_reason

    def begin(self, pending: PendingTransition) -> None:
        if self.invalid:
            raise RuntimeError("rollout is invalid")
        if self._pending is not None:
            raise RuntimeError("a transition is already pending")
        self._pending = pending
        self._reward = 0.0
        self._applied_phase = None
        self._applied_time_s = None

    def add_reward(self, reward: float) -> None:
        if self.invalid:
            raise RuntimeError("rollout is invalid")
        if self._pending is None:
            raise RuntimeError("no transition is pending")
        value = float(reward)
        if not math.isfinite(value):
            raise ValueError("transition reward must be finite")
        self._reward += value

    def discard_pending(self) -> bool:
        """Drop an action that never became a trainable transition."""

        if self.invalid:
            raise RuntimeError("rollout is invalid")
        if self._pending is None:
            return False
        self._pending = None
        self._reward = 0.0
        self._applied_phase = None
        self._applied_time_s = None
        return True

    def confirm_applied(
        self, applied_phase: int, simulation_time_s: float
    ) -> None:
        if self.invalid:
            raise RuntimeError("rollout is invalid")
        if self._pending is None:
            raise RuntimeError("no transition is pending")
        applied = int(applied_phase)
        requested = int(self._pending.requested_phase)
        if applied != requested:
            reason = (
                f"requested phase {requested} does not match applied phase {applied}"
            )
            self._invalid_reason = reason
            self._pending = None
            raise ActionAlignmentError(reason)
        applied_time = float(simulation_time_s)
        if not math.isfinite(applied_time):
            raise ValueError("applied simulation time must be finite")
        if self._applied_phase is not None:
            return
        self._applied_phase = applied
        self._applied_time_s = applied_time

    def complete(
        self,
        next_local_obs: np.ndarray,
        next_global_state: CentralizedState,
        next_value: float,
        *,
        terminated: bool = False,
        truncated: bool = False,
    ) -> Transition:
        if self.invalid:
            raise RuntimeError("rollout is invalid")
        if self._pending is None:
            raise RuntimeError("no transition is pending")
        if self._applied_phase is None or self._applied_time_s is None:
            raise RuntimeError("requested phase application has not been confirmed")
        if bool(terminated) and bool(truncated):
            raise ValueError("a transition cannot be both terminated and truncated")
        successor = np.asarray(next_local_obs, dtype=np.float32)
        if successor.shape != np.asarray(self._pending.local_obs).shape:
            raise ValueError("next local observation shape does not match current")
        if not np.isfinite(successor).all():
            raise ValueError("next local observation must contain only finite values")
        bootstrap = float(next_value)
        if not math.isfinite(bootstrap):
            raise ValueError("next value must be finite")

        pending = self._pending
        transition = Transition(
            local_obs=np.array(pending.local_obs, dtype=np.float32, copy=True),
            phase_features=np.array(
                pending.phase_features, dtype=np.float32, copy=True
            ),
            action_mask=np.array(pending.action_mask, dtype=np.bool_, copy=True),
            global_state=pending.global_state,
            agent_index=int(pending.agent_index),
            action=int(pending.action),
            requested_phase=int(pending.requested_phase),
            applied_phase=self._applied_phase,
            log_prob=float(pending.log_prob),
            value=float(pending.value),
            reward=float(self._reward),
            decision_time_s=float(pending.decision_time_s),
            applied_time_s=self._applied_time_s,
            policy_generation=int(pending.policy_generation),
            next_local_obs=np.array(successor, dtype=np.float32, copy=True),
            next_global_state=next_global_state,
            next_value=bootstrap,
            terminated=bool(terminated),
            truncated=bool(truncated),
        )
        self._pending = None
        self._reward = 0.0
        self._applied_phase = None
        self._applied_time_s = None
        return transition


def compute_gae(
    rewards: np.ndarray,
    values: np.ndarray,
    next_values: np.ndarray,
    terminated: np.ndarray,
    truncated: np.ndarray,
    gamma: float,
    gae_lambda: float,
) -> tuple[np.ndarray, np.ndarray]:
    reward_values = np.asarray(rewards, dtype=np.float32)
    current_values = np.asarray(values, dtype=np.float32)
    successor_values = np.asarray(next_values, dtype=np.float32)
    terminal_flags = np.asarray(terminated, dtype=np.bool_)
    truncation_flags = np.asarray(truncated, dtype=np.bool_)
    expected_shape = reward_values.shape
    arrays = (
        current_values,
        successor_values,
        terminal_flags,
        truncation_flags,
    )
    if reward_values.ndim != 1 or any(
        value.shape != expected_shape for value in arrays
    ):
        raise ValueError("GAE inputs must be one-dimensional arrays of equal length")
    if np.any(terminal_flags & truncation_flags):
        raise ValueError("a transition cannot be both terminated and truncated")
    if not all(
        np.isfinite(value).all()
        for value in (reward_values, current_values, successor_values)
    ):
        raise ValueError("GAE rewards and values must be finite")
    discount = float(gamma)
    trace_decay = float(gae_lambda)
    if not 0.0 <= discount <= 1.0:
        raise ValueError("gamma must be between zero and one")
    if not 0.0 <= trace_decay <= 1.0:
        raise ValueError("GAE lambda must be between zero and one")

    bootstrap_values = np.where(
        terminal_flags, np.float32(0.0), successor_values
    )
    deltas = reward_values + discount * bootstrap_values - current_values
    advantages = np.zeros_like(reward_values, dtype=np.float32)
    running_advantage = 0.0
    for index in range(len(reward_values) - 1, -1, -1):
        continues = not (
            bool(terminal_flags[index]) or bool(truncation_flags[index])
        )
        running_advantage = float(deltas[index]) + (
            discount * trace_decay * float(continues) * running_advantage
        )
        advantages[index] = running_advantage
    returns = advantages + current_values
    return advantages, returns.astype(np.float32, copy=False)
