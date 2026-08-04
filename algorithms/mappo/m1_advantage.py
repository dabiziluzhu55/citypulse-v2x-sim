"""M1 advantage component normalization and mixing (pure tensor functions)."""

from __future__ import annotations

import numpy as np
import torch

from algorithms.mappo.m1_config import ADV_NORM_EPS


def normalize_component(values: torch.Tensor) -> torch.Tensor:
    """整个 rollout/batch 范围标准化；std<ε 置零。"""
    std = values.std(unbiased=False)
    if float(std) < ADV_NORM_EPS:
        return torch.zeros_like(values)
    return (values - values.mean()) / std


def neighbor_mean(
    local_advantage: torch.Tensor, adjacency: np.ndarray
) -> torch.Tensor:
    """A_nbr[i] = mean_{j∈N(i)} A_local[j]，M_ii=0，孤立节点为 0。

    local_advantage: [*, num_agents]（最后维为 agent 槽）
    adjacency: [num_agents, num_agents] 对称 0/1，对角线 0
    """
    adj = torch.as_tensor(adjacency, dtype=torch.float32, device=local_advantage.device)
    if adj.ndim != 2 or adj.shape[0] != adj.shape[1]:
        raise ValueError("adjacency must be a square matrix")
    if int(adj.shape[0]) != int(local_advantage.shape[-1]):
        raise ValueError("adjacency size must match agent slots")
    if not torch.all(adj.diag() == 0):
        raise ValueError("adjacency diagonal must be zero (M_ii=0)")
    if not torch.all((adj == 0) | (adj == 1)):
        raise ValueError("adjacency must be binary")
    counts = adj.sum(dim=-1).clamp_min(1)
    return (local_advantage @ adj) / counts


def mix_advantages(
    *,
    local: torch.Tensor,
    neighbor: torch.Tensor | None,
    team: torch.Tensor,
    m1_arm: str,
    weights: tuple[float, float, float] | None = None,
) -> torch.Tensor:
    """组件分别标准化 → 加权混合 → 统一最终标准化。"""
    if m1_arm == "m1_0":
        return normalize_component(team)
    if weights is None:
        weights = {"m1_a": (0.95, 0.0, 0.05), "m1_b": (0.70, 0.25, 0.05)}[m1_arm]
    w_local, w_nbr, w_team = weights
    if neighbor is None and w_nbr != 0.0:
        raise ValueError("neighbor weight requires neighbor component")
    mixed = w_local * normalize_component(local)
    if neighbor is not None:
        mixed = mixed + w_nbr * normalize_component(neighbor)
    mixed = mixed + w_team * normalize_component(team)
    return normalize_component(mixed)
