"""Train CoSLight through the project's Protocol 2.0 local interface."""

from __future__ import annotations

import argparse
import gc
import logging
import math
import os
import random
import time
from pathlib import Path

import numpy as np
import torch

from algorithms.coslight import controller as coslight
from simulation.sumo.session import SimulationConfig, SimulationManager


logger = logging.getLogger("coslight.train")

INTERSECTIONS = tuple(f"demo_{index}" for index in range(1, 21))
DEFAULT_OUTPUT = Path(__file__).with_name("runs") / "coslight_v2"


def _save_recovery_checkpoint(final_path: Path) -> None:
    """Persist all previously completed rollouts without retrying the session."""
    from . import controller

    suffix = final_path.suffix or ".pt"
    recovery_path = final_path.with_name(f"{final_path.stem}.recovery{suffix}")
    try:
        controller.finalize_training(recovery_path)
    except RuntimeError:
        logger.warning("No initialized CoSLight model was available for recovery")
        return
    logger.info(
        "Recovery checkpoint saved: %s (resume with --resume %s)",
        recovery_path,
        recovery_path,
    )


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser()
    parser.add_argument("--episodes", type=int, default=200)
    parser.add_argument("--duration", type=int, default=300)
    parser.add_argument("--seed", type=int, default=42)
    parser.add_argument("--top-k", type=int, default=5)
    parser.add_argument(
        "--max-green-factor",
        type=float,
        default=coslight.DEFAULT_MAX_GREEN_FACTOR,
    )
    parser.add_argument(
        "--reward-mode",
        choices=("pressure", "legacy_delta"),
        default="pressure",
    )
    parser.add_argument("--save", type=Path, default=DEFAULT_OUTPUT / "final.pt")
    parser.add_argument("--checkpoint-dir", type=Path, default=DEFAULT_OUTPUT / "checkpoints")
    parser.add_argument("--resume", type=Path)
    parser.add_argument(
        "--vehicle-guidance",
        choices=("off", "rule"),
        default="off",
        help="Stage 1 isolates signal control; enable rule guidance only explicitly.",
    )
    return parser.parse_args()


def main() -> int:
    args = _parse_args()
    if args.episodes < 1 or args.duration < 1 or args.top_k < 1:
        raise ValueError("episodes, duration and top-k must be positive")
    if not math.isfinite(args.max_green_factor) or args.max_green_factor < 0:
        raise ValueError("max-green-factor must be finite and non-negative")

    os.environ.setdefault("SUMO_HOME", "/usr/share/sumo")
    os.environ["PATH"] = "/usr/share/sumo/bin:" + os.environ.get("PATH", "")
    os.environ["COSLIGHT_MODE"] = "train"
    os.environ["COSLIGHT_TOP_K"] = str(args.top_k)
    os.environ["COSLIGHT_REWARD_MODE"] = args.reward_mode
    os.environ["COSLIGHT_MAX_GREEN_FACTOR"] = str(args.max_green_factor)
    os.environ.pop("COSLIGHT_PRESSURE_SHIELD_MARGIN", None)
    os.environ.pop("COSLIGHT_RESIDUAL_MIN_BEST_PRESSURE", None)
    os.environ.pop("COSLIGHT_SWITCH_LOGIT_MARGIN", None)
    os.environ["COSLIGHT_CLOUD_MODE"] = "off"
    os.environ.pop("COSLIGHT_CLOUD_TOPOLOGY", None)
    os.environ["COSLIGHT_EPISODE_DURATION"] = str(args.duration)
    os.environ["COSLIGHT_CHECKPOINT_DIR"] = str(args.checkpoint_dir)
    os.environ["COSLIGHT_VEHICLE_GUIDANCE"] = args.vehicle_guidance
    if args.resume is not None:
        os.environ["COSLIGHT_RESUME_PATH"] = str(args.resume)
    else:
        os.environ.pop("COSLIGHT_RESUME_PATH", None)

    random.seed(args.seed)
    np.random.seed(args.seed)
    torch.manual_seed(args.seed)
    args.checkpoint_dir.mkdir(parents=True, exist_ok=True)
    args.save.parent.mkdir(parents=True, exist_ok=True)
    logger.info(
        "CoSLight training: episodes=%d duration=%ds seed=%d top_k=%d "
        "reward=%s max_green_factor=%.2f vehicles=%s",
        args.episodes,
        args.duration,
        args.seed,
        args.top_k,
        args.reward_mode,
        args.max_green_factor,
        args.vehicle_guidance,
    )

    completed = 0
    for episode in range(1, args.episodes + 1):
        manager = SimulationManager()
        session_id = None
        config = SimulationConfig(
            intersection_ids=INTERSECTIONS,
            period="off_peak",
            duration_seconds=args.duration,
            control_mode="algorithm",
            algorithm_transport="local",
            algorithm_module="algorithms.coslight",
            decision_interval=5.0,
            minimum_green=5.0,
            seed=args.seed + episode,
            step_length=0.05,
        )
        started = time.monotonic()
        try:
            session_id = manager.start(config)
            snapshot = manager.wait(
                session_id, timeout=max(600.0, float(args.duration) * 3.0)
            )
            elapsed = time.monotonic() - started
            if snapshot.state != "COMPLETED" or snapshot.metrics is None:
                logger.error(
                    "EP%d ended in state=%s after %.0fs: %s",
                    episode,
                    snapshot.state,
                    elapsed,
                    snapshot.error or "missing metrics",
                )
                _save_recovery_checkpoint(args.save)
                return 1
            completed = episode
            logger.info(
                "EP%d OK (%.0fs) departed=%d arrived=%d hard_braking=%d",
                episode,
                elapsed,
                snapshot.metrics.departed_vehicles,
                snapshot.metrics.arrived_vehicles,
                snapshot.metrics.hard_braking_events,
            )
        except Exception:
            logger.exception("EP%d raised an exception; training stops without retry", episode)
            if session_id is not None:
                try:
                    snapshot = manager.snapshot(session_id)
                    if snapshot.state in {"STARTING", "RUNNING", "PAUSED", "STOPPING"}:
                        manager.stop(session_id)
                        manager.wait(session_id, timeout=60.0)
                except Exception:
                    logger.exception("EP%d cleanup failed", episode)
            _save_recovery_checkpoint(args.save)
            return 1
        finally:
            del manager
            gc.collect()
            torch.cuda.empty_cache()
        time.sleep(2.0)

    from . import controller

    controller.finalize_training(args.save)
    logger.info("Training complete: %d/%d, final=%s", completed, args.episodes, args.save)
    return 0


if __name__ == "__main__":
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s [%(name)s] %(levelname)s: %(message)s",
    )
    raise SystemExit(main())
