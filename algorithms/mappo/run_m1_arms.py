"""M1 三 arm 训练编排：计划生成 + shared-init → checkpoint 转换 + 训练/冒烟 CLI。

设计契约（Task 4.3）：
- M1-0 单独先行，M1-A/M1-B 并行或串行；
- 三 arm 从同一共享初始化工件（ep0）出发；
- 正式训练前先跑 smoke（每 arm 1-2 ep，记录 pending_dropped / finite /
  worker 完整性 / gpu_peak_mib / episode wall time）。

用法：
    python -m algorithms.mappo.run_m1_arms --arm m1_0 --episodes 32 --workers 8 \
        --init .../mappo_v2_shared_init.pt --out .../m1_0
    python -m algorithms.mappo.run_m1_arms --smoke --arms m1_0,m1_a,m1_b \
        --episodes 2 --workers 8 --out .../smoke
"""
from __future__ import annotations

import argparse
import json
import random
import time
from pathlib import Path
from typing import Sequence

import numpy as np
import torch

from algorithms.mappo.checkpoint import CheckpointMetadata, save_checkpoint
from algorithms.mappo.config import (
    COOPERATIVE_M1_MODEL_VERSION,
    MAPPOConfig,
    REWARD_SCOPE_SHARED_TEAM,
)
from algorithms.mappo.models import MAPPOPolicy
from algorithms.mappo.shared_init import load_shared_init
from algorithms.mappo.train import (
    DEFAULT_INTERSECTION_IDS,
    IPPO_V8_LOCAL_OBSERVATION_SCHEMA,
    REWARD_DEFINITION,
    _build_training_config,
    main as train_main,
)
from algorithms.mappo.trainer import MAPPOTrainer
from traffic_control.ippo.identity import IDENTITY_SLOT_IDS


ARM_SPECS: dict[str, dict[str, object]] = {
    "m1_0": {
        "target_mode": "m1_0_scalar",
        "weights": (0.0, 0.0, 1.0),
        "needs_adjacency": False,
    },
    "m1_a": {
        "target_mode": "per_agent",
        "weights": (0.95, 0.0, 0.05),
        "needs_adjacency": True,
    },
    "m1_b": {
        "target_mode": "per_agent",
        "weights": (0.70, 0.25, 0.05),
        "needs_adjacency": True,
    },
}


def build_arm_plan(
    *, shared_init: str, adjacency: str, manifest: str
) -> dict[str, object]:
    """生成三 arm 执行计划（不执行）：M1-0 单独先行 → M1-A/M1-B 并行或串行。"""
    arms: list[dict[str, object]] = []
    for arm, spec in ARM_SPECS.items():
        entry: dict[str, object] = {
            "arm": arm,
            "target_mode": spec["target_mode"],
            "weights": spec["weights"],
            "init": shared_init,
        }
        if spec["needs_adjacency"]:
            entry["adjacency"] = adjacency
        arms.append(entry)
    return {
        "smoke_before_train": True,
        "m1_0_alone_first": True,
        "arms": arms,
        "manifest": manifest,
        "invalid_run_policy": {
            "training_worker_drop": "whole arm invalid",
            "evaluation_single_seed_rerun": "allowed with log",
        },
    }


def arm_config(
    arm: str, *, intersections: int, adjacency: str | None = None
) -> MAPPOConfig:
    """构造某 arm 的 M1 训练配置（与 train.py 完全一致的口径）。"""
    if arm not in ARM_SPECS:
        raise ValueError(f"unknown M1 arm: {arm!r}")
    spec = ARM_SPECS[arm]
    needs_adjacency = bool(spec["needs_adjacency"])
    if needs_adjacency and not adjacency:
        raise ValueError(f"arm {arm} requires --adjacency")
    return _build_training_config(
        DEFAULT_INTERSECTION_IDS[:intersections],
        critic_scope="global",
        model_version=COOPERATIVE_M1_MODEL_VERSION,
        reward_scope=REWARD_SCOPE_SHARED_TEAM,
        m1_target_mode=str(spec["target_mode"]),
        m1_arm=arm,
        m1_local_weight=float(spec["weights"][0]),
        m1_neighbor_weight=float(spec["weights"][1]),
        m1_team_weight=float(spec["weights"][2]),
        m1_adjacency_path=adjacency if needs_adjacency else None,
    )


def convert_shared_init_to_checkpoint(
    shared_init_path: str,
    checkpoint_path: str,
    *,
    arm: str,
    intersections: int = 20,
    adjacency: str | None = None,
    actor_init_seed: int = 42,
    critic_init_seed: int = 43,
    training_periods: tuple[str, ...] = ("off_peak",),
    training_workers: int | None = None,
    episode_duration_s: float | None = None,
) -> Path:
    """把共享初始化工件转成 train.py 可 --resume 的 ep0 checkpoint。

    共享初始化工件（shared_init.py）不是 checkpoint 格式：它保存嵌套的
    actor/critic state dict、optimizer state 与 python/numpy/torch RNG。
    这里重建同构 policy/trainer 并写入标准 checkpoint（episode=0,
    generation=0, seeds 0..0），保证三 arm 从字节级相同的 ep0 出发。
    """
    shared = load_shared_init(shared_init_path)
    config = arm_config(
        arm, intersections=intersections, adjacency=adjacency
    )
    policy = MAPPOPolicy(
        obs_dim=config.obs_dim,
        num_agents=len(IDENTITY_SLOT_IDS),
        critic_scope=config.critic_scope,
        actor_init_seed=actor_init_seed,
        critic_init_seed=critic_init_seed,
        hidden_dim=config.hidden_dim,
        phase_feature_dim=config.phase_feature_dim,
        model_version=config.model_version,
        actor_variant=config.actor_variant,
        residual_hidden_dim=config.residual_hidden_dim,
        identity_offset=config.identity_offset,
    )
    trainer = MAPPOTrainer(policy, config)
    policy.actor.load_state_dict(shared["policy"]["actor"], strict=True)
    policy.critic.load_state_dict(shared["policy"]["critic"], strict=True)
    trainer.actor_optimizer.load_state_dict(
        shared["optimizers"]["actor"]
    )
    trainer.critic_optimizer.load_state_dict(
        shared["optimizers"]["critic"]
    )
    metadata = CheckpointMetadata.from_config(
        config,
        episode=0,
        policy_generation=0,
        actor_init_seed=actor_init_seed,
        critic_init_seed=critic_init_seed,
        training_seed_start=0,
        training_seed_end=0,
        training_periods=training_periods,
        local_observation_schema=IPPO_V8_LOCAL_OBSERVATION_SCHEMA,
        reward_definition=REWARD_DEFINITION,
        training_workers=training_workers,
        episode_duration_s=episode_duration_s,
    )
    # 恢复 shared init 捕获的 RNG，确保三 arm 从同一 ep0 随机状态出发。
    random.setstate(shared["rng"]["python"])
    np.random.set_state(shared["rng"]["numpy"])
    torch.set_rng_state(shared["rng"]["torch"])
    target = Path(checkpoint_path)
    save_checkpoint(target, policy, trainer, metadata)
    return target


def _train_argv(
    *,
    arm: str,
    episodes: int,
    workers: int,
    duration: int,
    period: str,
    base_seed: int,
    checkpoint_every: int,
    intersections: int,
    init_checkpoint: str,
    save_path: str,
    diagnostics_path: str | None,
    adjacency: str | None,
) -> list[str]:
    spec = ARM_SPECS[arm]
    argv: list[str] = [
        "--model-version",
        COOPERATIVE_M1_MODEL_VERSION,
        "--reward-scope",
        REWARD_SCOPE_SHARED_TEAM,
        "--intersections",
        str(intersections),
        "--episodes",
        str(episodes),
        "--workers",
        str(workers),
        "--duration",
        str(duration),
        "--base-seed",
        str(base_seed),
        "--period",
        period,
        "--m1-arm",
        arm,
        "--m1-target-mode",
        str(spec["target_mode"]),
        "--m1-local-weight",
        str(spec["weights"][0]),
        "--m1-neighbor-weight",
        str(spec["weights"][1]),
        "--m1-team-weight",
        str(spec["weights"][2]),
        "--checkpoint-every",
        str(checkpoint_every),
        "--actor-init-seed",
        "42",
        "--critic-init-seed",
        "43",
        "--resume",
        init_checkpoint,
        "--save",
        save_path,
    ]
    if spec["needs_adjacency"]:
        if adjacency is None:
            raise ValueError(f"arm {arm} requires --adjacency")
        argv += ["--m1-adjacency", adjacency]
    if diagnostics_path is not None:
        argv += ["--diagnostics-output", diagnostics_path]
    return argv


def _run_arm(
    *,
    arm: str,
    episodes: int,
    workers: int,
    duration: int,
    period: str,
    base_seed: int,
    checkpoint_every: int,
    intersections: int,
    init_path: str,
    adjacency_path: str | None,
    out_dir: str,
) -> int:
    out = Path(out_dir)
    out.mkdir(parents=True, exist_ok=True)
    if base_seed != 1:
        raise SystemExit(
            "M1 ep0 shared-init checkpoint requires --base-seed 1 "
            "(resume seed range starts at 1)"
        )
    init_checkpoint = out / f"{arm}_init.pt"
    final_checkpoint = out / f"{arm}_ep{episodes:03d}.pt"
    diagnostics_path = out / f"{arm}_diagnostics.json"
    spec = ARM_SPECS[arm]
    convert_shared_init_to_checkpoint(
        init_path,
        str(init_checkpoint),
        arm=arm,
        intersections=intersections,
        adjacency=None if not spec["needs_adjacency"] else adjacency_path,
        training_workers=workers,
        episode_duration_s=float(duration),
        training_periods=(period,),
    )
    argv = _train_argv(
        arm=arm,
        episodes=episodes,
        workers=workers,
        duration=duration,
        period=period,
        base_seed=base_seed,
        checkpoint_every=checkpoint_every,
        intersections=intersections,
        init_checkpoint=str(init_checkpoint),
        save_path=str(final_checkpoint),
        diagnostics_path=str(diagnostics_path),
        adjacency=adjacency_path,
    )
    return int(train_main(argv))


def load_diagnostics(path: str | Path) -> dict[str, object]:
    """读取 train.py 写入的 diagnostics JSON；缺失/损坏返回空 dict。"""
    target = Path(path)
    if not target.is_file():
        return {}
    try:
        payload = json.loads(target.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}
    return payload if isinstance(payload, dict) else {}


def _smoke_entry(
    *,
    arm: str,
    episodes: int,
    exit_code: int,
    wall_s: float,
    gpu_peak_mib: float,
    diagnostics: dict[str, object],
) -> dict[str, object]:
    batches = diagnostics.get("batches", [])
    if not isinstance(batches, list):
        batches = []
    pending_dropped = sum(
        int(batch.get("dropped_pending_count", 0)) for batch in batches
    )
    finite = bool(batches) and all(
        bool(batch.get("finite", False)) for batch in batches
    )
    worker_success = sum(
        int(batch.get("worker_success_count", 0)) for batch in batches
    )
    worker_expected = sum(
        int(batch.get("worker_expected_count", 0)) for batch in batches
    )
    ok = (
        exit_code == 0
        and diagnostics.get("status") == "complete"
        and pending_dropped == 0
        and finite
        and worker_expected > 0
        and worker_success == worker_expected
    )
    return {
        "arm": arm,
        "episodes": episodes,
        "exit_code": exit_code,
        "wall_s": round(float(wall_s), 3),
        "gpu_peak_mib": round(float(gpu_peak_mib), 3),
        "status": diagnostics.get("status"),
        "pending_dropped": pending_dropped,
        "finite": finite,
        "worker_success": worker_success,
        "worker_expected": worker_expected,
        "ok": ok,
    }


def _run_smoke(
    *,
    arms: Sequence[str],
    episodes: int,
    workers: int,
    duration: int,
    period: str,
    base_seed: int,
    checkpoint_every: int,
    intersections: int,
    init_path: str,
    adjacency_path: str | None,
    out_dir: str,
) -> int:
    out = Path(out_dir)
    out.mkdir(parents=True, exist_ok=True)
    cuda_available = torch.cuda.is_available()
    entries: list[dict[str, object]] = []
    for arm in arms:
        if cuda_available:
            torch.cuda.reset_peak_memory_stats()
        started = time.time()
        arm_out = out / arm
        code = _run_arm(
            arm=arm,
            episodes=episodes,
            workers=workers,
            duration=duration,
            period=period,
            base_seed=base_seed,
            checkpoint_every=checkpoint_every,
            intersections=intersections,
            init_path=init_path,
            adjacency_path=adjacency_path,
            out_dir=str(arm_out),
        )
        wall_s = time.time() - started
        gpu_peak_mib = (
            float(torch.cuda.max_memory_allocated() / (1024**2))
            if cuda_available
            else 0.0
        )
        diagnostics = load_diagnostics(
            arm_out / f"{arm}_diagnostics.json"
        )
        entries.append(
            _smoke_entry(
                arm=arm,
                episodes=episodes,
                exit_code=code,
                wall_s=wall_s,
                gpu_peak_mib=gpu_peak_mib,
                diagnostics=diagnostics,
            )
        )
    report = {"schema": "m1_arm_smoke_v1", "arms": entries}
    report_path = out / "smoke_report.json"
    report_path.write_text(
        json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    print(json.dumps(report, ensure_ascii=False, indent=2))
    return 0 if all(bool(entry["ok"]) for entry in entries) else 1


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--smoke", action="store_true")
    parser.add_argument("--arm", choices=sorted(ARM_SPECS), default=None)
    parser.add_argument(
        "--arms", default=None, help="comma-separated arms for --smoke"
    )
    parser.add_argument("--episodes", type=int, default=32)
    parser.add_argument("--workers", type=int, default=8)
    parser.add_argument("--duration", type=int, default=300)
    parser.add_argument("--period", default="off_peak")
    parser.add_argument("--base-seed", type=int, default=1)
    parser.add_argument("--intersections", type=int, default=20)
    parser.add_argument("--checkpoint-every", type=int, default=20)
    parser.add_argument(
        "--init",
        default=(
            "algorithms/mappo/runs/mappo_v2/m0/mappo_v2_shared_init.pt"
        ),
    )
    parser.add_argument(
        "--adjacency",
        default=(
            "algorithms/mappo/runs/mappo_v2/m0/"
            "intersection_adjacency_m1_symmetric.json"
        ),
    )
    parser.add_argument("--out", required=True)
    return parser


def main(argv: list[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    if args.smoke:
        raw = args.arms or "m1_0,m1_a,m1_b"
        arms = tuple(
            arm for arm in (item.strip() for item in raw.split(",")) if arm
        )
        unknown = [arm for arm in arms if arm not in ARM_SPECS]
        if unknown:
            raise SystemExit(f"unknown smoke arms: {sorted(unknown)}")
        return _run_smoke(
            arms=arms,
            episodes=args.episodes,
            workers=args.workers,
            duration=args.duration,
            period=args.period,
            base_seed=args.base_seed,
            checkpoint_every=max(1, args.checkpoint_every),
            intersections=args.intersections,
            init_path=args.init,
            adjacency_path=args.adjacency,
            out_dir=args.out,
        )
    if args.arm is None:
        raise SystemExit("either --arm or --smoke is required")
    return _run_arm(
        arm=args.arm,
        episodes=args.episodes,
        workers=args.workers,
        duration=args.duration,
        period=args.period,
        base_seed=args.base_seed,
        checkpoint_every=max(1, args.checkpoint_every),
        intersections=args.intersections,
        init_path=args.init,
        adjacency_path=args.adjacency,
        out_dir=args.out,
    )


if __name__ == "__main__":
    raise SystemExit(main())
