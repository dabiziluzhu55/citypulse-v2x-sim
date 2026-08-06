"""Deployable MAPPO model definitions (self-contained inference copy).

Kept in sync with ``algorithms/mappo/models.py`` so the deployment layer does
not depend on the training workspace.
"""
from __future__ import annotations

import math

import torch
from torch import nn
from torch.nn import functional as F

COOPERATIVE_MODEL_VERSION = "cooperative_joint_v1"
MODEL_ACTOR_VARIANTS = {COOPERATIVE_MODEL_VERSION: "shared"}


MASKED_LOGIT = -1e8


def _orthogonal_init(layer: nn.Module, gain: float) -> None:
    if isinstance(layer, nn.Linear):
        nn.init.orthogonal_(layer.weight, gain=gain)
        nn.init.constant_(layer.bias, 0.0)


class CandidateActor(nn.Module):
    """IPPO-v8-compatible candidate scorer with local inputs only."""

    def __init__(
        self,
        obs_dim: int,
        phase_feature_dim: int = 11,
        hidden_dim: int = 128,
    ) -> None:
        super().__init__()
        if int(obs_dim) <= 0:
            raise ValueError("observation dimension must be positive")
        if int(phase_feature_dim) <= 0:
            raise ValueError("phase feature dimension must be positive")
        if int(hidden_dim) <= 0:
            raise ValueError("hidden dimension must be positive")
        self.obs_dim = int(obs_dim)
        self.phase_feature_dim = int(phase_feature_dim)
        self.hidden_dim = int(hidden_dim)
        self.actor_body = nn.Sequential(
            nn.Linear(self.obs_dim, self.hidden_dim),
            nn.Tanh(),
            nn.Linear(self.hidden_dim, self.hidden_dim),
            nn.Tanh(),
        )
        self.phase_actor = nn.Sequential(
            nn.Linear(
                self.hidden_dim + self.phase_feature_dim, self.hidden_dim
            ),
            nn.Tanh(),
            nn.Linear(self.hidden_dim, 1),
        )
        for layer in self.actor_body:
            _orthogonal_init(layer, math.sqrt(2.0))
        _orthogonal_init(self.phase_actor[0], math.sqrt(2.0))
        _orthogonal_init(self.phase_actor[2], 0.01)

    def unmasked_logits(
        self, obs: torch.Tensor, phase_features: torch.Tensor
    ) -> torch.Tensor:
        if obs.ndim != 2 or obs.shape[1] != self.obs_dim:
            raise ValueError(
                f"actor observations must have shape [batch, {self.obs_dim}]"
            )
        if phase_features.ndim != 3:
            raise ValueError(
                "phase features must have shape [batch, actions, features]"
            )
        expected_prefix = (obs.shape[0], phase_features.shape[1])
        if phase_features.shape[0] != obs.shape[0] or phase_features.shape[2] != self.phase_feature_dim:
            raise ValueError(
                "phase features must have shape "
                f"[{expected_prefix[0]}, actions, {self.phase_feature_dim}]"
            )
        context = self.actor_body(obs).unsqueeze(1).expand(
            -1, phase_features.shape[1], -1
        )
        return self.phase_actor(
            torch.cat((context, phase_features), dim=-1)
        ).squeeze(-1)

    def masked_logits(
        self,
        obs: torch.Tensor,
        phase_features: torch.Tensor,
        action_mask: torch.Tensor,
    ) -> torch.Tensor:
        logits = self.unmasked_logits(obs, phase_features)
        mask = torch.as_tensor(action_mask, device=logits.device, dtype=torch.bool)
        if mask.shape != logits.shape:
            raise ValueError("action mask must match candidate logits shape")
        if torch.any(~mask.any(dim=1)):
            raise ValueError("every row must contain at least one valid action")
        return logits.masked_fill(~mask, MASKED_LOGIT)

    def forward(
        self,
        obs: torch.Tensor,
        phase_features: torch.Tensor,
        action_mask: torch.Tensor,
    ) -> torch.distributions.Categorical:
        return torch.distributions.Categorical(
            logits=self.masked_logits(obs, phase_features, action_mask)
        )


class IsomorphicTeamValueCritic(nn.Module):
    """Parameter-isomorphic local or global critic for team returns."""

    def __init__(
        self,
        obs_dim: int,
        num_agents: int,
        context_scope: str,
        hidden_dim: int = 128,
    ) -> None:
        super().__init__()
        if context_scope not in {"local", "global"}:
            raise ValueError("context scope must be 'local' or 'global'")
        self.context_scope = context_scope
        self.obs_dim = int(obs_dim)
        self.num_agents = int(num_agents)
        self.hidden_dim = int(hidden_dim)
        if self.obs_dim <= 0 or self.num_agents <= 0 or self.hidden_dim <= 0:
            raise ValueError("critic dimensions must be positive")
        self.critic_body = nn.Sequential(
            nn.Linear(self.obs_dim, self.hidden_dim),
            nn.Tanh(),
            nn.Linear(self.hidden_dim, self.hidden_dim),
            nn.Tanh(),
        )
        self.value_head = nn.Sequential(
            nn.Linear(self.hidden_dim, self.hidden_dim),
            nn.Tanh(),
            nn.Linear(self.hidden_dim, 1),
        )
        for layer in self.critic_body:
            _orthogonal_init(layer, math.sqrt(2.0))
        _orthogonal_init(self.value_head[0], math.sqrt(2.0))
        _orthogonal_init(self.value_head[2], 1.0)

    def forward(
        self,
        global_obs: torch.Tensor,
        agent_mask: torch.Tensor,
        agent_index: torch.Tensor,
    ) -> torch.Tensor:
        if global_obs.ndim != 3 or global_obs.shape[1:] != (
            self.num_agents,
            self.obs_dim,
        ):
            raise ValueError(
                "global observations must have shape "
                f"[batch, {self.num_agents}, {self.obs_dim}]"
            )
        batch = global_obs.shape[0]
        mask = torch.as_tensor(
            agent_mask, device=global_obs.device, dtype=torch.bool
        )
        if mask.shape != (batch, self.num_agents):
            raise ValueError(
                f"agent mask must have shape [{batch}, {self.num_agents}]"
            )
        if torch.any(~mask.any(dim=1)):
            raise ValueError("every global state needs at least one valid agent")

        embeddings = self.critic_body(global_obs)
        if self.context_scope == "global":
            weights = mask.to(dtype=embeddings.dtype).unsqueeze(-1)
            context = (embeddings * weights).sum(dim=1) / weights.sum(dim=1)
        else:
            owners = torch.as_tensor(
                agent_index, device=global_obs.device, dtype=torch.long
            )
            if owners.shape != (batch,):
                raise ValueError(f"agent index must have shape [{batch}]")
            if torch.any(owners < 0) or torch.any(owners >= self.num_agents):
                raise ValueError("agent index is out of range")
            batch_indices = torch.arange(batch, device=global_obs.device)
            if torch.any(~mask[batch_indices, owners]):
                raise ValueError("owner agent must be valid in agent mask")
            context = embeddings[batch_indices, owners]
        return self.value_head(context)


class MAPPOPolicy(nn.Module):
    """Actor and swappable local/global critic with isolated RNG streams."""

    def __init__(
        self,
        obs_dim: int,
        num_agents: int,
        critic_scope: str,
        actor_init_seed: int,
        critic_init_seed: int,
        hidden_dim: int = 128,
        phase_feature_dim: int = 11,
        model_version: str = COOPERATIVE_MODEL_VERSION,
        actor_variant: str = "shared",
        residual_hidden_dim: int = 32,
        identity_offset: int = 9,
        residual_init_seed: int = 44,
    ) -> None:
        super().__init__()
        if critic_scope not in {"local", "global"}:
            raise ValueError("critic scope must be 'local' or 'global'")
        expected_actor_variant = MODEL_ACTOR_VARIANTS.get(model_version)
        if expected_actor_variant is None:
            raise ValueError(f"unknown MAPPO model version: {model_version!r}")
        if actor_variant != expected_actor_variant:
            raise ValueError(
                "model version and actor variant mismatch: "
                f"{model_version!r} requires {expected_actor_variant!r}"
            )
        self.critic_scope = critic_scope
        self.model_version = str(model_version)
        self.actor_variant = str(actor_variant)
        self.actor_init_seed = int(actor_init_seed)
        self.critic_init_seed = int(critic_init_seed)
        self.residual_init_seed = int(residual_init_seed)
        with torch.random.fork_rng(devices=[]):
            torch.manual_seed(self.actor_init_seed)
            self.actor = CandidateActor(
                obs_dim=obs_dim,
                phase_feature_dim=phase_feature_dim,
                hidden_dim=hidden_dim,
            )
        with torch.random.fork_rng(devices=[]):
            torch.manual_seed(self.critic_init_seed)
            if self.model_version != COOPERATIVE_MODEL_VERSION:
                raise ValueError(
                    f"unknown MAPPO model version: {self.model_version!r}"
                )
            self.critic = IsomorphicTeamValueCritic(
                obs_dim=obs_dim,
                num_agents=num_agents,
                context_scope=critic_scope,
                hidden_dim=hidden_dim,
            )

    def value(
        self,
        global_obs: torch.Tensor,
        agent_mask: torch.Tensor,
        agent_index: torch.Tensor,
    ) -> torch.Tensor:
        return self.critic(global_obs, agent_mask, agent_index)

    def actor_parameters(self):
        return self.actor.parameters()

    def critic_parameters(self):
        return self.critic.parameters()
