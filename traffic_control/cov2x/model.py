"""Joint Cloud/Vehicle policy and action distributions for CV Joint V1."""
from __future__ import annotations

import hashlib
import math
from collections.abc import Mapping
from typing import Any

import numpy as np
import torch
from torch import nn
from torch.nn import functional as F
from torch.distributions import Bernoulli, Normal

from .contracts import (
    BASE_VEHICLE_DIM,
    CONTEXT_DIM,
    LANE_SLOTS,
    MOVEMENT_DIM,
    PERMISSION_INDEX,
    SCHEMA_VERSION,
)


from .cloud.policy import _CloudActor
from .vehicle.policy import _VehicleActor


def _finite(tensor: torch.Tensor, label: str) -> torch.Tensor:
    if not torch.isfinite(tensor).all():
        raise ValueError(f"{label} must be finite")
    return tensor


def _vector(value: Any, size: int, label: str) -> torch.Tensor:
    tensor = torch.as_tensor(value, dtype=torch.float32, device="cpu")
    if tensor.shape != (size,):
        raise ValueError(f"{label} must have shape ({size},)")
    return _finite(tensor, label)


def _batch(value: Any, size: int, label: str) -> torch.Tensor:
    tensor = torch.as_tensor(value, dtype=torch.float32, device="cpu")
    if tensor.ndim != 2 or tensor.shape[1] != size:
        raise ValueError(f"{label} must have shape [N,{size}]")
    return _finite(tensor, label)


def _movement(value: Any, n_movements: int, n: int) -> torch.Tensor:
    tensor = torch.as_tensor(value, dtype=torch.long, device="cpu")
    if tensor.shape != (n,):
        raise ValueError(f"movement must have shape ({n},)")
    if tensor.numel() and ((tensor < 0).any() or (tensor >= n_movements).any()):
        raise ValueError("movement index is outside the policy catalog")
    return tensor


def _lane_mask(value: Any, n: int) -> torch.Tensor:
    tensor = torch.as_tensor(value, device="cpu")
    if tensor.dtype is not torch.bool:
        raise ValueError("lane_mask must be boolean")
    if tensor.shape != (n, LANE_SLOTS):
        raise ValueError(f"lane_mask must have shape ({n},{LANE_SLOTS})")
    if tensor.numel() and not tensor[:, 0].all():
        raise ValueError("KEEP (lane slot 0) must always be legal")
    if tensor.numel() and not tensor.any(dim=1).all():
        raise ValueError("each lane mask must contain a legal slot")
    return tensor


def _speed_mask(value: Any, n: int) -> torch.Tensor:
    tensor = torch.as_tensor(value, device="cpu")
    if tensor.dtype is not torch.bool or tensor.shape != (n,):
        raise ValueError(f"speed_mask must be boolean shape ({n},)")
    return tensor


def _lane_action(value: Any, n: int, mask: torch.Tensor) -> torch.Tensor:
    tensor = torch.as_tensor(value, dtype=torch.long, device="cpu")
    if tensor.shape != (n,):
        raise ValueError(f"lane_action must have shape ({n},)")
    if tensor.numel() and ((tensor < 0).any() or (tensor >= LANE_SLOTS).any()):
        raise ValueError("lane_action is outside the four lane slots")
    if tensor.numel() and not mask[torch.arange(n), tensor].all():
        raise ValueError("lane_action must be legal under lane_mask")
    return tensor


class SamplingRNG:
    """Independent CPU generators for the cloud, speed and lane draws."""

    def __init__(self, seed: int):
        if isinstance(seed, bool) or not isinstance(seed, (int, np.integer)):
            raise TypeError("seed must be an integer")
        base = int(seed) % (2**63 - 1)
        self.cloud = torch.Generator(device="cpu")
        self.speed = torch.Generator(device="cpu")
        self.lane = torch.Generator(device="cpu")
        self.cloud.manual_seed((base + 0x13579BDF) % (2**63 - 1))
        self.speed.manual_seed((base + 0x2468ACE0) % (2**63 - 1))
        self.lane.manual_seed((base + 0x31415926) % (2**63 - 1))

    def state_dict(self) -> dict[str, torch.Tensor]:
        return {
            "cloud": self.cloud.get_state().clone(),
            "speed": self.speed.get_state().clone(),
            "lane": self.lane.get_state().clone(),
        }

    def load_state_dict(self, state: Mapping[str, Any]) -> None:
        if not isinstance(state, Mapping):
            raise TypeError("RNG state must be a mapping")
        for name in ("cloud", "speed", "lane"):
            if name not in state:
                raise ValueError(f"missing RNG stream: {name}")
            value = torch.as_tensor(state[name], dtype=torch.uint8, device="cpu")
            if value.ndim != 1:
                raise ValueError(f"invalid RNG state for {name}")
            getattr(self, name).set_state(value.clone())






class _Critic(nn.Module):
    def __init__(self, n_movements: int, obs_dim: int):
        super().__init__()
        self.movement_embedding = nn.Embedding(n_movements, 8)
        self.net = nn.Sequential(
            nn.Linear(CONTEXT_DIM + obs_dim + 8, 128),
            nn.Tanh(),
            nn.Linear(128, 128),
            nn.Tanh(),
            nn.Linear(128, 1),
        )
        self.obs_dim = obs_dim

    def forward(self, context: torch.Tensor, obs: torch.Tensor,
                movement: torch.Tensor) -> torch.Tensor:
        embedded = self.movement_embedding(movement)
        return self.net(torch.cat((context, obs, embedded), dim=-1)).squeeze(-1)


class JointPolicy(nn.Module):
    """Four-module Cloud/Vehicle actor-critic policy with CPU sampling APIs."""

    def __init__(self, n_movements: int):
        super().__init__()
        if isinstance(n_movements, bool) or int(n_movements) <= 0:
            raise ValueError("n_movements must be a positive integer")
        self.n_movements = int(n_movements)
        self.cloud_actor = _CloudActor(self.n_movements)
        self.vehicle_actor = _VehicleActor(self.n_movements)
        self.cloud_critic = _Critic(self.n_movements, 65)
        self.vehicle_critic = _Critic(self.n_movements, 45)

    @staticmethod
    def _log_std(actor: _VehicleActor) -> torch.Tensor:
        return actor.log_std.clamp(-5.0, 1.0)

    def _cloud_tensors(self, context: Any, obs: Any, movement: Any
                       ) -> tuple[torch.Tensor, torch.Tensor, torch.Tensor]:
        context_t = _batch(context, CONTEXT_DIM, "context")
        obs_t = _batch(obs, 65, "obs")
        if context_t.shape[0] != obs_t.shape[0]:
            raise ValueError("context and obs batch lengths differ")
        movement_t = _movement(movement, self.n_movements, context_t.shape[0])
        device = self._device()
        return context_t.to(device), obs_t.to(device), movement_t.to(device)

    def _vehicle_tensors(self, context: Any, obs: Any, movement: Any,
                         lane_mask: Any, speed_mask: Any, raw_speed: Any,
                         lane_action: Any) -> tuple[torch.Tensor, ...]:
        context_t = _batch(context, CONTEXT_DIM, "context")
        obs_t = _batch(obs, 45, "obs")
        n = context_t.shape[0]
        if obs_t.shape[0] != n:
            raise ValueError("context and obs batch lengths differ")
        movement_t = _movement(movement, self.n_movements, n)
        lane_mask_t = _lane_mask(lane_mask, n)
        speed_mask_t = _speed_mask(speed_mask, n)
        raw_speed_t = torch.as_tensor(raw_speed, dtype=torch.float32, device="cpu")
        if raw_speed_t.shape != (n,):
            raise ValueError(f"raw_speed must have shape ({n},)")
        _finite(raw_speed_t, "raw_speed")
        lane_action_t = _lane_action(lane_action, n, lane_mask_t)
        device = self._device()
        return tuple(value.to(device) for value in (
            context_t, obs_t, movement_t, lane_mask_t, speed_mask_t,
            raw_speed_t, lane_action_t))

    def _cloud_forward(self, context: torch.Tensor, obs: torch.Tensor,
                       movement: torch.Tensor) -> tuple[torch.Tensor, torch.Tensor]:
        logit = self.cloud_actor(context, obs, movement)
        value = self.cloud_critic(context, obs, movement)
        return logit, value

    def _vehicle_forward(self, context: torch.Tensor, obs: torch.Tensor,
                         movement: torch.Tensor
                         ) -> tuple[torch.Tensor, torch.Tensor, torch.Tensor]:
        speed_mean, lane_logits = self.vehicle_actor(obs, movement)
        value = self.vehicle_critic(context, obs, movement)
        return speed_mean, lane_logits, value

    def cloud_logprob_value(self, context: Any, obs: Any, movement: Any,
                            action: Any
                            ) -> tuple[torch.Tensor, torch.Tensor, torch.Tensor]:
        context_t, obs_t, movement_t = self._cloud_tensors(context, obs, movement)
        action_t = torch.as_tensor(action, dtype=torch.long, device="cpu")
        if action_t.shape != (context_t.shape[0],):
            raise ValueError("action shape does not match the batch")
        if action_t.numel() and ((action_t < 0).any() | (action_t > 1).any()):
            raise ValueError("cloud action must be 0 or 1")
        logit, value = self._cloud_forward(context_t, obs_t, movement_t)
        distribution = Bernoulli(logits=logit)
        action_float = action_t.to(dtype=torch.float32, device=logit.device)
        return distribution.log_prob(action_float), distribution.entropy(), value

    def vehicle_logprob_value(
        self, context: Any, obs: Any, movement: Any, lane_mask: Any,
        speed_mask: Any, raw_speed: Any, lane_action: Any
    ) -> tuple[torch.Tensor, torch.Tensor, torch.Tensor]:
        (context_t, obs_t, movement_t, lane_mask_t, speed_mask_t,
         raw_speed_t, lane_action_t) = self._vehicle_tensors(
             context, obs, movement, lane_mask, speed_mask, raw_speed, lane_action)
        speed_mean, lane_logits, value = self._vehicle_forward(
            context_t, obs_t, movement_t)
        log_std = self._log_std(self.vehicle_actor)
        normal = Normal(speed_mean, torch.exp(log_std))
        speed_logp = normal.log_prob(raw_speed_t)
        speed_entropy = normal.entropy()
        speed_logp = torch.where(speed_mask_t, speed_logp,
                                 torch.zeros_like(speed_logp))
        speed_entropy = torch.where(speed_mask_t, speed_entropy,
                                    torch.zeros_like(speed_entropy))

        masked_logits = lane_logits.masked_fill(~lane_mask_t, -1e9)
        log_probs = F.log_softmax(masked_logits, dim=-1)
        lane_logp = log_probs[torch.arange(lane_action_t.shape[0], device=log_probs.device),
                              lane_action_t]
        lane_probs = log_probs.exp()
        lane_entropy = -(lane_probs * log_probs).sum(dim=-1)
        lane_active = lane_mask_t.sum(dim=-1) > 1
        lane_logp = torch.where(lane_active, lane_logp,
                                torch.zeros_like(lane_logp))
        lane_entropy = torch.where(lane_active, lane_entropy,
                                   torch.zeros_like(lane_entropy))
        return speed_logp + lane_logp, speed_entropy + lane_entropy, value

    def act_cloud(self, context: Any, obs: Any, movement: int, *,
                  rng: SamplingRNG | None, deterministic: bool
                  ) -> dict[str, Any]:
        device = self._device()
        context_t = _vector(context, CONTEXT_DIM, "context").unsqueeze(0).to(device)
        obs_t = _vector(obs, 65, "obs").unsqueeze(0).to(device)
        movement_t = _movement([movement], self.n_movements, 1).to(device)
        logit, value = self._cloud_forward(context_t, obs_t, movement_t)
        probability = float(torch.sigmoid(logit[0]).item())
        if deterministic:
            action = int(probability > 0.5)
        else:
            if rng is None:
                raise ValueError("stochastic cloud action requires SamplingRNG")
            action = int(torch.rand((), generator=rng.cloud).item() < probability)
        distribution = Bernoulli(logits=logit[0])
        logp = float(distribution.log_prob(torch.tensor(float(action), device=logit.device)).item())
        return {
            "action": action,
            "logp": logp,
            "value": float(value[0].item()),
            "probability": probability,
        }

    def act_vehicle(
        self, context: Any, obs: Any, movement: int, lane_mask: Any,
        speed_mask: bool, *, rng: SamplingRNG | None, deterministic: bool
    ) -> dict[str, Any]:
        device = self._device()
        context_t = _vector(context, CONTEXT_DIM, "context").unsqueeze(0).to(device)
        obs_t = _vector(obs, 45, "obs").unsqueeze(0).to(device)
        movement_t = _movement([movement], self.n_movements, 1).to(device)
        raw_lane_mask = torch.as_tensor(lane_mask, device="cpu")
        if raw_lane_mask.shape != (LANE_SLOTS,):
            raise ValueError(f"lane_mask must have shape ({LANE_SLOTS},)")
        if raw_lane_mask.dtype is not torch.bool:
            raise ValueError("lane_mask must be boolean")
        lane_mask_t = _lane_mask(raw_lane_mask[None], 1).to(device)
        if not isinstance(speed_mask, (bool, np.bool_)):
            raise TypeError("speed_mask must be boolean")
        speed_enabled = bool(speed_mask)
        speed_mean, lane_logits, value = self._vehicle_forward(
            context_t, obs_t, movement_t)
        mean = speed_mean[0]
        log_std = self._log_std(self.vehicle_actor)
        std = torch.exp(log_std)
        distribution = Normal(mean, std)
        if speed_enabled:
            if deterministic:
                raw = mean
            else:
                if rng is None:
                    raise ValueError("stochastic vehicle action requires SamplingRNG")
                noise = torch.randn((), generator=rng.speed).to(device)
                raw = mean + std * noise
            reduction = 0.10 * torch.sigmoid(raw)
            speed_logp = distribution.log_prob(raw)
        else:
            raw = torch.tensor(0.0, device=device)
            reduction = torch.tensor(0.0, device=device)
            speed_logp = torch.tensor(0.0, device=device)

        legal = lane_mask_t[0]
        legal_count = int(legal.sum().item())
        if legal_count == 1:
            lane_action = int(torch.nonzero(legal, as_tuple=False)[0, 0].item())
            lane_logp = torch.tensor(0.0, device=device)
        else:
            masked_logits = lane_logits[0].masked_fill(~legal, -1e9)
            log_probs = F.log_softmax(masked_logits, dim=-1)
            if deterministic:
                lane_action = int(torch.argmax(masked_logits, dim=-1).item())
            else:
                if rng is None:
                    raise ValueError("stochastic vehicle action requires SamplingRNG")
                lane_probs = log_probs.exp().detach().cpu()
                lane_action = int(torch.multinomial(
                    lane_probs, 1, generator=rng.lane).item())
            lane_logp = log_probs[lane_action]
        actor_mask = bool(speed_enabled or legal_count > 1)
        return {
            "raw_speed": float(raw.item()),
            "reduction": float(reduction.item()),
            "lane_action": lane_action,
            "logp": float((speed_logp + lane_logp).item()),
            "value": float(value[0].item()),
            "actor_mask": actor_mask,
        }

    def actor_parameters(self, role: str):
        if role == "cloud":
            return tuple(self.cloud_actor.parameters())
        if role == "vehicle":
            return tuple(self.vehicle_actor.parameters())
        raise ValueError("role must be cloud or vehicle")

    def critic_parameters(self, role: str):
        if role == "cloud":
            return tuple(self.cloud_critic.parameters())
        if role == "vehicle":
            return tuple(self.vehicle_critic.parameters())
        raise ValueError("role must be cloud or vehicle")

    def _device(self) -> torch.device:
        return next(self.parameters()).device


def policy_version(policy: JointPolicy) -> str:
    digest = hashlib.sha256()
    digest.update(SCHEMA_VERSION.encode("utf-8"))
    digest.update(str(policy.n_movements).encode("ascii"))
    for name, tensor in sorted(policy.state_dict().items()):
        value = tensor.detach().cpu().contiguous()
        digest.update(name.encode("utf-8"))
        digest.update(str(value.dtype).encode("ascii"))
        digest.update(repr(tuple(value.shape)).encode("ascii"))
        digest.update(value.numpy().tobytes())
    return f"cv_joint_v1:{digest.hexdigest()}"
