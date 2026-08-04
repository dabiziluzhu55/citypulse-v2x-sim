"""Evaluate a current-version IPPO checkpoint with held-out SUMO seeds."""

from __future__ import annotations

import argparse
import json
import logging
import os
import statistics
import sys
import time
from pathlib import Path
from typing import Iterable


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
os.environ["IPPO_MODE"] = "model"

from algorithms.ippo.controller import (  # noqa: E402
    DEFAULT_ACTION_INTERVAL,
    DEFAULT_INTERSECTION_IDS,
    load_checkpoint_metadata,
)
from simulation.sumo.session import SimulationConfig, SimulationManager  # noqa: E402


logging.basicConfig(level=logging.INFO, format="%(asctime)s [eval] %(message)s")
logger = logging.getLogger("eval")

EVAL_SEEDS = (1042, 1142, 1242, 1342, 1442)
DEFAULT_DURATION = 300


def _stop_timed_out_session(manager: SimulationManager, session_id: str | None) -> None:
    if session_id is None:
        return
    try:
        manager.stop(session_id)
        manager.wait(session_id, timeout=60)
    except Exception as exc:
        logger.error("SUMO timeout cleanup failed: %s", exc)


def _validate_evaluation_seeds(metadata: dict, seeds: Iterable[int]) -> None:
    """Refuse a checkpoint evaluation that reuses a training seed."""

    seed_range = metadata.get("training_seed_range")
    if not seed_range or len(seed_range) != 2:
        raise ValueError(
            "checkpoint 缺少 training_seed_range，无法确认评估种子与训练种子独立"
        )
    if isinstance(seed_range, dict):
        low, high = int(seed_range["start"]), int(seed_range["end"])
    else:
        low, high = int(seed_range[0]), int(seed_range[1])
    overlap = sorted(seed for seed in set(seeds) if low <= seed <= high)
    if overlap:
        raise ValueError(f"评估种子与训练种子重叠: {overlap}")


def evaluate(
    model_path: str,
    *,
    seeds: Iterable[int] = EVAL_SEEDS,
    duration: int = DEFAULT_DURATION,
    action_interval: float = DEFAULT_ACTION_INTERVAL,
) -> dict:
    checkpoint = Path(model_path).expanduser().resolve()
    metadata = load_checkpoint_metadata(checkpoint)
    seeds = tuple(int(seed) for seed in seeds)
    _validate_evaluation_seeds(metadata, seeds)

    checkpoint_intersections = tuple(metadata.get("intersection_ids", ()))
    if not checkpoint_intersections:
        raise ValueError("checkpoint 没有 intersection_ids")
    unknown = set(checkpoint_intersections) - set(DEFAULT_INTERSECTION_IDS)
    if unknown:
        raise ValueError(f"checkpoint 包含未知路口: {sorted(unknown)}")

    trained_interval = float(metadata.get("action_interval", action_interval))
    if abs(trained_interval - action_interval) > 1e-9:
        raise ValueError(
            f"评估 action_interval={action_interval} 与训练值 {trained_interval} 不一致"
        )

    os.environ["IPPO_MODEL_PATH"] = str(checkpoint)
    os.environ["IPPO_ACTION_INTERVAL"] = str(action_interval)
    results: list[dict] = []

    for seed in seeds:
        started_at = time.time()
        manager = SimulationManager()
        session_id = None
        config = SimulationConfig(
            intersection_ids=list(checkpoint_intersections),
            period="off_peak",
            duration_seconds=duration,
            control_mode="algorithm",
            algorithm_transport="local",
            algorithm_module="algorithms.ippo",
            decision_interval=5.0,
            minimum_green=5.0,
            seed=seed,
            step_length=0.05,
        )
        try:
            session_id = manager.start(config)
            snapshot = manager.wait(session_id, timeout=max(600, duration * 3))
            elapsed = time.time() - started_at
            if snapshot and snapshot.state == "COMPLETED" and snapshot.metrics:
                result = {
                    "seed": seed,
                    "state": snapshot.state,
                    "departed": snapshot.metrics.departed_vehicles,
                    "arrived": snapshot.metrics.arrived_vehicles,
                    "waiting": snapshot.metrics.total_waiting_time,
                    "elapsed": round(elapsed, 1),
                }
                results.append(result)
                logger.info(
                    "seed=%d dep=%d arr=%d wait=%.0f (%.1fs)",
                    seed,
                    result["departed"],
                    result["arrived"],
                    result["waiting"],
                    elapsed,
                )
            else:
                state = getattr(snapshot, "state", "NO_SNAPSHOT")
                error = getattr(snapshot, "error", None) or f"terminal state={state}"
                logger.error("seed=%d FAILED: %s", seed, error)
                results.append({"seed": seed, "state": state, "error": str(error)})
        except TimeoutError as exc:
            _stop_timed_out_session(manager, session_id)
            logger.error("seed=%d TIMEOUT: %s", seed, exc)
            results.append({"seed": seed, "state": "TIMEOUT", "error": str(exc)})
        except Exception as exc:  # a failed SUMO run must not become a zero-valued score
            logger.exception("seed=%d evaluation failed", seed)
            results.append({"seed": seed, "state": "EXCEPTION", "error": str(exc)})

    successes = [item for item in results if "arrived" in item]
    arrivals = [int(item["arrived"]) for item in successes]
    waits = [float(item["waiting"]) for item in successes]
    failures = len(results) - len(successes)
    summary = {
        "status": "complete" if failures == 0 else "failed",
        "model": str(checkpoint),
        "checkpoint_version": metadata.get("model_version"),
        "evaluation_seeds": list(seeds),
        "duration_seconds": duration,
        "action_interval": action_interval,
        "successful_runs": len(successes),
        "failed_runs": failures,
        "mean_arrived": round(statistics.fmean(arrivals), 1) if arrivals else None,
        "std_arrived": round(statistics.pstdev(arrivals), 1) if arrivals else None,
        "mean_waiting": round(statistics.fmean(waits), 1) if waits else None,
        "best_arrived": max(arrivals) if arrivals else None,
        "worst_arrived": min(arrivals) if arrivals else None,
        "details": results,
    }
    return summary


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("model_path")
    parser.add_argument("--output", default="")
    parser.add_argument("--duration", type=int, default=DEFAULT_DURATION)
    parser.add_argument("--action-interval", type=float, default=DEFAULT_ACTION_INTERVAL)
    parser.add_argument("--seeds", type=int, nargs="+", default=list(EVAL_SEEDS))
    args = parser.parse_args(argv)
    if args.duration <= 0 or args.action_interval <= 0:
        parser.error("duration and action interval must be positive")

    try:
        logger.info("评估: %s", args.model_path)
        summary = evaluate(
            args.model_path,
            seeds=args.seeds,
            duration=args.duration,
            action_interval=args.action_interval,
        )
        output_path = Path(args.output or args.model_path.replace(".pt", "_eval.json"))
        output_path.parent.mkdir(parents=True, exist_ok=True)
        output_path.write_text(json.dumps(summary, indent=2, ensure_ascii=False))
        logger.info(
            "结果: status=%s success=%d failed=%d mean_arr=%s wait=%s | %s",
            summary["status"],
            summary["successful_runs"],
            summary["failed_runs"],
            summary["mean_arrived"],
            summary["mean_waiting"],
            output_path,
        )
        return 0 if summary["status"] == "complete" else 1
    except Exception:
        logger.exception("checkpoint evaluation aborted")
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
