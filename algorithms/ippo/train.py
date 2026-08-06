"""Train the current IPPO policy through the local SUMO protocol."""

from __future__ import annotations

import argparse
import gc
import logging
import os
import random
import sys
import time
from pathlib import Path

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
os.environ["IPPO_MODE"] = "train"

from algorithms.ippo.controller import (  # noqa: E402
    DEFAULT_ACTION_INTERVAL,
    DEFAULT_INTERSECTION_IDS,
    MODEL_VERSION,
    load_checkpoint_metadata,
    save_checkpoint,
)
from simulation.sumo.session import SimulationConfig, SimulationManager  # noqa: E402

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(name)s] %(levelname)s: %(message)s",
)
logger = logging.getLogger("ippo.train")

DEFAULT_SAVE_PATH = REPO_ROOT / "algorithms" / "models" / f"ippo_{MODEL_VERSION}_20"


def _stop_timed_out_session(manager: SimulationManager, session_id: str | None) -> None:
    if session_id is None:
        return
    try:
        manager.stop(session_id)
        manager.wait(session_id, timeout=60)
    except Exception as exc:
        logger.error("SUMO timeout cleanup failed: %s", exc)


def _positive(parser: argparse.ArgumentParser, name: str, value: float) -> None:
    if value <= 0:
        parser.error(f"{name} must be positive")


def _training_seed_range(
    base_seed: int, episodes: int, resume_path: Path | None
) -> tuple[int, int, int]:
    first_seed = base_seed + 1
    recorded_start = first_seed
    if resume_path is not None:
        metadata = load_checkpoint_metadata(resume_path)
        previous_range = metadata.get("training_seed_range")
        if (
            not isinstance(previous_range, dict)
            or "start" not in previous_range
            or "end" not in previous_range
        ):
            raise ValueError(
                "resume checkpoint 缺少 training_seed_range，无法避免重复训练种子"
            )
        first_seed = max(first_seed, int(previous_range["end"]) + 1)
        recorded_start = int(previous_range["start"])
    return first_seed, first_seed + episodes - 1, recorded_start


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--episodes", type=int, default=200)
    parser.add_argument("--duration", type=int, default=300)
    parser.add_argument("--step-length", type=float, default=0.1)
    parser.add_argument("--seed", type=int, default=42)
    parser.add_argument("--action-interval", type=float, default=DEFAULT_ACTION_INTERVAL)
    parser.add_argument("--save", type=Path, default=DEFAULT_SAVE_PATH)
    parser.add_argument("--resume", type=Path, default=None)
    args = parser.parse_args(argv)

    _positive(parser, "episodes", args.episodes)
    _positive(parser, "duration", args.duration)
    _positive(parser, "action-interval", args.action_interval)
    _positive(parser, "step-length", args.step_length)

    resume_path = args.resume
    if resume_path is None and os.environ.get("IPPO_MODEL_PATH"):
        resume_path = Path(os.environ["IPPO_MODEL_PATH"])
    if resume_path is not None:
        resume_path = resume_path.expanduser().resolve()
        if not resume_path.is_file():
            parser.error(f"resume checkpoint does not exist: {resume_path}")
        os.environ["IPPO_MODEL_PATH"] = str(resume_path)
    else:
        os.environ.pop("IPPO_MODEL_PATH", None)

    try:
        (
            first_training_seed,
            last_training_seed,
            recorded_seed_start,
        ) = _training_seed_range(args.seed, args.episodes, resume_path)
    except (OSError, ValueError) as exc:
        parser.error(str(exc))

    random.seed(args.seed)
    np.random.seed(args.seed)
    torch.manual_seed(args.seed)
    os.environ["IPPO_ACTION_INTERVAL"] = str(args.action_interval)
    os.environ["IPPO_TRAIN_SEED_START"] = str(recorded_seed_start)
    os.environ["IPPO_TRAIN_SEED_END"] = str(last_training_seed)

    logger.info(
        "IPPO %s 开始训练: %d episodes × %ds, seeds=%d..%d, "
        "action_interval=%.1fs%s",
        MODEL_VERSION,
        args.episodes,
        args.duration,
        first_training_seed,
        last_training_seed,
        args.action_interval,
        f", resume={resume_path}" if resume_path else "",
    )

    for episode in range(1, args.episodes + 1):
        manager = SimulationManager()
        config = SimulationConfig(
            intersection_ids=list(DEFAULT_INTERSECTION_IDS),
            period="off_peak",
            duration_seconds=args.duration,
            control_mode="algorithm",
            algorithm_transport="local",
            algorithm_module="algorithms.ippo",
            decision_interval=5.0,
            minimum_green=5.0,
            seed=first_training_seed + episode - 1,
            step_length=args.step_length,
        )
        started_at = time.time()
        failure = None
        session_id = None
        try:
            session_id = manager.start(config)
            snapshot = manager.wait(
                session_id, timeout=max(600, int(args.duration * 3))
            )
            elapsed = time.time() - started_at
            if snapshot is None:
                failure = "TIMEOUT"
            elif snapshot.state != "COMPLETED":
                failure = f"state={snapshot.state} error={snapshot.error or ''}".strip()
            elif snapshot.metrics is None:
                failure = "COMPLETED without metrics"
            else:
                logger.info(
                    "EP%d OK (%.0fs) departed=%d arrived=%d",
                    episode,
                    elapsed,
                    snapshot.metrics.departed_vehicles,
                    snapshot.metrics.arrived_vehicles,
                )
        except TimeoutError as exc:
            failure = f"TimeoutError: {exc}"
            _stop_timed_out_session(manager, session_id)
        except Exception as exc:
            failure = f"{type(exc).__name__}: {exc}"
        finally:
            del manager
            gc.collect()
            if torch.cuda.is_available():
                torch.cuda.empty_cache()

        if failure is not None:
            logger.error("EP%d ABORT: %s", episode, failure)
            logger.error("失败 rollout 不计入训练，也不保存最终模型。")
            return 1
        time.sleep(2.0)

    saved_path = save_checkpoint(args.save)
    logger.info("训练完成: %d episodes, model=%s", args.episodes, saved_path)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
