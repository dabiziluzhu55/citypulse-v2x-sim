"""Synchronous MAPPO training: SUMO workers collect, one learner updates."""

from __future__ import annotations

import argparse
from dataclasses import asdict
import gc
import json
import logging
import multiprocessing
import os
from pathlib import Path
import random
import sys
import time
from concurrent.futures import ProcessPoolExecutor, as_completed
from typing import Iterable, Mapping, Sequence

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
from algorithms.mappo.checkpoint import (  # noqa: E402
    CheckpointMetadata,
    load_checkpoint,
    policy_digest,
    read_checkpoint_metadata,
    save_checkpoint,
)
from algorithms.mappo.config import (  # noqa: E402
    COOPERATIVE_MODEL_VERSION,
    MODEL_ACTOR_VARIANTS,
    MAPPOConfig,
    REWARD_SCOPE_SHARED_TEAM,
    algorithm_label,
    configuration_signature,
)
from algorithms.mappo.features import (  # noqa: E402
    IPPO_V8_LOCAL_OBSERVATION_SCHEMA,
)
from algorithms.mappo.diagnostics import (  # noqa: E402
    merge_reward_diagnostics,
    reward_metric_alignment,
)
from algorithms.mappo.models import MAPPOPolicy
from traffic_control.ippo.identity import IDENTITY_SLOT_IDS  # noqa: E402
from algorithms.mappo.parallel_train import (  # noqa: E402
    CentralUpdateCoordinator,
    WorkerRollout,
    build_ppo_batch,
)
from algorithms.mappo.trainer import MAPPOTrainer  # noqa: E402
from simulation.sumo.session import SimulationConfig, SimulationManager  # noqa: E402


DEFAULT_INTERSECTION_IDS = tuple(f"demo_{index}" for index in range(1, 21))
REWARD_DEFINITION = "v5a:-0.60D+0.20F_safe-0.15B+0.05H;clip[-3,1]"

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(processName)s:%(name)s] %(levelname)s: %(message)s",
)
logger = logging.getLogger("mappo.train")


def _seed_batches(
    *, base_seed: int, episodes: int, workers: int
) -> Iterable[tuple[int, ...]]:
    for offset in range(0, episodes, workers):
        yield tuple(
            range(base_seed + offset, base_seed + min(offset + workers, episodes))
        )


def _period_batch(
    seeds: Sequence[int], *, periods: Sequence[str], training_seed_start: int
) -> tuple[str, ...]:
    if not periods:
        raise ValueError("at least one training period is required")
    return tuple(
        str(periods[(int(seed) - int(training_seed_start)) % len(periods)])
        for seed in seeds
    )


def _training_seed_range(
    *,
    base_seed: int,
    episodes: int,
    previous_start: int | None = None,
    previous_end: int | None = None,
) -> tuple[int, int]:
    first = int(base_seed)
    count = int(episodes)
    if count <= 0:
        raise ValueError("episodes must be positive")
    recorded_start = first
    if previous_start is not None or previous_end is not None:
        if previous_start is None or previous_end is None:
            raise ValueError("resume seed metadata is incomplete")
        required = int(previous_end) + 1
        if first != required:
            raise ValueError(f"resume base seed must start at {required}")
        recorded_start = int(previous_start)
    return recorded_start, first + count - 1


def _should_save_periodic_checkpoint(
    *, completed_episodes: int, last_saved_episode: int, checkpoint_every: int
) -> bool:
    interval = int(checkpoint_every)
    if interval <= 0:
        raise ValueError("checkpoint interval must be positive")
    return int(completed_episodes) - int(last_saved_episode) >= interval


def _periodic_checkpoint_path(
    final_path: str | os.PathLike[str], *, episode: int
) -> Path:
    target = Path(final_path)
    suffix = target.suffix or ".pt"
    stem = target.stem if target.suffix else target.name
    return target.parent / "checkpoints" / f"{stem}_ep{int(episode):06d}{suffix}"


def _write_training_diagnostics(
    path: Path, payload: Mapping[str, object]
) -> None:
    """Atomically persist evidence from complete on-policy updates only."""

    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(f"{path.name}.tmp-{os.getpid()}")
    try:
        with temporary.open("w", encoding="utf-8") as handle:
            json.dump(
                payload,
                handle,
                ensure_ascii=False,
                indent=2,
                allow_nan=False,
            )
            handle.write("\n")
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary, path)
    finally:
        temporary.unlink(missing_ok=True)


def _checkpoint_metadata(
    config: MAPPOConfig,
    *,
    episode: int,
    policy_generation: int,
    actor_init_seed: int,
    critic_init_seed: int,
    training_seed_start: int,
    training_seed_end: int,
    training_periods: tuple[str, ...],
    training_workers: int,
    episode_duration_s: float,
) -> CheckpointMetadata:
    return CheckpointMetadata.from_config(
        config,
        episode=episode,
        policy_generation=policy_generation,
        actor_init_seed=actor_init_seed,
        critic_init_seed=critic_init_seed,
        training_seed_start=training_seed_start,
        training_seed_end=training_seed_end,
        training_periods=training_periods,
        local_observation_schema=IPPO_V8_LOCAL_OBSERVATION_SCHEMA,
        reward_definition=REWARD_DEFINITION,
        training_workers=training_workers,
        episode_duration_s=episode_duration_s,
    )


def _failed_worker_rollout(
    *,
    seed: int,
    error: str,
    policy_generation: int,
    expected_policy_digest: str,
    config: MAPPOConfig,
) -> WorkerRollout:
    return WorkerRollout(
        seed=int(seed),
        status="error",
        policy_generation=int(policy_generation),
        policy_digest=str(expected_policy_digest),
        config_signature=configuration_signature(config),
        local_observation_schema=IPPO_V8_LOCAL_OBSERVATION_SCHEMA,
        centralized_state_schema=config.centralized_state_schema,
        transitions=(),
        pending_count=0,
        invalid_reason=None,
        error=str(error),
        reward_scope=config.reward_scope,
        team_reward_schema=config.team_reward_schema,
        joint_step_schema=config.joint_step_schema,
    )


def _metrics_dict(snapshot: object) -> dict[str, float | int]:
    metrics = snapshot.metrics
    return {
        "departed": int(metrics.departed_vehicles),
        "arrived": int(metrics.arrived_vehicles),
        "waiting": float(metrics.total_waiting_time),
        "hard_braking": int(metrics.hard_braking_events),
        "fuel_mg": float(metrics.fuel_consumed_mg),
    }


def _stop_timed_out_session(
    manager: SimulationManager, session_id: str | None
) -> None:
    if session_id is None:
        return
    try:
        manager.stop(session_id)
        manager.wait(session_id, timeout=60)
    except Exception:
        logger.exception("SUMO worker timeout cleanup failed")


def _run_sumo_worker(request: Mapping[str, object]) -> dict[str, object]:
    """Collect exactly one episode without owning an optimizer."""

    seed = int(request["seed"])
    config = request["config"]
    if not isinstance(config, MAPPOConfig):
        raise TypeError("worker config must be MAPPOConfig")
    generation = int(request["policy_generation"])
    expected_digest = str(request["policy_digest"])
    os.environ["OMP_NUM_THREADS"] = "1"
    os.environ["MKL_NUM_THREADS"] = "1"
    torch.set_num_threads(1)
    random.seed(seed)
    np.random.seed(seed % (2**32 - 1))
    torch.manual_seed(seed)
    entrypoint.prepare_collector(
        policy_state=request["policy_state"],
        config=config,
        policy_generation=generation,
        rollout_seed=seed,
        actor_init_seed=int(request["actor_init_seed"]),
        critic_init_seed=int(request["critic_init_seed"]),
        expected_duration_s=float(request["duration"]),
        mode="collect",
        record_evaluation=False,
    )

    manager = SimulationManager()
    session_id: str | None = None
    started_at = time.time()
    try:
        session_config = SimulationConfig(
            intersection_ids=list(config.intersection_ids),
            period=str(request["period"]),
            duration_seconds=int(request["duration"]),
            control_mode="algorithm",
            algorithm_transport="local",
            algorithm_module="algorithms.mappo",
            decision_interval=5.0,
            minimum_green=5.0,
            seed=seed,
            step_length=float(request["step_length"]),
        )
        session_id = manager.start(session_config)
        snapshot = manager.wait(
            session_id,
            timeout=max(600, int(request["duration"]) * 3),
        )
        if snapshot is None:
            raise RuntimeError("SUMO returned no terminal snapshot")
        if snapshot.state != "COMPLETED" or snapshot.metrics is None:
            raise RuntimeError(
                f"SUMO state={snapshot.state} error={snapshot.error or ''}".strip()
            )
        rollout = entrypoint.pop_collected_rollout()
        if rollout is None:
            raise RuntimeError("MAPPO controller returned no complete rollout")
        if rollout.policy_digest != expected_digest:
            raise RuntimeError("worker policy digest changed during sampling")
        return {
            "status": "complete",
            "seed": seed,
            "elapsed": time.time() - started_at,
            "metrics": _metrics_dict(snapshot),
            "rollout": rollout,
        }
    except TimeoutError as error:
        _stop_timed_out_session(manager, session_id)
        detail = f"TimeoutError: {error}"
    except BaseException as error:
        detail = f"{type(error).__name__}: {error}"
    finally:
        del manager
        gc.collect()
    return {
        "status": "failed",
        "seed": seed,
        "elapsed": time.time() - started_at,
        "metrics": None,
        "rollout": _failed_worker_rollout(
            seed=seed,
            error=detail,
            policy_generation=generation,
            expected_policy_digest=expected_digest,
            config=config,
        ),
    }


def _run_policy_batch(
    *,
    seeds: Sequence[int],
    periods: Sequence[str],
    config: MAPPOConfig,
    duration: int,
    step_length: float,
    policy: MAPPOPolicy,
    policy_generation: int,
    actor_init_seed: int,
    critic_init_seed: int,
) -> list[dict[str, object]]:
    if len(seeds) != len(periods):
        raise ValueError("periods must contain one value per worker")
    state = {
        name: tensor.detach().cpu().clone()
        for name, tensor in policy.state_dict().items()
    }
    digest = policy_digest(policy)
    base_request = {
        "config": config,
        "duration": int(duration),
        "step_length": float(step_length),
        "policy_state": state,
        "policy_generation": int(policy_generation),
        "policy_digest": digest,
        "actor_init_seed": int(actor_init_seed),
        "critic_init_seed": int(critic_init_seed),
    }
    context = multiprocessing.get_context("spawn")
    results: list[dict[str, object]] = []
    with ProcessPoolExecutor(
        max_workers=len(seeds), mp_context=context
    ) as executor:
        futures = {
            executor.submit(
                _run_sumo_worker,
                {**base_request, "seed": int(seed), "period": str(period)},
            ): int(seed)
            for seed, period in zip(seeds, periods)
        }
        for future in as_completed(futures):
            seed = futures[future]
            try:
                results.append(future.result())
            except BaseException as error:
                detail = f"worker process {type(error).__name__}: {error}"
                results.append(
                    {
                        "status": "failed",
                        "seed": seed,
                        "elapsed": 0.0,
                        "metrics": None,
                        "rollout": _failed_worker_rollout(
                            seed=seed,
                            error=detail,
                            policy_generation=policy_generation,
                            expected_policy_digest=digest,
                            config=config,
                        ),
                    }
                )
    return sorted(results, key=lambda item: int(item["seed"]))


def _positive(parser: argparse.ArgumentParser, name: str, value: float) -> None:
    if value <= 0:
        parser.error(f"{name} must be positive")


def _training_arg_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--model-version",
        choices=(COOPERATIVE_MODEL_VERSION,),
        default=COOPERATIVE_MODEL_VERSION,
    )
    parser.add_argument("--critic-scope", choices=("local", "global"), default="global")
    parser.add_argument("--init", choices=("random",), default="random")
    from algorithms.presets import SCENARIO_PRESET_REGISTRY

    scope = parser.add_mutually_exclusive_group()
    scope.add_argument(
        "--scenario-preset",
        choices=sorted(SCENARIO_PRESET_REGISTRY),
        default=None,
        help="Typical scenario preset (xiongan_20 / east_dense / west_dense); "
        "mutually exclusive with --intersections",
    )
    scope.add_argument("--intersections", type=int, default=None)
    parser.add_argument("--episodes", type=int, default=200)
    parser.add_argument("--workers", type=int, default=4)
    parser.add_argument("--duration", type=int, default=300)
    parser.add_argument(
        "--step-length", type=float, default=0.1, help="SUMO simulation step (s)"
    )
    parser.add_argument("--base-seed", type=int, required=True)
    parser.add_argument("--period", default="off_peak")
    parser.add_argument("--periods", nargs="+", default=None)
    parser.add_argument("--actor-init-seed", type=int, default=42)
    parser.add_argument("--critic-init-seed", type=int, default=43)
    parser.add_argument("--checkpoint-every", type=int, default=20)
    parser.add_argument("--save", type=Path, default=None)
    parser.add_argument("--resume", type=Path, default=None)
    parser.add_argument("--diagnostics-output", type=Path, default=None)
    return parser


def _parse_training_args(
    argv: list[str] | None = None,
    *,
    parser: argparse.ArgumentParser | None = None,
) -> argparse.Namespace:
    current_parser = parser or _training_arg_parser()
    return current_parser.parse_args(argv)


def _build_training_config(
    intersection_ids: Sequence[str],
    *,
    critic_scope: str = "global",
    model_version: str = COOPERATIVE_MODEL_VERSION,
) -> MAPPOConfig:
    actor_variant = MODEL_ACTOR_VARIANTS[model_version]
    return MAPPOConfig(
        tuple(str(value) for value in intersection_ids),
        critic_scope=critic_scope,
        model_version=model_version,
        actor_variant=actor_variant,
        reward_scope=REWARD_SCOPE_SHARED_TEAM,
        critic_target_scope="team_return",
    )


def _training_run_label(config: MAPPOConfig) -> str:
    return algorithm_label(config)


def _default_checkpoint_path(
    config: MAPPOConfig, *, intersections: int
) -> Path:
    return (
        REPO_ROOT
        / "algorithms"
        / "mappo"
        / "runs"
        / f"{_training_run_label(config)}_{int(intersections)}tls.pt"
    )


def main(argv: list[str] | None = None) -> int:
    parser = _training_arg_parser()
    args = _parse_training_args(argv, parser=parser)

    for name in ("episodes", "workers", "duration", "checkpoint_every", "step_length"):
        _positive(parser, name, getattr(args, name))
    periods = tuple(str(value) for value in (args.periods or (args.period,)))
    if any(not value.strip() for value in periods):
        parser.error("training periods must be non-empty")
    if args.scenario_preset is not None:
        from algorithms.presets import SCENARIO_PRESET_REGISTRY

        intersections = tuple(
            SCENARIO_PRESET_REGISTRY[args.scenario_preset].intersection_ids
        )
    else:
        _positive(parser, "intersections", args.intersections or 1)
        if (args.intersections or len(DEFAULT_INTERSECTION_IDS)) > len(
            DEFAULT_INTERSECTION_IDS
        ):
            parser.error(
                f"intersections must be <= {len(DEFAULT_INTERSECTION_IDS)}"
            )
        intersections = DEFAULT_INTERSECTION_IDS[
            : (args.intersections or len(DEFAULT_INTERSECTION_IDS))
        ]
    workers = min(args.workers, args.episodes)
    try:
        config = _build_training_config(
            intersections,
            critic_scope=args.critic_scope,
            model_version=args.model_version,
        )
    except (KeyError, ValueError) as error:
        parser.error(str(error))
    actor_variant = config.actor_variant
    run_label = _training_run_label(config)
    save_path = args.save or _default_checkpoint_path(
        config, intersections=len(intersections)
    )
    diagnostics_path = (
        None
        if args.diagnostics_output is None
        else args.diagnostics_output.expanduser().resolve()
    )
    random.seed(args.actor_init_seed)
    np.random.seed(args.actor_init_seed % (2**32 - 1))
    torch.manual_seed(args.actor_init_seed)
    policy = MAPPOPolicy(
        obs_dim=config.obs_dim,
        num_agents=len(IDENTITY_SLOT_IDS),
        critic_scope=config.critic_scope,
        actor_init_seed=args.actor_init_seed,
        critic_init_seed=args.critic_init_seed,
        hidden_dim=config.hidden_dim,
        phase_feature_dim=config.phase_feature_dim,
        model_version=config.model_version,
        actor_variant=config.actor_variant,
        identity_offset=config.identity_offset,
    )
    trainer = MAPPOTrainer(policy, config)
    completed_episodes = 0
    policy_generation = 0
    previous_start: int | None = None
    previous_end: int | None = None
    resume_path = args.resume.expanduser().resolve() if args.resume else None
    resume_metadata: CheckpointMetadata | None = None
    if resume_path is not None:
        if not resume_path.is_file():
            parser.error(f"resume checkpoint does not exist: {resume_path}")
        resume_metadata = read_checkpoint_metadata(resume_path)
        if resume_metadata.actor_init_seed != args.actor_init_seed:
            parser.error("resume checkpoint actor init seed does not match")
        if resume_metadata.critic_init_seed != args.critic_init_seed:
            parser.error("resume checkpoint critic init seed does not match")
        if resume_metadata.training_periods != periods:
            parser.error("resume checkpoint training periods do not match")
        if (
            resume_metadata.training_workers is not None
            and resume_metadata.training_workers != workers
        ):
            parser.error("resume checkpoint training worker count does not match")
        if (
            resume_metadata.episode_duration_s is not None
            and resume_metadata.episode_duration_s != float(args.duration)
        ):
            parser.error("resume checkpoint episode duration does not match")
        if (
            resume_metadata.training_workers is None
            or resume_metadata.episode_duration_s is None
        ):
            logger.warning(
                "legacy checkpoint lacks worker/duration metadata; "
                "current resume values will be recorded on the next save"
            )
        completed_episodes = resume_metadata.episode
        policy_generation = resume_metadata.policy_generation
        previous_start = resume_metadata.training_seed_start
        previous_end = resume_metadata.training_seed_end

    try:
        training_seed_start, training_seed_end = _training_seed_range(
            base_seed=args.base_seed,
            episodes=args.episodes,
            previous_start=previous_start,
            previous_end=previous_end,
        )
    except ValueError as error:
        parser.error(str(error))

    if resume_path is not None and resume_metadata is not None:
        load_checkpoint(
            resume_path,
            policy,
            trainer,
            expected_config=config,
            expected_local_observation_schema=IPPO_V8_LOCAL_OBSERVATION_SCHEMA,
            expected_reward_definition=REWARD_DEFINITION,
            expected_metadata=resume_metadata,
        )

    coordinator = CentralUpdateCoordinator(
        trainer=trainer,
        policy_generation=policy_generation,
        policy_digest=policy_digest(policy),
        config_signature=configuration_signature(config),
        local_observation_schema=IPPO_V8_LOCAL_OBSERVATION_SCHEMA,
        centralized_state_schema=config.centralized_state_schema,
        reward_scope=config.reward_scope,
        team_reward_schema=config.team_reward_schema,
        joint_step_schema=config.joint_step_schema,
        digest_provider=lambda: policy_digest(policy),
        batch_builder=lambda rollouts: build_ppo_batch(
            rollouts, config=config
        ),
    )
    diagnostic_batches: list[dict[str, object]] = []
    diagnostic_payload: dict[str, object] = {
        "status": "running",
        "schema": "mappo_training_diagnostics_v1",
        "frozen_config": {
            "config": asdict(config),
            "config_signature": configuration_signature(config),
            "algorithm_variant": run_label,
            "model_version": config.model_version,
            "actor_variant": config.actor_variant,
            "actor_init_seed": args.actor_init_seed,
            "critic_init_seed": args.critic_init_seed,
            "training_seed_start": training_seed_start,
            "training_seed_end": training_seed_end,
            "periods": list(periods),
            "workers": workers,
            "episode_duration_s": args.duration,
        },
        "batches": diagnostic_batches,
    }
    if diagnostics_path is not None:
        _write_training_diagnostics(diagnostics_path, diagnostic_payload)
    last_periodic_checkpoint_episode = completed_episodes
    total_started = time.time()
    logger.info(
        "%s start actor=%s scope=%s init=random episodes=%d duration=%ds "
        "workers=%d tls=%d seeds=%d..%d periods=%s generation=%d",
        run_label,
        config.actor_variant,
        config.critic_scope,
        args.episodes,
        args.duration,
        workers,
        len(intersections),
        args.base_seed,
        training_seed_end,
        periods,
        policy_generation,
    )
    try:
        for batch_number, seeds in enumerate(
            _seed_batches(
                base_seed=args.base_seed,
                episodes=args.episodes,
                workers=workers,
            ),
            start=1,
        ):
            batch_started = time.time()
            results = _run_policy_batch(
                seeds=seeds,
                periods=_period_batch(
                    seeds,
                    periods=periods,
                    training_seed_start=training_seed_start,
                ),
                config=config,
                duration=args.duration,
                step_length=args.step_length,
                policy=policy,
                policy_generation=coordinator.policy_generation,
                actor_init_seed=args.actor_init_seed,
                critic_init_seed=args.critic_init_seed,
                    )
            rollouts = [result["rollout"] for result in results]
            diagnostics = coordinator.update_from_workers(
                rollouts, expected_seeds=seeds
            )
            completed_episodes += len(seeds)
            samples = sum(len(rollout.transitions) for rollout in rollouts)
            dropped_pending = sum(rollout.dropped_pending for rollout in rollouts)
            elapsed_values = [float(result["elapsed"]) for result in results]
            metrics = [result["metrics"] for result in results]
            reward_summaries = [
                rollout.reward_diagnostics
                for rollout in rollouts
                if isinstance(rollout.reward_diagnostics, Mapping)
            ]
            reward_summary = merge_reward_diagnostics(reward_summaries)
            reward_alignment = reward_metric_alignment(
                [
                    {
                        "reward_diagnostics": rollout.reward_diagnostics,
                        "metrics": result["metrics"],
                    }
                    for rollout, result in zip(rollouts, results, strict=True)
                ]
            )
            action_fractions = tuple(
                round(diagnostics[f"action_{index}_fraction"], 6)
                for index in range(config.max_action_dim)
            )
            logger.info(
                "BATCH%d OK seeds=%s episodes=%d samples=%d generation=%d "
                "wall=%.1fs worker_mean=%.1fs actor_loss=%.6f "
                "critic_loss=%.6f entropy=%.6f kl=%.6f clip=%.6f "
                "actor_grad=%.6f critic_grad=%.6f advantage_abs=%.6f "
                "return_mean=%.6f return_std=%.6f value_mean=%.6f "
                "value_std=%.6f explained_variance=%.6f "
                "ev_pre=%.6f ev_post=%.6f ev_gain=%.6f "
                "ev_agent_pre=%.6f ev_agent_post=%.6f "
                "joint_states=%d critic_inputs=%d joint_reuse=%.2f "
                "value_replay_error=%.9f valid_actions=%.3f "
                "unselected_valid=%.6f action_fractions=%s "
                "dropped_pending=%d arrived=%.1f",
                batch_number,
                list(seeds),
                completed_episodes,
                samples,
                diagnostics["policy_generation"],
                time.time() - batch_started,
                float(np.mean(elapsed_values)),
                diagnostics["actor_loss"],
                diagnostics["critic_loss"],
                diagnostics["entropy"],
                diagnostics["approx_kl"],
                diagnostics["clip_fraction"],
                diagnostics["actor_grad_norm"],
                diagnostics["critic_grad_norm"],
                diagnostics["advantage_abs_mean"],
                diagnostics["return_mean"],
                diagnostics["return_std"],
                diagnostics["value_mean"],
                diagnostics["value_std"],
                diagnostics["explained_variance"],
                diagnostics["explained_variance_pre"],
                diagnostics["explained_variance_post"],
                diagnostics["explained_variance_gain"],
                diagnostics["explained_variance_pre_agent_mean"],
                diagnostics["explained_variance_post_agent_mean"],
                int(diagnostics["unique_joint_state_count"]),
                int(diagnostics["unique_critic_input_count"]),
                diagnostics["joint_state_reuse_factor"],
                diagnostics["rollout_value_max_abs_error"],
                diagnostics["valid_action_count_mean"],
                diagnostics["unselected_valid_action_fraction"],
                action_fractions,
                dropped_pending,
                float(np.mean([item["arrived"] for item in metrics])),
            )
            correlations = reward_alignment["correlations"].get(
                "reward_mean", {}
            )
            logger.info(
                "BATCH%d reward mean=%.6f raw_mean=%.6f clip=%.6f "
                "D=%.6f F_safe=%.6f B=%.6f H=%.6f "
                "corr_arrived=%s corr_waiting=%s",
                batch_number,
                reward_summary["reward"]["mean"],
                reward_summary["raw_reward"]["mean"],
                reward_summary["clipped_fraction"],
                reward_summary["components"]["D"]["mean"],
                reward_summary["components"]["F_safe"]["mean"],
                reward_summary["components"]["B"]["mean"],
                reward_summary["components"]["H"]["mean"],
                (
                    "N/A"
                    if correlations.get("arrived") is None
                    else f"{correlations['arrived']:.6f}"
                ),
                (
                    "N/A"
                    if correlations.get("waiting") is None
                    else f"{correlations['waiting']:.6f}"
                ),
            )
            scalar_diagnostics = {
                name: float(diagnostics[name])
                for name in (
                    "actor_loss",
                    "critic_loss",
                    "entropy",
                    "approx_kl",
                    "clip_fraction",
                    "actor_grad_norm",
                    "critic_grad_norm",
                    "advantage_abs_mean",
                    "return_mean",
                    "return_std",
                    "value_mean",
                    "value_std",
                    "explained_variance_pre",
                    "explained_variance_post",
                    "explained_variance_gain",
                )
            }
            batch_record: dict[str, object] = {
                "batch_number": batch_number,
                "seeds": [int(seed) for seed in seeds],
                "sampled_policy_generation": (
                    int(diagnostics["policy_generation"]) - 1
                ),
                "sampled_policy_digest": rollouts[0].policy_digest,
                "policy_generation": int(diagnostics["policy_generation"]),
                "policy_digest": coordinator.policy_digest,
                "worker_success_count": sum(
                    result["status"] == "complete" for result in results
                ),
                "worker_expected_count": len(seeds),
                "sample_count": samples,
                "dropped_pending_count": dropped_pending,
                "unique_joint_state_count": int(
                    diagnostics["unique_joint_state_count"]
                ),
                "unique_critic_input_count": int(
                    diagnostics["unique_critic_input_count"]
                ),
                **scalar_diagnostics,
                "finite": bool(
                    all(np.isfinite(value) for value in scalar_diagnostics.values())
                ),
            }
            diagnostic_batches.append(batch_record)
            if diagnostics_path is not None:
                _write_training_diagnostics(
                    diagnostics_path, diagnostic_payload
                )
            if _should_save_periodic_checkpoint(
                completed_episodes=completed_episodes,
                last_saved_episode=last_periodic_checkpoint_episode,
                checkpoint_every=args.checkpoint_every,
            ):
                periodic_path = _periodic_checkpoint_path(
                    save_path, episode=completed_episodes
                )
                periodic_metadata = _checkpoint_metadata(
                    config,
                    episode=completed_episodes,
                    policy_generation=coordinator.policy_generation,
                    actor_init_seed=args.actor_init_seed,
                    critic_init_seed=args.critic_init_seed,
                                training_seed_start=training_seed_start,
                    training_seed_end=int(seeds[-1]),
                    training_periods=periods,
                    training_workers=workers,
                    episode_duration_s=args.duration,
                )
                save_checkpoint(
                    periodic_path, policy, trainer, periodic_metadata
                )
                last_periodic_checkpoint_episode = completed_episodes
                logger.info("periodic checkpoint saved: %s", periodic_path)
    except BaseException as error:
        logger.exception(
            "%s training aborted: current policy batch was not saved",
            run_label,
        )
        diagnostic_payload["status"] = "failed"
        diagnostic_payload["failure"] = (
            f"{type(error).__name__}: {error}"
        )
        if diagnostics_path is not None:
            try:
                _write_training_diagnostics(
                    diagnostics_path, diagnostic_payload
                )
            except BaseException:
                logger.exception("failed to persist aborted-run diagnostics")
        return 1

    metadata = _checkpoint_metadata(
        config,
        episode=completed_episodes,
        policy_generation=coordinator.policy_generation,
        actor_init_seed=args.actor_init_seed,
        critic_init_seed=args.critic_init_seed,
        training_seed_start=training_seed_start,
        training_seed_end=training_seed_end,
        training_periods=periods,
        training_workers=workers,
        episode_duration_s=args.duration,
    )
    save_checkpoint(save_path, policy, trainer, metadata)
    diagnostic_payload["status"] = "complete"
    diagnostic_payload["completed_episodes"] = completed_episodes
    diagnostic_payload["final_policy_generation"] = (
        coordinator.policy_generation
    )
    diagnostic_payload["final_policy_digest"] = policy_digest(policy)
    diagnostic_payload["final_checkpoint"] = str(save_path)
    if diagnostics_path is not None:
        _write_training_diagnostics(diagnostics_path, diagnostic_payload)
    logger.info(
        "%s complete episodes=%d generation=%d wall=%.1fs model=%s sha256=%s",
        run_label,
        completed_episodes,
        coordinator.policy_generation,
        time.time() - total_started,
        save_path,
        policy_digest(policy),
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
