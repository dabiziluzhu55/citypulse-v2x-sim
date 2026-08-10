"""Synchronous CoSLight training with one isolated SUMO process per rollout."""

from __future__ import annotations

import argparse
import gc
import logging
import math
import multiprocessing
import os
import random
import sys
import time
from concurrent.futures import ProcessPoolExecutor, as_completed
from pathlib import Path
from typing import Iterable, Mapping, Sequence

import numpy as np
import torch


REPO_ROOT = Path(__file__).resolve().parents[2]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

os.environ.setdefault("SUMO_HOME", "/usr/share/sumo")
sumo_bin = str(Path(os.environ["SUMO_HOME"]) / "bin")
path_entries = [
    entry
    for entry in os.environ.get("PATH", "").split(os.pathsep)
    if entry and entry != sumo_bin
]
os.environ["PATH"] = os.pathsep.join([*path_entries, sumo_bin])

from algorithms.coslight import controller  # noqa: E402
from algorithms.coslight.controller import (  # noqa: E402
    CHECKPOINT_INTERVAL,
    COLLABORATION_SCHEMA,
    PHASE_FEATURE_SCHEMA,
    POLICY_ARCHITECTURE,
    POLICY_OBJECTIVE,
    load_checkpoint_metadata,
)
from simulation.sumo.session import SimulationConfig, SimulationManager  # noqa: E402


logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(processName)s:%(name)s] %(levelname)s: %(message)s",
)
logger = logging.getLogger("coslight.parallel")

DEFAULT_INTERSECTIONS = tuple(f"demo_{index}" for index in range(1, 21))
DEFAULT_OUTPUT = Path(__file__).with_name("runs") / "coslight_parallel"


def _seed_batches(
    *, first_seed: int, episodes: int, workers: int
) -> Iterable[tuple[int, ...]]:
    for offset in range(0, episodes, workers):
        yield tuple(
            range(first_seed + offset, first_seed + min(offset + workers, episodes))
        )


def _canonical_tls_order(intersections: Sequence[str]) -> tuple[str, ...]:
    """Match the controller's stable checkpoint and observation order."""

    return tuple(sorted(str(intersection) for intersection in intersections))


def _validated_worker_results(
    results: Sequence[Mapping[str, object]], *, expected_generation: int
) -> list[dict]:
    failures = [result for result in results if result.get("status") != "complete"]
    if failures:
        details = "; ".join(
            f"seed={result.get('seed')} {result.get('error', result.get('status'))}"
            for result in failures
        )
        raise RuntimeError(f"Parallel policy batch discarded: {details}")

    completed = [dict(result) for result in results]
    seeds = [int(result["seed"]) for result in completed]
    if len(set(seeds)) != len(seeds):
        raise RuntimeError(f"Parallel policy batch contains duplicate seeds: {seeds}")
    for result in completed:
        rollout = result.get("rollout")
        if not isinstance(rollout, Mapping) or int(rollout.get("sample_count", 0)) <= 0:
            raise RuntimeError(
                f"Parallel worker seed={result['seed']} returned no samples"
            )
        generation = int(rollout.get("policy_generation", -1))
        if generation != expected_generation:
            raise RuntimeError(
                "Parallel policy generation mismatch: "
                f"seed={result['seed']} expected={expected_generation} got={generation}"
            )
    return sorted(completed, key=lambda item: int(item["seed"]))


def _stop_timed_out_session(
    manager: SimulationManager, session_id: str | None
) -> None:
    if session_id is None:
        return
    try:
        manager.stop(session_id)
        manager.wait(session_id, timeout=60.0)
    except Exception:
        logger.exception("SUMO worker timeout cleanup failed")


def _metrics_dict(snapshot) -> dict:
    metrics = snapshot.metrics
    return {
        "departed": int(metrics.departed_vehicles),
        "arrived": int(metrics.arrived_vehicles),
        "waiting": float(metrics.total_waiting_time),
        "hard_braking": int(metrics.hard_braking_events),
        "fuel_mg": float(metrics.fuel_consumed_mg),
    }


def _aggregate_signal_diagnostics(
    rollouts: Sequence[Mapping[str, object]],
) -> dict:
    diagnostics = [
        rollout.get("signal_execution", {})
        for rollout in rollouts
    ]
    commands = sum(int(item.get("commands", 0)) for item in diagnostics)
    requested = sum(int(item.get("change_requests", 0)) for item in diagnostics)
    observed = sum(int(item.get("observed_changes", 0)) for item in diagnostics)
    unresolved = sum(
        int(item.get("unresolved_changes", 0)) for item in diagnostics
    )
    forced = sum(
        int(item.get("max_green_forced_commands", 0))
        for item in diagnostics
    )
    delay_total = sum(
        float(item.get("mean_change_delay_s", 0.0))
        * int(item.get("observed_changes", 0))
        for item in diagnostics
    )
    return {
        "commands": commands,
        "change_requests": requested,
        "observed_changes": observed,
        "unresolved_changes": unresolved,
        "max_green_forced_commands": forced,
        "change_execution_rate": observed / requested if requested else 1.0,
        "mean_change_delay_s": delay_total / observed if observed else 0.0,
        "max_change_delay_s": max(
            (float(item.get("max_change_delay_s", 0.0)) for item in diagnostics),
            default=0.0,
        ),
        "max_observed_green_s": max(
            (float(item.get("max_observed_green_s", 0.0)) for item in diagnostics),
            default=0.0,
        ),
        "mean_phase_dominance": float(
            np.mean(
                [float(item.get("mean_phase_dominance", 0.0)) for item in diagnostics]
            )
        ),
        "max_phase_dominance": max(
            (float(item.get("max_phase_dominance", 0.0)) for item in diagnostics),
            default=0.0,
        ),
        "multi_phase_max_observed_green_s": max(
            (
                float(item.get("multi_phase_max_observed_green_s", 0.0))
                for item in diagnostics
            ),
            default=0.0,
        ),
        "multi_phase_mean_phase_dominance": float(
            np.mean(
                [
                    float(item.get("multi_phase_mean_phase_dominance", 0.0))
                    for item in diagnostics
                ]
            )
        ),
        "multi_phase_max_phase_dominance": max(
            (
                float(item.get("multi_phase_max_phase_dominance", 0.0))
                for item in diagnostics
            ),
            default=0.0,
        ),
    }


def _run_sumo_worker(request: Mapping[str, object]) -> dict:
    """Collect one raw rollout without optimizer or reward-stat updates."""

    seed = int(request["seed"])
    os.environ["COSLIGHT_MODE"] = "collect"
    os.environ["COSLIGHT_TOP_K"] = str(request["top_k"])
    os.environ["COSLIGHT_REWARD_MODE"] = str(request["reward_mode"])
    os.environ["COSLIGHT_MAX_GREEN_FACTOR"] = str(
        request["max_green_factor"]
    )
    os.environ.pop("COSLIGHT_PRESSURE_SHIELD_MARGIN", None)
    os.environ.pop("COSLIGHT_RESIDUAL_MIN_BEST_PRESSURE", None)
    os.environ.pop("COSLIGHT_SWITCH_LOGIT_MARGIN", None)
    os.environ["COSLIGHT_CLOUD_MODE"] = "off"
    os.environ.pop("COSLIGHT_CLOUD_TOPOLOGY", None)
    os.environ["COSLIGHT_EPISODE_DURATION"] = str(request["duration"])
    os.environ["COSLIGHT_VEHICLE_GUIDANCE"] = str(request["vehicle_guidance"])
    os.environ.pop("COSLIGHT_MODEL_PATH", None)
    os.environ.pop("COSLIGHT_RESUME_PATH", None)
    os.environ["OMP_NUM_THREADS"] = "1"
    os.environ["MKL_NUM_THREADS"] = "1"
    torch.set_num_threads(1)
    random.seed(seed)
    np.random.seed(seed % (2**32 - 1))
    controller.prepare_collector(
        policy_state=request.get("policy_state"),
        policy_seed=int(request["policy_seed"]),
        rollout_seed=seed,
        policy_generation=int(request["policy_generation"]),
        value_stats=request.get("value_stats"),
    )

    manager = SimulationManager()
    session_id = None
    started_at = time.monotonic()
    try:
        config = SimulationConfig(
            intersection_ids=list(request["intersection_ids"]),
            period=str(request["period"]),
            duration_seconds=int(request["duration"]),
            control_mode="algorithm",
            algorithm_transport="local",
            algorithm_module="algorithms.coslight",
            decision_interval=5.0,
            minimum_green=5.0,
            seed=seed,
            step_length=0.05,
        )
        session_id = manager.start(config)
        snapshot = manager.wait(
            session_id,
            timeout=max(600.0, float(request["duration"]) * 3.0),
        )
        if snapshot is None:
            raise RuntimeError("SUMO returned no terminal snapshot")
        if snapshot.state != "COMPLETED" or snapshot.metrics is None:
            raise RuntimeError(
                f"SUMO state={snapshot.state} error={snapshot.error or ''}".strip()
            )
        return {
            "status": "complete",
            "seed": seed,
            "elapsed": time.monotonic() - started_at,
            "metrics": _metrics_dict(snapshot),
            "rollout": controller.take_collected_rollout(),
        }
    except TimeoutError as exc:
        _stop_timed_out_session(manager, session_id)
        return {"status": "failed", "seed": seed, "error": f"TimeoutError: {exc}"}
    except BaseException as exc:
        return {
            "status": "failed",
            "seed": seed,
            "error": f"{type(exc).__name__}: {exc}",
        }
    finally:
        del manager
        gc.collect()


def _policy_states_equal(
    left: Mapping[str, torch.Tensor], right: Mapping[str, torch.Tensor]
) -> bool:
    return left.keys() == right.keys() and all(
        torch.equal(left[name].cpu(), right[name].cpu()) for name in left
    )


def _value_stats_equal(
    left: Mapping[str, object], right: Mapping[str, object]
) -> bool:
    return (
        int(left.get("count", -1)) == int(right.get("count", -1))
        and math.isclose(
            float(left.get("mean", math.nan)),
            float(right.get("mean", math.nan)),
            rel_tol=0.0,
            abs_tol=0.0,
        )
        and math.isclose(
            float(left.get("m2", math.nan)),
            float(right.get("m2", math.nan)),
            rel_tol=0.0,
            abs_tol=0.0,
        )
    )


def _metadata_signature(metadata: Mapping[str, object]) -> tuple:
    intersections = metadata.get("intersections", {})
    if not isinstance(intersections, Mapping):
        raise ValueError("Worker metadata has no intersection mapping")
    return tuple(
        (
            str(intersection_id),
            tuple(
                int(phase)
                for phase in intersections[intersection_id].get("phase_order", ())
            ),
        )
        for intersection_id in sorted(intersections)
    )


def _training_seed_range(
    base_seed: int, episodes: int, resume_path: Path | None
) -> tuple[int, int, int]:
    first_seed = base_seed + 1
    recorded_start = first_seed
    if resume_path is not None:
        checkpoint = load_checkpoint_metadata(resume_path)
        previous = checkpoint.get("training_seed_range")
        if isinstance(previous, Mapping) and "start" in previous and "end" in previous:
            first_seed = max(first_seed, int(previous["end"]) + 1)
            recorded_start = int(previous["start"])
        else:
            completed = int(checkpoint.get("episode", 0))
            first_seed = max(first_seed, base_seed + completed + 1)
    return first_seed, first_seed + episodes - 1, recorded_start


def _run_policy_batch(
    *,
    seeds: Sequence[int],
    intersections: Sequence[str],
    duration: int,
    period: str,
    top_k: int,
    max_green_factor: float,
    vehicle_guidance: str,
    reward_mode: str,
    policy_seed: int,
    policy_generation: int,
    policy_state: Mapping[str, torch.Tensor] | None,
    value_stats: Mapping[str, object] | None,
) -> list[dict]:
    request_base = {
        "intersection_ids": tuple(intersections),
        "duration": duration,
        "period": period,
        "top_k": top_k,
        "max_green_factor": max_green_factor,
        "vehicle_guidance": vehicle_guidance,
        "reward_mode": reward_mode,
        "policy_seed": policy_seed,
        "policy_generation": policy_generation,
        "policy_state": policy_state,
        "value_stats": value_stats,
    }
    context = multiprocessing.get_context("spawn")
    results = []
    with ProcessPoolExecutor(max_workers=len(seeds), mp_context=context) as executor:
        futures = {
            executor.submit(_run_sumo_worker, {**request_base, "seed": seed}): seed
            for seed in seeds
        }
        for future in as_completed(futures):
            seed = futures[future]
            try:
                results.append(future.result())
            except BaseException as exc:
                results.append(
                    {
                        "status": "failed",
                        "seed": seed,
                        "error": f"worker process {type(exc).__name__}: {exc}",
                    }
                )
    return _validated_worker_results(
        results, expected_generation=policy_generation
    )


def _positive(parser: argparse.ArgumentParser, name: str, value: float) -> None:
    if value <= 0:
        parser.error(f"{name} must be positive")


def _nonnegative(parser: argparse.ArgumentParser, name: str, value: float) -> None:
    if not math.isfinite(value) or value < 0:
        parser.error(f"{name} must be finite and non-negative")


def _recovery_path(final_path: Path) -> Path:
    suffix = final_path.suffix or ".pt"
    return final_path.with_name(f"{final_path.stem}.recovery{suffix}")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--episodes", type=int, default=200)
    parser.add_argument("--duration", type=int, default=300)
    parser.add_argument("--workers", type=int, default=4)
    parser.add_argument("--intersections", type=int, default=20)
    parser.add_argument("--period", default="off_peak")
    parser.add_argument("--seed", type=int, default=42)
    parser.add_argument("--top-k", type=int, default=5)
    parser.add_argument(
        "--max-green-factor",
        type=float,
        default=controller.DEFAULT_MAX_GREEN_FACTOR,
        help="Force another phase after max(60s, factor * nominal green); 0 disables.",
    )
    parser.add_argument(
        "--reward-mode",
        choices=controller.REWARD_MODES,
        default="pressure",
    )
    parser.add_argument(
        "--vehicle-guidance",
        choices=("off", "rule"),
        default="off",
    )
    parser.add_argument(
        "--ppo-epochs",
        type=int,
        default=None,
        help=(
            "Optimizer passes over each on-policy batch. Fresh runs default to "
            "4; resumed runs inherit the checkpoint unless explicitly overridden."
        ),
    )
    parser.add_argument("--save", type=Path, default=DEFAULT_OUTPUT / "final.pt")
    parser.add_argument(
        "--checkpoint-dir",
        type=Path,
        default=DEFAULT_OUTPUT / "checkpoints",
    )
    checkpoint_group = parser.add_mutually_exclusive_group()
    checkpoint_group.add_argument("--resume", type=Path)
    checkpoint_group.add_argument(
        "--warm-start",
        type=Path,
        help=(
            "Fork V17 from a compatible V16 policy/value checkpoint while "
            "resetting optimizers, episode counters, policy generation, and seeds."
        ),
    )
    args = parser.parse_args(argv)

    for name in ("episodes", "duration", "workers", "intersections", "top_k"):
        _positive(parser, name, getattr(args, name))
    if args.ppo_epochs is not None:
        _positive(parser, "ppo-epochs", args.ppo_epochs)
    _nonnegative(parser, "max-green-factor", args.max_green_factor)
    if args.intersections > len(DEFAULT_INTERSECTIONS):
        parser.error(f"intersections must be <= {len(DEFAULT_INTERSECTIONS)}")
    workers = min(args.workers, args.episodes)
    intersections = DEFAULT_INTERSECTIONS[: args.intersections]
    max_meaningful_top_k = 1 if len(intersections) == 1 else len(intersections) - 1
    if args.top_k > max_meaningful_top_k:
        parser.error(
            "learned Top-K must leave at least one collaborator unselected; "
            f"for {len(intersections)} intersections use --top-k <= "
            f"{max_meaningful_top_k}"
        )
    save_path = args.save.expanduser().resolve()
    checkpoint_dir = args.checkpoint_dir.expanduser().resolve()
    resume_path = args.resume.expanduser().resolve() if args.resume else None
    warm_start_path = (
        args.warm_start.expanduser().resolve() if args.warm_start else None
    )
    if resume_path is not None and not resume_path.is_file():
        parser.error(f"resume checkpoint does not exist: {resume_path}")
    if warm_start_path is not None and not warm_start_path.is_file():
        parser.error(f"warm-start checkpoint does not exist: {warm_start_path}")

    try:
        first_seed, last_seed, recorded_start = _training_seed_range(
            args.seed, args.episodes, resume_path
        )
    except (OSError, ValueError) as exc:
        parser.error(str(exc))

    initial_policy_state = None
    initial_value_stats = None
    generation = 0
    ppo_epochs = args.ppo_epochs
    if resume_path is not None:
        checkpoint = load_checkpoint_metadata(resume_path)
        training_config = checkpoint.get("training_config", {})
        if ppo_epochs is None:
            ppo_epochs = int(
                training_config.get("ppo_epochs", controller.PPO_EPOCHS)
            )
        if (
            not bool(training_config.get("value_normalization", False))
            or training_config.get("critic_architecture")
            != "independent_transformer"
            or training_config.get("policy_objective") != POLICY_OBJECTIVE
            or training_config.get("policy_architecture")
            != POLICY_ARCHITECTURE
            or training_config.get("phase_feature_schema")
            != PHASE_FEATURE_SCHEMA
            or training_config.get("collaboration_schema")
            != COLLABORATION_SCHEMA
            or training_config.get("actor_encoder_scope")
            != controller.ACTOR_ENCODER_SCOPE
            or training_config.get("reward_schema") != controller.REWARD_SCHEMA
            or training_config.get("terminal_transition_schema")
            != controller.TERMINAL_TRANSITION_SCHEMA
            or not math.isclose(
                float(training_config.get("spillback_coef", math.nan)),
                controller.LAMBDA_SPILL,
                rel_tol=0.0,
                abs_tol=1e-12,
            )
            or not math.isclose(
                float(training_config.get("actor_lr", math.nan)),
                controller.ACTOR_LR,
                rel_tol=0.0,
                abs_tol=1e-12,
            )
            or not math.isclose(
                float(training_config.get("critic_lr", math.nan)),
                controller.CRITIC_LR,
                rel_tol=0.0,
                abs_tol=1e-12,
            )
            or not math.isclose(
                float(
                    training_config.get("phase_scorer_lr_multiplier", math.nan)
                ),
                controller.PHASE_SCORER_LR_MULTIPLIER,
                rel_tol=0.0,
                abs_tol=1e-12,
            )
            or not math.isclose(
                float(training_config.get("pressure_prior_scale", math.nan)),
                controller.PRESSURE_PRIOR_SCALE,
                rel_tol=0.0,
                abs_tol=1e-12,
            )
            or not math.isclose(
                float(training_config.get("hold_prior_bias", math.nan)),
                controller.HOLD_PRIOR_BIAS,
                rel_tol=0.0,
                abs_tol=1e-12,
            )
            or not math.isclose(
                float(
                    training_config.get("collaborator_policy_coef", math.nan)
                ),
                controller.COLLABORATOR_POLICY_COEF,
                rel_tol=0.0,
                abs_tol=1e-12,
            )
            or not math.isclose(
                float(
                    training_config.get("collaborator_entropy_coef", math.nan)
                ),
                controller.COLLABORATOR_ENTROPY_COEF,
                rel_tol=0.0,
                abs_tol=1e-12,
            )
            or not math.isclose(
                float(
                    training_config.get("collaborator_diagonal_coef", math.nan)
                ),
                controller.COLLABORATOR_DIAGONAL_COEF,
                rel_tol=0.0,
                abs_tol=1e-12,
            )
            or not math.isclose(
                float(
                    training_config.get("collaborator_symmetry_coef", math.nan)
                ),
                controller.COLLABORATOR_SYMMETRY_COEF,
                rel_tol=0.0,
                abs_tol=1e-12,
            )
        ):
            parser.error(
                "resume checkpoint is not a native V17 learned-Top-K run with "
                "the current policy, reward, optimizer, and prior semantics; "
                "use --warm-start for the validated V16 checkpoint"
            )
        if not isinstance(checkpoint.get("value_normalization"), Mapping):
            parser.error(
                "resume checkpoint is missing value-normalization statistics"
            )
        if (
            not isinstance(checkpoint.get("actor_optimizer_state_dict"), Mapping)
            or not isinstance(
                checkpoint.get("critic_optimizer_state_dict"), Mapping
            )
        ):
            parser.error("resume checkpoint is missing separate optimizer states")
        model_config = checkpoint.get("model_config", {})
        if int(model_config.get("num_agents", -1)) != len(intersections):
            parser.error("resume checkpoint intersection count does not match this run")
        if int(model_config.get("top_k", -1)) != min(args.top_k, len(intersections)):
            parser.error("resume checkpoint top-k does not match this run")
        initial_policy_state = checkpoint["model_state_dict"]
        initial_value_stats = checkpoint.get("value_normalization")
        generation = int(
            checkpoint.get(
                "policy_generation",
                int(checkpoint.get("episode", 0)) // controller.ACCUMULATE_EPISODES,
            )
        )
    elif warm_start_path is not None:
        checkpoint = load_checkpoint_metadata(warm_start_path)
        training_config = checkpoint.get("training_config", {})
        expected_v16_semantics = {
            "policy_objective": "phase_action_ratio",
            "policy_architecture": "movement_pressure_local_topology_residual_v4",
            "phase_feature_schema": PHASE_FEATURE_SCHEMA,
            "collaboration_schema": "direct_neighbors_softmax_v1",
            "actor_encoder_scope": "local_then_topology_neighbors_v1",
            "reward_schema": controller.REWARD_SCHEMA,
            "terminal_transition_schema": controller.TERMINAL_TRANSITION_SCHEMA,
        }
        if int(checkpoint.get("format_version", -1)) != 16 or any(
            training_config.get(key) != expected
            for key, expected in expected_v16_semantics.items()
        ):
            parser.error(
                "warm-start requires the validated V16 local-topology Stage-1 "
                "checkpoint; use --resume only for a native V17 checkpoint"
            )
        if not isinstance(checkpoint.get("model_state_dict"), Mapping):
            parser.error("warm-start checkpoint is missing model weights")
        if not isinstance(checkpoint.get("value_normalization"), Mapping):
            parser.error(
                "warm-start checkpoint is missing value-normalization statistics"
            )
        model_config = checkpoint.get("model_config", {})
        if int(model_config.get("num_agents", -1)) != len(intersections):
            parser.error(
                "warm-start checkpoint intersection count does not match this run"
            )
        if int(model_config.get("obs_dim", -1)) != controller.OBS_DIM:
            parser.error("warm-start checkpoint observation size does not match")
        if int(model_config.get("hidden", -1)) != controller.TRANS_HIDDEN:
            parser.error("warm-start checkpoint hidden size does not match")
        checkpoint_tls_order = checkpoint.get("tls_order")
        if checkpoint_tls_order is not None and tuple(
            checkpoint_tls_order
        ) != _canonical_tls_order(intersections):
            parser.error("warm-start checkpoint intersection order does not match")
        initial_policy_state = checkpoint["model_state_dict"]
        initial_value_stats = checkpoint["value_normalization"]
        generation = 0
    if ppo_epochs is None:
        ppo_epochs = controller.PPO_EPOCHS
    if ppo_epochs < 1:
        parser.error("ppo-epochs must be a positive integer")

    os.environ["COSLIGHT_MODE"] = "train"
    os.environ["COSLIGHT_TOP_K"] = str(args.top_k)
    os.environ["COSLIGHT_REWARD_MODE"] = args.reward_mode
    os.environ["COSLIGHT_MAX_GREEN_FACTOR"] = str(args.max_green_factor)
    os.environ.pop("COSLIGHT_PRESSURE_SHIELD_MARGIN", None)
    os.environ.pop("COSLIGHT_RESIDUAL_MIN_BEST_PRESSURE", None)
    os.environ.pop("COSLIGHT_SWITCH_LOGIT_MARGIN", None)
    os.environ["COSLIGHT_CLOUD_MODE"] = "off"
    os.environ.pop("COSLIGHT_CLOUD_TOPOLOGY", None)
    os.environ["COSLIGHT_VEHICLE_GUIDANCE"] = args.vehicle_guidance
    os.environ["COSLIGHT_PPO_EPOCHS"] = str(ppo_epochs)
    os.environ["COSLIGHT_CHECKPOINT_DIR"] = str(checkpoint_dir)
    os.environ["COSLIGHT_TRAIN_SEED_START"] = str(recorded_start)
    os.environ["COSLIGHT_TRAIN_SEED_END"] = str(first_seed - 1)
    if resume_path is None:
        os.environ.pop("COSLIGHT_RESUME_PATH", None)
    else:
        os.environ["COSLIGHT_RESUME_PATH"] = str(resume_path)
    random.seed(args.seed)
    np.random.seed(args.seed)
    torch.manual_seed(args.seed)
    checkpoint_dir.mkdir(parents=True, exist_ok=True)
    save_path.parent.mkdir(parents=True, exist_ok=True)

    logger.info(
        "CoSLight synchronous training: episodes=%d duration=%ds workers=%d "
        "tls=%d seeds=%d..%d top_k=%d reward=%s max_green_factor=%.2f "
        "vehicles=%s ppo_epochs=%d warm_start=%s",
        args.episodes,
        args.duration,
        workers,
        len(intersections),
        first_seed,
        last_seed,
        args.top_k,
        args.reward_mode,
        args.max_green_factor,
        args.vehicle_guidance,
        ppo_epochs,
        str(warm_start_path) if warm_start_path is not None else "none",
    )

    policy_state = initial_policy_state
    value_stats = initial_value_stats
    learner_initialized = False
    checkpoint_bucket = 0
    consumed_episodes = 0
    total_started = time.monotonic()
    try:
        for batch_number, seeds in enumerate(
            _seed_batches(
                first_seed=first_seed,
                episodes=args.episodes,
                workers=workers,
            ),
            start=1,
        ):
            batch_started = time.monotonic()
            results = _run_policy_batch(
                seeds=seeds,
                intersections=intersections,
                duration=args.duration,
                period=args.period,
                top_k=args.top_k,
                max_green_factor=args.max_green_factor,
                vehicle_guidance=args.vehicle_guidance,
                reward_mode=args.reward_mode,
                policy_seed=args.seed,
                policy_generation=generation,
                policy_state=policy_state,
                value_stats=value_stats,
            )
            rollouts = [result["rollout"] for result in results]
            signatures = {
                _metadata_signature(rollout["metadata"]) for rollout in rollouts
            }
            if len(signatures) != 1:
                raise RuntimeError("Parallel workers returned incompatible SUMO metadata")
            worker_policy = rollouts[0]["policy_state"]
            if any(
                not _policy_states_equal(worker_policy, rollout["policy_state"])
                for rollout in rollouts[1:]
            ):
                raise RuntimeError("Parallel workers did not share one policy snapshot")
            worker_value_stats = rollouts[0].get("value_normalization", {})
            if any(
                not _value_stats_equal(
                    worker_value_stats,
                    rollout.get("value_normalization", {}),
                )
                for rollout in rollouts[1:]
            ):
                raise RuntimeError(
                    "Parallel workers did not share one value-normalization snapshot"
                )

            if not learner_initialized:
                torch.manual_seed(args.seed)
                controller.initialize(rollouts[0]["metadata"])
                if policy_state is None or warm_start_path is not None:
                    controller.install_parallel_initial_policy(
                        worker_policy,
                        worker_value_stats if warm_start_path is not None else None,
                    )
                learner_initialized = True
                checkpoint_bucket = (
                    controller.training_episode_count() // CHECKPOINT_INTERVAL
                )
                if controller.policy_generation() != generation:
                    raise RuntimeError(
                        "Learner and workers disagree on initial policy generation"
                    )

            central_state = controller.export_policy_state()
            if any(
                not _policy_states_equal(central_state, rollout["policy_state"])
                for rollout in rollouts
            ):
                raise RuntimeError(
                    "A worker collected with a different central policy snapshot"
                )
            if not _value_stats_equal(
                controller.export_value_stats(), worker_value_stats
            ):
                raise RuntimeError(
                    "Workers collected with stale value-normalization statistics"
                )

            update = controller.ingest_parallel_rollouts(rollouts, update=True)
            consumed_episodes += len(rollouts)
            os.environ["COSLIGHT_TRAIN_SEED_END"] = str(seeds[-1])
            policy_state = controller.export_policy_state()
            value_stats = controller.export_value_stats()
            generation = controller.policy_generation()
            metrics = [result["metrics"] for result in results]
            signal_diagnostics = _aggregate_signal_diagnostics(rollouts)
            episode_count = controller.training_episode_count()
            logger.info(
                "BATCH%d OK generation=%d seeds=%s episodes=%d joint_steps=%d "
                "wall=%.1fs arrived=%.1f waiting=%.1f "
                "switch_applied=%d/%d rate=%.3f delay=%.1fs unresolved=%d "
                "forced_max_green=%d "
                "multi_phase_max_green=%.1fs multi_phase_dominance=%.3f/%.3f",
                batch_number,
                generation,
                list(seeds),
                update["episodes"],
                update["samples"],
                time.monotonic() - batch_started,
                float(np.mean([item["arrived"] for item in metrics])),
                float(np.mean([item["waiting"] for item in metrics])),
                signal_diagnostics["observed_changes"],
                signal_diagnostics["change_requests"],
                signal_diagnostics["change_execution_rate"],
                signal_diagnostics["mean_change_delay_s"],
                signal_diagnostics["unresolved_changes"],
                signal_diagnostics["max_green_forced_commands"],
                signal_diagnostics["multi_phase_max_observed_green_s"],
                signal_diagnostics["multi_phase_mean_phase_dominance"],
                signal_diagnostics["multi_phase_max_phase_dominance"],
            )

            current_bucket = episode_count // CHECKPOINT_INTERVAL
            if current_bucket > checkpoint_bucket:
                controller.save_checkpoint(
                    checkpoint_dir
                    / f"coslight_parallel_ep{episode_count}.pt"
                )
                checkpoint_bucket = current_bucket
    except BaseException:
        logger.exception("Parallel training stopped; the failed policy batch was discarded")
        if learner_initialized and consumed_episodes:
            recovery = controller.save_checkpoint(_recovery_path(save_path))
            logger.info("Recovery checkpoint saved: %s", recovery)
        return 1

    saved = controller.save_checkpoint(save_path)
    logger.info(
        "Parallel training complete: episodes=%d wall=%.1fs model=%s",
        args.episodes,
        time.monotonic() - total_started,
        saved,
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
