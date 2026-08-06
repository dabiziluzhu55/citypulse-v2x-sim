"""Paired six-metric evaluation for fixed and two MaxPressure variants."""

from __future__ import annotations

import argparse
import hashlib
import json
import logging
import multiprocessing
import os
import subprocess
import sys
import time
from concurrent.futures import ProcessPoolExecutor, as_completed
from pathlib import Path
from typing import Mapping, Sequence


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

from algorithms.ippo import evaluate_paired as shared  # noqa: E402
from algorithms.maxpressure_benchmark import (  # noqa: E402
    DEFAULT_LEGACY_ROOT,
    DEFAULT_SENIOR_REF,
    SENIOR_SOURCE_PATH,
)
from simulation.sumo.session import SimulationConfig, SimulationManager  # noqa: E402


logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(processName)s:%(name)s] %(levelname)s: %(message)s",
)
logger = logging.getLogger("maxpressure_benchmark.evaluate")

METHODS = ("fixed", "ours", "senior")
DEFAULT_INTERSECTIONS = tuple(f"demo_{index}" for index in range(1, 21))
OPTIONAL_OFFICIAL_METRIC_NAMES = frozenset(
    {
        "emergency_braking_exposure_per_1000",
        "severe_conflict_exposure_per_10000",
    }
)


def _missing_official_metrics(
    method: str, official_metrics: Mapping[str, object] | None
) -> tuple[list[str], list[str]]:
    required = set(shared.OFFICIAL_METRIC_NAMES) - OPTIONAL_OFFICIAL_METRIC_NAMES
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


def _run_evaluation(request: Mapping[str, object]) -> dict:
    method = str(request["method"])
    seed = int(request["seed"])
    os.environ["OMP_NUM_THREADS"] = "1"
    os.environ["MKL_NUM_THREADS"] = "1"
    os.environ["MAXPRESSURE_LEGACY_ROOT"] = str(request["legacy_root"])
    os.environ["MAXPRESSURE_SENIOR_REF"] = str(request["senior_ref"])
    if method != "fixed":
        os.environ["MAXPRESSURE_BENCHMARK_VARIANT"] = method

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
            algorithm_module=(
                "algorithms.maxpressure_benchmark" if algorithm else ""
            ),
            decision_interval=5.0,
            minimum_green=5.0,
            seed=seed,
            step_length=float(request["step_length"]),
            ai_observer_module="algorithms.evaluation.observer",
            ai_frame_interval_seconds=1.0,
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
        from algorithms.evaluation.metrics import apply_tripinfo_completed_metrics
        from algorithms.evaluation.runtime import last_result

        official = last_result(session_id)
        if official is not None:
            official = apply_tripinfo_completed_metrics(official, str(tripinfo_path))
        official_metrics = official.to_dict() if official is not None else None
        required_missing, optional_missing = _missing_official_metrics(
            method, official_metrics
        )
        if required_missing:
            raise RuntimeError(
                "missing official metrics: " + ", ".join(required_missing)
            )

        return {
            "status": "complete",
            "method": method,
            "seed": seed,
            "session_id": session_id,
            "elapsed_s": time.monotonic() - started_at,
            **shared._snapshot_metrics(snapshot),
            **shared._parse_tripinfo(tripinfo_path),
            "official_metrics": official_metrics,
            "missing_official_metrics": optional_missing,
        }
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


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _source_metadata(legacy_root: Path, senior_ref: str) -> dict:
    legacy_controller = legacy_root / "algorithms" / "coslight" / "controller.py"
    legacy_lane_state = legacy_root / "algorithms" / "coslight" / "lane_state.py"
    for path in (legacy_controller, legacy_lane_state):
        if not path.is_file():
            raise FileNotFoundError(path)

    resolved_ref = subprocess.run(
        ["git", "-C", str(REPO_ROOT), "rev-parse", f"{senior_ref}^{{commit}}"],
        check=True,
        capture_output=True,
        text=True,
        timeout=30,
    ).stdout.strip()
    senior_blob = subprocess.run(
        [
            "git",
            "-C",
            str(REPO_ROOT),
            "rev-parse",
            f"{resolved_ref}:{SENIOR_SOURCE_PATH}",
        ],
        check=True,
        capture_output=True,
        text=True,
        timeout=30,
    ).stdout.strip()
    return {
        "ours": {
            "implementation": "historical_coslight_density_pressure_with_max_green",
            "controller_path": str(legacy_controller),
            "controller_sha256": _sha256(legacy_controller),
            "lane_state_sha256": _sha256(legacy_lane_state),
            "internal_joint_decision_interval_s": 15.0,
            "max_green_factor": 2.0,
        },
        "senior": {
            "implementation": "backend_movement_halting_max_pressure",
            "git_commit": resolved_ref,
            "git_blob": senior_blob,
            "source_path": SENIOR_SOURCE_PATH,
        },
    }


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--methods", nargs="+", choices=METHODS, default=METHODS)
    parser.add_argument("--seeds", type=int, nargs="+", default=[62001, 62002, 62003, 62004])
    parser.add_argument("--workers", type=int, default=4)
    parser.add_argument("--duration", type=int, default=300)
    parser.add_argument("--intersections", type=int, default=20)
    parser.add_argument("--period", default="off_peak")
    parser.add_argument("--step-length", type=float, default=0.1)
    parser.add_argument("--legacy-root", type=Path, default=DEFAULT_LEGACY_ROOT)
    parser.add_argument("--senior-ref", default=DEFAULT_SENIOR_REF)
    parser.add_argument(
        "--output",
        type=Path,
        default=Path(__file__).resolve().parent / "runs" / "comparison.json",
    )
    args = parser.parse_args(argv)

    if args.workers <= 0 or args.duration <= 0 or args.intersections <= 0:
        parser.error("workers, duration and intersections must be positive")
    if args.intersections > len(DEFAULT_INTERSECTIONS):
        parser.error(f"intersections must be <= {len(DEFAULT_INTERSECTIONS)}")
    methods = tuple(dict.fromkeys(args.methods))
    seeds = tuple(dict.fromkeys(int(seed) for seed in args.seeds))
    intersections = DEFAULT_INTERSECTIONS[: args.intersections]
    legacy_root = args.legacy_root.expanduser().resolve()
    source_metadata = _source_metadata(legacy_root, args.senior_ref)

    jobs = [
        {
            "method": method,
            "seed": seed,
            "intersection_ids": intersections,
            "period": args.period,
            "duration": args.duration,
            "legacy_root": str(legacy_root),
            "senior_ref": source_metadata["senior"]["git_commit"],
            "step_length": args.step_length,
        }
        for method in methods
        for seed in seeds
    ]
    logger.info(
        "paired benchmark methods=%s seeds=%s duration=%ds tls=%d workers=%d",
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
                    "%s seed=%d arrived=%d waiting=%.1f travel=%.2fs",
                    row["method"],
                    row["seed"],
                    row["arrived"],
                    row["waiting"],
                    row["official_metrics"]["avg_travel_time_s"],
                )
            else:
                logger.error(
                    "%s seed=%d failed: %s",
                    row["method"],
                    row["seed"],
                    row["error"],
                )

    order = {method: index for index, method in enumerate(methods)}
    rows.sort(key=lambda row: (order[str(row["method"])], int(row["seed"])))
    report = {
        "config": {
            "methods": list(methods),
            "seeds": list(seeds),
            "duration_s": args.duration,
            "intersections": list(intersections),
            "period": args.period,
            "callback_interval_s": 5.0,
            "minimum_green_s": 5.0,
            "step_length_s": args.step_length,
            "observer_interval_s": 1.0,
        },
        "sources": source_metadata,
        "summary": shared._summarize(rows),
        "runs": rows,
    }
    output = args.output.expanduser().resolve()
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(
        json.dumps(report, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    failures = [row for row in rows if row["status"] != "complete"]
    logger.info("benchmark report: %s", output)
    return 1 if failures else 0


if __name__ == "__main__":
    raise SystemExit(main())
