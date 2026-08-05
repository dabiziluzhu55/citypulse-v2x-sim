"""Synchronous vectorized IPPO training with one SUMO process per rollout."""

from __future__ import annotations

import argparse
import gc
import logging
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

from algorithms.ippo import controller  # noqa: E402
from algorithms.ippo.controller import (  # noqa: E402
    CHECKPOINT_INTERVAL,
    DEFAULT_ACTION_INTERVAL,
    DEFAULT_INTERSECTION_IDS,
    MODEL_VERSION,
    PHASE_FEATURE_SCHEMA,
    load_checkpoint_metadata,
)
from simulation.sumo.session import SimulationConfig, SimulationManager  # noqa: E402


logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(processName)s:%(name)s] %(levelname)s: %(message)s",
)
logger = logging.getLogger("ippo.parallel")


def _seed_batches(
    *, first_seed: int, episodes: int, workers: int
) -> Iterable[tuple[int, ...]]:
    for offset in range(0, episodes, workers):
        yield tuple(range(first_seed + offset, first_seed + min(offset + workers, episodes)))


def _period_batch(
    seeds: Sequence[int], *, periods: Sequence[str], training_seed_start: int
) -> tuple[str, ...]:
    if not periods:
        raise ValueError("At least one training period is required.")
    return tuple(
        str(periods[(int(seed) - training_seed_start) % len(periods)])
        for seed in seeds
    )


def _validated_worker_results(results: Sequence[Mapping[str, object]]) -> list[dict]:
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
            raise RuntimeError(f"Parallel worker seed={result['seed']} returned no samples")
    return sorted(completed, key=lambda item: int(item["seed"]))


def _stop_timed_out_session(manager: SimulationManager, session_id: str | None) -> None:
    if session_id is None:
        return
    try:
        manager.stop(session_id)
        manager.wait(session_id, timeout=60)
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


def _run_sumo_worker(request: Mapping[str, object]) -> dict:
    """Collect one episode in an isolated process without updating the model."""

    seed = int(request["seed"])
    os.environ["IPPO_MODE"] = "collect"
    os.environ["IPPO_ACTION_INTERVAL"] = str(request["action_interval"])
    os.environ["IPPO_EFFECTIVE_DEMAND"] = (
        "on" if bool(request["effective_demand_enabled"]) else "off"
    )
    os.environ.pop("IPPO_MODEL_PATH", None)
    os.environ["OMP_NUM_THREADS"] = "1"
    os.environ["MKL_NUM_THREADS"] = "1"
    torch.set_num_threads(1)
    random.seed(seed)
    np.random.seed(seed % (2**32 - 1))
    controller.prepare_collector(
        policy_state=request.get("policy_state"),
        policy_seed=int(request["policy_seed"]),
        rollout_seed=seed,
    )

    manager = SimulationManager()
    session_id = None
    started_at = time.time()
    try:
        config = SimulationConfig(
            intersection_ids=list(request["intersection_ids"]),
            period=str(request["period"]),
            duration_seconds=int(request["duration"]),
            control_mode="algorithm",
            algorithm_transport="local",
            algorithm_module="algorithms.ippo",
            decision_interval=5.0,
            minimum_green=5.0,
            seed=seed,
            step_length=0.05,
        )
        session_id = manager.start(config)
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
        rollout = controller.take_collected_rollout()
        return {
            "status": "complete",
            "seed": seed,
            "elapsed": time.time() - started_at,
            "metrics": _metrics_dict(snapshot),
            "rollout": rollout,
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


def _metadata_signature(metadata: Mapping[str, object]) -> tuple:
    intersections = metadata.get("intersections", {})
    if not isinstance(intersections, Mapping):
        raise ValueError("Worker metadata has no intersection mapping.")
    signature = []
    for intersection_id, item in intersections.items():
        connections = {
            str(connection.get("connection_id")): (
                str(connection.get("from_lane")),
                str(connection.get("to_lane")),
            )
            for connection in item.get("connections", ())
            if connection.get("connection_id") is not None
            and connection.get("from_lane") is not None
            and connection.get("to_lane") is not None
        }
        phases = item.get("phases", {})
        phase_semantics = []
        for phase_id in item.get("phase_order", ()):
            phase = phases.get(str(phase_id), phases.get(phase_id, {}))
            served_pairs = tuple(
                sorted(
                    connections[str(connection_id)]
                    for connection_id in phase.get("connection_priorities", {})
                    if str(connection_id) in connections
                )
            )
            phase_semantics.append(
                (
                    served_pairs,
                    float(phase.get("green_seconds", 30.0)),
                )
            )
        signature.append(
            (
                str(intersection_id),
                tuple(item.get("phase_order", ())),
                tuple(item.get("incoming_lanes", ())),
                tuple(item.get("outgoing_lanes", ())),
                tuple(phase_semantics),
            )
        )
    return tuple(signature)


def _metadata_signatures_compatible(signatures: Sequence[tuple]) -> bool:
    """Allow missing trailing phases, but never phase reordering or lane drift."""

    if not signatures:
        return False
    reference_ids = tuple(item[0] for item in signatures[0])
    for signature in signatures:
        if tuple(item[0] for item in signature) != reference_ids:
            return False
    for intersection_offset in range(len(reference_ids)):
        entries = [signature[intersection_offset] for signature in signatures]
        if any(entry[2:4] != entries[0][2:4] for entry in entries[1:]):
            return False
        phase_orders = [entry[1] for entry in entries]
        longest = max(phase_orders, key=len)
        if any(tuple(longest[: len(order)]) != tuple(order) for order in phase_orders):
            return False
        phase_semantics = [entry[4] for entry in entries]
        longest_semantics = max(phase_semantics, key=len)
        if any(
            tuple(longest_semantics[: len(semantics)]) != tuple(semantics)
            for semantics in phase_semantics
        ):
            return False
    return True


def _training_seed_range(
    base_seed: int, episodes: int, resume_path: Path | None
) -> tuple[int, int, int]:
    first_seed = base_seed + 1
    recorded_start = first_seed
    if resume_path is not None:
        checkpoint = load_checkpoint_metadata(resume_path)
        previous = checkpoint.get("training_seed_range")
        if not isinstance(previous, Mapping) or "start" not in previous or "end" not in previous:
            raise ValueError("resume checkpoint 缺少 training_seed_range")
        first_seed = max(first_seed, int(previous["end"]) + 1)
        recorded_start = int(previous["start"])
    return first_seed, first_seed + episodes - 1, recorded_start


def _run_policy_batch(
    *,
    seeds: Sequence[int],
    intersections: Sequence[str],
    duration: int,
    periods: Sequence[str],
    action_interval: float,
    effective_demand_enabled: bool,
    policy_seed: int,
    policy_state: Mapping[str, torch.Tensor] | None,
) -> list[dict]:
    if len(periods) != len(seeds):
        raise ValueError("periods must contain one value per rollout seed")
    request_base = {
        "intersection_ids": tuple(intersections),
        "duration": duration,
        "action_interval": action_interval,
        "effective_demand_enabled": effective_demand_enabled,
        "policy_seed": policy_seed,
        "policy_state": policy_state,
    }
    context = multiprocessing.get_context("spawn")
    results = []
    with ProcessPoolExecutor(max_workers=len(seeds), mp_context=context) as executor:
        futures = {
            executor.submit(
                _run_sumo_worker,
                {**request_base, "seed": seed, "period": period},
            ): seed
            for seed, period in zip(seeds, periods)
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
    return _validated_worker_results(results)


def _positive(parser: argparse.ArgumentParser, name: str, value: float) -> None:
    if value <= 0:
        parser.error(f"{name} must be positive")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--episodes", type=int, default=200)
    parser.add_argument("--duration", type=int, default=300)
    parser.add_argument("--workers", type=int, default=4)
    parser.add_argument("--intersections", type=int, default=20)
    parser.add_argument(
        "--intersection-ids",
        nargs="+",
        default=None,
        help="Explicit controlled intersection ids (subset of the canonical 20 slots); "
        "mutually exclusive with --intersections.",
    )
    parser.add_argument("--period", default="off_peak")
    parser.add_argument(
        "--periods",
        nargs="+",
        default=None,
        help="Round-robin training periods; overrides --period.",
    )
    parser.add_argument("--seed", type=int, default=42)
    parser.add_argument("--action-interval", type=float, default=DEFAULT_ACTION_INTERVAL)
    parser.add_argument(
        "--effective-demand",
        choices=("on", "off"),
        default="on",
        help="enable v8 near/far ETA demand features; use off for ablation",
    )
    parser.add_argument(
        "--checkpoint-every",
        type=int,
        default=CHECKPOINT_INTERVAL,
        help="save an intermediate checkpoint after this many completed episodes",
    )
    parser.add_argument("--save", type=Path, default=None)
    parser.add_argument(
        "--no-reset-optimizer",
        action="store_true",
        help="resume with the checkpoint optimizer state (default: reset optimizer).",
    )
    parser.add_argument("--resume", type=Path, default=None)
    args = parser.parse_args(argv)

    for name in ("episodes", "duration", "workers", "intersections"):
        _positive(parser, name, getattr(args, name))
    _positive(parser, "checkpoint-every", args.checkpoint_every)
    _positive(parser, "action-interval", args.action_interval)
    if (args.intersection_ids is None) == (getattr(args, "intersections", None) is None):
        # exactly one of --intersection-ids / --intersections must be given
        if args.intersection_ids is None and getattr(args, "intersections", None) is None:
            parser.error("either --intersections or --intersection-ids is required")
    if args.intersection_ids is not None:
        from traffic_control.ippo.identity import identity_slots_for

        intersections = tuple(args.intersection_ids)
        identity_slots_for(intersections)  # raises for ids outside the canonical 20 slots
        if len(intersections) == 0:
            parser.error("--intersection-ids must not be empty")
    else:
        if args.intersections > len(DEFAULT_INTERSECTION_IDS):
            parser.error(f"intersections must be <= {len(DEFAULT_INTERSECTION_IDS)}")
        intersections = tuple(DEFAULT_INTERSECTION_IDS[: args.intersections])
    workers = min(args.workers, args.episodes)
    periods = tuple(args.periods or (args.period,))
    if any(not str(period).strip() for period in periods):
        parser.error("training periods must be non-empty")
    effective_demand_enabled = args.effective_demand == "on"
    save_path = args.save or (
        REPO_ROOT
        / "algorithms"
        / "models"
        / f"ippo_{MODEL_VERSION}_parallel_{args.intersections}tls.pt"
    )
    checkpoint_dir = Path(
        os.environ.get(
            "IPPO_CHECKPOINT_DIR",
            str(Path(save_path).parent / "checkpoints"),
        )
    )

    resume_path = args.resume.expanduser().resolve() if args.resume else None
    if resume_path is not None and not resume_path.is_file():
        parser.error(f"resume checkpoint does not exist: {resume_path}")
    try:
        first_seed, last_seed, recorded_start = _training_seed_range(
            args.seed, args.episodes, resume_path
        )
    except (OSError, ValueError) as exc:
        parser.error(str(exc))

    initial_policy_state = None
    if resume_path is not None:
        checkpoint = load_checkpoint_metadata(resume_path)
        if checkpoint.get("model_version") != MODEL_VERSION:
            parser.error(
                f"resume checkpoint model version is not {MODEL_VERSION}"
            )
        if checkpoint.get("phase_feature_schema") != PHASE_FEATURE_SCHEMA:
            parser.error("resume checkpoint phase feature schema does not match")
        if not set(intersections) <= set(str(iid) for iid in checkpoint["intersection_ids"]):
            parser.error(
                "resume checkpoint intersection_ids are not a superset of this run: "
                f"{intersections}"
            )
        if abs(float(checkpoint["action_interval"]) - args.action_interval) > 1e-9:
            parser.error("resume checkpoint action interval does not match this run")
        saved_periods = tuple(checkpoint.get("training_periods", ()))
        if saved_periods and saved_periods != periods:
            parser.error(
                f"resume checkpoint training periods {saved_periods} do not match {periods}"
            )
        saved_effective_demand = bool(
            checkpoint.get("effective_demand_enabled", True)
        )
        if saved_effective_demand != effective_demand_enabled:
            parser.error(
                "resume checkpoint effective-demand setting does not match this run"
            )
        initial_policy_state = checkpoint["model_state_dict"]

    reset_optimizer = not bool(getattr(args, "no_reset_optimizer", False))
    os.environ["IPPO_RESET_OPTIMIZER"] = "1" if reset_optimizer else "0"
    os.environ["IPPO_MODE"] = "train"
    os.environ["IPPO_ACTION_INTERVAL"] = str(args.action_interval)
    os.environ["IPPO_EFFECTIVE_DEMAND"] = args.effective_demand
    os.environ["IPPO_TRAIN_SEED_START"] = str(recorded_start)
    os.environ["IPPO_TRAIN_SEED_END"] = str(last_seed)
    os.environ["IPPO_TRAIN_PERIODS"] = ",".join(periods)
    if resume_path is None:
        os.environ.pop("IPPO_MODEL_PATH", None)
    else:
        os.environ["IPPO_MODEL_PATH"] = str(resume_path)
    random.seed(args.seed)
    np.random.seed(args.seed)
    torch.manual_seed(args.seed)

    logger.info(
        "IPPO %s 同步并行训练: %d episodes × %ds, workers=%d, tls=%d, "
        "periods=%s, effective_demand=%s, seeds=%d..%d",
        MODEL_VERSION,
        args.episodes,
        args.duration,
        workers,
        len(intersections),
        list(periods),
        args.effective_demand,
        first_seed,
        last_seed,
    )

    policy_state = initial_policy_state
    learner_initialized = False
    checkpoint_bucket = 0
    total_started = time.time()
    try:
        for batch_number, seeds in enumerate(
            _seed_batches(first_seed=first_seed, episodes=args.episodes, workers=workers),
            start=1,
        ):
            batch_started = time.time()
            results = _run_policy_batch(
                seeds=seeds,
                intersections=intersections,
                duration=args.duration,
                periods=_period_batch(
                    seeds,
                    periods=periods,
                    training_seed_start=recorded_start,
                ),
                action_interval=args.action_interval,
                effective_demand_enabled=effective_demand_enabled,
                policy_seed=args.seed,
                policy_state=policy_state,
            )
            rollouts = [result["rollout"] for result in results]
            signatures = [
                _metadata_signature(rollout["metadata"]) for rollout in rollouts
            ]
            if not _metadata_signatures_compatible(signatures):
                raise RuntimeError("Parallel workers returned incompatible SUMO metadata")
            worker_policy = rollouts[0]["policy_state"]
            if any(
                not _policy_states_equal(worker_policy, rollout["policy_state"])
                for rollout in rollouts[1:]
            ):
                raise RuntimeError("Parallel workers did not share one policy generation")

            if not learner_initialized:
                torch.manual_seed(args.seed)
                controller.initialize(rollouts[0]["metadata"])
                if policy_state is None:
                    controller.install_parallel_initial_policy(worker_policy)
                learner_initialized = True
                checkpoint_bucket = (
                    controller.training_episode_count() // args.checkpoint_every
                )

            central_state = controller.export_policy_state()
            if any(
                not _policy_states_equal(central_state, rollout["policy_state"])
                for rollout in rollouts
            ):
                raise RuntimeError("A worker collected with a different policy generation")

            update = controller.ingest_parallel_rollouts(rollouts, update=True)
            os.environ["IPPO_TRAIN_SEED_END"] = str(seeds[-1])
            policy_state = controller.export_policy_state()
            metrics = [result["metrics"] for result in results]
            worker_elapsed = [float(result.get("elapsed", 0.0)) for result in results]
            episode_count = controller.training_episode_count()
            logger.info(
                "BATCH%d OK seeds=%s episodes=%d samples=%d wall=%.1fs "
                "worker_mean=%.1fs worker_max=%.1fs arrived=%.1f waiting=%.1f",
                batch_number,
                list(seeds),
                update["episodes"],
                update["samples"],
                time.time() - batch_started,
                float(np.mean(worker_elapsed)),
                max(worker_elapsed, default=0.0),
                float(np.mean([item["arrived"] for item in metrics])),
                float(np.mean([item["waiting"] for item in metrics])),
            )

            current_bucket = episode_count // args.checkpoint_every
            if current_bucket > checkpoint_bucket:
                controller.save_checkpoint(
                    checkpoint_dir / f"ippo_{MODEL_VERSION}_parallel_ep{episode_count}.pt"
                )
                checkpoint_bucket = current_bucket
    except BaseException:
        logger.exception("并行训练中止：本 policy batch 不进入PPO，不保存最终模型")
        return 1

    saved = controller.save_checkpoint(save_path)
    logger.info(
        "并行训练完成: episodes=%d wall=%.1fs model=%s",
        args.episodes,
        time.time() - total_started,
        saved,
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
