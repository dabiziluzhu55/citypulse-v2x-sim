"""Shared ep0 initialization artifact for the three M1 arms.

Deterministic policy/optimizer/RNG state so that M1-0/M1-A/M1-B start from
byte-identical parameters (workpiece-level same initialization).
"""

from __future__ import annotations

import hashlib
import json
import random
from pathlib import Path

import numpy as np
import torch

from algorithms.mappo.config import COOPERATIVE_MODEL_VERSION
from algorithms.mappo.models import MAPPOPolicy


def _content_hash(payload: dict[str, object]) -> str:
    """对 payload 做确定性内容哈希（排除 meta.sha256 自引用字段）。

    json.dumps 用 default=str 序列化 torch/numpy/RNG 对象；该哈希只用于
    完整性自校验（load 时重算），不替代 manifest 中对文件字节的 SHA-256。
    """
    body = dict(payload)
    meta = dict(body["meta"])
    meta.pop("sha256", None)
    body["meta"] = meta
    text = json.dumps(
        body, sort_keys=True, separators=(",", ":"), default=str
    ).encode("utf-8")
    return hashlib.sha256(text).hexdigest()


def _make_policy(
    obs_dim: int,
    num_agents: int,
    critic_scope: str,
    model_version: str,
) -> MAPPOPolicy:
    policy = MAPPOPolicy(
        obs_dim=obs_dim,
        num_agents=num_agents,
        critic_scope=critic_scope,
        actor_init_seed=42,
        critic_init_seed=43,
        model_version=model_version,
    )
    return policy


def create_shared_init(
    out_path: str,
    obs_dim: int = 132,
    num_agents: int = 20,
    model_version: str = COOPERATIVE_MODEL_VERSION,
) -> dict[str, object]:
    """生成三 arm 共享的 ep0 初始化工件：policy + optimizers + RNG + cursor。

    正式 M1 工件必须用 cooperative_m1_v1 生成（Task 3.4 Step 6）；
    本函数在 Task 2.3 阶段用 vanilla model_version 跑通单测与临时验证。
    """
    policy = _make_policy(
        obs_dim, num_agents, critic_scope="global", model_version=model_version
    )
    actor_opt = torch.optim.Adam(policy.actor_parameters(), lr=3e-4, eps=1e-5)
    critic_opt = torch.optim.Adam(policy.critic_parameters(), lr=1e-4, eps=1e-5)
    payload = {
        "policy": {
            "actor": {
                k: v.detach().cpu().clone()
                for k, v in policy.actor.state_dict().items()
            },
            "critic": {
                k: v.detach().cpu().clone()
                for k, v in policy.critic.state_dict().items()
            },
        },
        "optimizers": {
            "actor": actor_opt.state_dict(),
            "critic": critic_opt.state_dict(),
        },
        "rng": {
            "python": random.getstate(),
            "numpy": np.random.get_state(),
            "torch": torch.random.get_rng_state(),
        },
        "cursor": {"episode": 0, "generation": 0},
        "meta": {
            "obs_dim": obs_dim,
            "num_agents": num_agents,
            "critic_scope": "global",
            "model_version": model_version,
            "actor_init_seed": 42,
            "critic_init_seed": 43,
        },
    }
    payload["meta"]["sha256"] = _content_hash(payload)
    path = Path(out_path)
    path.parent.mkdir(parents=True, exist_ok=True)
    torch.save(payload, path)
    return payload["meta"]


def load_shared_init(path: str) -> dict[str, object]:
    payload = torch.load(Path(path), map_location="cpu", weights_only=False)
    if payload["meta"]["sha256"] != _content_hash(payload):
        raise ValueError("shared init integrity check failed")
    return payload
