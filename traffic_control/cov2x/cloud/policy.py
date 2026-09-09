"""Deployed _CloudActor architecture; checkpoint parameter names are preserved."""
import torch
from torch import nn
from ..contracts import CONTEXT_DIM, MOVEMENT_DIM, LANE_SLOTS, PERMISSION_INDEX

class _CloudActor(nn.Module):
    def __init__(self, n_movements: int):
        super().__init__()
        self.movement_embedding = nn.Embedding(n_movements, 8)
        self.context = nn.Sequential(nn.Linear(CONTEXT_DIM, 64), nn.Tanh())
        self.trunk = nn.Sequential(
            nn.Linear(64 + MOVEMENT_DIM + 8, 64),
            nn.Tanh(),
            nn.Linear(64, 64),
            nn.Tanh(),
        )
        self.logit = nn.Linear(64, 1)
        nn.init.zeros_(self.logit.weight)
        nn.init.zeros_(self.logit.bias)

    def forward(self, context: torch.Tensor, movement_obs: torch.Tensor,
                movement: torch.Tensor) -> torch.Tensor:
        context_hidden = self.context(context)
        embedded = self.movement_embedding(movement)
        hidden = self.trunk(torch.cat((context_hidden, movement_obs, embedded), dim=-1))
        return self.logit(hidden).squeeze(-1)
