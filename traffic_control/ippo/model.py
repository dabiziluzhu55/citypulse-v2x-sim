"""IPPO actor-critic网络，用于在线推理"""

from __future__ import annotations

import math
from typing import Iterable, Optional, Tuple

import torch
import torch.nn as nn

PHASE_FEATURES = 11


def _orthogonal_init(layer: nn.Module, gain: float) -> None:
    if isinstance(layer, nn.Linear):
        nn.init.orthogonal_(layer.weight, gain)
        nn.init.constant_(layer.bias, 0.0)


class IPPONetwork(nn.Module):
    """Parameter-shared, candidate-scoring policy with a separate critic."""

    def __init__(self, obs_dim: int, act_dim: int, hidden: int = 128):
        super().__init__()
        self.obs_dim = int(obs_dim)
        self.act_dim = int(act_dim)
        self.actor_body = nn.Sequential(
            nn.Linear(obs_dim, hidden),
            nn.Tanh(),
            nn.Linear(hidden, hidden),
            nn.Tanh(),
        )
        self.critic_body = nn.Sequential(
            nn.Linear(obs_dim, hidden),
            nn.Tanh(),
            nn.Linear(hidden, hidden),
            nn.Tanh(),
        )
        self.phase_actor = nn.Sequential(
            nn.Linear(hidden + PHASE_FEATURES, hidden),
            nn.Tanh(),
            nn.Linear(hidden, 1),
        )
        self.critic = nn.Linear(hidden, 1)

        for layer in self.actor_body:
            _orthogonal_init(layer, math.sqrt(2.0))
        for layer in self.critic_body:
            _orthogonal_init(layer, math.sqrt(2.0))
        _orthogonal_init(self.phase_actor[0], math.sqrt(2.0))
        _orthogonal_init(self.phase_actor[2], 0.01)
        _orthogonal_init(self.critic, 1.0)

    def actor_forward(
        self, obs: torch.Tensor, phase_features: Optional[torch.Tensor] = None
    ) -> torch.Tensor:
        if obs.ndim != 2:
            raise ValueError("actor observations must have shape [batch, features].")
        batch = obs.shape[0]
        if phase_features is None:
            phase_features = torch.zeros(
                batch,
                self.act_dim,
                PHASE_FEATURES,
                dtype=obs.dtype,
                device=obs.device,
            )
        expected = (batch, self.act_dim, PHASE_FEATURES)
        if tuple(phase_features.shape) != expected:
            raise ValueError(
                f"phase features must have shape {expected}, got {tuple(phase_features.shape)}"
            )
        context = self.actor_body(obs).unsqueeze(1).expand(-1, self.act_dim, -1)
        return self.phase_actor(torch.cat((context, phase_features), dim=-1)).squeeze(-1)

    def critic_forward(self, obs: torch.Tensor) -> torch.Tensor:
        return self.critic(self.critic_body(obs))

    def forward(
        self, obs: torch.Tensor, phase_features: Optional[torch.Tensor] = None
    ) -> Tuple[torch.Tensor, torch.Tensor]:
        return self.actor_forward(obs, phase_features), self.critic_forward(obs)

    def actor_parameters(self) -> Iterable[nn.Parameter]:
        return (*self.actor_body.parameters(), *self.phase_actor.parameters())

    def critic_parameters(self) -> Iterable[nn.Parameter]:
        return (*self.critic_body.parameters(), *self.critic.parameters())

