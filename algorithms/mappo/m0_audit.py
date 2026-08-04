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


def run_vanilla_diagnostics(
    checkpoint_path: str,
    out_path: str,
    *,
    seed: int = 66501,
    duration_s: float = 300.0,
) -> str:
    """加载 cooperative_joint_v1 ep160 checkpoint，跑 1 个 300s rollout，
    计算 M0 §2.2 诊断并写 JSON（只读 SUMO，不改 simulation/）。

    复用 train.py 的 worker 装配（_build_training_config / _run_sumo_worker /
    build_ppo_batch），与正式训练走同一条 rollout 路径。
    """
    from algorithms.mappo.checkpoint import load_checkpoint, policy_digest
    from algorithms.mappo.config import (
        COOPERATIVE_MODEL_VERSION,
        REWARD_SCOPE_SHARED_TEAM,
    )
    from algorithms.mappo.features import IPPO_V8_LOCAL_OBSERVATION_SCHEMA
    from algorithms.mappo.models import MAPPOPolicy
    from algorithms.mappo.parallel_train import build_ppo_batch
    from algorithms.mappo.train import (
        DEFAULT_INTERSECTION_IDS,
        REWARD_DEFINITION,
        _build_training_config,
        _run_sumo_worker,
    )
    from algorithms.mappo.trainer import MAPPOTrainer
    from algorithms.mappo.diagnostics import (
        advantage_quantiles,
        actor_grad_cosine,
        td_target_duplicate_stats,
    )

    config = _build_training_config(
        DEFAULT_INTERSECTION_IDS,
        critic_scope="global",
        model_version=COOPERATIVE_MODEL_VERSION,
        reward_scope=REWARD_SCOPE_SHARED_TEAM,
    )
    policy = MAPPOPolicy(
        obs_dim=config.obs_dim,
        num_agents=len(config.intersection_ids),
        critic_scope=config.critic_scope,
        actor_init_seed=42,
        critic_init_seed=43,
        hidden_dim=config.hidden_dim,
        phase_feature_dim=config.phase_feature_dim,
        model_version=config.model_version,
    )
    trainer = MAPPOTrainer(policy, config)
    load_checkpoint(
        checkpoint_path,
        policy,
        trainer,
        expected_config=config,
        expected_local_observation_schema=IPPO_V8_LOCAL_OBSERVATION_SCHEMA,
        expected_reward_definition=REWARD_DEFINITION,
    )
    request = {
        "seed": int(seed),
        "config": config,
        "policy_generation": 0,
        "policy_digest": policy_digest(policy),
        "policy_state": policy.state_dict(),
        "actor_init_seed": 42,
        "critic_init_seed": 43,
        "residual_init_seed": None,
        "duration": float(duration_s),
        "period": "off_peak",
    }
    result = _run_sumo_worker(request)
    if result["status"] != "complete":
        raise RuntimeError(f"vanilla rollout failed: {result.get('error')}")
    rollout = result["rollout"]
    batch = build_ppo_batch([rollout], config=config)

    # 1) 梯度 cosine 在 update 之前（未污染参数）
    cosine = actor_grad_cosine(
        policy.actor, batch.local_obs, batch.phase_features,
        batch.action_mask, batch.actions,
    )
    # 2) 一次 on-policy update 得到 PPO/KL/entropy 诊断
    update_diag = trainer.update(batch)
    # 3) per_agent_corr：vanilla 广播语义下 joint 内 owner target 完全相同 -> 1.0
    per_agent_corr = 1.0 - _within_joint_variance_ratio(batch)
    payload = {
        "advantage": advantage_quantiles(batch.advantages),
        "td_target": {
            **td_target_duplicate_stats(batch.returns, batch.joint_step_index),
            "per_agent_corr": float(per_agent_corr),
        },
        "actor_grad": {
            "unclipped_norm": float(batch.advantages.abs().mean()),
            "final_norm": float(update_diag["actor_grad_norm"]),
            "cosine": cosine,
        },
        "critic": {
            "per_agent_ev": {
                "agent_mean": float(update_diag["explained_variance_pre_agent_mean"]),
                "agent_min": float(update_diag["explained_variance_pre_agent_min"]),
            },
            "td_error": float(update_diag["rollout_value_max_abs_error"]),
        },
        "ppo": {
            "kl": float(update_diag["approx_kl"]),
            "clip_fraction": float(update_diag["clip_fraction"]),
            "entropy": float(update_diag["entropy"]),
        },
        "meta": {
            "checkpoint_path": checkpoint_path,
            "checkpoint_policy_digest": policy_digest(policy),
            "seed": int(seed),
            "duration_s": float(duration_s),
            "rollout_pending_dropped": int(rollout.dropped_pending),
        },
    }
    Path(out_path).parent.mkdir(parents=True, exist_ok=True)
    Path(out_path).write_text(
        json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    return out_path


def _within_joint_variance_ratio(batch) -> float:
    """1 - (joint 内 owner target 方差 / 总方差)；广播语义下为 1.0。"""
    import numpy as np
    returns = batch.returns.detach().float().cpu().numpy()
    joint_ids = batch.joint_step_index.detach().cpu().numpy()
    total_var = float(returns.var())
    within_sum = 0.0
    for jid in np.unique(joint_ids):
        rows = returns[joint_ids == jid]
        within_sum += float(rows.var()) * (rows.size - 1)
    within = within_sum / max(returns.size - len(np.unique(joint_ids)), 1)
    if total_var <= 1e-12:
        return 1.0
    return float(max(0.0, 1.0 - within / total_var))
