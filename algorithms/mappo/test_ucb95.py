import numpy as np

from algorithms.mappo.ucb95 import bootstrap_paired_mean_diffs, ucb95


def test_ucb95_is_95th_percentile_not_975th():
    diffs = np.arange(1.0, 101.0)  # 对称均匀分布
    b = 10000
    dist = bootstrap_paired_mean_diffs(diffs, b=b, seed=20260804)
    # 有放回均值应接近总体均值 50.5，且分布对称：
    # 95th percentile 应 < 97.5th percentile
    p95 = np.percentile(dist, 95.0)
    p975 = np.percentile(dist, 97.5)
    assert p95 < p975
    assert ucb95(diffs, b=b, seed=20260804) == p95


def test_bootstrap_deterministic_seed():
    diffs = np.random.default_rng(7).normal(size=8)
    d1 = bootstrap_paired_mean_diffs(diffs, b=500, seed=1)
    d2 = bootstrap_paired_mean_diffs(diffs, b=500, seed=1)
    assert np.array_equal(d1, d2)
