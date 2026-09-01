"""Intersection-level masked pooling over movement-aware vehicle tokens."""
from __future__ import annotations
from typing import Sequence
import numpy as np


def movement_token(*, queue: float, speed: float, gap: float, movement_index: int, movement_count: int) -> np.ndarray:
    one_hot = np.zeros(max(1, int(movement_count)), dtype=np.float32)
    if 0 <= int(movement_index) < len(one_hot):
        one_hot[int(movement_index)] = 1.0
    return np.concatenate(([float(queue), float(speed), float(gap)], one_hot)).astype(np.float32)


def masked_movement_pool(tokens: np.ndarray, mask: Sequence[bool] | None = None) -> np.ndarray:
    values = np.asarray(tokens, dtype=np.float32)
    if values.ndim != 2:
        raise ValueError("tokens must have shape [n_tokens, token_dim]")
    valid = np.ones(values.shape[0], dtype=bool) if mask is None else np.asarray(mask, dtype=bool)
    if valid.shape != (values.shape[0],):
        raise ValueError("mask shape mismatch")
    selected = values[valid]
    if not len(selected):
        return np.zeros(values.shape[1] * 2 + 2, dtype=np.float32)
    mean, maximum = selected.mean(axis=0), selected.max(axis=0)
    return np.concatenate((mean, maximum, [float(len(selected)), float(valid.mean())])).astype(np.float32)
