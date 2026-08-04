"""Frozen-Actor, same-rollout Local/Global Critic signal diagnostic."""

from __future__ import annotations

import argparse
from dataclasses import asdict
import logging
import os
from pathlib import Path
import sys
from typing import Mapping

REPO_ROOT = Path(__file__).resolve().parents[2]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

os.environ.setdefault("SUMO_HOME", "/usr/share/sumo")
_SUMO_BIN = str(Path(os.environ["SUMO_HOME"]) / "bin")
_PATH_ENTRIES = [
    entry
    for entry in os.environ.get("PATH", "").split(os.pathsep)
    if entry and entry != _SUMO_BIN
]
os.environ["PATH"] = os.pathsep.join([*_PATH_ENTRIES, _SUMO_BIN])

from algorithms.mappo.config import MAPPOConfig  # noqa: E402
from algorithms.mappo.diagnostics import (  # noqa: E402
    merge_action_diagnostics,
    merge_reward_diagnostics,
    probe_critic_signal,
    reward_metric_alignment,
)
from algorithms.mappo.evaluate_checkpoint import (  # noqa: E402
    EvaluationCheckpoint,
    _atomic_write_json,
    load_evaluation_checkpoint,
    validate_evaluation_seeds,
)
from algorithms.mappo.models import MAPPOPolicy
from traffic_control.ippo.identity import IDENTITY_SLOT_IDS  # noqa: E402
from algorithms.mappo.train import _run_policy_batch  # noqa: E402


logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(processName)s:%(name)s] %(levelname)s: %(message)s",
)
logger = logging.getLogger("mappo.diagnose_critic_signal")


def _policy(checkpoint: EvaluationCheckpoint) -> MAPPOPolicy:
    config = checkpoint.config
    policy = MAPPOPolicy(
        obs_dim=config.obs_dim,
        num_agents=len(IDENTITY_SLOT_IDS),
        critic_scope=config.critic_scope,
        actor_init_seed=checkpoint.metadata.actor_init_seed,
        critic_init_seed=checkpoint.metadata.critic_init_seed,
        hidden_dim=config.hidden_dim,
        phase_feature_dim=config.phase_feature_dim,
        model_version=config.model_version,
        actor_variant=config.actor_variant,
        residual_hidden_dim=config.residual_hidden_dim,
        identity_offset=config.identity_offset,
        residual_init_seed=(
            44
            if checkpoint.metadata.residual_init_seed is None
            else checkpoint.metadata.residual_init_seed
        ),
    )
    policy.load_state_dict(checkpoint.policy_state, strict=True)
    policy.eval()
    return policy


def _scope_neutral_config(config: MAPPOConfig) -> dict[str, object]:
    values = asdict(config)
    values.pop("critic_scope")
    return values


def _validate_checkpoints(
    actor: EvaluationCheckpoint,
    local: EvaluationCheckpoint,
    global_checkpoint: EvaluationCheckpoint,
) -> None:
    if local.config.critic_scope != "local":
        raise ValueError("--local-checkpoint must contain a Local Critic")
    if global_checkpoint.config.critic_scope != "global":
        raise ValueError("--global-checkpoint must contain a Global Critic")
    expected = _scope_neutral_config(actor.config)
    for label, checkpoint in (
        ("local", local),
        ("global", global_checkpoint),
    ):
        if _scope_neutral_config(checkpoint.config) != expected:
            raise ValueError(
                f"{label} checkpoint differs beyond critic_scope"
            )


def _checkpoint_descriptor(
    checkpoint: EvaluationCheckpoint,
) -> dict[str, object]:
    return {
        "path": str(checkpoint.path),
        "metadata": asdict(checkpoint.metadata),
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--actor-checkpoint", type=Path, required=True)
    parser.add_argument("--local-checkpoint", type=Path, required=True)
    parser.add_argument("--global-checkpoint", type=Path, required=True)
    parser.add_argument("--seeds", nargs="+", type=int, required=True)
    parser.add_argument("--duration", type=int, default=300)
    parser.add_argument("--period", default="off_peak")
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args(argv)
    if args.duration <= 0:
        parser.error("duration must be positive")
    if len(args.seeds) != len(set(args.seeds)):
        parser.error("seeds must be unique")

    try:
        actor_checkpoint = load_evaluation_checkpoint(args.actor_checkpoint)
        local_checkpoint = load_evaluation_checkpoint(args.local_checkpoint)
        global_checkpoint = load_evaluation_checkpoint(args.global_checkpoint)
        _validate_checkpoints(
            actor_checkpoint, local_checkpoint, global_checkpoint
        )
        for checkpoint in (
            actor_checkpoint,
            local_checkpoint,
            global_checkpoint,
        ):
            validate_evaluation_seeds(checkpoint.metadata, args.seeds)
    except (KeyError, RuntimeError, TypeError, ValueError) as exc:
        parser.error(str(exc))

    actor_policy = _policy(actor_checkpoint)
    local_policy = _policy(local_checkpoint)
    global_policy = _policy(global_checkpoint)
    config = actor_checkpoint.config
    seeds = tuple(int(seed) for seed in args.seeds)
    logger.info(
        "Frozen-Actor probe actor_scope=%s seeds=%s duration=%ds tls=%d",
        config.critic_scope,
        list(seeds),
        args.duration,
        len(config.intersection_ids),
    )
    results = _run_policy_batch(
        seeds=seeds,
        periods=tuple(args.period for _ in seeds),
        config=config,
        duration=args.duration,
        policy=actor_policy,
        policy_generation=actor_checkpoint.metadata.policy_generation,
        actor_init_seed=actor_checkpoint.metadata.actor_init_seed,
        critic_init_seed=actor_checkpoint.metadata.critic_init_seed,
        residual_init_seed=actor_checkpoint.metadata.residual_init_seed,
    )
    failures = [result for result in results if result["status"] != "complete"]
    if failures:
        report = {
            "status": "failed",
            "reason": "incomplete rollout batch; probe not computed",
            "seeds": list(seeds),
            "failures": [
                {
                    "seed": result["seed"],
                    "status": result["status"],
                    "rollout_error": getattr(result["rollout"], "error", None),
                }
                for result in failures
            ],
        }
        _atomic_write_json(args.output.expanduser().resolve(), report)
        return 1

    rollouts = tuple(result["rollout"] for result in results)
    signal = probe_critic_signal(
        rollouts,
        config=config,
        actor_policy=actor_policy,
        local_policy=local_policy,
        global_policy=global_policy,
    )
    reward_records = [
        {
            "reward_diagnostics": rollout.reward_diagnostics,
            "metrics": result["metrics"],
        }
        for rollout, result in zip(rollouts, results, strict=True)
    ]
    action_snapshots = [
        diagnostics
        for rollout in rollouts
        if isinstance((diagnostics := rollout.action_diagnostics), Mapping)
    ]
    reward_summaries = [
        diagnostics
        for rollout in rollouts
        if isinstance((diagnostics := rollout.reward_diagnostics), Mapping)
    ]
    report = {
        "status": "complete",
        "schema": "mappo_frozen_actor_probe_report_v4",
        "config": {
            "seeds": list(seeds),
            "duration_s": args.duration,
            "period": args.period,
            "intersections": list(config.intersection_ids),
            "parallel_workers": len(seeds),
            "actor_scope": config.critic_scope,
        },
        "checkpoints": {
            "actor": _checkpoint_descriptor(actor_checkpoint),
            "local": _checkpoint_descriptor(local_checkpoint),
            "global": _checkpoint_descriptor(global_checkpoint),
        },
        "critic_signal": signal,
        "action_diagnostics": merge_action_diagnostics(action_snapshots),
        "reward_diagnostics": merge_reward_diagnostics(reward_summaries),
        "reward_metric_alignment": reward_metric_alignment(reward_records),
        "workers": [
            {
                "seed": result["seed"],
                "elapsed_s": result["elapsed"],
                "metrics": result["metrics"],
                "transition_count": len(rollout.transitions),
                "reward_diagnostics": rollout.reward_diagnostics,
            }
            for rollout, result in zip(rollouts, results, strict=True)
        ],
    }
    output = args.output.expanduser().resolve()
    _atomic_write_json(output, report)
    logger.info(
        "Probe complete samples=%d adv_corr=%s sign_disagree=%.6f "
        "gradient_cosine=%s output=%s",
        signal["sample_count"],
        (
            "N/A"
            if signal["advantage_correlation"] is None
            else f"{signal['advantage_correlation']:.6f}"
        ),
        signal["advantage_sign_disagreement_fraction"],
        (
            "N/A"
            if signal["actor_gradient_cosine"] is None
            else f"{signal['actor_gradient_cosine']:.6f}"
        ),
        output,
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
