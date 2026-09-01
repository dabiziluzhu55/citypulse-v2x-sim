"""Physical-time SMDP transitions and GAE for heterogeneous clocks."""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Iterable

import numpy as np


@dataclass
class SMDPTransition:
    role: str
    snapshot_id: str
    observation: Any
    action: Any
    old_logprob: float
    value: float
    reward: float
    duration_s: float
    next_value: float | None = None
    done: bool = False
    policy_generation: int = 0
    causal_parents: tuple[str, ...] = ()
    executed_action: Any = None
    entity_id: str = ""
    decision_time_s: float = 0.0
    global_state: Any = None
    agent_slot: int | None = None

    def close(self, *, next_value: float | None, reward: float, done: bool = False, duration_s: float | None = None) -> None:
        if self.next_value is not None or self.done:
            raise ValueError("SMDP transition already closed")
        if duration_s is not None:
            self.duration_s = float(duration_s)
        if self.duration_s <= 0.0:
            raise ValueError("duration_s must be positive")
        self.next_value = None if done else float(next_value if next_value is not None else 0.0)
        self.reward = float(reward)
        self.done = bool(done)


def smdp_gae(transitions: Iterable[SMDPTransition], *, gamma: float = 0.99, lam: float = 0.95, base_interval_s: float = 5.0) -> tuple[np.ndarray, np.ndarray]:
    """Use gamma^(dt/5) and lambda^(dt/5) on one ordered role/entity sequence."""
    items = list(transitions)
    if not items:
        return np.zeros(0, dtype=np.float32), np.zeros(0, dtype=np.float32)
    advantages = np.zeros(len(items), dtype=np.float64)
    running = 0.0
    for index in range(len(items) - 1, -1, -1):
        step = items[index]
        exponent = float(step.duration_s) / max(float(base_interval_s), 1e-9)
        gamma_eff = float(gamma) ** exponent
        lambda_eff = float(lam) ** exponent
        bootstrap = 0.0 if step.done or step.next_value is None else float(step.next_value)
        delta = float(step.reward) + gamma_eff * bootstrap - float(step.value)
        if step.done:
            running = 0.0
        running = delta + gamma_eff * lambda_eff * running
        advantages[index] = running
    values = np.asarray([float(item.value) for item in items], dtype=np.float64)
    return advantages.astype(np.float32), (advantages + values).astype(np.float32)


def role_entity_gae(transitions: Iterable[SMDPTransition], *, gamma: float = 0.99, lam: float = 0.95, base_interval_s: float = 5.0) -> tuple[np.ndarray, np.ndarray]:
    """Compute GAE without leaking recurrence between heterogeneous identities."""
    items = list(transitions)
    advantages = np.zeros(len(items), dtype=np.float32)
    returns = np.zeros(len(items), dtype=np.float32)
    groups: dict[tuple[str, str], list[int]] = {}
    for index, item in enumerate(items):
        groups.setdefault((item.role, item.entity_id), []).append(index)
    for indices in groups.values():
        adv, ret = smdp_gae([items[index] for index in indices], gamma=gamma, lam=lam, base_interval_s=base_interval_s)
        advantages[indices] = adv; returns[indices] = ret
    return advantages, returns


def assert_closed(transitions: Iterable[SMDPTransition]) -> None:
    for transition in transitions:
        if not transition.done and transition.next_value is None:
            raise ValueError(f"open SMDP transition: {transition.snapshot_id}:{transition.entity_id}")
