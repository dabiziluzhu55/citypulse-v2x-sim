"""CoV2X 部署契约。

``traffic_control/cov2x`` 是算法端车路云三端联合 CTDE 控制器的自包含
推理拷贝（checkpoint format_version=2，当前交付为 EP12 初版）。

本模块只校验部署前置事实；模型内部语义（训练配置、拓扑指纹等）由
控制器加载时全量校验，不在此重复实现。
"""

from __future__ import annotations

import hashlib
from pathlib import Path
from typing import Any, Mapping

CHECKPOINT_CONTRACT_VERSION = 2
JOINT_CHECKPOINT_FORMAT_VERSION = 2
DEFAULT_JOINT_MODEL_FILENAME = "cov2x_joint_ep12.pt"

# 雄安 20 路口（demo_1..demo_20），与算法端 presets.SCENARIO_PRESET_REGISTRY 一致。
TRAINING_INTERSECTION_IDS: tuple[str, ...] = tuple(
    f"demo_{i}" for i in range(1, 21)
)

# 与 controller.py + joint_policy.py 默认构造一致的期望维度。
# 联合策略为固定维度 MLP；checkpoint 维度若与此不一致会在加载时报错，
# 这里提前给出可读的契约错误。
EXPECTED_JOINT_MODEL_CONFIG: dict[str, int] = {
    "vehicle_obs_dim": 44,  # 41-dim lane state + cloud priority one-hot(3)
    "signal_obs_dim": 37,
    "cloud_obs_dim": 86,
    "global_state_dim": 146,
    "hidden_dim": 128,
    "critic_hidden_dim": 256,
    "lane_action_dim": 3,
    "speed_action_dim": 5,
    "signal_action_dim": 2,
    "cloud_priority_classes": 3,
    "max_intersections": 20,
}


def checkpoint_sha256(path: str | Path) -> str:
    """SHA-256 of a checkpoint file bytes."""
    digest = hashlib.sha256()
    with open(path, "rb") as handle:
        for chunk in iter(lambda: handle.read(1 << 20), b""):
            digest.update(chunk)
    return digest.hexdigest()


def load_joint_contract(
    checkpoint_path: str | Path,
    checkpoint: Mapping[str, Any],
) -> tuple[int, dict[str, Any]]:
    """Return ``(contract_version, view)`` for a joint 车路云 checkpoint.

    Joint checkpoints are produced by ``algorithms/cov2x/controller.py``
    (``format_version=2``) and contain a ``joint_policy`` state dict plus the
    full ``config`` used at training time.
    """
    if not isinstance(checkpoint, Mapping):
        raise ValueError("CoV2X joint checkpoint must be a dictionary")
    format_version = int(checkpoint.get("format_version", 0))
    if format_version != JOINT_CHECKPOINT_FORMAT_VERSION:
        raise ValueError(
            f"CoV2X joint checkpoint format_version={format_version!r} is not "
            f"supported; expected {JOINT_CHECKPOINT_FORMAT_VERSION}"
        )
    joint_policy = checkpoint.get("joint_policy")
    if not isinstance(joint_policy, Mapping):
        raise ValueError(
            "CoV2X joint checkpoint is missing joint_policy mapping"
        )
    for family in ("vehicle_actor", "signal_actor", "cloud_actor", "critic"):
        if family not in joint_policy:
            raise ValueError(
                f"CoV2X joint checkpoint joint_policy is missing {family!r}"
            )
    config = checkpoint.get("config")
    if not isinstance(config, Mapping):
        raise ValueError("CoV2X joint checkpoint is missing config")
    actual = {
        name: int(config[name])
        for name in EXPECTED_JOINT_MODEL_CONFIG
        if name in config
    }
    missing = sorted(set(EXPECTED_JOINT_MODEL_CONFIG) - set(actual))
    if missing:
        raise ValueError(
            f"CoV2X joint checkpoint config is missing: {missing}"
        )
    if actual != EXPECTED_JOINT_MODEL_CONFIG:
        raise ValueError(
            f"CoV2X joint checkpoint config mismatch: expected "
            f"{EXPECTED_JOINT_MODEL_CONFIG}, got {actual}"
        )
    view = {
        "checkpoint_contract_version": CHECKPOINT_CONTRACT_VERSION,
        "checkpoint_filename": Path(checkpoint_path).name,
        "sha256": checkpoint_sha256(checkpoint_path),
        "format_version": format_version,
        "model_family": "joint",
        "config": dict(config),
        "phase_orders": checkpoint.get("phase_orders"),
        "episode_count": checkpoint.get("episode_count"),
        "policy_generation": checkpoint.get("policy_generation"),
    }
    return CHECKPOINT_CONTRACT_VERSION, view
