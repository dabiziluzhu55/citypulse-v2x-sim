"""Deployed _VehicleActor architecture; checkpoint parameter names are preserved."""
import torch
from torch import nn
from ..contracts import CONTEXT_DIM, MOVEMENT_DIM, LANE_SLOTS, PERMISSION_INDEX

_ACTOR_OBS_INDICES = tuple(list(range(PERMISSION_INDEX)) + list(range(PERMISSION_INDEX + 1, 45)))
_ACTOR_OBS_DIM = len(_ACTOR_OBS_INDICES)

class _VehicleActor(nn.Module):
    def __init__(self, n_movements: int):
        super().__init__()
        self.movement_embedding = nn.Embedding(n_movements, 8)
        self.observation = nn.Sequential(nn.Linear(_ACTOR_OBS_DIM, 64), nn.Tanh())
        self.trunk = nn.Sequential(
            nn.Linear(64 + 8, 64),
            nn.Tanh(),
            nn.Linear(64, 64),
            nn.Tanh(),
        )
        self.speed_mean = nn.Linear(64, 1)
        self.lane_logits = nn.Linear(64, LANE_SLOTS)
        self.log_std = nn.Parameter(torch.tensor(-0.7, dtype=torch.float32))
        nn.init.zeros_(self.speed_mean.weight)
        nn.init.zeros_(self.speed_mean.bias)
        nn.init.zeros_(self.lane_logits.weight)
        nn.init.zeros_(self.lane_logits.bias)

    def forward(self, obs: torch.Tensor, movement: torch.Tensor
                ) -> tuple[torch.Tensor, torch.Tensor]:
        actor_obs = obs[:, _ACTOR_OBS_INDICES]
        observed = self.observation(actor_obs)
        embedded = self.movement_embedding(movement)
        hidden = self.trunk(torch.cat((observed, embedded), dim=-1))
        return self.speed_mean(hidden).squeeze(-1), self.lane_logits(hidden)
