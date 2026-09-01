"""Paired-seed evaluation for IPPO and the official SUMO fixed controller."""

from __future__ import annotations

import argparse
import json
import logging
import multiprocessing
import os
import random
import statistics
import sys
import time
import xml.etree.ElementTree as ET
from concurrent.futures import ProcessPoolExecutor, as_completed
from pathlib import Path
from typing import Mapping, Sequence

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

from algorithms.ippo.controller import (  # noqa: E402
    DEFAULT_ACTION_INTERVAL,
    DEFAULT_INTERSECTION_IDS,
    load_checkpoint_metadata,
)
from simulation.sumo.engine.session import SimulationConfig, SimulationManager  # noqa: E402
from algorithms.evaluation.tripinfo_diagnostics import (  # noqa: E402
    parse_tripinfo_diagnostics,
    residual_mismatch,
)


logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(processName)s:%(name)s] %(levelname)s: %(message)s",
)
logger = logging.getLogger("ippo.evaluate_paired")

METHODS = ("fixed", "random", "model")
OFFICIAL_METRIC_NAMES = (
    "avg_travel_time_s",
    "avg_waiting_time_s",
    "avg_queue_length_veh",
    "throughput_veh_per_h",
    "avg_decision_latency_ms",
    "fuel_intensity_L_per_100km",
    "severe_conflict_exposure_per_10000",
    "emergency_braking_exposure_per_1000",
)
OPTIONAL_OFFICIAL_METRIC_NAMES = frozenset(
    {
        "emergency_braking_exposure_per_1000",
        "severe_conflict_exposure_per_10000",
    }
)
SUMMARY_METRICS = (
    "departed",
    "arrived",
    "remaining",
    "waiting",
    "halting",
    "mean_speed",
    "hard_braking",
    "fuel_mg",
    "completed_trips",
    "unfinished_trips",
    "completion_rate",
    "residual_mismatch",
    "completed_duration_mean_s",
    "completed_waiting_mean_s",
    "completed_time_loss_mean_s",
    "unfinished_waiting_total_s",
    "all_waiting_total_s",
    "end_waiting_total_s",
    "end_waiting_mean_s",
    "all_time_loss_total_s",
)


def _validate_evaluation_seeds(metadata: Mapping[str, object], seeds: Sequence[int]) -> None:
    seed_range = metadata.get("training_seed_range")
    if not isinstance(seed_range, Mapping):
        raise ValueError(
            "checkpoint has no training_seed_range; held-out evaluation cannot be verified"
        )
    low = int(seed_range["start"])
    high = int(seed_range["end"])
    overlap = sorted(seed for seed in set(seeds) if low <= seed <= high)
    if overlap:
        raise ValueError(f"evaluation seeds overlap training seeds: {overlap}")


def _missing_official_metrics(
    method: str, official_metrics: Mapping[str, object] | None
) -> tuple[list[str], list[str]]:
    required = set(OFFICIAL_METRIC_NAMES) - OPTIONAL_OFFICIAL_METRIC_NAMES
    if method == "fixed":
        required.remove("avg_decision_latency_ms")
    required_missing = sorted(
        name
        for name in required
        if official_metrics is None or official_metrics.get(name) is None
    )
    optional_missing = sorted(
        name
        for name in OPTIONAL_OFFICIAL_METRIC_NAMES
        if official_metrics is None or official_metrics.get(name) is None
    )
    return required_missing, optional_missing


def _mean(values: Sequence[float]) -> float:
    return float(statistics.fmean(values)) if values else 0.0


def _parse_tripinfo(path: Path) -> dict:
    """TripInfo 诊断（统一口径，见 simulation/sumo/tripinfo.py）。"""
    return parse_tripinfo_diagnostics(path)


def _snapshot_metrics(snapshot) -> dict:
    metrics = snapshot.metrics
    return {
        "active": int(metrics.active_vehicles),
        "departed": int(metrics.departed_vehicles),
        "arrived": int(metrics.arrived_vehicles),
        "remaining": int(metrics.remaining_vehicles),
        "halting": int(metrics.halting_vehicles),
        "waiting": float(metrics.total_waiting_time),
        "mean_speed": float(metrics.mean_speed),
        "fuel_mg": float(metrics.fuel_consumed_mg),
        "hard_braking": int(metrics.hard_braking_events),
    }


def _run_evaluation(request: Mapping[str, object]) -> dict:
    method = str(request["method"])
    seed = int(request["seed"])
    os.environ["IPPO_MODE"] = method
    os.environ["IPPO_ACTION_INTERVAL"] = str(request["action_interval"])
    checkpoint = str(request.get("checkpoint") or "")
    if checkpoint:
        os.environ["IPPO_MODEL_PATH"] = checkpoint
    else:
        os.environ.pop("IPPO_MODEL_PATH", None)
    os.environ["OMP_NUM_THREADS"] = "1"
    os.environ["MKL_NUM_THREADS"] = "1"
    torch.set_num_threads(1)
    random.seed(seed)
    np.random.seed(seed % (2**32 - 1))
    torch.manual_seed(seed)

    manager = SimulationManager()
    session_id = None
    started_at = time.monotonic()
    try:
        algorithm = method != "fixed"
        config = SimulationConfig(
            intersection_ids=tuple(request["intersection_ids"]),
            period=str(request["period"]),
            duration_seconds=int(request["duration"]),
            control_mode="algorithm" if algorithm else "fixed",
            algorithm_transport="local",
            algorithm_module="algorithms.ippo" if algorithm else "",
            decision_interval=5.0,
            minimum_green=5.0,
            seed=seed,
            step_length=0.05,
            ai_observer_module="algorithms.evaluation.observer",
            # Safety events need denser observations than queue/fuel metrics.
            ai_frame_interval_seconds=0.2,
        )
        session_id = manager.start(config)
        snapshot = manager.wait(
            session_id,
            timeout=max(600.0, float(request["duration"]) * 3.0),
        )
        if snapshot.state != "COMPLETED" or snapshot.metrics is None:
            raise RuntimeError(
                f"SUMO state={snapshot.state} error={snapshot.error or ''}".strip()
            )
        tripinfo_path = manager.session_root / session_id / "tripinfo.xml"
        from algorithms.evaluation.runtime import last_result
        from algorithms.evaluation.metrics import apply_tripinfo_completed_metrics

        official = last_result(session_id)
        if official is not None:
            official = apply_tripinfo_completed_metrics(
                official, str(tripinfo_path)
            )
        official_metrics = official.to_dict() if official is not None else None
        required_missing, optional_missing = _missing_official_metrics(
            method, official_metrics
        )
        if required_missing:
            raise RuntimeError(
                "missing official metrics: " + ", ".join(required_missing)
            )
        result = {
            "status": "complete",
            "method": method,
            "seed": seed,
            "session_id": session_id,
            "elapsed_s": time.monotonic() - started_at,
            **_snapshot_metrics(snapshot),
            **_parse_tripinfo(tripinfo_path),
            "official_metrics": official_metrics,
            "missing_official_metrics": optional_missing,
        }
        result["residual_mismatch"] = residual_mismatch(
            result["unfinished_trips"], result["remaining"]
        )
        return result
    except BaseException as exc:
        if session_id is not None:
            try:
                manager.stop(session_id)
                manager.wait(session_id, timeout=60.0)
            except BaseException:
                pass
        return {
            "status": "failed",
            "method": method,
            "seed": seed,
            "session_id": session_id,
            "elapsed_s": time.monotonic() - started_at,
            "error": f"{type(exc).__name__}: {exc}",
        }


def _describe(values: Sequence[float | None]) -> dict:
    available = [float(value) for value in values if value is not None]
    if not available:
        return {
            "available_runs": 0,
            "missing_runs": len(values),
            "mean": None,
            "std": None,
            "median": None,
            "min": None,
            "max": None,
        }
    array = np.asarray(available, dtype=np.float64)
    return {
        "available_runs": len(available),
        "missing_runs": len(values) - len(available),
        "mean": float(array.mean()),
        "std": float(array.std()),
        "median": float(np.median(array)),
        "min": float(array.min()),
        "max": float(array.max()),
    }


def _official_summary(rows: Sequence[Mapping[str, object]]) -> dict:
    return {
        name: _describe(
            [
                row.get("official_metrics", {}).get(name)
                if isinstance(row.get("official_metrics"), Mapping)
                else None
                for row in rows
            ]
        )
        for name in OFFICIAL_METRIC_NAMES
    }



def _summarize(rows: Sequence[Mapping[str, object]]) -> dict:
    complete = [row for row in rows if row.get("status") == "complete"]
    methods = list(dict.fromkeys(str(row["method"]) for row in complete))
    fixed_by_seed = {
        int(row["seed"]): row for row in complete if row["method"] == "fixed"
    }
    summary = {}
    for method in methods:
        selected = [row for row in complete if row["method"] == method]
        item = {
            "episodes": len(selected),
            **{
                metric: _describe([float(row[metric]) for row in selected])
                for metric in SUMMARY_METRICS
                if selected and all(metric in row for row in selected)
            },
            "official_metrics": _official_summary(selected),
        }
        if method != "fixed":
            pairs = [
                (row, fixed_by_seed[int(row["seed"])])
                for row in selected
                if int(row["seed"]) in fixed_by_seed
            ]
            if pairs:
                item["paired_vs_fixed"] = {
                    "pair_count": len(pairs),
                    "arrived_mean_delta": _mean(
                        [float(row["arrived"]) - float(fixed["arrived"]) for row, fixed in pairs]
                    ),
                    "waiting_mean_delta": _mean(
                        [float(row["waiting"]) - float(fixed["waiting"]) for row, fixed in pairs]
                    ),
                    "completed_duration_mean_delta_s": _mean(
                        [
                            float(row["completed_duration_mean_s"])
                            - float(fixed["completed_duration_mean_s"])
                            for row, fixed in pairs
                        ]
                    ),
                    "all_waiting_total_mean_delta_s": _mean(
                        [
                            float(row["all_waiting_total_s"])
                            - float(fixed["all_waiting_total_s"])
                            for row, fixed in pairs
                        ]
                    ),
                    "end_waiting_total_mean_delta_s": _mean(
                        [
                            float(row["end_waiting_total_s"])
                            - float(fixed["end_waiting_total_s"])
                            for row, fixed in pairs
                            if "end_waiting_total_s" in row
                            and "end_waiting_total_s" in fixed
                        ]
                    ),
                    "unfinished_waiting_total_mean_delta_s": _mean(
                        [
                            float(row["unfinished_waiting_total_s"])
                            - float(fixed["unfinished_waiting_total_s"])
                            for row, fixed in pairs
                            if "unfinished_waiting_total_s" in row
                            and "unfinished_waiting_total_s" in fixed
                        ]
                    ),
                }
        summary[method] = item
    return summary


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--methods", nargs="+", choices=METHODS, default=METHODS)
    parser.add_argument("--episodes", type=int, default=4)
    parser.add_argument("--workers", type=int, default=4)
    parser.add_argument("--duration", type=int, default=300)
    parser.add_argument("--intersections", type=int, default=20)
    parser.add_argument(
        "--preset",
        choices=("east_dense", "west_dense", "xiongan_20"),
        default=None,
        help="scenario preset; controlled IDs come from algorithms/config/scenario_presets.py",
    )
    parser.add_argument(
        "--seeds",
        nargs="+",
        type=int,
        default=None,
        help="explicit seed list (default: pre-registered 10 seeds for the gate)",
    )
    parser.add_argument("--period", default="off_peak")
    parser.add_argument("--seed", type=int, default=10000)
    parser.add_argument("--action-interval", type=float, default=DEFAULT_ACTION_INTERVAL)
    parser.add_argument("--checkpoint", type=Path)
    parser.add_argument(
        "--output",
        type=Path,
        default=Path(__file__).with_name("runs") / "paired_evaluation.json",
    )
    args = parser.parse_args(argv)

    for name in ("episodes", "workers", "duration", "intersections"):
        if getattr(args, name) <= 0:
            parser.error(f"{name} must be positive")
    if args.action_interval <= 0:
        parser.error("action-interval must be positive")
    if args.intersections > len(DEFAULT_INTERSECTION_IDS):
        parser.error(f"intersections must be <= {len(DEFAULT_INTERSECTION_IDS)}")

    methods = tuple(dict.fromkeys(args.methods))
    if args.preset is not None:
        from algorithms.config.scenario_presets import SCENARIO_PRESET_REGISTRY

        intersections = SCENARIO_PRESET_REGISTRY[args.preset].intersection_ids
    else:
        intersections = tuple(DEFAULT_INTERSECTION_IDS[: args.intersections])
    checkpoint = args.checkpoint.expanduser().resolve() if args.checkpoint else None
    seeds = (
        tuple(args.seeds)
        if args.seeds is not None
        else tuple(range(args.seed + 1, args.seed + args.episodes + 1))
    )
    if "model" in methods:
        if checkpoint is None or not checkpoint.is_file():
            parser.error("model evaluation requires an existing --checkpoint")
        metadata = load_checkpoint_metadata(checkpoint)
        saved_ids = tuple(str(value) for value in metadata["intersection_ids"])
        if not set(intersections) <= set(saved_ids):
            parser.error(
                f"checkpoint intersections {saved_ids} are not a superset of requested {intersections}"
            )
        if abs(float(metadata["action_interval"]) - args.action_interval) > 1e-9:
            parser.error("checkpoint action interval does not match --action-interval")
        try:
            _validate_evaluation_seeds(metadata, seeds)
        except (KeyError, TypeError, ValueError) as exc:
            parser.error(str(exc))

    jobs = [
        {
            "method": method,
            "seed": seed,
            "intersection_ids": intersections,
            "period": args.period,
            "duration": args.duration,
            "action_interval": args.action_interval,
            "checkpoint": str(checkpoint) if method == "model" else "",
        }
        for method in methods
        for seed in seeds
    ]
    logger.info(
        "IPPO paired evaluation: methods=%s seeds=%s duration=%ds tls=%d workers=%d",
        list(methods),
        list(seeds),
        args.duration,
        len(intersections),
        min(args.workers, len(jobs)),
    )

    rows = []
    context = multiprocessing.get_context("spawn")
    with ProcessPoolExecutor(
        max_workers=min(args.workers, len(jobs)), mp_context=context
    ) as executor:
        futures = {executor.submit(_run_evaluation, job): job for job in jobs}
        for future in as_completed(futures):
            job = futures[future]
            try:
                row = future.result()
            except BaseException as exc:
                row = {
                    "status": "failed",
                    "method": job["method"],
                    "seed": job["seed"],
                    "error": f"worker process {type(exc).__name__}: {exc}",
                }
            rows.append(row)
            if row["status"] == "complete":
                logger.info(
                    "%s seed=%d arrived=%d remaining=%d waiting=%.1f completed=%d trip=%.1fs",
                    row["method"],
                    row["seed"],
                    row["arrived"],
                    row["remaining"],
                    row["waiting"],
                    row["completed_trips"],
                    row["completed_duration_mean_s"],
                )
            else:
                logger.error(
                    "%s seed=%d failed: %s",
                    row["method"],
                    row["seed"],
                    row["error"],
                )

    method_order = {method: index for index, method in enumerate(methods)}
    rows.sort(key=lambda row: (method_order[str(row["method"])], int(row["seed"])))
    report = {
        "config": {
            "methods": list(methods),
            "seeds": list(seeds),
            "duration_s": args.duration,
            "intersections": list(intersections),
            "period": args.period,
            "action_interval": args.action_interval,
            "checkpoint": str(checkpoint) if checkpoint else None,
        },
        "summary": _summarize(rows),
        "runs": rows,
    }
    output = args.output.expanduser().resolve()
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(
        json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    failures = [row for row in rows if row["status"] != "complete"]
    logger.info("Evaluation report: %s", output)
    return 1 if failures else 0


if __name__ == "__main__":
    raise SystemExit(main())
