"""M1 extended configuration and validation (pure data structures)."""

from __future__ import annotations

from dataclasses import dataclass

ADV_NORM_EPS = 1e-8
LOCAL_REWARD_SCHEMA_V1 = "v5a_local_raw_then_clip_v1"


@dataclass(frozen=True)
class M1Config:
    arm: str                       # "m1_0" | "m1_a" | "m1_b"
    local_weight: float = 0.95
    neighbor_weight: float = 0.0
    team_weight: float = 0.05
    adjacency_path: str | None = None
    reward_schema: str = LOCAL_REWARD_SCHEMA_V1


def validate_m1_config(cfg: M1Config) -> None:
    if cfg.arm not in {"m1_0", "m1_a", "m1_b"}:
        raise ValueError(f"unknown M1 arm: {cfg.arm}")
    weights = (cfg.local_weight, cfg.neighbor_weight, cfg.team_weight)
    if any(not (0.0 <= w <= 1.0) for w in weights):
        raise ValueError("M1 weights must be in [0, 1]")
    if abs((cfg.local_weight + cfg.neighbor_weight + cfg.team_weight) - 1.0) > 1e-6:
        raise ValueError("M1 weights must sum to 1")
    if cfg.arm == "m1_a" and cfg.neighbor_weight != 0.0:
        raise ValueError("m1_a must have zero neighbor weight")
    if cfg.arm == "m1_b" and cfg.neighbor_weight <= 0.0:
        raise ValueError("m1_b must have positive neighbor weight")
    if cfg.arm == "m1_0" and (cfg.local_weight != 0.0 or cfg.neighbor_weight != 0.0):
        raise ValueError("m1_0 must use pure team advantage (local/neighbor weights zero)")
    if cfg.arm in {"m1_a", "m1_b"} and cfg.adjacency_path is None:
        raise ValueError("m1_a/m1_b require adjacency_path")
    if cfg.reward_schema != LOCAL_REWARD_SCHEMA_V1:
        raise ValueError(f"unexpected reward schema: {cfg.reward_schema}")
