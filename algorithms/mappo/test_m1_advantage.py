import numpy as np
import torch

from algorithms.mappo.m1_advantage import (
    normalize_component,
    mix_advantages,
    neighbor_mean,
)


def test_normalize_component_zero_std_returns_zero():
    values = torch.zeros(8)
    out = normalize_component(values)
    assert torch.all(out == 0.0)


def test_mix_weights_and_final_norm():
    local = torch.tensor([1.0, 2.0, 3.0, 4.0])
    team = torch.tensor([1.5, 1.5, 1.5, 1.5])
    mixed = mix_advantages(local=local, neighbor=None, team=team, m1_arm="m1_a")
    assert mixed.shape == local.shape
    # 最终标准化后均值≈0、标准差≈1
    assert abs(mixed.mean().item()) < 1e-6
    assert abs(mixed.std(unbiased=False).item() - 1.0) < 1e-4


def test_neighbor_mean_excludes_self_and_isolated_zero():
    # M 为冻结对称邻接；N(i) 不含自身
    M = np.array([[0, 1, 0], [1, 0, 0], [0, 0, 0]])  # 节点2孤立
    adv = torch.tensor([[1.0, 2.0, 3.0]])  # 单 joint：agent0,1,2
    out = neighbor_mean(adv, M)
    assert out.shape == adv.shape
    assert out[0, 0] == 2.0  # agent0 的邻居只有 agent1
    assert out[0, 2] == 0.0  # 孤立节点为 0
