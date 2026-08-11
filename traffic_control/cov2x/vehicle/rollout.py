"""Per-episode rollout storage, GAE, and training-array conversion.

The rollout is organized around the approach-advisor slot identity: each
``RolloutStep`` belongs to one fixed incoming-edge slot of a controlled
intersection.  A step's ``reward``/``next_value`` are only populated when the
same slot is active at the following decision; otherwise the segment ends
(terminal bootstrap with value 0).  This gives a well-defined truncation
semantics before GAE is computed, per the develop-coslight-marl skill.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Mapping, Sequence

import numpy as np

from traffic_control.cov2x.vehicle.agent import VehicleObservation


@dataclass
class RolloutStep:
    """One approach-slot decision plus its settled outcome."""

    slot_index: int
    vehicle_id: str
    edge_id: str
    tls_id: str
    obs: VehicleObservation
    lane_action: int
    speed_bin: int
    logprob: float
    value: float
    sim_time: float
    step_id: int
    reward: float | None = None
    next_value: float | None = None
    reward_components: dict[str, float] = field(default_factory=dict)
    reward_basis: str = ""  # fresh | stale | terminal
    requested: dict[str, Any] = field(default_factory=dict)
    executed: dict[str, Any] = field(default_factory=dict)
    traffic_before: Any = None
    global_state: Any = None
    advantage: float | None = None
    return_: float | None = None


@dataclass
class Rollout:
    """One complete episode's transitions for a single PPO update."""

    episode_id: str
    period: str
    seed: int
    duration_s: float
    generation: int
    signal_mode: str
    steps: list[RolloutStep] = field(default_factory=list)
    metrics: dict[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        self.metrics.setdefault("decision_count", 0)
        self.metrics.setdefault("command_count", 0)
        self.metrics.setdefault("lane_change_requested", 0)
        self.metrics.setdefault("lane_change_completed", 0)
        self.metrics.setdefault("lane_change_not_completed", 0)
        self.metrics.setdefault("speed_command_count", 0)
        self.metrics.setdefault("reward_fresh_count", 0)
        self.metrics.setdefault("reward_stale_count", 0)
        self.metrics.setdefault("reward_terminal_count", 0)


def split_segments(steps: Sequence[RolloutStep]) -> list[list[RolloutStep]]:
    """Group steps by slot and split where the next observation is missing."""
    by_slot: dict[int, list[RolloutStep]] = {}
    order: list[int] = []
    for step in steps:
        if step.slot_index not in by_slot:
            by_slot[step.slot_index] = []
            order.append(step.slot_index)
        by_slot[step.slot_index].append(step)

    segments: list[list[RolloutStep]] = []
    for slot_index in order:
        current: list[RolloutStep] = []
        for step in by_slot[slot_index]:
            current.append(step)
            if step.next_value is None:
                segments.append(current)
                current = []
        if current:
            segments.append(current)
    return segments


def compute_gae(
    steps: Sequence[RolloutStep],
    *,
    gamma: float = 0.99,
    lam: float = 0.95,
) -> None:
    """Fill ``advantage``/``return_`` on each step (in-place)."""
    for segment in split_segments(steps):
        size = len(segment)
        values = np.asarray([step.value for step in segment], dtype=np.float64)
        rewards = np.zeros(size, dtype=np.float64)
        next_values = np.zeros(size, dtype=np.float64)
        for index, step in enumerate(segment):
            rewards[index] = (
                float(step.reward) if step.reward is not None else 0.0
            )
            next_values[index] = (
                float(step.next_value)
                if step.next_value is not None
                else 0.0
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


def to_training_arrays(rollout: Rollout) -> dict[str, np.ndarray]:
    """Stack rollout steps into PPO input arrays."""
    if not rollout.steps:
        return {}
    states = np.stack(
        [np.asarray(step.obs.state_41, dtype=np.float32) for step in rollout.steps]
    )
    masks = np.stack(
        [np.asarray(step.obs.action_mask, dtype=bool) for step in rollout.steps]
    )
    lane_actions = np.asarray(
        [step.lane_action for step in rollout.steps], dtype=np.int64
    )
    speed_bins = np.asarray([step.speed_bin for step in rollout.steps], dtype=np.int64)
    old_logprobs = np.asarray([step.logprob for step in rollout.steps], dtype=np.float32)
    advantages = np.asarray(
        [step.advantage if step.advantage is not None else 0.0 for step in rollout.steps],
        dtype=np.float32,
    )
    returns = np.asarray(
        [step.return_ if step.return_ is not None else 0.0 for step in rollout.steps],
        dtype=np.float32,
    )
    values = np.asarray([step.value for step in rollout.steps], dtype=np.float32)
    return {
        "states": states,
        "masks": masks,
        "lane_actions": lane_actions,
        "speed_bins": speed_bins,
        "old_logprobs": old_logprobs,
        "advantages": advantages,
        "returns": returns,
        "values": values,
    }


def episode_summary(rollout: Rollout) -> dict[str, Any]:
    """Auditable summary of commands, execution, and reward components."""
    summary: dict[str, Any] = {
        "decision_count": len(rollout.steps),
        "command_count": 0,
        "speed_command_count": 0,
        "lane_change_requested": 0,
        "lane_change_completed": 0,
        "lane_change_not_completed": 0,
        "reward_fresh_count": 0,
        "reward_stale_count": 0,
        "reward_terminal_count": 0,
        "reward_sum": 0.0,
        "reward_mean": None,
        "reward_components": {},
    }
    if not rollout.steps:
        return summary

    component_totals: dict[str, float] = {}
    reward_values: list[float] = []
    for step in rollout.steps:
        if step.requested:
            summary["command_count"] += 1
        if "target_speed_mps" in step.requested:
            summary["speed_command_count"] += 1
        if "target_lane_index" in step.requested:
            summary["lane_change_requested"] += 1
            status = step.executed.get("lane_change_status")
            if status == "completed":
                summary["lane_change_completed"] += 1
            elif status == "not_completed":
                summary["lane_change_not_completed"] += 1
        if step.reward is not None:
            reward_values.append(float(step.reward))
            for key, value in (step.reward_components or {}).items():
                component_totals[key] = component_totals.get(key, 0.0) + float(value)
        summary[f"reward_{step.reward_basis}_count"] = (
            summary.get(f"reward_{step.reward_basis}_count", 0) + 1
        )

    summary["reward_sum"] = float(sum(reward_values))
    summary["reward_mean"] = (
        float(np.mean(reward_values)) if reward_values else None
    )
    summary["reward_components"] = {
        key: round(value, 6) for key, value in component_totals.items()
    }
    if summary["lane_change_requested"]:
        summary["lane_change_execution_rate"] = round(
            summary["lane_change_completed"] / summary["lane_change_requested"],
            4,
        )
    else:
        summary["lane_change_execution_rate"] = None
    return summary
