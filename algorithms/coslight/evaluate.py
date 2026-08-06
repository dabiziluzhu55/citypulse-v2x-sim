"""Paired-seed evaluation for CoSLight and the official SUMO fixed controller."""

from __future__ import annotations

import argparse
import json
import logging
import math
import multiprocessing
import os
import random
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

from algorithms.coslight import controller  # noqa: E402
from algorithms.coslight.scope_cli import (  # noqa: E402
    build_scope_block, parse_intersections, resolve_scope,
)
from algorithms.presets import SCENARIO_PRESET_REGISTRY  # noqa: E402
from simulation.sumo.session import SimulationConfig, SimulationManager  # noqa: E402
from algorithms.evaluation.tripinfo_diagnostics import (  # noqa: E402
    parse_tripinfo_diagnostics,
    residual_mismatch,
)


logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(processName)s:%(name)s] %(levelname)s: %(message)s",
)
logger = logging.getLogger("coslight.evaluate")

DEFAULT_INTERSECTIONS = tuple(f"demo_{index}" for index in range(1, 21))
DEFAULT_METHODS = (
    "fixed",
    "max_pressure",
    "random",
    "untrained",
    "model",
    "stochastic_model",
)
MODEL_METHODS = {"model", "stochastic_model"}
SUMMARY_METRICS = (
    "arrived",
    "remaining",
    "waiting",
    "halting",
    "mean_speed",
    "hard_braking",
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


def _simulation_config(
    *,
    method: str,
    intersections: Sequence[str],
    period: str,
    duration: int,
    seed: int,
) -> SimulationConfig:
    algorithm = method != "fixed"
    return SimulationConfig(
        intersection_ids=tuple(intersections),
        period=period,
        duration_seconds=duration,
        control_mode="algorithm" if algorithm else "fixed",
        algorithm_transport="local",
        algorithm_module="algorithms.coslight" if algorithm else "",
        decision_interval=5.0,
        minimum_green=5.0,
        seed=seed,
        step_length=0.05,
    )


def _mean(values: Sequence[float]) -> float:
    return float(np.mean(values)) if values else 0.0


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
    os.environ["COSLIGHT_MODE"] = method
    os.environ["COSLIGHT_TOP_K"] = str(request["top_k"])
    os.environ["COSLIGHT_POLICY_SEED"] = str(request["policy_seed"])
    os.environ["COSLIGHT_REWARD_MODE"] = str(request["reward_mode"])
    os.environ["COSLIGHT_MAX_GREEN_FACTOR"] = str(
        request["max_green_factor"]
    )
    os.environ["COSLIGHT_PRESSURE_SHIELD_MARGIN"] = str(
        request["pressure_shield_margin"]
    )
    os.environ["COSLIGHT_RESIDUAL_MIN_BEST_PRESSURE"] = str(
        request["residual_min_best_pressure"]
    )
    os.environ["COSLIGHT_SWITCH_LOGIT_MARGIN"] = str(
        request["switch_logit_margin"]
    )
    os.environ["COSLIGHT_CLOUD_MODE"] = str(request["cloud_mode"])
    cloud_topology = str(request.get("cloud_topology") or "")
    if cloud_topology:
        os.environ["COSLIGHT_CLOUD_TOPOLOGY"] = cloud_topology
    else:
        os.environ.pop("COSLIGHT_CLOUD_TOPOLOGY", None)
    os.environ["COSLIGHT_CLOUD_UPDATE_INTERVAL"] = str(
        request["cloud_update_interval"]
    )
    os.environ["COSLIGHT_CLOUD_MAX_WEIGHT"] = str(request["cloud_max_weight"])
    os.environ["COSLIGHT_CLOUD_TARGET_QUEUE"] = str(
        request["cloud_target_queue"]
    )
    os.environ["COSLIGHT_CLOUD_SPILL_THRESHOLD"] = str(
        request["cloud_spill_threshold"]
    )
    os.environ["COSLIGHT_CLOUD_MIN_PLATOON_VEHICLES"] = str(
        request["cloud_min_platoon_vehicles"]
    )
    os.environ["COSLIGHT_CLOUD_PLATOON_LEAD"] = str(
        request["cloud_platoon_lead"]
    )
    os.environ["COSLIGHT_CLOUD_PLATOON_LAG"] = str(
        request["cloud_platoon_lag"]
    )
    os.environ["COSLIGHT_CLOUD_HOLD_COOLDOWN"] = str(
        request["cloud_hold_cooldown"]
    )
    os.environ["COSLIGHT_VEHICLE_GUIDANCE"] = str(request["vehicle_guidance"])
    checkpoint = str(request.get("checkpoint") or "")
    if checkpoint:
        os.environ["COSLIGHT_MODEL_PATH"] = checkpoint
    else:
        os.environ.pop("COSLIGHT_MODEL_PATH", None)
    os.environ.pop("COSLIGHT_RESUME_PATH", None)
    v2x_log = str(request.get("v2x_log") or "")
    if v2x_log:
        os.environ["COSLIGHT_V2X_LOG"] = v2x_log
        os.environ["COSLIGHT_V2X_RUN_ID"] = f"{method}-{seed}"
    else:
        os.environ.pop("COSLIGHT_V2X_LOG", None)
        os.environ.pop("COSLIGHT_V2X_RUN_ID", None)
    if request.get("v2x_collab"):
        os.environ["COSLIGHT_V2X_COLLAB"] = "1"
        os.environ["COSLIGHT_V2X_COLLAB_MODE"] = str(request["v2x_collab_mode"])
        os.environ["COSLIGHT_V2X_GUIDANCE_MODE"] = str(
            request["v2x_guidance_mode"])
        os.environ["COSLIGHT_V2X_SCOPE_SOURCE"] = str(request["scope_source"])
        os.environ["COSLIGHT_V2X_SCOPE_PRESET_ID"] = str(
            request.get("scope_preset_id") or "")
        os.environ["COSLIGHT_V2X_SCOPE_MANAGED_IDS"] = ",".join(
            str(iid) for iid in request["scope_managed_ids"])
    else:
        for key in (
            "COSLIGHT_V2X_COLLAB", "COSLIGHT_V2X_COLLAB_MODE",
            "COSLIGHT_V2X_GUIDANCE_MODE", "COSLIGHT_V2X_SCOPE_SOURCE",
            "COSLIGHT_V2X_SCOPE_PRESET_ID", "COSLIGHT_V2X_SCOPE_MANAGED_IDS",
        ):
            os.environ.pop(key, None)
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
        config = _simulation_config(
            method=method,
            intersections=request["intersection_ids"],
            period=str(request["period"]),
            duration=int(request["duration"]),
            seed=seed,
        )
        session_id = manager.start(config)
        snapshot = manager.wait(
            session_id,
            timeout=max(600.0, float(request["duration"]) * 3.0),
        )
        if snapshot.state != "COMPLETED":
            raise RuntimeError(
                f"SUMO state={snapshot.state} error={snapshot.error or ''}".strip()
            )
        tripinfo_path = manager.session_root / session_id / "tripinfo.xml"
        result = {
            "status": "complete",
            "method": method,
            "seed": seed,
            "session_id": session_id,
            "elapsed_s": time.monotonic() - started_at,
            **_snapshot_metrics(snapshot),
            **_parse_tripinfo(tripinfo_path),
        }
        result["residual_mismatch"] = residual_mismatch(
            result["unfinished_trips"], result["remaining"]
        )
        if method != "fixed":
            result["signal_execution"] = controller.signal_execution_diagnostics()
        if request.get("v2x_collab"):
            from algorithms.v2x.adapters.coslight import last_collab_summary
            collab_summary = last_collab_summary()
            if collab_summary is not None:
                result["collab"] = collab_summary.get("collab")
                result["scope"] = collab_summary.get("scope")
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


def _describe(values: Sequence[float]) -> dict:
    array = np.asarray(values, dtype=np.float64)
    return {
        "mean": float(array.mean()),
        "std": float(array.std()),
        "median": float(np.median(array)),
        "min": float(array.min()),
        "max": float(array.max()),
    }


def _summarize(rows: Sequence[Mapping[str, object]]) -> dict:
    complete = [row for row in rows if row.get("status", "complete") == "complete"]
    methods = list(dict.fromkeys(str(row["method"]) for row in complete))
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
        }
        if method != "fixed":
            fixed_by_seed = {
                int(row["seed"]): row
                for row in complete
                if row["method"] == "fixed"
            }
            pairs = [
                (row, fixed_by_seed[int(row["seed"])])
                for row in selected
                if int(row["seed"]) in fixed_by_seed
            ]
            if pairs:
                item["paired_vs_fixed"] = {
                    "pair_count": len(pairs),
                    "waiting_mean_delta": _mean(
                        [
                            float(row["waiting"]) - float(fixed["waiting"])
                            for row, fixed in pairs
                        ]
                    ),
                    "arrived_mean_delta": _mean(
                        [
                            float(row["arrived"]) - float(fixed["arrived"])
                            for row, fixed in pairs
                        ]
                    ),
                    "completed_duration_mean_delta_s": _mean(
                        [
                            float(row["completed_duration_mean_s"])
                            - float(fixed["completed_duration_mean_s"])
                            for row, fixed in pairs
                            if "completed_duration_mean_s" in row
                            and "completed_duration_mean_s" in fixed
                        ]
                    ),
                    "all_waiting_total_mean_delta_s": _mean(
                        [
                            float(row["all_waiting_total_s"])
                            - float(fixed["all_waiting_total_s"])
                            for row, fixed in pairs
                            if "all_waiting_total_s" in row
                            and "all_waiting_total_s" in fixed
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


def _positive(parser: argparse.ArgumentParser, name: str, value: int) -> None:
    if value <= 0:
        parser.error(f"{name} must be positive")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--methods", nargs="+", choices=DEFAULT_METHODS, default=DEFAULT_METHODS)
    parser.add_argument("--episodes", type=int, default=4, help="episodes per method")
    parser.add_argument("--workers", type=int, default=4)
    parser.add_argument("--duration", type=int, default=300)
    scope_group = parser.add_mutually_exclusive_group()
    scope_group.add_argument(
        "--scenario-preset",
        choices=sorted(SCENARIO_PRESET_REGISTRY),
        help="Select a predefined algorithm/collaboration intersection scope. "
             "Does not change the SUMO network or traffic demand.")
    scope_group.add_argument(
        "--intersections", type=parse_intersections, default=None,
        help="Comma-separated demo_N ids, or a single integer N for demo_1..N")
    parser.add_argument("--period", default="off_peak")
    parser.add_argument("--seed", type=int, default=10000)
    parser.add_argument("--policy-seed", type=int, default=42)
    parser.add_argument("--top-k", type=int, default=5)
    parser.add_argument(
        "--reward-mode", choices=controller.REWARD_MODES, default="pressure"
    )
    parser.add_argument(
        "--max-green-factor",
        type=float,
        default=controller.DEFAULT_MAX_GREEN_FACTOR,
    )
    parser.add_argument(
        "--pressure-shield-margin",
        type=float,
        default=controller.DEFAULT_PRESSURE_SHIELD_MARGIN,
        help="capacity-normalized MaxPressure regret bound; inf disables it",
    )
    parser.add_argument(
        "--residual-min-best-pressure",
        type=float,
        default=controller.DEFAULT_RESIDUAL_MIN_BEST_PRESSURE,
        help="fall back to MaxPressure at or below this best-action pressure; -inf disables it",
    )
    parser.add_argument(
        "--switch-logit-margin",
        type=float,
        default=controller.DEFAULT_SWITCH_LOGIT_MARGIN,
        help="hold low-confidence deterministic switches; -inf records only",
    )
    parser.add_argument("--vehicle-guidance", choices=("off", "rule"), default="off")
    parser.add_argument(
        "--cloud-mode",
        choices=(
            "off",
            "regional_rule",
            "platoon_shadow",
            "platoon_control",
            "platoon_hold_shadow",
            "platoon_hold_control",
            "platoon_hold_safe_shadow",
            "platoon_hold_safe_control",
        ),
        default="off",
    )
    parser.add_argument("--cloud-topology", type=Path)
    parser.add_argument("--cloud-update-interval", type=float, default=60.0)
    parser.add_argument("--cloud-max-weight", type=float, default=1.2)
    parser.add_argument("--cloud-target-queue", type=float, default=0.01)
    parser.add_argument("--cloud-spill-threshold", type=float, default=0.7)
    parser.add_argument("--cloud-min-platoon-vehicles", type=int, default=1)
    parser.add_argument("--cloud-platoon-lead", type=float, default=15.0)
    parser.add_argument("--cloud-platoon-lag", type=float, default=15.0)
    parser.add_argument("--cloud-hold-cooldown", type=float, default=0.0)
    parser.add_argument("--checkpoint", type=Path)
    parser.add_argument(
        "--v2x-log", type=Path, help="V2X shadow-mode 日志路径（JSONL）"
    )
    parser.add_argument(
        "--v2x-collab", action="store_true",
        help="启用车路云协同决策层（shadow 闭环；隐含启动 hub 与内存记录器）")
    parser.add_argument(
        "--v2x-collab-mode", choices=("off", "shadow", "active"),
        default="shadow", help="默认 shadow；active 在 v1 运行时不可用")
    parser.add_argument(
        "--v2x-guidance-mode", choices=("threshold", "full", "disabled"),
        default="threshold", help="RSI 发射模式（默认 threshold）")
    parser.add_argument(
        "--output",
        type=Path,
        default=Path(__file__).with_name("runs") / "evaluation.json",
    )
    args = parser.parse_args(argv)

    for name in ("episodes", "workers", "duration", "top_k"):
        _positive(parser, name, getattr(args, name))
    if not math.isfinite(args.max_green_factor) or args.max_green_factor < 0:
        parser.error("max-green-factor must be finite and non-negative")
    if math.isnan(args.pressure_shield_margin) or args.pressure_shield_margin < 0:
        parser.error("pressure-shield-margin must be non-negative or inf")
    if math.isnan(args.residual_min_best_pressure):
        parser.error("residual-min-best-pressure must not be NaN")
    if (
        math.isnan(args.switch_logit_margin)
        or args.switch_logit_margin == math.inf
        or (
            math.isfinite(args.switch_logit_margin)
            and args.switch_logit_margin < 0
        )
    ):
        parser.error(
            "switch-logit-margin must be -inf or finite and non-negative"
        )
    if args.v2x_collab and args.v2x_collab_mode == "active":
        parser.error(
            "--v2x-collab-mode active is unavailable in v1 (spec §4.1); "
            "use shadow or off")
    methods = tuple(dict.fromkeys(args.methods))
    checkpoint = args.checkpoint.expanduser().resolve() if args.checkpoint else None
    if MODEL_METHODS.intersection(methods):
        if checkpoint is None or not checkpoint.is_file():
            parser.error("model evaluation methods require an existing --checkpoint")
    cloud_topology = (
        args.cloud_topology.expanduser().resolve()
        if args.cloud_topology
        else None
    )
    if args.cloud_mode != "off":
        if not MODEL_METHODS.intersection(methods):
            parser.error("cloud evaluation requires a model evaluation method")
        if cloud_topology is None or not cloud_topology.is_file():
            parser.error("cloud evaluation requires an existing --cloud-topology")
    if not math.isfinite(args.cloud_update_interval) or args.cloud_update_interval <= 0:
        parser.error("cloud-update-interval must be finite and positive")
    if not math.isfinite(args.cloud_max_weight) or not 1 <= args.cloud_max_weight <= 2:
        parser.error("cloud-max-weight must be in [1, 2]")
    if not 0 <= args.cloud_target_queue < 1:
        parser.error("cloud-target-queue must be in [0, 1)")
    if not 0 < args.cloud_spill_threshold <= 1:
        parser.error("cloud-spill-threshold must be in (0, 1]")
    if args.cloud_min_platoon_vehicles < 1:
        parser.error("cloud-min-platoon-vehicles must be positive")
    if not math.isfinite(args.cloud_platoon_lead) or args.cloud_platoon_lead < 0:
        parser.error("cloud-platoon-lead must be finite and non-negative")
    if not math.isfinite(args.cloud_platoon_lag) or args.cloud_platoon_lag < 0:
        parser.error("cloud-platoon-lag must be finite and non-negative")
    if not math.isfinite(args.cloud_hold_cooldown) or args.cloud_hold_cooldown < 0:
        parser.error("cloud-hold-cooldown must be finite and non-negative")
    output = args.output.expanduser().resolve()
    if output.is_dir():
        parser.error(f"output must be a JSON file path, got directory: {output}")

    scope = resolve_scope(args.scenario_preset, args.intersections)
    intersections = scope.managed_ids
    seeds = tuple(range(args.seed + 1, args.seed + args.episodes + 1))
    jobs = [
        {
            "method": method,
            "seed": seed,
            "intersection_ids": intersections,
            "period": args.period,
            "duration": args.duration,
            "top_k": args.top_k,
            "policy_seed": args.policy_seed,
            "reward_mode": args.reward_mode,
            "max_green_factor": args.max_green_factor,
            "pressure_shield_margin": args.pressure_shield_margin,
            "residual_min_best_pressure": args.residual_min_best_pressure,
            "switch_logit_margin": args.switch_logit_margin,
            "vehicle_guidance": args.vehicle_guidance,
            "cloud_mode": (
                args.cloud_mode if method in MODEL_METHODS else "off"
            ),
            "cloud_topology": (
                str(cloud_topology)
                if method in MODEL_METHODS and cloud_topology is not None
                else ""
            ),
            "cloud_update_interval": args.cloud_update_interval,
            "cloud_max_weight": args.cloud_max_weight,
            "cloud_target_queue": args.cloud_target_queue,
            "cloud_spill_threshold": args.cloud_spill_threshold,
            "cloud_min_platoon_vehicles": args.cloud_min_platoon_vehicles,
            "cloud_platoon_lead": args.cloud_platoon_lead,
            "cloud_platoon_lag": args.cloud_platoon_lag,
            "cloud_hold_cooldown": args.cloud_hold_cooldown,
            "checkpoint": str(checkpoint) if method in MODEL_METHODS else "",
            "v2x_log": str(args.v2x_log) if args.v2x_log else "",
            "v2x_collab": args.v2x_collab,
            "v2x_collab_mode": (
                args.v2x_collab_mode if args.v2x_collab else "shadow"),
            "v2x_guidance_mode": (
                args.v2x_guidance_mode if args.v2x_collab else "threshold"),
            "scope_source": scope.source,
            "scope_preset_id": scope.preset_id,
            "scope_managed_ids": list(intersections),
        }
        for method in methods
        for seed in seeds
    ]
    logger.info(
        "CoSLight evaluation: methods=%s seeds=%s duration=%ds tls=%d workers=%d",
        list(methods),
        list(seeds),
        args.duration,
        len(intersections),
        min(args.workers, len(jobs)),
    )

    context = multiprocessing.get_context("spawn")
    rows = []
    with ProcessPoolExecutor(
        max_workers=min(args.workers, len(jobs)),
        mp_context=context,
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
                    "%s seed=%d arrived=%d remaining=%d waiting=%.1f "
                    "completed=%d trip=%.1fs",
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
            "top_k": args.top_k,
            "reward_mode": args.reward_mode,
            "max_green_factor": args.max_green_factor,
            "pressure_shield_margin": (
                args.pressure_shield_margin
                if math.isfinite(args.pressure_shield_margin)
                else None
            ),
            "residual_min_best_pressure": (
                args.residual_min_best_pressure
                if math.isfinite(args.residual_min_best_pressure)
                else None
            ),
            "switch_logit_margin": (
                args.switch_logit_margin
                if math.isfinite(args.switch_logit_margin)
                else None
            ),
            "vehicle_guidance": args.vehicle_guidance,
            "cloud_mode": args.cloud_mode,
            "cloud_topology": str(cloud_topology) if cloud_topology else None,
            "cloud_update_interval_s": args.cloud_update_interval,
            "cloud_max_weight": args.cloud_max_weight,
            "cloud_target_queue": args.cloud_target_queue,
            "cloud_spill_threshold": args.cloud_spill_threshold,
            "cloud_min_platoon_vehicles": args.cloud_min_platoon_vehicles,
            "cloud_platoon_lead_s": args.cloud_platoon_lead,
            "cloud_platoon_lag_s": args.cloud_platoon_lag,
            "cloud_hold_cooldown_s": args.cloud_hold_cooldown,
            "checkpoint": str(checkpoint) if checkpoint else None,
            "scope": build_scope_block(scope, DEFAULT_INTERSECTIONS),
            "v2x_collab": args.v2x_collab,
            "v2x_collab_mode": args.v2x_collab_mode,
            "v2x_guidance_mode": args.v2x_guidance_mode,
        },
        "summary": _summarize(rows),
        "runs": rows,
    }
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(
        json.dumps(report, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    failures = [row for row in rows if row["status"] != "complete"]
    logger.info("Evaluation report: %s", output)
    return 1 if failures else 0


if __name__ == "__main__":
    raise SystemExit(main())
