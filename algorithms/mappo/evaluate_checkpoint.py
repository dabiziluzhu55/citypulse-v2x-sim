"""Deterministic held-out evaluation for one MAPPO-compatible checkpoint."""

from __future__ import annotations

import argparse
from concurrent.futures import ProcessPoolExecutor, as_completed
from dataclasses import asdict, dataclass
import json
import logging
import multiprocessing
import os
from pathlib import Path
import random
import sys
import time
from typing import Mapping, Sequence

import numpy as np
import torch


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

import algorithms.mappo as entrypoint  # noqa: E402
from algorithms.ippo.evaluate_paired import (  # noqa: E402
    OFFICIAL_METRIC_NAMES,
    SUMMARY_METRICS,
    _describe,
    _parse_tripinfo,
    _snapshot_metrics,
)
from algorithms.mappo.checkpoint import (  # noqa: E402
    CheckpointMetadata,
    load_checkpoint,
    read_checkpoint_metadata,
)
from algorithms.mappo.config import (  # noqa: E402
    JOINT_STEP_SCHEMA,
    MAPPOConfig,
    TEAM_REWARD_SCHEMA,
    assert_seed_disjoint,
)
from algorithms.mappo.diagnostics import merge_action_diagnostics  # noqa: E402
from algorithms.mappo.features import (  # noqa: E402
    IPPO_V8_LOCAL_OBSERVATION_SCHEMA,
)
from algorithms.mappo.models import MAPPOPolicy  # noqa: E402
from algorithms.mappo.train import REWARD_DEFINITION  # noqa: E402
from traffic_control.ippo.identity import IDENTITY_SLOT_IDS  # noqa: E402
from algorithms.mappo.trainer import MAPPOTrainer  # noqa: E402
from simulation.sumo.session import (  # noqa: E402
    SimulationConfig,
    SimulationManager,
)


logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(processName)s:%(name)s] "
    "%(levelname)s: %(message)s",
)
logger = logging.getLogger("mappo.evaluate_checkpoint")


@dataclass(frozen=True)
class EvaluationCheckpoint:
    path: Path
    config: MAPPOConfig
    metadata: CheckpointMetadata
    policy_state: Mapping[str, torch.Tensor]


def _config_from_metadata(
    metadata: CheckpointMetadata,
    *,
    intersection_ids: Sequence[str] | None = None,
) -> MAPPOConfig:
    return MAPPOConfig(
        intersection_ids=(
            metadata.intersection_ids
            if intersection_ids is None
            else tuple(intersection_ids)
        ),
        critic_scope=metadata.critic_scope,
        model_version=metadata.model_version,
        actor_variant=metadata.actor_variant or "shared",
        residual_hidden_dim=metadata.residual_hidden_dim or 32,
        identity_offset=metadata.identity_offset or 9,
        phase_feature_schema=metadata.phase_feature_schema,
        phase_feature_dim=metadata.phase_feature_dim,
        max_action_dim=metadata.max_action_dim,
        hidden_dim=metadata.hidden_dim,
        action_interval_s=metadata.action_interval_s,
        max_green_factor=metadata.max_green_factor,
        effective_demand_enabled=metadata.effective_demand_enabled,
        actor_lr=metadata.actor_lr,
        critic_lr=metadata.critic_lr,
        gamma=metadata.gamma,
        gae_lambda=metadata.gae_lambda,
        ppo_clip=metadata.ppo_clip,
        entropy_coef=metadata.entropy_coef,
        ppo_epochs=metadata.ppo_epochs,
        minibatch_size=metadata.minibatch_size,
        max_grad_norm=metadata.max_grad_norm,
        huber_delta=metadata.huber_delta,
        centralized_state_schema=metadata.centralized_state_schema,
        reward_scope=metadata.reward_scope,
        team_reward_schema=(
            TEAM_REWARD_SCHEMA
            if metadata.team_reward_schema == "N/A"
            else metadata.team_reward_schema
        ),
        joint_step_schema=(
            JOINT_STEP_SCHEMA
            if metadata.joint_step_schema == "N/A"
            else metadata.joint_step_schema
        ),
        critic_target_scope=metadata.critic_target_scope,
    )


def load_evaluation_checkpoint(
    path: str | os.PathLike[str],
    *,
    intersection_ids: Sequence[str] | None = None,
) -> EvaluationCheckpoint:
    """Load a fully compatibility-checked CPU snapshot for evaluation workers.

    ``intersection_ids`` may be a proper subset of the checkpoint training
    intersections for zero-shot scenario evaluation (east_dense / west_dense /
    custom subsets); the fixed 20-slot identity policy remains unchanged.
    """

    checkpoint_path = Path(path).expanduser().resolve()
    metadata = read_checkpoint_metadata(checkpoint_path)
    config = _config_from_metadata(metadata, intersection_ids=intersection_ids)
    policy = MAPPOPolicy(
        obs_dim=config.obs_dim,
        num_agents=len(IDENTITY_SLOT_IDS),
        critic_scope=config.critic_scope,
        actor_init_seed=metadata.actor_init_seed,
        critic_init_seed=metadata.critic_init_seed,
        hidden_dim=config.hidden_dim,
        phase_feature_dim=config.phase_feature_dim,
        model_version=config.model_version,
        actor_variant=config.actor_variant,
        residual_hidden_dim=config.residual_hidden_dim,
        identity_offset=config.identity_offset,
        residual_init_seed=(
            44
            if metadata.residual_init_seed is None
            else metadata.residual_init_seed
        ),
    )
    trainer = MAPPOTrainer(policy, config)
    loaded_metadata = load_checkpoint(
        checkpoint_path,
        policy,
        trainer,
        expected_config=config,
        expected_local_observation_schema=IPPO_V8_LOCAL_OBSERVATION_SCHEMA,
        expected_reward_definition=REWARD_DEFINITION,
        expected_residual_init_seed=metadata.residual_init_seed,
        restore_rng=False,
    )
    if loaded_metadata != metadata:
        raise RuntimeError("checkpoint metadata changed during evaluation load")
    return EvaluationCheckpoint(
        path=checkpoint_path,
        config=config,
        metadata=metadata,
        policy_state={
            name: value.detach().cpu().clone()
            for name, value in policy.state_dict().items()
        },
    )


def validate_evaluation_seeds(
    metadata: CheckpointMetadata, seeds: Sequence[int]
) -> None:
    normalized = tuple(int(seed) for seed in seeds)
    if not normalized:
        raise ValueError("at least one evaluation seed is required")
    if len(normalized) != len(set(normalized)):
        raise ValueError("evaluation seeds must be unique")
    assert_seed_disjoint(
        range(metadata.training_seed_start, metadata.training_seed_end + 1),
        normalized,
    )


def _run_evaluation(request: Mapping[str, object]) -> dict[str, object]:
    seed = int(request["seed"])
    os.environ["OMP_NUM_THREADS"] = "1"
    os.environ["MKL_NUM_THREADS"] = "1"
    torch.set_num_threads(1)
    random.seed(seed)
    np.random.seed(seed % (2**32 - 1))
    torch.manual_seed(seed)

    config = request["mappo_config"]
    metadata = request["checkpoint_metadata"]
    policy_state = request["policy_state"]
    if not isinstance(config, MAPPOConfig):
        raise TypeError("evaluation request has no MAPPOConfig")
    if not isinstance(metadata, CheckpointMetadata):
        raise TypeError("evaluation request has no checkpoint metadata")
    if not isinstance(policy_state, Mapping):
        raise TypeError("evaluation request has no policy state")

    entrypoint.prepare_collector(
        policy_state=policy_state,
        config=config,
        policy_generation=metadata.policy_generation,
        rollout_seed=seed,
        actor_init_seed=metadata.actor_init_seed,
        critic_init_seed=metadata.critic_init_seed,
        residual_init_seed=metadata.residual_init_seed,
        expected_duration_s=float(request["duration"]),
        mode="model",
        record_evaluation=True,
    )
    manager = SimulationManager()
    session_id: str | None = None
    started_at = time.monotonic()
    try:
        simulation_config = SimulationConfig(
            intersection_ids=config.intersection_ids,
            period=str(request["period"]),
            duration_seconds=int(request["duration"]),
            control_mode="algorithm",
            algorithm_transport="local",
            algorithm_module="algorithms.mappo",
            decision_interval=5.0,
            minimum_green=5.0,
            seed=seed,
            step_length=0.05,
            ai_observer_module="algorithms.evaluation.observer",
            ai_frame_interval_seconds=1.0,
        )
        session_id = manager.start(simulation_config)
        snapshot = manager.wait(
            session_id,
            timeout=max(600.0, float(request["duration"]) * 3.0),
        )
        if snapshot.state != "COMPLETED" or snapshot.metrics is None:
            raise RuntimeError(
                f"SUMO state={snapshot.state} "
                f"error={snapshot.error or ''}".strip()
            )
        tripinfo_path = manager.session_root / session_id / "tripinfo.xml"
        from algorithms.evaluation.metrics import (
            apply_tripinfo_completed_metrics,
        )
        from algorithms.evaluation.runtime import last_result

        official = last_result(session_id)
        if official is not None:
            official = apply_tripinfo_completed_metrics(
                official, str(tripinfo_path)
            )
        official_metrics = official.to_dict() if official is not None else {}
        action_diagnostics = entrypoint.pop_collected_diagnostics()
        for name in OFFICIAL_METRIC_NAMES:
            official_metrics.setdefault(name, None)
        missing_official = [
            name
            for name in OFFICIAL_METRIC_NAMES
            if official_metrics.get(name) is None
        ]
        return {
            "status": "complete",
            "seed": seed,
            "session_id": session_id,
            "elapsed_s": time.monotonic() - started_at,
            **_snapshot_metrics(snapshot),
            **_parse_tripinfo(tripinfo_path),
            "official_metrics": official_metrics,
            "missing_official_metrics": missing_official,
            "action_diagnostics": action_diagnostics,
        }
    except BaseException as exc:
        cleanup_error: str | None = None
        if session_id is not None:
            try:
                manager.stop(session_id)
                manager.wait(session_id, timeout=60.0)
            except BaseException as cleanup_exc:
                cleanup_error = (
                    f"{type(cleanup_exc).__name__}: {cleanup_exc}"
                )
        failure = {
            "status": "failed",
            "seed": seed,
            "session_id": session_id,
            "elapsed_s": time.monotonic() - started_at,
            "error": f"{type(exc).__name__}: {exc}",
        }
        if cleanup_error is not None:
            failure["cleanup_error"] = cleanup_error
        return failure


def _summarize(rows: Sequence[Mapping[str, object]]) -> dict[str, object]:
    complete = [row for row in rows if row.get("status") == "complete"]
    failed = [row for row in rows if row.get("status") != "complete"]
    return {
        "episodes_requested": len(rows),
        "episodes_complete": len(complete),
        "episodes_failed": len(failed),
        **{
            metric: _describe(
                [
                    row.get(metric)
                    if row.get("status") == "complete"
                    else None
                    for row in rows
                ]
            )
            for metric in SUMMARY_METRICS
        },
        "official_metrics": {
            name: _describe(
                [
                    row.get("official_metrics", {}).get(name)
                    if isinstance(row.get("official_metrics"), Mapping)
                    else None
                    for row in rows
                ]
            )
            for name in OFFICIAL_METRIC_NAMES
        },
        "action_diagnostics": merge_action_diagnostics(
            [
                diagnostics
                for row in complete
                if isinstance(
                    (diagnostics := row.get("action_diagnostics")), Mapping
                )
            ]
        ),
    }


def _atomic_write_json(path: Path, payload: Mapping[str, object]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(f"{path.name}.tmp-{os.getpid()}")
    try:
        temporary.write_text(
            json.dumps(payload, ensure_ascii=False, indent=2) + "\n",
            encoding="utf-8",
        )
        os.replace(temporary, path)
    finally:
        temporary.unlink(missing_ok=True)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--checkpoint", type=Path, required=True)
    parser.add_argument("--seeds", nargs="+", type=int, required=True)
    parser.add_argument("--workers", type=int, default=4)
    parser.add_argument("--duration", type=int, default=300)
    parser.add_argument("--period", default="off_peak")
    parser.add_argument("--label")
    parser.add_argument("--output", type=Path, required=True)
    scope = parser.add_mutually_exclusive_group()
    scope.add_argument(
        "--preset",
        help="Scenario preset (east_dense / west_dense / xiongan_20); "
        "zero-shot subset of the checkpoint training intersections",
    )
    scope.add_argument(
        "--intersections",
        nargs="+",
        help="Explicit controlled intersection subset (zero-shot)",
    )
    args = parser.parse_args(argv)

    if args.workers <= 0:
        parser.error("workers must be positive")
    if args.duration <= 0:
        parser.error("duration must be positive")
    if not args.period:
        parser.error("period must be non-empty")
    checkpoint_path = args.checkpoint.expanduser().resolve()
    if not checkpoint_path.is_file():
        parser.error("checkpoint must be an existing file")
    if args.preset is not None:
        from backend.app.scenario.presets import SCENARIO_PRESET_REGISTRY

        if args.preset not in SCENARIO_PRESET_REGISTRY:
            parser.error(
                "unknown scenario preset "
                f"{args.preset!r}; available: "
                f"{sorted(SCENARIO_PRESET_REGISTRY)}"
            )
        intersections = SCENARIO_PRESET_REGISTRY[args.preset].intersection_ids
    elif args.intersections is not None:
        intersections = tuple(dict.fromkeys(args.intersections))
    else:
        intersections = None
    try:
        checkpoint = load_evaluation_checkpoint(
            checkpoint_path, intersection_ids=intersections
        )
        validate_evaluation_seeds(checkpoint.metadata, args.seeds)
    except (KeyError, RuntimeError, TypeError, ValueError) as exc:
        parser.error(str(exc))

    seeds = tuple(int(seed) for seed in args.seeds)
    if args.workers < len(seeds):
        parser.error(
            "workers must be at least the number of seeds so failed "
            "sessions cannot contaminate a reused worker process"
        )
    worker_count = len(seeds)
    label = str(args.label or checkpoint.path.stem)
    jobs = [
        {
            "seed": seed,
            "period": args.period,
            "duration": args.duration,
            "mappo_config": checkpoint.config,
            "checkpoint_metadata": checkpoint.metadata,
            "policy_state": checkpoint.policy_state,
        }
        for seed in seeds
    ]
    logger.info(
        "Deterministic checkpoint evaluation: label=%s seeds=%s "
        "duration=%ds tls=%d workers=%d",
        label,
        list(seeds),
        args.duration,
        len(checkpoint.config.intersection_ids),
        worker_count,
    )

    rows: list[dict[str, object]] = []
    context = multiprocessing.get_context("spawn")
    with ProcessPoolExecutor(
        max_workers=worker_count, mp_context=context
    ) as executor:
        futures = {
            executor.submit(_run_evaluation, job): job for job in jobs
        }
        for future in as_completed(futures):
            job = futures[future]
            try:
                row = future.result()
            except BaseException as exc:
                row = {
                    "status": "failed",
                    "seed": job["seed"],
                    "error": "worker process "
                    f"{type(exc).__name__}: {exc}",
                }
            rows.append(row)
            if row["status"] == "complete":
                logger.info(
                    "%s seed=%d arrived=%d remaining=%d waiting=%.1f "
                    "missing_official=%s",
                    label,
                    row["seed"],
                    row["arrived"],
                    row["remaining"],
                    row["waiting"],
                    row["missing_official_metrics"],
                )
            else:
                logger.error(
                    "%s seed=%d failed: %s",
                    label,
                    row["seed"],
                    row["error"],
                )

    rows.sort(key=lambda row: int(row["seed"]))
    report = {
        "label": label,
        "checkpoint": str(checkpoint.path),
        "checkpoint_metadata": asdict(checkpoint.metadata),
        "config": {
            "seeds": list(seeds),
            "duration_s": args.duration,
            "intersections": list(checkpoint.config.intersection_ids),
            "period": args.period,
            "deterministic": True,
            "action_interval_s": checkpoint.config.action_interval_s,
            "parallel_workers": worker_count,
        },
        "summary": _summarize(rows),
        "runs": rows,
    }
    output = args.output.expanduser().resolve()
    _atomic_write_json(output, report)
    failures = [row for row in rows if row["status"] != "complete"]
    logger.info("Evaluation report: %s", output)
    return 1 if failures else 0


if __name__ == "__main__":
    raise SystemExit(main())
