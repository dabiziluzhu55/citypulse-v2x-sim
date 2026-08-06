"""Launch a short read-only probe rollout with the per-intersection wrapper."""
from __future__ import annotations

import argparse
import logging
import os
import random
import sys
from pathlib import Path

import numpy as np
import torch


REPO_ROOT = Path(__file__).resolve().parents[2]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

os.environ.setdefault("SUMO_HOME", "/usr/share/sumo")
sumo_bin = str(Path(os.environ["SUMO_HOME"]) / "bin")
path_entries = [
    e for e in os.environ.get("PATH", "").split(os.pathsep) if e and e != sumo_bin
]
os.environ["PATH"] = os.pathsep.join([*path_entries, sumo_bin])

from simulation.sumo.session import SimulationConfig, SimulationManager  # noqa: E402


logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(name)s] %(levelname)s: %(message)s",
)
logger = logging.getLogger("probe_analysis")


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--checkpoint", required=True)
    parser.add_argument("--output", default="/tmp/probe_v17_vs_v16.json")
    parser.add_argument("--intersections", default="demo_1..demo_20")
    parser.add_argument("--period", default="off_peak")
    parser.add_argument("--duration", type=int, default=300)
    parser.add_argument("--seed", type=int, default=77123)
    parser.add_argument("--top-k", type=int, default=5)
    args = parser.parse_args()

    ids = tuple(args.intersections.split(".."))
    if len(ids) == 2:
        ids = tuple(f"demo_{i}" for i in range(int(ids[0][5:]), int(ids[1][5:]) + 1))

    os.environ["COSLIGHT_MODE"] = "model"
    os.environ["COSLIGHT_MODEL_PATH"] = args.checkpoint
    os.environ["COSLIGHT_TOP_K"] = str(args.top_k)
    os.environ["COSLIGHT_POLICY_SEED"] = "42"
    os.environ["COSLIGHT_REWARD_MODE"] = "pressure"
    os.environ["COSLIGHT_MAX_GREEN_FACTOR"] = "2.0"
    os.environ["COSLIGHT_PRESSURE_SHIELD_MARGIN"] = "0"
    os.environ["COSLIGHT_RESIDUAL_MIN_BEST_PRESSURE"] = "0"
    os.environ["COSLIGHT_SWITCH_LOGIT_MARGIN"] = "0"
    os.environ["COSLIGHT_CLOUD_MODE"] = "off"
    os.environ["COSLIGHT_VEHICLE_GUIDANCE"] = "off"
    os.environ["COSLIGHT_PROBE_OUTPUT"] = args.output
    os.environ["OMP_NUM_THREADS"] = "1"
    os.environ["MKL_NUM_THREADS"] = "1"
    torch.set_num_threads(1)
    random.seed(args.seed)
    np.random.seed(args.seed % (2**32 - 1))
    torch.manual_seed(args.seed)

    config = SimulationConfig(
        intersection_ids=ids,
        period=args.period,
        duration_seconds=args.duration,
        control_mode="algorithm",
        algorithm_transport="local",
        algorithm_module="algorithms.coslight.probe_analysis",
        decision_interval=5.0,
        minimum_green=5.0,
        seed=args.seed,
        step_length=0.05,
    )
    manager = SimulationManager()
    session_id = None
    try:
        session_id = manager.start(config)
        snapshot = manager.wait(
            session_id, timeout=max(600.0, float(args.duration) * 3.0)
        )
        if snapshot.state != "COMPLETED":
            raise RuntimeError(
                f"SUMO state={snapshot.state} error={snapshot.error or ''}".strip()
            )
        logger.info("probe session COMPLETED session_id=%s elapsed=%.1fs", session_id, snapshot.elapsed_seconds)
    finally:
        if session_id is not None:
            try:
                manager.stop(session_id)
                manager.wait(session_id, timeout=60.0)
            except BaseException:
                pass
    logger.info("probe output: %s", args.output)


if __name__ == "__main__":
    main()
