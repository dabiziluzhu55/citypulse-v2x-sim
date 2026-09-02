"""Shared vehicle/approach policy: PPO actor-critic with masked lane head.

Agent identity follows the approach-advisor contract in
``algorithms/cov2x/agents/vehicle.py``: one fixed slot per incoming edge of a
controlled intersection, parameters shared across the homogeneous
population.  The actor emits a masked discrete lane command (keep/left/right)
and a discrete speed fraction; the critic is a local per-slot value function
for Stage 2 (vehicle-only) learning.  Centralized CTDE critics are a later
stage and intentionally not claimed here.
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field
from typing import Any, Iterable, Mapping, Sequence

import numpy as np
import torch
import torch.nn as nn
import torch.nn.functional as F

from algorithms.cov2x.vehicle.agent import (
    LANE_ACTION_KEEP,
    SPEED_FRACTIONS,
    VehicleAction,
    VehicleAgent,
    VehicleObservation,
)
from algorithms.cov2x.vehicle.rewards import (
    GUIDE_ZONE_MAX_M,
    MAX_ACCEL_MPS2_DEFAULT,
    VehicleRewardWeights,
)


@dataclass(frozen=True)
class VehiclePolicyConfig:
    """Hyperparameters for the shared vehicle/approach policy."""

    obs_dim: int = 41
    hidden_dim: int = 128
    lane_action_dim: int = 3
    speed_action_dim: int = len(SPEED_FRACTIONS)
    init_keep_lane_bias: float = 1.0
    init_keep_speed_bias: float = 2.0
    decision_interval_s: float = 5.0
    reward_weights: VehicleRewardWeights = VehicleRewardWeights()
    max_accel_mps2: float = MAX_ACCEL_MPS2_DEFAULT
    guide_zone_max_m: float = GUIDE_ZONE_MAX_M
    gamma: float = 0.99
    lam: float = 0.95
    clip_eps: float = 0.2
    value_coef: float = 0.5
    entropy_coef: float = 0.01
    ppo_epochs: int = 4
    minibatch_size: int = 256
    lr_actor: float = 3e-4
    lr_critic: float = 1e-3
    max_grad_norm: float = 0.5
    norm_adv: bool = True
    device: str = "cpu"


@dataclass(frozen=True)
class VehiclePolicyStep:
    """Tensors returned by one batched policy call."""

    lane_action: torch.Tensor
    speed_bin: torch.Tensor
    logprob: torch.Tensor
    value: torch.Tensor
    entropy: torch.Tensor


@dataclass(frozen=True)
class VehicleBatchStep:
    """CPU/numpy view of one batched policy call."""

    lane_action: np.ndarray
    speed_bin: np.ndarray
    logprob: np.ndarray
    value: np.ndarray
    entropy: np.ndarray


class VehicleActorCritic(nn.Module):
    """MLP actor-critic for the homogeneous approach-advisor population."""

    def __init__(self, config: VehiclePolicyConfig) -> None:
        super().__init__()
        self.config = config
        hidden = config.hidden_dim
        self.trunk = nn.Sequential(
            nn.Linear(config.obs_dim, hidden),
            nn.Tanh(),
            nn.Linear(hidden, hidden),
            nn.Tanh(),
        )
        self.lane_head = nn.Linear(hidden, config.lane_action_dim)
        self.speed_head = nn.Linear(hidden, config.speed_action_dim)
        self.critic = nn.Sequential(
            nn.Linear(config.obs_dim, hidden),
            nn.Tanh(),
            nn.Linear(hidden, hidden),
            nn.Tanh(),
            nn.Linear(hidden, 1),
        )
        self._apply_init_biases()

    def _apply_init_biases(self) -> None:
        with torch.no_grad():
            if self.config.init_keep_lane_bias:
                self.lane_head.bias.add_(
                    torch.tensor(
                        [
                            self.config.init_keep_lane_bias,
                            0.0,
                            0.0,
                        ]
                    )
                )
            if self.config.init_keep_speed_bias:
                bias = torch.zeros(self.config.speed_action_dim)
                bias[-1] = self.config.init_keep_speed_bias
                self.speed_head.bias.add_(bias)

    def _forward_heads(self, obs: torch.Tensor, masks: torch.Tensor) -> tuple[torch.Tensor, torch.Tensor, torch.Tensor]:
        features = self.trunk(obs)
        lane_logits = self.lane_head(features)
        speed_logits = self.speed_head(features)
        if masks is not None:
            masks = masks.bool().clone()
            # A slot must always keep at least the KEEP action legal.
            masks[~masks.any(dim=-1), LANE_ACTION_KEEP] = True
            lane_logits = lane_logits.masked_fill(
                ~masks, -torch.inf
            )
        value = self.critic(obs).squeeze(-1)
        return lane_logits, speed_logits, value

    def act(
        self,
        obs: torch.Tensor,
        masks: torch.Tensor,
        *,
        deterministic: bool,
    ) -> VehiclePolicyStep:
        lane_logits, speed_logits, value = self._forward_heads(obs, masks)
        lane_dist = torch.distributions.Categorical(logits=lane_logits)
        speed_dist = torch.distributions.Categorical(logits=speed_logits)
        if deterministic:
            lane_action = lane_dist.probs.argmax(dim=-1)
            speed_bin = speed_dist.probs.argmax(dim=-1)
        else:
            lane_action = lane_dist.sample()
            speed_bin = speed_dist.sample()
        logprob = lane_dist.log_prob(lane_action) + speed_dist.log_prob(speed_bin)
        entropy = lane_dist.entropy() + speed_dist.entropy()
        return VehiclePolicyStep(
            lane_action=lane_action,
            speed_bin=speed_bin,
            logprob=logprob,
            value=value,
            entropy=entropy,
        )

    def evaluate(
        self,
        obs: torch.Tensor,
        masks: torch.Tensor,
        lane_action: torch.Tensor,
        speed_bin: torch.Tensor,
    ) -> tuple[torch.Tensor, torch.Tensor, torch.Tensor]:
        """Return ``(logprob, entropy, value)`` for given actions."""
        lane_logits, speed_logits, value = self._forward_heads(obs, masks)
        lane_dist = torch.distributions.Categorical(logits=lane_logits)
        speed_dist = torch.distributions.Categorical(logits=speed_logits)
        logprob = lane_dist.log_prob(lane_action) + speed_dist.log_prob(speed_bin)
        entropy = lane_dist.entropy() + speed_dist.entropy()
        return logprob, entropy, value

    def get_value(self, obs: torch.Tensor) -> torch.Tensor:
        return self.critic(obs).squeeze(-1)


class VehiclePPOAgent(VehicleAgent):
    """Trainable shared approach-advisor policy (PPO)."""

    def __init__(self, config: VehiclePolicyConfig) -> None:
        # VehicleAgent stores ``self.config``; VehiclePPOAgent uses the
        # richer VehiclePolicyConfig (decision interval included).
        self.config = config
        self.net = VehicleActorCritic(config).to(config.device)
        actor_params = list(self.net.trunk.parameters()) + list(
            self.net.lane_head.parameters()
        ) + list(self.net.speed_head.parameters())
        self.actor_optimizer = torch.optim.Adam(
            actor_params, lr=config.lr_actor
        )
        self.critic_optimizer = torch.optim.Adam(
            self.net.critic.parameters(), lr=config.lr_critic
        )
        self.policy_generation = 0
        self.episode_count = 0

    @property
    def decision_interval_s(self) -> float:
        return self.config.decision_interval_s

    def reset(self, episode_id: str) -> None:
        self.episode_id = episode_id

    def act_batch(
        self,
        observations: Sequence[VehicleObservation],
        *,
        deterministic: bool = False,
    ) -> VehicleBatchStep:
        """Sample actions for one batch of active approach slots."""
        if not observations:
            return VehicleBatchStep(
                lane_action=np.zeros(0, dtype=np.int64),
                speed_bin=np.zeros(0, dtype=np.int64),
                logprob=np.zeros(0, dtype=np.float32),
                value=np.zeros(0, dtype=np.float32),
                entropy=np.zeros(0, dtype=np.float32),
            )
        states = np.stack(
            [np.asarray(obs.state_41, dtype=np.float32) for obs in observations]
        )
        masks = np.stack(
            [np.asarray(obs.action_mask, dtype=bool) for obs in observations]
        )
        return self.act_tensors(states, masks, deterministic=deterministic)

    def act_tensors(
        self,
        states: np.ndarray,
        masks: np.ndarray,
        *,
        deterministic: bool = False,
    ) -> VehicleBatchStep:
        obs = torch.from_numpy(np.asarray(states, dtype=np.float32)).to(
            self.config.device
        )
        mask = torch.from_numpy(np.asarray(masks, dtype=bool)).to(
            self.config.device
        )
        with torch.no_grad():
            step = self.net.act(obs, mask, deterministic=deterministic)
        return VehicleBatchStep(
            lane_action=step.lane_action.cpu().numpy().astype(np.int64),
            speed_bin=step.speed_bin.cpu().numpy().astype(np.int64),
            logprob=step.logprob.cpu().numpy().astype(np.float32),
            value=step.value.cpu().numpy().astype(np.float32),
            entropy=step.entropy.cpu().numpy().astype(np.float32),
        )

    def decide(self, obs: VehicleObservation) -> VehicleAction:
        """Deterministic single-slot action (inference path)."""
        if obs.state_41 is None or obs.action_mask is None:
            raise ValueError("learned vehicle decision requires state_41/action_mask")
        step = self.act_batch([obs], deterministic=True)
        lane_action = int(step.lane_action[0])
        speed_frac = float(SPEED_FRACTIONS[int(step.speed_bin[0])])
        target_lane = None
        if lane_action != LANE_ACTION_KEEP and obs.lane_index is not None:
            target_lane = obs.lane_index + (1 if lane_action == 1 else -1)
        return VehicleAction(
            target_speed_mps=float(speed_frac * obs.allowed_speed_mps),
            target_lane_index=target_lane,
            source="learned",
        )

    def update(
        self,
        *,
        states: np.ndarray,
        masks: np.ndarray,
        lane_actions: np.ndarray,
        speed_bins: np.ndarray,
        old_logprobs: np.ndarray,
        advantages: np.ndarray,
        returns: np.ndarray,
    ) -> dict[str, float]:
        """One PPO update over an episode's transitions.

        Returns diagnostic scalars (losses, entropy, clip fraction, gradient
        and parameter deltas) so the learning causal chain can be audited.
        """
        obs = torch.from_numpy(np.asarray(states, dtype=np.float32)).to(
            self.config.device
        )
        mask = torch.from_numpy(np.asarray(masks, dtype=bool)).to(
            self.config.device
        )
        lane = torch.from_numpy(np.asarray(lane_actions, dtype=np.int64)).to(
            self.config.device
        )
        speed = torch.from_numpy(np.asarray(speed_bins, dtype=np.int64)).to(
            self.config.device
        )
        old_logp = torch.from_numpy(
            np.asarray(old_logprobs, dtype=np.float32)
        ).to(self.config.device)
        adv = torch.from_numpy(np.asarray(advantages, dtype=np.float32)).to(
            self.config.device
        )
        ret = torch.from_numpy(np.asarray(returns, dtype=np.float32)).to(
            self.config.device
        )
        if self.config.norm_adv and adv.numel() > 1:
            adv = (adv - adv.mean()) / (adv.std() + 1e-8)

        before = {
            name: param.detach().clone()
            for name, param in self.net.named_parameters()
        }
        n = obs.shape[0]
        indices = np.arange(n)
        clip_fraction = 0.0
        total_batches = 0
        diagnostics: dict[str, float] = {}

        for _ in range(self.config.ppo_epochs):
            np.random.shuffle(indices)
            batch_size = max(1, min(self.config.minibatch_size, n))
            for start in range(0, n, batch_size):
                batch = indices[start : start + batch_size]
                b_obs = obs[batch]
                b_mask = mask[batch]
                b_lane = lane[batch]
                b_speed = speed[batch]
                b_old = old_logp[batch]
                b_adv = adv[batch]
                b_ret = ret[batch]

                logprob, entropy, value = self.net.evaluate(
                    b_obs, b_mask, b_lane, b_speed
                )
                ratio = torch.exp(logprob - b_old)
                clipped = torch.clamp(
                    ratio, 1.0 - self.config.clip_eps, 1.0 + self.config.clip_eps
                )
                surr1 = ratio * b_adv
                surr2 = clipped * b_adv
                actor_loss = -(torch.min(surr1, surr2).mean())
                actor_loss = actor_loss - self.config.entropy_coef * entropy.mean()
                value_loss = F.mse_loss(value, b_ret)
                total_loss = actor_loss + self.config.value_coef * value_loss

                self.actor_optimizer.zero_grad()
                self.critic_optimizer.zero_grad()
                total_loss.backward()
                actor_grad = self._grad_norm(
                    self.net.trunk.parameters(),
                    self.net.lane_head.parameters(),
                    self.net.speed_head.parameters(),
                )
                critic_grad = self._grad_norm(self.net.critic.parameters())
                nn.utils.clip_grad_norm_(
                    self.net.trunk.parameters(), self.config.max_grad_norm
                )
                nn.utils.clip_grad_norm_(
                    self.net.lane_head.parameters(), self.config.max_grad_norm
                )
                nn.utils.clip_grad_norm_(
                    self.net.speed_head.parameters(), self.config.max_grad_norm
                )
                nn.utils.clip_grad_norm_(
                    self.net.critic.parameters(), self.config.max_grad_norm
                )
                self.actor_optimizer.step()
                self.critic_optimizer.step()

                clip_fraction += float(
                    ((ratio - 1.0).abs() > self.config.clip_eps)
                    .float()
                    .mean()
                    .item()
                )
                total_batches += 1
                diagnostics = {
                    "actor_loss": float(actor_loss.item()),
                    "critic_loss": float(value_loss.item()),
                    "entropy": float(entropy.mean().item()),
                    "clip_fraction": clip_fraction / max(total_batches, 1),
                    "actor_grad_norm": float(actor_grad),
                    "critic_grad_norm": float(critic_grad),
                    "advantage_mean": float(adv.mean().item()),
                    "advantage_abs_mean": float(adv.abs().mean().item()),
                    "advantage_std": float(adv.std().item()),
                }

        with torch.no_grad():
            full_values = self.net.get_value(obs)
        diagnostics["explained_variance"] = float(
            self._explained_variance(full_values, ret)
        )
        after = {
            name: param.detach()
            for name, param in self.net.named_parameters()
        }
        total_delta = 0.0
        for name in before:
            delta = (after[name] - before[name]).norm().item()
            total_delta += delta * delta
        diagnostics["parameter_delta_l2"] = float(math.sqrt(total_delta))
        self.policy_generation += 1
        return diagnostics

    @staticmethod
    def _grad_norm(*parameter_groups: Iterable[nn.Parameter]) -> float:
        total = 0.0
        for group in parameter_groups:
            for parameter in group:
                if parameter.grad is not None:
                    total += float(parameter.grad.detach().norm().item() ** 2)
        return float(math.sqrt(total))

    @staticmethod
    def _explained_variance(value: torch.Tensor, returns: torch.Tensor) -> float:
        if value.numel() < 2 or returns.numel() < 2:
            return 0.0
        var_y = returns.var().item()
        if var_y < 1e-12:
            return 1.0
        return float(
            1.0
            - F.mse_loss(value.detach(), returns).item() / var_y
        )

    def state_dict(self) -> dict[str, Any]:
        return {
            "net": self.net.state_dict(),
            "actor_optimizer": self.actor_optimizer.state_dict(),
            "critic_optimizer": self.critic_optimizer.state_dict(),
            "policy_generation": self.policy_generation,
            "episode_count": self.episode_count,
        }

    def load_state_dict(self, state: Mapping[str, Any]) -> None:
        self.net.load_state_dict(state["net"])
        self.actor_optimizer.load_state_dict(state["actor_optimizer"])
        self.critic_optimizer.load_state_dict(state["critic_optimizer"])
        self.policy_generation = int(state.get("policy_generation", 0))
        self.episode_count = int(state.get("episode_count", 0))
