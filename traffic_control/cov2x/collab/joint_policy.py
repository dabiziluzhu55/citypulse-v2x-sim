"""Joint vehicle-road-cloud CTDE policy family (MAPPO-style).

Three heterogeneous actor families share one centralized critic:

- signal actor: one slot per controlled intersection, discrete phase index;
- vehicle actor: one slot per incoming-edge approach, lane + speed actions;
- cloud actor: one network-level slot, per-intersection priority class.

Each family owns its parameters (shared only within the family).  The critic
sees a global state (all intersections + network traffic + cloud priorities)
and predicts the value used for every family's GAE.  Updates are applied per
family with their own optimizer; the critic is trained on all families'
(global state, return) pairs.  This is the smallest defensible MAPPO-style
joint stack and does not claim HAPPO-style sequential updates.
"""

from __future__ import annotations

import math
from dataclasses import dataclass
from typing import Any, Mapping, Sequence

import numpy as np
import torch
import torch.nn as nn
import torch.nn.functional as F

from traffic_control.cov2x.vehicle.agent import (
    LANE_ACTION_KEEP,
    SPEED_FRACTIONS,
    VehicleObservation,
)
from traffic_control.cov2x.cloud.observations import (
    CLOUD_OBS_DIM,
    CLOUD_PRIORITY_CLASSES,
    GLOBAL_STATE_DIM,
    CloudObservation,
)
from traffic_control.cov2x.collab.joint_rollout import (
    JointRollout,
    to_cloud_joint_arrays,
    to_critic_arrays,
    to_signal_joint_arrays,
    to_vehicle_joint_arrays,
)
from traffic_control.cov2x.vehicle.policy import VehicleBatchStep
from traffic_control.cov2x.road.signal_observations import (
    SIGNAL_ACTION_DIM,
    SIGNAL_OBS_DIM,
    SignalObservation,
)

VEHICLE_JOINT_OBS_DIM = 41 + 3  # state_41 + cloud priority one-hot


@dataclass(frozen=True)
class JointPolicyConfig:
    """Hyperparameters for the joint CTDE policy stack."""

    vehicle_obs_dim: int = VEHICLE_JOINT_OBS_DIM
    signal_obs_dim: int = SIGNAL_OBS_DIM
    cloud_obs_dim: int = CLOUD_OBS_DIM
    global_state_dim: int = GLOBAL_STATE_DIM
    hidden_dim: int = 128
    critic_hidden_dim: int = 256
    lane_action_dim: int = 3
    speed_action_dim: int = len(SPEED_FRACTIONS)
    signal_action_dim: int = SIGNAL_ACTION_DIM
    cloud_priority_classes: int = CLOUD_PRIORITY_CLASSES
    max_intersections: int = 20
    init_keep_lane_bias: float = 1.0
    init_keep_speed_bias: float = 2.0
    decision_interval_s: float = 5.0
    signal_decision_interval_s: float = 15.0
    cloud_decision_interval_s: float = 30.0
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

    @property
    def signal_gamma(self) -> float:
        return self.gamma ** (
            self.signal_decision_interval_s / self.decision_interval_s
        )

    @property
    def cloud_gamma(self) -> float:
        return self.gamma ** (
            self.cloud_decision_interval_s / self.decision_interval_s
        )


@dataclass(frozen=True)
class SignalBatchStep:
    """CPU/numpy view of one batched signal policy call."""

    action: np.ndarray
    logprob: np.ndarray
    entropy: np.ndarray


@dataclass(frozen=True)
class CloudBatchStep:
    """CPU/numpy view of one cloud policy call."""

    action: np.ndarray  # (n_intersections,)
    logprob: float
    entropy: float


class VehicleJointActor(nn.Module):
    """Vehicle approach-advisor actor without a local critic."""

    def __init__(self, config: JointPolicyConfig) -> None:
        super().__init__()
        hidden = config.hidden_dim
        self.trunk = nn.Sequential(
            nn.Linear(config.vehicle_obs_dim, hidden),
            nn.Tanh(),
            nn.Linear(hidden, hidden),
            nn.Tanh(),
        )
        self.lane_head = nn.Linear(hidden, config.lane_action_dim)
        self.speed_head = nn.Linear(hidden, config.speed_action_dim)
        self._apply_init_biases(config)

    def _apply_init_biases(self, config: JointPolicyConfig) -> None:
        with torch.no_grad():
            if config.init_keep_lane_bias:
                self.lane_head.bias.add_(
                    torch.tensor(
                        [config.init_keep_lane_bias, 0.0, 0.0]
                    )
                )
            if config.init_keep_speed_bias:
                bias = torch.zeros(config.speed_action_dim)
                bias[-1] = config.init_keep_speed_bias
                self.speed_head.bias.add_(bias)

    def forward(
        self,
        obs: torch.Tensor,
        mask: torch.Tensor,
    ) -> tuple[torch.Tensor, torch.Tensor]:
        features = self.trunk(obs)
        lane_logits = self.lane_head(features)
        speed_logits = self.speed_head(features)
        if mask is not None:
            mask = mask.bool().clone()
            mask[~mask.any(dim=-1), LANE_ACTION_KEEP] = True
            lane_logits = lane_logits.masked_fill(~mask, -torch.inf)
        return lane_logits, speed_logits

    def act(
        self,
        obs: torch.Tensor,
        mask: torch.Tensor,
        *,
        deterministic: bool,
    ) -> tuple[torch.Tensor, torch.Tensor, torch.Tensor, torch.Tensor]:
        lane_logits, speed_logits = self.forward(obs, mask)
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
        return lane_action, speed_bin, logprob, entropy

    def evaluate(
        self,
        obs: torch.Tensor,
        mask: torch.Tensor,
        lane_action: torch.Tensor,
        speed_bin: torch.Tensor,
    ) -> tuple[torch.Tensor, torch.Tensor]:
        lane_logits, speed_logits = self.forward(obs, mask)
        lane_dist = torch.distributions.Categorical(logits=lane_logits)
        speed_dist = torch.distributions.Categorical(logits=speed_logits)
        logprob = lane_dist.log_prob(lane_action) + speed_dist.log_prob(speed_bin)
        entropy = lane_dist.entropy() + speed_dist.entropy()
        return logprob, entropy


class SignalActor(nn.Module):
    """Shared MLP signal policy over {keep current phase, advance}."""

    def __init__(self, config: JointPolicyConfig) -> None:
        super().__init__()
        hidden = config.hidden_dim
        self.trunk = nn.Sequential(
            nn.Linear(config.signal_obs_dim, hidden),
            nn.Tanh(),
            nn.Linear(hidden, hidden),
            nn.Tanh(),
        )
        self.phase_head = nn.Linear(hidden, config.signal_action_dim)

    def _logits(
        self, obs: torch.Tensor, mask: torch.Tensor
    ) -> torch.Tensor:
        logits = self.phase_head(self.trunk(obs))
        if mask is not None:
            logits = logits.masked_fill(~mask.bool(), -torch.inf)
        return logits

    def act(
        self,
        obs: torch.Tensor,
        mask: torch.Tensor,
        *,
        deterministic: bool,
    ) -> tuple[torch.Tensor, torch.Tensor, torch.Tensor]:
        logits = self._logits(obs, mask)
        dist = torch.distributions.Categorical(logits=logits)
        if deterministic:
            action = dist.probs.argmax(dim=-1)
        else:
            action = dist.sample()
        return action, dist.log_prob(action), dist.entropy()

    def evaluate(
        self,
        obs: torch.Tensor,
        mask: torch.Tensor,
        action: torch.Tensor,
    ) -> tuple[torch.Tensor, torch.Tensor]:
        logits = self._logits(obs, mask)
        dist = torch.distributions.Categorical(logits=logits)
        return dist.log_prob(action), dist.entropy()


class CloudActor(nn.Module):
    """Shared MLP cloud coordinator over per-intersection priority classes."""

    def __init__(self, config: JointPolicyConfig) -> None:
        super().__init__()
        hidden = config.hidden_dim
        self.max_intersections = config.max_intersections
        self.priority_classes = config.cloud_priority_classes
        self.trunk = nn.Sequential(
            nn.Linear(config.cloud_obs_dim, hidden),
            nn.Tanh(),
            nn.Linear(hidden, hidden),
            nn.Tanh(),
        )
        self.priority_head = nn.Linear(
            hidden,
            self.max_intersections * self.priority_classes,
        )

    def act(
        self,
        obs: torch.Tensor,
        n_intersections: int,
        *,
        deterministic: bool,
    ) -> tuple[torch.Tensor, torch.Tensor, torch.Tensor]:
        logits = self.priority_head(self.trunk(obs))
        logits = logits.view(-1, self.max_intersections, self.priority_classes)[
            :, :n_intersections
        ]
        dist = torch.distributions.Categorical(logits=logits)
        if deterministic:
            action = dist.probs.argmax(dim=-1)
        else:
            action = dist.sample()
        logprob = dist.log_prob(action).sum(dim=-1)
        entropy = dist.entropy().sum(dim=-1)
        return action, logprob, entropy

    def evaluate(
        self,
        obs: torch.Tensor,
        n_intersections: int,
        action: torch.Tensor,
    ) -> tuple[torch.Tensor, torch.Tensor]:
        logits = self.priority_head(self.trunk(obs))
        logits = logits.view(-1, self.max_intersections, self.priority_classes)[
            :, :n_intersections
        ]
        dist = torch.distributions.Categorical(logits=logits)
        return (
            dist.log_prob(action).sum(dim=-1),
            dist.entropy().sum(dim=-1),
        )


class JointCritic(nn.Module):
    """Centralized value function over the global network state."""

    def __init__(self, config: JointPolicyConfig) -> None:
        super().__init__()
        hidden = config.critic_hidden_dim
        self.trunk = nn.Sequential(
            nn.Linear(config.global_state_dim, hidden),
            nn.Tanh(),
            nn.Linear(hidden, hidden),
            nn.Tanh(),
        )
        self.value_head = nn.Linear(hidden, 1)

    def forward(self, global_state: torch.Tensor) -> torch.Tensor:
        return self.value_head(self.trunk(global_state)).squeeze(-1)


class JointPPOAgent:
    """Trainable joint policy: signal + vehicle + cloud actors, shared critic."""

    def __init__(self, config: JointPolicyConfig) -> None:
        self.config = config
        self.vehicle_actor = VehicleJointActor(config).to(config.device)
        self.signal_actor = SignalActor(config).to(config.device)
        self.cloud_actor = CloudActor(config).to(config.device)
        self.critic = JointCritic(config).to(config.device)
        self.vehicle_optimizer = torch.optim.Adam(
            self.vehicle_actor.parameters(), lr=config.lr_actor
        )
        self.signal_optimizer = torch.optim.Adam(
            self.signal_actor.parameters(), lr=config.lr_actor
        )
        self.cloud_optimizer = torch.optim.Adam(
            self.cloud_actor.parameters(), lr=config.lr_actor
        )
        self.critic_optimizer = torch.optim.Adam(
            self.critic.parameters(), lr=config.lr_critic
        )
        self.policy_generation = 0
        self.episode_count = 0
        self.episode_id = ""

    def reset(self, episode_id: str) -> None:
        self.episode_id = episode_id

    def _vehicle_states(
        self, observations: Sequence[VehicleObservation]
    ) -> tuple[torch.Tensor, torch.Tensor]:
        states = np.stack(
            [
                np.concatenate(
                    [
                        np.asarray(obs.state_41, dtype=np.float32),
                        (
                            np.asarray(obs.cloud_context, dtype=np.float32)
                            if obs.cloud_context is not None
                            else np.asarray([0.0, 1.0, 0.0], dtype=np.float32)
                        ),
                    ]
                )
                for obs in observations
            ]
        )
        masks = np.stack(
            [np.asarray(obs.action_mask, dtype=bool) for obs in observations]
        )
        return (
            torch.from_numpy(states).to(self.config.device),
            torch.from_numpy(masks).to(self.config.device),
        )

    def act_vehicle_batch(
        self,
        observations: Sequence[VehicleObservation],
        *,
        deterministic: bool = False,
    ) -> VehicleBatchStep:
        if not observations:
            return VehicleBatchStep(
                lane_action=np.zeros(0, dtype=np.int64),
                speed_bin=np.zeros(0, dtype=np.int64),
                logprob=np.zeros(0, dtype=np.float32),
                value=np.zeros(0, dtype=np.float32),
                entropy=np.zeros(0, dtype=np.float32),
            )
        obs, mask = self._vehicle_states(observations)
        with torch.no_grad():
            lane, speed, logprob, entropy = self.vehicle_actor.act(
                obs, mask, deterministic=deterministic
            )
        return VehicleBatchStep(
            lane_action=lane.cpu().numpy().astype(np.int64),
            speed_bin=speed.cpu().numpy().astype(np.int64),
            logprob=logprob.cpu().numpy().astype(np.float32),
            value=np.zeros(len(observations), dtype=np.float32),
            entropy=entropy.cpu().numpy().astype(np.float32),
        )

    def act_signal_batch(
        self,
        observations: Sequence[SignalObservation],
        *,
        deterministic: bool = False,
    ) -> SignalBatchStep:
        if not observations:
            return SignalBatchStep(
                action=np.zeros(0, dtype=np.int64),
                logprob=np.zeros(0, dtype=np.float32),
                entropy=np.zeros(0, dtype=np.float32),
            )
        states = np.stack(
            [np.asarray(obs.state, dtype=np.float32) for obs in observations]
        )
        masks = np.stack(
            [np.asarray(obs.action_mask, dtype=bool) for obs in observations]
        )
        obs = torch.from_numpy(states).to(self.config.device)
        mask = torch.from_numpy(masks).to(self.config.device)
        with torch.no_grad():
            action, logprob, entropy = self.signal_actor.act(
                obs, mask, deterministic=deterministic
            )
        return SignalBatchStep(
            action=action.cpu().numpy().astype(np.int64),
            logprob=logprob.cpu().numpy().astype(np.float32),
            entropy=entropy.cpu().numpy().astype(np.float32),
        )

    def act_cloud(
        self,
        observation: CloudObservation,
        *,
        deterministic: bool = False,
    ) -> CloudBatchStep:
        n_intersections = len(observation.intersection_ids)
        obs = torch.from_numpy(
            np.asarray(observation.state, dtype=np.float32).reshape(1, -1)
        ).to(self.config.device)
        with torch.no_grad():
            action, logprob, entropy = self.cloud_actor.act(
                obs, n_intersections, deterministic=deterministic
            )
        return CloudBatchStep(
            action=action.cpu().numpy().astype(np.int64).reshape(-1),
            logprob=float(logprob.cpu().item()),
            entropy=float(entropy.cpu().item()),
        )

    def critic_value(self, global_state: np.ndarray) -> float:
        obs = torch.from_numpy(
            np.asarray(global_state, dtype=np.float32).reshape(1, -1)
        ).to(self.config.device)
        with torch.no_grad():
            return float(self.critic(obs).cpu().item())

    def update(self, rollout: JointRollout) -> dict[str, Any]:
        """Apply one PPO update over all three families and the shared critic."""
        vehicle_arrays = to_vehicle_joint_arrays(rollout)
        signal_arrays = to_signal_joint_arrays(rollout)
        cloud_arrays = to_cloud_joint_arrays(rollout)
        critic_arrays = to_critic_arrays(rollout)
        diagnostics: dict[str, Any] = {
            "steps": {
                "vehicle": int(len(rollout.vehicle_steps)),
                "signal": int(len(rollout.signal_steps)),
                "cloud": int(len(rollout.cloud_steps)),
            }
        }
        if vehicle_arrays and vehicle_arrays["states"].shape[0]:
            diagnostics["vehicle"] = self._update_vehicle(vehicle_arrays)
        if signal_arrays and signal_arrays["states"].shape[0]:
            diagnostics["signal"] = self._update_signal(signal_arrays)
        if cloud_arrays and cloud_arrays["states"].shape[0]:
            diagnostics["cloud"] = self._update_cloud(cloud_arrays)
        if critic_arrays and critic_arrays["global_states"].shape[0]:
            diagnostics["critic"] = self._update_critic(critic_arrays)
        self.policy_generation += 1
        self.episode_count += 1
        diagnostics["policy_generation"] = int(self.policy_generation)
        diagnostics["episode_count"] = int(self.episode_count)
        return diagnostics

    def _update_vehicle(self, arrays: Mapping[str, np.ndarray]) -> dict[str, float]:
        return self._update_actor_family(
            name="vehicle",
            optimizer=self.vehicle_optimizer,
            network=self.vehicle_actor,
            states=arrays["states"],
            masks=arrays["masks"],
            actions=(arrays["lane_actions"], arrays["speed_bins"]),
            old_logprobs=arrays["old_logprobs"],
            values=arrays["values"],
            advantages=arrays["advantages"],
            returns=arrays["returns"],
        )

    def _update_signal(self, arrays: Mapping[str, np.ndarray]) -> dict[str, float]:
        return self._update_actor_family(
            name="signal",
            optimizer=self.signal_optimizer,
            network=self.signal_actor,
            states=arrays["states"],
            masks=arrays["masks"],
            actions=(arrays["actions"],),
            old_logprobs=arrays["old_logprobs"],
            values=arrays["values"],
            advantages=arrays["advantages"],
            returns=arrays["returns"],
        )

    def _update_cloud(self, arrays: Mapping[str, np.ndarray]) -> dict[str, float]:
        return self._update_actor_family(
            name="cloud",
            optimizer=self.cloud_optimizer,
            network=self.cloud_actor,
            states=arrays["states"],
            masks=None,
            actions=(arrays["actions"],),
            old_logprobs=arrays["old_logprobs"],
            values=arrays["values"],
            advantages=arrays["advantages"],
            returns=arrays["returns"],
        )

    def _update_actor_family(
        self,
        *,
        name: str,
        optimizer: torch.optim.Optimizer,
        network: nn.Module,
        states: np.ndarray,
        masks: np.ndarray | None,
        actions: tuple[np.ndarray, ...],
        old_logprobs: np.ndarray,
        values: np.ndarray,
        advantages: np.ndarray,
        returns: np.ndarray,
    ) -> dict[str, float]:
        obs = torch.from_numpy(np.asarray(states, dtype=np.float32)).to(
            self.config.device
        )
        mask = (
            torch.from_numpy(np.asarray(masks, dtype=bool)).to(self.config.device)
            if masks is not None and len(masks)
            else None
        )
        acts = [
            torch.from_numpy(np.asarray(action, dtype=np.int64)).to(
                self.config.device
            )
            for action in actions
        ]
        old_logp = torch.from_numpy(
            np.asarray(old_logprobs, dtype=np.float32)
        ).to(self.config.device)
        adv = torch.from_numpy(np.asarray(advantages, dtype=np.float32)).to(
            self.config.device
        )
        ret = torch.from_numpy(np.asarray(returns, dtype=np.float32)).to(
            self.config.device
        )
        value_t = torch.from_numpy(
            np.asarray(values, dtype=np.float32)
        ).to(self.config.device)
        if self.config.norm_adv and adv.numel() > 1:
            adv = (adv - adv.mean()) / (adv.std() + 1e-8)

        before = {
            param_id: param.detach().clone()
            for param_id, param in enumerate(network.parameters())
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
                b_mask = mask[batch] if mask is not None else None
                b_acts = [act[batch] for act in acts]
                b_old = old_logp[batch]
                b_adv = adv[batch]
                b_ret = ret[batch]

                if name == "vehicle":
                    logprob, entropy = network.evaluate(
                        b_obs, b_mask, b_acts[0], b_acts[1]
                    )
                elif name == "cloud":
                    logprob, entropy = network.evaluate(
                        b_obs, b_acts[0].shape[-1], b_acts[0]
                    )
                else:
                    logprob, entropy = network.evaluate(
                        b_obs, b_mask, b_acts[0]
                    )
                ratio = torch.exp(logprob - b_old)
                clipped = torch.clamp(
                    ratio, 1.0 - self.config.clip_eps, 1.0 + self.config.clip_eps
                )
                surr1 = ratio * b_adv
                surr2 = clipped * b_adv
                actor_loss = -(
                    torch.min(surr1, surr2).mean()
                    - self.config.entropy_coef * entropy.mean()
                )

                optimizer.zero_grad()
                actor_loss.backward()
                grad_norm = self._grad_norm(network.parameters())
                nn.utils.clip_grad_norm_(
                    network.parameters(), self.config.max_grad_norm
                )
                optimizer.step()

                clip_fraction += float(
                    ((ratio - 1.0).abs() > self.config.clip_eps)
                    .float()
                    .mean()
                    .item()
                )
                total_batches += 1
                diagnostics.update(
                    {
                        f"{name}_actor_loss": float(actor_loss.item()),
                        f"{name}_entropy": float(entropy.mean().item()),
                        f"{name}_clip_fraction": clip_fraction
                        / max(total_batches, 1),
                        f"{name}_grad_norm": float(grad_norm),
                        f"{name}_advantage_mean": float(adv.mean().item()),
                        f"{name}_advantage_abs_mean": float(
                            adv.abs().mean().item()
                        ),
                        f"{name}_advantage_std": float(adv.std().item()),
                    }
                )

        total_delta = 0.0
        for param_id, param in enumerate(network.parameters()):
            delta = (param.detach() - before[param_id]).norm().item()
            total_delta += delta * delta
        diagnostics[f"{name}_parameter_delta_l2"] = float(
            math.sqrt(total_delta)
        )
        diagnostics[f"{name}_value_explained_variance"] = (
            self._explained_variance(value_t, ret)
        )
        return diagnostics

    def _update_critic(self, arrays: Mapping[str, np.ndarray]) -> dict[str, float]:
        states = torch.from_numpy(
            np.asarray(arrays["global_states"], dtype=np.float32)
        ).to(self.config.device)
        returns = torch.from_numpy(
            np.asarray(arrays["returns"], dtype=np.float32)
        ).to(self.config.device)
        n = states.shape[0]
        indices = np.arange(n)
        total_loss = 0.0
        total_batches = 0
        for _ in range(self.config.ppo_epochs):
            np.random.shuffle(indices)
            batch_size = max(1, min(self.config.minibatch_size, n))
            for start in range(0, n, batch_size):
                batch = indices[start : start + batch_size]
                value = self.critic(states[batch])
                loss = F.mse_loss(value, returns[batch])
                self.critic_optimizer.zero_grad()
                loss.backward()
                grad_norm = self._grad_norm(self.critic.parameters())
                nn.utils.clip_grad_norm_(
                    self.critic.parameters(), self.config.max_grad_norm
                )
                self.critic_optimizer.step()
                total_loss += float(loss.item())
                total_batches += 1
        return {
            "critic_loss": total_loss / max(total_batches, 1),
            "critic_grad_norm": float(grad_norm),
            "critic_samples": int(n),
        }

    @staticmethod
    def _grad_norm(parameters: Any) -> float:
        total = 0.0
        for parameter in parameters:
            if parameter.grad is not None:
                total += float(parameter.grad.detach().norm().item() ** 2)
        return float(math.sqrt(total))

    @staticmethod
    def _explained_variance(values: torch.Tensor, returns: torch.Tensor) -> float:
        if values.numel() < 2 or returns.numel() < 2:
            return 0.0
        var_y = returns.var().item()
        if var_y < 1e-12:
            return 1.0
        return float(1.0 - F.mse_loss(values.detach(), returns).item() / var_y)

    def state_dict(self) -> dict[str, Any]:
        return {
            "vehicle_actor": self.vehicle_actor.state_dict(),
            "signal_actor": self.signal_actor.state_dict(),
            "cloud_actor": self.cloud_actor.state_dict(),
            "critic": self.critic.state_dict(),
            "vehicle_optimizer": self.vehicle_optimizer.state_dict(),
            "signal_optimizer": self.signal_optimizer.state_dict(),
            "cloud_optimizer": self.cloud_optimizer.state_dict(),
            "critic_optimizer": self.critic_optimizer.state_dict(),
            "policy_generation": self.policy_generation,
            "episode_count": self.episode_count,
        }

    def load_state_dict(self, state: Mapping[str, Any]) -> None:
        self.vehicle_actor.load_state_dict(state["vehicle_actor"])
        self.signal_actor.load_state_dict(state["signal_actor"])
        self.cloud_actor.load_state_dict(state["cloud_actor"])
        self.critic.load_state_dict(state["critic"])
        self.vehicle_optimizer.load_state_dict(state["vehicle_optimizer"])
        self.signal_optimizer.load_state_dict(state["signal_optimizer"])
        self.cloud_optimizer.load_state_dict(state["cloud_optimizer"])
        self.critic_optimizer.load_state_dict(state["critic_optimizer"])
        self.policy_generation = int(state.get("policy_generation", 0))
        self.episode_count = int(state.get("episode_count", 0))
