"""Single-sided 95th percentile UCB bootstrap for paired per-seed differences."""

from __future__ import annotations

import numpy as np


def bootstrap_paired_mean_diffs(
    diffs: np.ndarray, *, b: int = 10000, seed: int = 20260804
) -> np.ndarray:
    """Bootstrap the mean of paired per-seed differences (resample seeds)."""
    values = np.asarray(diffs, dtype=np.float64)
    if values.ndim != 1 or values.size == 0:
        raise ValueError("diffs must be a non-empty 1-D array")
    rng = np.random.default_rng(seed)
    n = values.size
    means = np.empty(b, dtype=np.float64)
    for index in range(b):
        idx = rng.integers(0, n, size=n)
        means[index] = values[idx].mean()
    return means


def ucb95(diffs: np.ndarray, *, b: int = 10000, seed: int = 20260804) -> float:
    """Single-sided 95th percentile upper confidence bound."""
    return float(
        np.percentile(
            bootstrap_paired_mean_diffs(diffs, b=b, seed=seed), 95.0
        )
    )
