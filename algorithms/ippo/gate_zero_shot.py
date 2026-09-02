# algorithms/ippo/gate_zero_shot.py
"""Run the pre-registered zero-shot gate for east-4 / west-3.

For each scenario and each pre-registered seed it runs fixed and model via
``evaluate_paired._run_evaluation``, then computes the primary gate, the
non-inferiority guardrails and the safety gate, and writes a JSON + Markdown
report.  The report marks the criteria as engineering criteria, not industry
standards.
"""

from __future__ import annotations

import argparse
import json
import multiprocessing
import os
import sys
from concurrent.futures import ProcessPoolExecutor, as_completed
from pathlib import Path
from typing import Any

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

from algorithms.ippo.evaluate_paired import _run_evaluation  # noqa: E402
from algorithms.ippo.gate_stats import (  # noqa: E402
    PREREGISTERED_SEEDS,
    evaluate_non_inferiority,
    evaluate_primary_gate,
    evaluate_safety,
)
from algorithms.presets import SCENARIO_PRESET_REGISTRY  # noqa: E402

SCENARIOS = ("east_dense", "west_dense")

CONTROLLED_OFFICIAL_KEYS = (
    "controlled_avg_waiting_time_s",
    "avg_queue_length_veh",
    "avg_decision_latency_ms",
    "emergency_braking_exposure_per_1000",
)
NETWORK_OFFICIAL_KEYS = (
    "avg_travel_time_s",
    "avg_waiting_time_s",
    "throughput_veh_per_h",
    "fuel_intensity_L_per_100km",
)


def _extract(row: dict, key: str):
    metrics = row.get("official_metrics") or {}
    return metrics.get(key)


def _safety_rows(rows: list[dict], metric: str) -> list[dict]:
    # 安全指标仅保留紧急制动暴露率（2026-08-06）；参数 metric 保留以便门禁统一调用。
    out = []
    for row in rows:
        metrics = row.get("official_metrics") or {}
        availability = metrics.get("emergency_braking_availability")
        out.append(
            {
                "events": int(metrics.get("emergency_braking_events", 0) or 0),
                "exposures": int(metrics.get("controlled_intersection_passages", 0) or 0),
                "availability": (availability or {}).get("status", "unavailable"),
            }
        )
    return out


def _run_scenario(
    scenario: str,
    *,
    seeds: tuple[int, ...],
    duration: int,
    period: str,
    checkpoint: Path,
    action_interval: float,
    workers: int = 1,
) -> tuple[list[dict], list[dict]]:
    intersections = SCENARIO_PRESET_REGISTRY[scenario].intersection_ids
    jobs = [
        {
            "method": method,
            "seed": seed,
            "intersection_ids": intersections,
            "period": period,
            "duration": duration,
            "action_interval": action_interval,
            "checkpoint": str(checkpoint) if method == "model" else "",
        }
        for method in ("fixed", "model")
        for seed in seeds
    ]
    context = multiprocessing.get_context("spawn")
    results: list[dict] = []
    with ProcessPoolExecutor(
        max_workers=min(workers, len(jobs)), mp_context=context
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
            results.append(row)
    failures = [row for row in results if row.get("status") != "complete"]
    if failures:
        raise RuntimeError(f"{scenario}: failed runs: {failures}")
    fixed_rows = [row for row in results if row["method"] == "fixed"]
    model_rows = [row for row in results if row["method"] == "model"]
    fixed_rows.sort(key=lambda row: row["seed"])
    model_rows.sort(key=lambda row: row["seed"])
    return fixed_rows, model_rows


def _verdicts(
    scenario: str,
    fixed_rows: list[dict],
    model_rows: list[dict],
) -> dict[str, Any]:
    primary = evaluate_primary_gate(
        scenario,
        model_waiting=[_extract(row, "controlled_avg_waiting_time_s") for row in model_rows],
        fixed_waiting=[_extract(row, "controlled_avg_waiting_time_s") for row in fixed_rows],
    )
    non_inferiority = {}
    for metric, bound in {
        "avg_travel_time_s": 0.05,
        "avg_queue_length_veh": 0.05,
        "fuel_intensity_L_per_100km": 0.05,
    }.items():
        non_inferiority[metric] = evaluate_non_inferiority(
            [_extract(row, metric) for row in model_rows],
            [_extract(row, metric) for row in fixed_rows],
            metric=metric,
            lower_is_better=True,
            bound=bound,
        )
    non_inferiority["throughput_veh_per_h"] = evaluate_non_inferiority(
        [_extract(row, "throughput_veh_per_h") for row in model_rows],
        [_extract(row, "throughput_veh_per_h") for row in fixed_rows],
        metric="throughput_veh_per_h",
        lower_is_better=False,
        bound=0.05,
    )
    safety = {
        metric: evaluate_safety(
            _safety_rows(model_rows, metric),
            _safety_rows(fixed_rows, metric),
            metric=metric,
        )
        for metric in (
            "emergency_braking_exposure_per_1000",
        )
    }
    network_waiting = {
        "model": [_extract(row, "avg_waiting_time_s") for row in model_rows],
        "fixed": [_extract(row, "avg_waiting_time_s") for row in fixed_rows],
    }
    return {
        "primary": primary.to_dict(),
        "non_inferiority": non_inferiority,
        "safety": safety,
        "network_scope_waiting_secondary": network_waiting,
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--scenarios", nargs="+", choices=SCENARIOS, default=list(SCENARIOS))
    parser.add_argument("--seeds", nargs="+", type=int, default=None)
    parser.add_argument("--duration", type=int, default=300)
    parser.add_argument("--period", default="off_peak")
    parser.add_argument("--checkpoint", type=Path, default=None)
    parser.add_argument("--action-interval", type=float, default=15.0)
    parser.add_argument(
        "--output-dir",
        type=Path,
        default=REPO_ROOT / "algorithms" / "ippo" / "gate_outputs",
    )
    parser.add_argument("--workers", type=int, default=1)
    args = parser.parse_args(argv)

    checkpoint = args.checkpoint or (
        REPO_ROOT / "traffic_control" / "ippo" / "models" / "ippo_v8_20tls_ep160.pt"
    )
    seeds = tuple(args.seeds) if args.seeds else PREREGISTERED_SEEDS
    preliminary = len(seeds) < 8
    if preliminary:
        print(
            f"WARNING: {len(seeds)} seed(s) < 8; results are PRELIMINARY only "
            "(pre-registered formal gate needs 10 seeds)",
            file=sys.stderr,
        )
    args.output_dir.mkdir(parents=True, exist_ok=True)

    report: dict[str, Any] = {
        "nature": "engineering_criterion_not_industry_standard",
        "preliminary": preliminary,
        "checkpoint": str(checkpoint),
        "seeds": list(seeds),
        "duration_s": args.duration,
        "period": args.period,
        "scenarios": {},
    }
    for scenario in args.scenarios:
        fixed_rows, model_rows = _run_scenario(
            scenario,
            seeds=seeds,
            duration=args.duration,
            period=args.period,
            checkpoint=checkpoint,
            action_interval=args.action_interval,
            workers=args.workers,
        )
        report["scenarios"][scenario] = {
            "verdicts": _verdicts(scenario, fixed_rows, model_rows),
            "runs": {"fixed": fixed_rows, "model": model_rows},
        }

    output_path = args.output_dir / f"gate_zero_shot_{'_'.join(args.scenarios)}.json"
    output_path.write_text(
        json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    print(f"gate report -> {output_path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
