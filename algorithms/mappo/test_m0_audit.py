import json
from pathlib import Path

import pytest

from algorithms.mappo.m0_audit import (
    FINAL_SEEDS,
    baseline_sha256,
    freeze_ippo_baseline,
)


def test_freeze_ippo_baseline_writes_frozen_json(tmp_path):
    rows = [
        {
            "seed": seed,
            "avg_waiting_time_s": 8.5 + 0.1 * index,
            "arrived": 75 + index,
        }
        for index, seed in enumerate(FINAL_SEEDS)
    ]
    out = freeze_ippo_baseline(rows, out_path=str(tmp_path / "ippo_baseline.json"))
    data = json.loads(Path(out).read_text(encoding="utf-8"))
    assert data["seeds"] == list(FINAL_SEEDS)
    assert "sha256" in data
    assert baseline_sha256(out) == data["sha256"]


def test_baseline_sha256_stable():
    import hashlib
    from algorithms.mappo.m0_audit import baseline_sha256_str
    content = b'{"a": 1}\n'
    assert baseline_sha256_str(content) == hashlib.sha256(content).hexdigest()


class _FakeBatch:
    def __init__(self, returns, joint_step_index):
        import torch
        self.returns = torch.tensor(returns, dtype=torch.float32)
        self.joint_step_index = torch.tensor(joint_step_index)


def test_per_agent_target_corr_broadcast_is_one():
    from algorithms.mappo.m0_audit import _per_agent_target_corr

    batch = _FakeBatch(
        [1.0, 1.0, 1.0, 5.0, 5.0, 5.0],
        [0, 0, 0, 1, 1, 1],
    )
    assert _per_agent_target_corr(batch) == pytest.approx(1.0, abs=1e-6)


def test_per_agent_target_corr_independent_is_zero():
    from algorithms.mappo.m0_audit import _per_agent_target_corr

    batch = _FakeBatch(
        [1.0, 2.0, 3.0, 1.0, 2.0, 3.0],
        [0, 0, 0, 1, 1, 1],
    )
    assert _per_agent_target_corr(batch) == pytest.approx(0.0, abs=1e-6)
