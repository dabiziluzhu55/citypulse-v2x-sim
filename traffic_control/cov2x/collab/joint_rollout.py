"""Multi-timescale rollout storage for the joint vehicle-road-cloud stack.

The three policy families act on different clocks (vehicle 5 s, signal 15 s,
cloud 30 s by default).  Each family keeps its own sequence of decision steps;
a step's ``reward``/``next_value`` are populated only when the same identity
is active at the following decision of that family, otherwise the segment
ends (terminal bootstrap with value 0).  This gives well-defined truncation
semantics before GAE is computed, per the develop-coslight-marl skill.

Advantages/returns are computed per family with a physical-time-consistent
discount: ``gamma_family = gamma ** (decision_interval_s / 5.0)``, so one
15 s signal step discounts the same as three 5 s vehicle steps.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Callable, Sequence

import numpy as np

from traffic_control.cov2x.collab.joint_rewards import TrafficSnapshot
from traffic_control.cov2x.vehicle.rollout import RolloutStep


@dataclass
class SignalRolloutStep:
    """One road-agent decision for one intersection."""

    tls_id: str
    state: np.ndarray  # SIGNAL_OBS_DIM
    mask: np.ndarray  # MAX_PHASES bool
    action: int  # 0 = keep current phase, 1 = advance to next phase
    logprob: float
    value: float
    sim_time: float
    step_id: int
    source_phase: int
    requested_phase: int
    phase_order: tuple[int, ...]
    traffic_before: TrafficSnapshot | None = None
    global_state: np.ndarray | None = None
    reward: float | None = None
    next_value: float | None = None
    reward_components: dict[str, float] = field(default_factory=dict)
    reward_basis: str = ""
    executed_phase: int | None = None
    executed_switch: bool = False
    advantage: float | None = None
    return_: float | None = None


@dataclass
class CloudRolloutStep:
    """One cloud coordinator decision for the whole network."""

    state: np.ndarray  # CLOUD_OBS_DIM
    action: np.ndarray  # (n_intersections,) priority class per intersection
    logprob: float
    value: float
    sim_time: float
    step_id: int
    intersection_ids: tuple[str, ...]
    traffic_before: TrafficSnapshot | None = None
    global_state: np.ndarray | None = None
    reward: float | None = None
    next_value: float | None = None
    reward_components: dict[str, float] = field(default_factory=dict)
    reward_basis: str = ""
    advantage: float | None = None
    return_: float | None = None


@dataclass
class JointRollout:
    """One episode's transitions for one joint PPO update."""

    episode_id: str
    period: str
    seed: int
    duration_s: float
    generation: int
    signal_mode: str
    vehicle_steps: list[RolloutStep] = field(default_factory=list)
    signal_steps: list[SignalRolloutStep] = field(default_factory=list)
    cloud_steps: list[CloudRolloutStep] = field(default_factory=list)
    metrics: dict[str, Any] = field(default_factory=dict)


def _split_segments(
    steps: Sequence[Any], key_fn: Callable[[Any], Any]
) -> list[list[Any]]:
    by_key: dict[Any, list[Any]] = {}
    order: list[Any] = []
    for step in steps:
        key = key_fn(step)
        if key not in by_key:
            by_key[key] = []
            order.append(key)
        by_key[key].append(step)
    segments: list[list[Any]] = []
    for key in order:
        current: list[Any] = []
        for step in by_key[key]:
            current.append(step)
            if step.next_value is None:
                segments.append(current)
                current = []
        if current:
            segments.append(current)
    return segments


def compute_gae(
    steps: Sequence[Any],
    key_fn: Callable[[Any], Any],
    *,
    gamma: float = 0.99,
    lam: float = 0.95,
) -> None:
    """Fill ``advantage``/``return_`` on a family's steps (in-place)."""
    for segment in _split_segments(steps, key_fn):
        size = len(segment)
        values = np.asarray([step.value for step in segment], dtype=np.float64)
        rewards = np.asarray(
            [float(step.reward) if step.reward is not None else 0.0 for step in segment],
            dtype=np.float64,
        )
        next_values = np.asarray(
            [float(step.next_value) if step.next_value is not None else 0.0 for step in segment],
            dtype=np.float64,
        )
        deltas = rewards + gamma * next_values - values
        advantages = np.zeros(size, dtype=np.float64)
        running = 0.0
        for index in range(size - 1, -1, -1):
            running = deltas[index] + gamma * lam * running
            advantages[index] = running
        returns = advantages + values
        for index, step in enumerate(segment):
            step.advantage = float(advantages[index])
            step.return_ = float(returns[index])


def _reward_array(
    steps: Sequence[Any], attr: str
) -> np.ndarray:
    return np.asarray(
        [getattr(step, attr) if getattr(step, attr) is not None else 0.0 for step in steps],
        dtype=np.float32,
    )


def to_vehicle_joint_arrays(rollout: JointRollout) -> dict[str, np.ndarray]:
    """Stack vehicle steps with the cloud context appended to state_41."""
    steps = rollout.vehicle_steps
    if not steps:
        return {}
    states = np.stack(
        [
            np.concatenate(
                [
                    np.asarray(step.obs.state_41, dtype=np.float32),
                    (
                        np.asarray(step.obs.cloud_context, dtype=np.float32)
                        if step.obs.cloud_context is not None
                        else np.asarray([0.0, 1.0, 0.0], dtype=np.float32)
                    ),
                ]
            )
            for step in steps
        ]
    )
    masks = np.stack(
        [np.asarray(step.obs.action_mask, dtype=bool) for step in steps]
    )
    return {
        "states": states,
        "masks": masks,
        "lane_actions": np.asarray([step.lane_action for step in steps], dtype=np.int64),
        "speed_bins": np.asarray([step.speed_bin for step in steps], dtype=np.int64),
        "old_logprobs": np.asarray([step.logprob for step in steps], dtype=np.float32),
        "values": np.asarray([step.value for step in steps], dtype=np.float32),
        "advantages": _reward_array(steps, "advantage"),
        "returns": _reward_array(steps, "return_"),
        "global_states": np.stack(
            [step.global_state for step in steps if step.global_state is not None]
        )
        if all(step.global_state is not None for step in steps)
        else np.zeros((len(steps), 0), dtype=np.float32),
    }


def to_signal_joint_arrays(rollout: JointRollout) -> dict[str, np.ndarray]:
    steps = rollout.signal_steps
    if not steps:
        return {}
    return {
        "states": np.stack([np.asarray(step.state, dtype=np.float32) for step in steps]),
        "masks": np.stack([np.asarray(step.mask, dtype=bool) for step in steps]),
        "actions": np.asarray([step.action for step in steps], dtype=np.int64),
        "old_logprobs": np.asarray([step.logprob for step in steps], dtype=np.float32),
        "values": np.asarray([step.value for step in steps], dtype=np.float32),
        "advantages": _reward_array(steps, "advantage"),
        "returns": _reward_array(steps, "return_"),
        "global_states": np.stack(
            [step.global_state for step in steps if step.global_state is not None]
        )
        if all(step.global_state is not None for step in steps)
        else np.zeros((len(steps), 0), dtype=np.float32),
    }


def to_cloud_joint_arrays(rollout: JointRollout) -> dict[str, np.ndarray]:
    steps = rollout.cloud_steps
    if not steps:
        return {}
    return {
        "states": np.stack([np.asarray(step.state, dtype=np.float32) for step in steps]),
        "actions": np.stack([np.asarray(step.action, dtype=np.int64) for step in steps]),
        "old_logprobs": np.asarray([step.logprob for step in steps], dtype=np.float32),
        "values": np.asarray([step.value for step in steps], dtype=np.float32),
        "advantages": _reward_array(steps, "advantage"),
        "returns": _reward_array(steps, "return_"),
        "global_states": np.stack(
            [step.global_state for step in steps if step.global_state is not None]
        )
        if all(step.global_state is not None for step in steps)
        else np.zeros((len(steps), 0), dtype=np.float32),
    }


def to_critic_arrays(rollout: JointRollout) -> dict[str, np.ndarray]:
    """Collect every (global state, return) pair across all three families."""
    pairs: list[tuple[np.ndarray, float]] = []
    for step in rollout.vehicle_steps:
        if step.global_state is not None and step.return_ is not None:
            pairs.append((step.global_state, step.return_))
    for step in rollout.signal_steps:
        if step.global_state is not None and step.return_ is not None:
            pairs.append((step.global_state, step.return_))
    for step in rollout.cloud_steps:
        if step.global_state is not None and step.return_ is not None:
            pairs.append((step.global_state, step.return_))
    if not pairs:
        return {}
    states = np.stack([pair[0] for pair in pairs])
    returns = np.asarray([pair[1] for pair in pairs], dtype=np.float32)
    return {"global_states": states, "returns": returns}
