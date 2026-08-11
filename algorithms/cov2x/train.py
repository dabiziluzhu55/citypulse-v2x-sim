"""Train/evaluate the CoV2X vehicle-road-cloud joint policy.

With ``--signal-mode learned`` the controller runs the joint CTDE stack: a
shared signal policy (phase decisions every 15 s), the approach-advisor
vehicle policy (every 5 s), and a cloud coordinator policy (every 30 s) all
collect one episode of transitions and receive one joint PPO update in the
main process.  ``--signal-mode max_pressure`` preserves the Stage-2
frozen-signal vehicle-only training path.  Training and evaluation reuse the
same ``algorithms.cov2x.controller`` module so command validation and
execution telemetry are identical.

Example:
    python -m algorithms.cov2x.train --mode train --episodes 10 \\
        --duration 300 --period off_peak --preset xiongan_20 \\
        --signal-mode learned --cloud-mode learned \\
        --checkpoint-dir runs/cov2x_joint_v1/checkpoints \\
        --save runs/cov2x_joint_v1/final.pt --log runs/cov2x_joint_v1/episodes.jsonl
"""

from __future__ import annotations

import argparse
import gc
import json
import logging
import os
import random
import time
from pathlib import Path
from typing import Any

import numpy as np
import torch

from algorithms.cov2x import controller as cov2x
from simulation.sumo.session import SimulationConfig, SimulationManager


logger = logging.getLogger("cov2x.train")

PRESET_INTERSECTIONS: dict[str, tuple[str, ...]] = {
    "xiongan_20": tuple(f"demo_{i}" for i in range(1, 21)),
    "east_dense": ("demo_3", "demo_5", "demo_6", "demo_9"),
    "west_dense": ("demo_14", "demo_15", "demo_19"),
}

DEFAULT_OUTPUT = Path(__file__).resolve().parent / "runs" / "cov2x_vehicle_v1"
JOINT_DEFAULT_OUTPUT = Path(__file__).resolve().parent / "runs" / "cov2x_joint_v1"


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--mode",
        choices=("train", "eval", "rule"),
        default="train",
        help="train=PPO episodes, eval=deterministic checkpoint, rule=rule baseline",
    )
    parser.add_argument("--episodes", type=int, default=20)
    parser.add_argument("--duration", type=int, default=300)
    parser.add_argument(
        "--period",
        choices=("morning_peak", "off_peak", "evening_peak"),
        default="off_peak",
    )
    parser.add_argument(
        "--periods",
        help=(
            "comma-separated period rotation for multi-period training "
            "(overrides --period), e.g. morning_peak,off_peak,evening_peak"
        ),
    )
    parser.add_argument(
        "--preset",
        choices=tuple(PRESET_INTERSECTIONS),
        default="xiongan_20",
    )
    parser.add_argument("--seed", type=int, default=42)
    parser.add_argument(
        "--signal-mode",
        choices=("max_pressure", "fixed", "learned"),
        default="max_pressure",
    )
    parser.add_argument(
        "--cloud-mode",
        choices=("learned", "off"),
        default="",
        help="learned cloud coordinator (joint mode); off sends neutral priorities",
    )
    parser.add_argument(
        "--vehicle-mode",
        choices=("learned", "rule", "off"),
        default="learned",
    )
    parser.add_argument("--decision-interval", type=float, default=5.0)
    parser.add_argument(
        "--signal-decision-interval", type=float, default=15.0
    )
    parser.add_argument(
        "--cloud-decision-interval", type=float, default=30.0
    )
    parser.add_argument("--step-length", type=float, default=0.05)
    parser.add_argument("--save", type=Path, default=DEFAULT_OUTPUT / "final.pt")
    parser.add_argument(
        "--checkpoint-dir",
        type=Path,
        default=DEFAULT_OUTPUT / "checkpoints",
    )
    parser.add_argument("--resume", type=Path)
    parser.add_argument("--log", type=Path, default=DEFAULT_OUTPUT / "episodes.jsonl")
    return parser.parse_args()


def _set_env(args: argparse.Namespace) -> None:
    os.environ.setdefault("SUMO_HOME", "/usr/share/sumo")
    os.environ["PATH"] = "/usr/share/sumo/bin:" + os.environ.get("PATH", "")
    os.environ["COV2X_MODE"] = args.mode
    os.environ["COV2X_SIGNAL_MODE"] = args.signal_mode
    os.environ["COV2X_CLOUD_MODE"] = args.cloud_mode or (
        "learned" if args.signal_mode == "learned" else "off"
    )
    os.environ["COV2X_VEHICLE_MODE"] = args.vehicle_mode
    os.environ["COV2X_SIGNAL_DECISION_INTERVAL"] = str(
        args.signal_decision_interval
    )
    os.environ["COV2X_CLOUD_DECISION_INTERVAL"] = str(
        args.cloud_decision_interval
    )
    os.environ["COV2X_CHECKPOINT_DIR"] = str(args.checkpoint_dir)
    if args.resume is not None:
        os.environ["COV2X_MODEL_PATH"] = str(args.resume)
    else:
        os.environ.pop("COV2X_MODEL_PATH", None)


def _append_log(path: Path, record: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8") as handle:
        handle.write(json.dumps(record, ensure_ascii=False, default=str) + "\n")


def _run_episode(
    args: argparse.Namespace,
    episode: int,
    period: str,
) -> dict[str, Any]:
    manager = SimulationManager()
    session_id = None
    started = time.monotonic()
    try:
        session_id = manager.start(
            SimulationConfig(
                intersection_ids=PRESET_INTERSECTIONS[args.preset],
                period=period,
                duration_seconds=args.duration,
                control_mode="algorithm",
                algorithm_transport="local",
                algorithm_module="algorithms.cov2x.controller",
                decision_interval=args.decision_interval,
                minimum_green=5.0,
                seed=args.seed + episode,
                step_length=args.step_length,
            )
        )
        snapshot = manager.wait(
            session_id,
            timeout=max(600.0, float(args.duration) * 3.0),
        )
        elapsed = time.monotonic() - started
        if snapshot.state != "COMPLETED" or snapshot.metrics is None:
            return {
                "episode": episode,
                "period": period,
                "state": snapshot.state,
                "error": snapshot.error or "missing metrics",
                "elapsed_wall_s": round(elapsed, 1),
            }
        metrics = snapshot.metrics.__dict__
        metrics.update(_tripinfo_metrics(manager, session_id, metrics))
        return {
            "episode": episode,
            "period": period,
            "state": snapshot.state,
            "elapsed_wall_s": round(elapsed, 1),
            "metrics": metrics,
        }
    finally:
        del manager
        gc.collect()
        torch.cuda.empty_cache()


def _tripinfo_metrics(
    manager: SimulationManager,
    session_id: str,
    metrics: dict[str, Any],
) -> dict[str, Any]:
    """Best-effort authoritative tripinfo averages for the current harness."""
    try:
        from traffic_eval.tripinfo import parse_departed_tripinfo

        record = manager._sessions[session_id]  # noqa: SLF001
        path = record.scenario.tripinfo_file
        trips, warning = parse_departed_tripinfo(path)
        departed = int(metrics.get("departed_vehicles", 0))
        if warning or len(trips) != departed or not trips:
            return {}
        travel_total = sum(float(trip.get("duration", 0.0)) for trip in trips)
        waiting_total = sum(
            float(trip.get("waitingTime", 0.0)) for trip in trips
        )
        return {
            "avg_travel_time": travel_total / departed,
            "avg_waiting_time": waiting_total / departed,
        }
    except Exception:
        return {}


def main() -> int:
    args = _parse_args()
    if args.signal_mode == "learned":
        if args.cloud_mode not in ("", "learned"):
            raise SystemExit(
                "--signal-mode learned requires --cloud-mode learned "
                "(joint vehicle-road-cloud training)"
            )
        args.cloud_mode = "learned"
        if args.save == DEFAULT_OUTPUT / "final.pt":
            args.save = JOINT_DEFAULT_OUTPUT / "final.pt"
        if args.checkpoint_dir == DEFAULT_OUTPUT / "checkpoints":
            args.checkpoint_dir = JOINT_DEFAULT_OUTPUT / "checkpoints"
        if args.log == DEFAULT_OUTPUT / "episodes.jsonl":
            args.log = JOINT_DEFAULT_OUTPUT / "episodes.jsonl"
    if (
        args.mode == "eval"
        and args.vehicle_mode == "learned"
        and args.signal_mode == "learned"
        and args.resume is None
    ):
        raise SystemExit(
            "--mode eval with --signal-mode learned requires "
            "--resume <joint_checkpoint.pt>"
        )
    if (
        args.mode == "eval"
        and args.vehicle_mode == "learned"
        and args.signal_mode != "learned"
        and args.resume is None
    ):
        raise SystemExit("--mode eval requires --resume <checkpoint.pt>")
    if args.mode == "train" and args.vehicle_mode != "learned":
        raise SystemExit(
            "--mode train requires --vehicle-mode learned "
            "(rule/off are evaluation baselines)"
        )
    if args.mode == "rule":
        args.vehicle_mode = "rule"
        if args.signal_mode == "learned":
            args.signal_mode = "max_pressure"
    if args.episodes < 1 or args.duration < 1:
        raise SystemExit("--episodes and --duration must be positive")
    periods: list[str] = []
    if args.periods:
        periods = [
            item.strip()
            for item in args.periods.split(",")
            if item.strip()
        ]
        unknown = set(periods) - {
            "morning_peak",
            "off_peak",
            "evening_peak",
        }
        if unknown:
            raise SystemExit(
                f"unknown period(s) in --periods: {sorted(unknown)}"
            )
    if not periods:
        periods = [args.period]

    random.seed(args.seed)
    np.random.seed(args.seed)
    torch.manual_seed(args.seed)
    args.save.parent.mkdir(parents=True, exist_ok=True)
    args.checkpoint_dir.mkdir(parents=True, exist_ok=True)
    _set_env(args)
    logger.info(
        "CoV2X %s: episodes=%d duration=%ds period=%s preset=%s seed=%d "
        "signal=%s cloud=%s vehicle=%s",
        args.mode,
        args.episodes,
        args.duration,
        ",".join(periods),
        args.preset,
        args.seed,
        args.signal_mode,
        args.cloud_mode,
        args.vehicle_mode,
    )

    completed = 0
    for episode in range(1, args.episodes + 1):
        period = periods[(episode - 1) % len(periods)]
        record = _run_episode(args, episode, period)
        if record.get("state") != "COMPLETED":
            logger.error(
                "EP%d failed: %s",
                episode,
                record.get("error") or record.get("state"),
            )
            _append_log(args.log, record)
            return 1
        _append_log(args.log, record)
        completed = episode
        logger.info(
            "EP%d OK (%.0fs) arrived=%s departed=%s",
            episode,
            record["elapsed_wall_s"],
            record["metrics"].get("arrived_vehicles"),
            record["metrics"].get("departed_vehicles"),
        )

        if args.mode == "train":
            rollout = cov2x.take_collected_rollout()
            diagnostics = cov2x.train_on_rollout(rollout)
            _append_log(
                args.log,
                {"episode": episode, "training": diagnostics or {}},
            )
            logger.info(
                "EP%d update: steps=%s gen=%s vehicle_reward=%s "
                "execution_rate=%s",
                episode,
                (diagnostics or {}).get("steps"),
                (diagnostics or {}).get("policy_generation"),
                (
                    (diagnostics or {})
                    .get("episode_summary", {})
                    .get("vehicle", {})
                    .get("reward_mean")
                ),
                (
                    (diagnostics or {})
                    .get("episode_summary", {})
                    .get("vehicle", {})
                    .get("lane_change_execution_rate")
                ),
            )
        time.sleep(2.0)

    if args.mode == "train":
        try:
            final_path = cov2x.save_checkpoint(args.save)
            logger.info("Training complete: %d/%d, final=%s", completed, args.episodes, final_path)
        except RuntimeError:
            logger.warning("No trained policy to save; check --mode")
    else:
        logger.info("Evaluation complete: %d episodes", completed)
    return 0


if __name__ == "__main__":
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s [%(name)s] %(levelname)s: %(message)s",
    )
    raise SystemExit(main())
