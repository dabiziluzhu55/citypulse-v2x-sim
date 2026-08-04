import json
from pathlib import Path

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
