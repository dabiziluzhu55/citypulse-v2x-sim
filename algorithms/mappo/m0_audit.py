"""M0 audit: freeze the IPPO 10-seed baseline JSON with a stable SHA-256."""

from __future__ import annotations

import hashlib
import json
import math
from pathlib import Path
from typing import Mapping, Sequence

FINAL_SEEDS = (1042, 1142, 1242, 1342, 1442, 1542, 1642, 1742, 1842, 1942)


def _mean(values: Sequence[float]) -> float:
    return float(sum(values) / len(values))


def _std(values: Sequence[float]) -> float:
    m = _mean(values)
    return float(math.sqrt(sum((v - m) ** 2 for v in values) / len(values)))


def baseline_sha256_str(content: bytes) -> str:
    return hashlib.sha256(content).hexdigest()


def _canonical_bytes(payload: dict) -> bytes:
    return (json.dumps(payload, indent=2, sort_keys=True) + "\n").encode("utf-8")


def baseline_sha256(path: str) -> str:
    """冻结文件的自校验 hash：解析 JSON 后去掉 sha256 字段再规范化序列化。

    sha256 字段记录的是"去掉该字段后的规范内容"的哈希（类似 lockfile 自校验），
    因此 baseline_sha256(path) == data["sha256"] 恒成立，且文件可整体重放校验。
    """
    data = json.loads(Path(path).read_text(encoding="utf-8"))
    data.pop("sha256", None)
    return baseline_sha256_str(_canonical_bytes(data))


def freeze_ippo_baseline(rows: Sequence[Mapping[str, object]], out_path: str) -> str:
    """rows: 每个 seed 一个 dict，含 seed/avg_waiting_time_s/arrived 等官方指标。

    冻结 JSON 结构：
    {seeds, waiting: {mean,std}, arrived: {mean,std}, metric_keys: [...],
     per_seed: {...}, sha256}
    """
    ordered = sorted(rows, key=lambda r: int(r["seed"]))
    seeds = [int(r["seed"]) for r in ordered]
    if seeds != list(FINAL_SEEDS):
        raise ValueError(f"baseline seeds must be exactly {FINAL_SEEDS}")
    waiting = [float(r["avg_waiting_time_s"]) for r in ordered]
    arrived = [float(r["arrived"]) for r in ordered]
    payload = {
        "seeds": seeds,
        "waiting": {"mean": _mean(waiting), "std": _std(waiting)},
        "arrived": {"mean": _mean(arrived), "std": _std(arrived)},
        "metric_keys": sorted({k for r in ordered for k in r.keys()}),
        "per_seed": {str(s): dict(r) for s, r in zip(seeds, ordered)},
    }
    text = _canonical_bytes(payload)
    path = Path(out_path)
    path.parent.mkdir(parents=True, exist_ok=True)
    payload["sha256"] = baseline_sha256_str(text)
    path.write_bytes(_canonical_bytes(payload))
    return str(path)
