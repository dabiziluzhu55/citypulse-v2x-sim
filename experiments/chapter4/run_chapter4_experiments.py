#!/usr/bin/env python3
"""第四章附加实验一键批跑：复用 SimulationManager + SessionMetricsHub。

不修改 frontend / backend / simulation / traffic_control / traffic_eval。
"""

from __future__ import annotations

import argparse
import csv
import json
import math
import os
import queue
import statistics
import sys
import time
import traceback
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Mapping

_PROJECT_ROOT = Path(__file__).resolve().parents[2]
if str(_PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(_PROJECT_ROOT))

from backend.app.scenario.presets import require_scenario_preset
from simulation.sumo.building.artifacts import DEFAULT_GENERATED_DIR, PROJECT_ROOT
from simulation.sumo.engine.session import SimulationConfig, SimulationManager, SimulationSnapshot
from traffic_control.registry import list_control_modes, require_control_mode
from traffic_eval.session_hub import SessionMetricsHub

try:
    from experiments.chapter4.experiment_configs import (
        ALL_MODE_NAMES,
        CORE_METRIC_KEYS,
        DEFAULT_DECISION_INTERVAL,
        DEFAULT_DURATION_SECONDS,
        DEFAULT_SEED,
        DEFAULT_SNAPSHOT_INTERVAL,
        DEFAULT_STEP_LENGTH,
        EXPERIMENT_GROUPS,
        HIGHER_BETTER_METRICS,
        IMPROVEMENT_METRICS,
        LOWER_BETTER_METRICS,
        METRIC_LABELS_ZH,
        ROBUSTNESS_PAIRS,
        ExperimentGroup,
        ResolvedEvent,
        group_by_id,
        resolve_group_events,
        run_key,
    )
except ImportError:  # 直接 python experiments/chapter4/run_chapter4_experiments.py
    from experiment_configs import (
    ALL_MODE_NAMES,
    CORE_METRIC_KEYS,
    DEFAULT_DECISION_INTERVAL,
    DEFAULT_DURATION_SECONDS,
    DEFAULT_SEED,
    DEFAULT_SNAPSHOT_INTERVAL,
    DEFAULT_STEP_LENGTH,
    EXPERIMENT_GROUPS,
    HIGHER_BETTER_METRICS,
    IMPROVEMENT_METRICS,
    LOWER_BETTER_METRICS,
    METRIC_LABELS_ZH,
    ROBUSTNESS_PAIRS,
    ExperimentGroup,
    ResolvedEvent,
    group_by_id,
    resolve_group_events,
    run_key,
)

TERMINAL_STATES = frozenset({"COMPLETED", "STOPPED", "FAILED"})
STATUS_SUCCESS = "SUCCESS"
STATUS_FAILED = "FAILED"


def _ensure_sumo_env() -> None:
    os.environ.setdefault("SUMO_HOME", "/usr/share/sumo")
    sumo_home = Path(os.environ["SUMO_HOME"])
    sumo_bin = sumo_home / "bin"
    if sumo_bin.is_dir():
        path_entries = [
            entry
            for entry in os.environ.get("PATH", "").split(os.pathsep)
            if entry and entry != str(sumo_bin)
        ]
        os.environ["PATH"] = os.pathsep.join([*path_entries, str(sumo_bin)])


def parse_modes(raw: str) -> list[str]:
    text = (raw or "").strip()
    if not text:
        raise SystemExit("--modes 不能为空")
    if text.lower() == "all":
        modes = list(list_control_modes())
    else:
        modes = [item.strip() for item in text.split(",") if item.strip()]
    if not modes:
        raise SystemExit("--modes 不能为空")
    registry_order = list(list_control_modes())
    unknown = [mode for mode in modes if mode not in set(registry_order)]
    if unknown:
        raise SystemExit(
            f"不支持的 control_mode: {unknown}；registry={registry_order}"
        )
    for mode in modes:
        require_control_mode(mode)
    return [mode for mode in registry_order if mode in set(modes)]


def parse_groups(raw: str | None) -> list[ExperimentGroup]:
    mapping = group_by_id()
    if not raw or raw.strip().lower() == "all":
        return list(EXPERIMENT_GROUPS)
    requested = [item.strip() for item in raw.split(",") if item.strip()]
    unknown = [item for item in requested if item not in mapping]
    if unknown:
        raise SystemExit(
            f"未知实验组: {unknown}；允许 {list(mapping)}"
        )
    return [mapping[item] for item in requested]


def atomic_write_json(path: Path, payload: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    tmp.replace(path)


def load_json(path: Path, default: Any) -> Any:
    if not path.is_file():
        return default
    return json.loads(path.read_text(encoding="utf-8"))


def percentile(values: list[float], q: float) -> float:
    if not values:
        raise ValueError("empty values")
    ordered = sorted(values)
    if len(ordered) == 1:
        return ordered[0]
    rank = q * (len(ordered) - 1)
    low = int(math.floor(rank))
    high = int(math.ceil(rank))
    if low == high:
        return ordered[low]
    weight = rank - low
    return ordered[low] * (1.0 - weight) + ordered[high] * weight


def latency_stats(samples: list[float]) -> dict[str, Any]:
    valid = [float(item) for item in samples if item is not None]
    if not valid:
        return {
            "n": 0,
            "mean": None,
            "median": None,
            "p95": None,
            "max": None,
        }
    return {
        "n": len(valid),
        "mean": round(statistics.fmean(valid), 6),
        "median": round(statistics.median(valid), 6),
        "p95": round(percentile(valid, 0.95), 6),
        "max": round(max(valid), 6),
    }


def collect_event_lifecycle(snapshots: list[SimulationSnapshot]) -> dict[str, Any]:
    by_event: dict[str, dict[str, Any]] = {}
    for snap in snapshots:
        elapsed = float(snap.elapsed_seconds)
        for item in snap.events or ():
            event_id = str(item.event_id)
            bucket = by_event.setdefault(
                event_id,
                {
                    "event_id": event_id,
                    "event_type": item.event_type,
                    "states": [],
                    "errors": [],
                    "timeline": [],
                },
            )
            state = str(item.state)
            if state and (not bucket["states"] or bucket["states"][-1] != state):
                bucket["states"].append(state)
            if item.error:
                text = str(item.error)
                if text not in bucket["errors"]:
                    bucket["errors"].append(text)
            if not bucket["timeline"] or bucket["timeline"][-1]["state"] != state:
                bucket["timeline"].append(
                    {
                        "elapsed_seconds": elapsed,
                        "state": state,
                        "error": item.error,
                    }
                )
    return by_event


def format_progress(snap: SimulationSnapshot) -> str:
    duration = float(snap.duration_seconds) if snap.duration_seconds else 0.0
    elapsed = float(snap.elapsed_seconds)
    if snap.progress is not None:
        pct = float(snap.progress) * 100.0
    elif duration > 0:
        pct = min(100.0, elapsed / duration * 100.0)
    else:
        pct = 0.0
    sid = (snap.session_id or "")[:8] or "-"
    if duration > 0:
        time_part = f"{elapsed:.1f}/{duration:.0f}s"
    else:
        time_part = f"{elapsed:.1f}s"
    return f"  [{sid}] {snap.state}  {pct:5.1f}%  sim={time_part}"


def build_config(
    *,
    group: ExperimentGroup,
    control_mode: str,
    events: tuple[Any, ...],
    observer,
    seed: int,
) -> SimulationConfig:
    spec = require_control_mode(control_mode)
    preset = require_scenario_preset(group.preset)
    if not spec.allows_preset(group.preset):
        raise ValueError(
            f"{control_mode} does not allow preset {group.preset}"
        )
    return SimulationConfig(
        intersection_ids=preset.intersection_ids,
        period=group.period,
        scenario_preset_id=preset.preset_id,
        window_start_seconds=0.0,
        duration_seconds=DEFAULT_DURATION_SECONDS,
        flow_multiplier=1.0,
        control_mode=spec.kernel_mode,
        algorithm_transport=spec.algorithm_transport or "local",
        algorithm_module=spec.algorithm_module,
        decision_interval=DEFAULT_DECISION_INTERVAL,
        seed=int(seed),
        step_length=DEFAULT_STEP_LENGTH,
        gui=False,
        realtime=False,
        snapshot_interval_seconds=DEFAULT_SNAPSHOT_INTERVAL,
        initial_events=events,
        algorithm_decision_observer=observer,
    )


def run_one_episode(
    *,
    group: ExperimentGroup,
    control_mode: str,
    resolved_events: list[ResolvedEvent],
    manager: SimulationManager,
    session_root: Path,
    generated_dir: Path,
    seed: int,
) -> dict[str, Any]:
    latency_samples: list[float] = []

    def observer(payload: Mapping[str, Any]) -> None:
        value = payload.get("decision_latency_ms")
        if value is None:
            return
        latency_samples.append(float(value))

    spec = require_control_mode(control_mode)
    use_observer = spec.needs_algorithm
    config = build_config(
        group=group,
        control_mode=control_mode,
        events=tuple(item.event for item in resolved_events),
        observer=observer if use_observer else None,
        seed=seed,
    )
    hub = SessionMetricsHub(
        session_root=session_root,
        traffic_manifest_path=generated_dir / "manifests" / "traffic_manifest.json",
    )
    timeout = max(300.0, DEFAULT_DURATION_SECONDS * 20.0)
    t0 = time.perf_counter()
    session_id = ""
    subscription = None
    snapshots: list[SimulationSnapshot] = []
    try:
        session_id = manager.start(config)
        hub.start_session(session_id, control_mode)
        subscription = manager.subscribe(session_id)
        deadline = time.monotonic() + timeout
        final_snap: SimulationSnapshot | None = None
        last_progress_at = 0.0
        while time.monotonic() < deadline:
            try:
                snap = subscription.get(timeout=2.0)
            except queue.Empty:
                snap = manager.snapshot(session_id)
                if snap.state not in TERMINAL_STATES:
                    now = time.monotonic()
                    if now - last_progress_at >= 2.0:
                        print(f"\r{format_progress(snap):<100}", end="", file=sys.stderr, flush=True)
                        last_progress_at = now
                    continue
            snapshots.append(snap)
            if snap.state not in TERMINAL_STATES:
                hub.observe(snap)
                now = time.monotonic()
                if now - last_progress_at >= 2.0:
                    print(f"\r{format_progress(snap):<100}", end="", file=sys.stderr, flush=True)
                    last_progress_at = now
                continue
            hub.observe(snap)
            print(f"\r{format_progress(snap):<100}", file=sys.stderr, flush=True)
            final_snap = snap
            break
        else:
            try:
                manager.stop(session_id)
            except Exception:
                pass
            raise TimeoutError(
                f"Session {session_id} did not finish within {timeout:.0f}s"
            )

        assert final_snap is not None
        stats = latency_stats(latency_samples) if use_observer else {
            "n": 0,
            "mean": None,
            "median": None,
            "p95": None,
            "max": None,
        }
        mean_latency = stats["mean"] if use_observer else None
        result = hub.finalize(final_snap, decision_latency_ms=mean_latency)
        metrics = result.to_frontend_metrics()
        metrics["episode_id"] = session_id
        metrics["finished"] = True
        if not metrics.get("algorithm"):
            metrics["algorithm"] = control_mode
        if not use_observer:
            metrics["avg_decision_latency_ms"] = None

        lifecycle = collect_event_lifecycle(snapshots)
        failed_events = [
            item
            for item in lifecycle.values()
            if "FAILED" in item.get("states", [])
        ]
        error = str(final_snap.error) if final_snap.error else None
        status = STATUS_SUCCESS
        if final_snap.state != "COMPLETED":
            status = STATUS_FAILED
            error = error or f"session state={final_snap.state}"
        elif failed_events:
            status = STATUS_FAILED
            error = error or (
                "disturbance event FAILED: "
                + "; ".join(
                    f"{item['event_id']} {item.get('errors')}"
                    for item in failed_events
                )
            )

        return {
            "experiment_id": group.experiment_id,
            "algorithm": control_mode,
            "seed": seed,
            "run_key": run_key(group.experiment_id, control_mode, seed),
            "session_id": session_id,
            "state": str(final_snap.state),
            "status": status,
            "error": error,
            "elapsed_wall_s": time.perf_counter() - t0,
            "metrics": metrics,
            "decision_latency": {
                "applicable": use_observer,
                "display": "—" if not use_observer else stats["mean"],
                **stats,
            },
            "event_lifecycle": lifecycle,
            "warnings": list(metrics.get("warnings") or []),
        }
    except Exception as exc:
        if session_id:
            try:
                manager.stop(session_id)
            except Exception:
                pass
            try:
                hub.abort_without_snapshot(session_id)
            except Exception:
                pass
        return {
            "experiment_id": group.experiment_id,
            "algorithm": control_mode,
            "seed": seed,
            "run_key": run_key(group.experiment_id, control_mode, seed),
            "session_id": session_id,
            "state": "FAILED",
            "status": STATUS_FAILED,
            "error": f"{exc}\n{traceback.format_exc()}",
            "elapsed_wall_s": time.perf_counter() - t0,
            "metrics": {"algorithm": control_mode},
            "decision_latency": {
                "applicable": spec.needs_algorithm,
                "display": "—" if not spec.needs_algorithm else None,
                "n": len(latency_samples),
                "mean": None,
                "median": None,
                "p95": None,
                "max": None,
            },
            "event_lifecycle": collect_event_lifecycle(snapshots),
            "warnings": [],
        }
    finally:
        if subscription is not None:
            try:
                subscription.close()
            except Exception:
                pass


def _metric_value(row: Mapping[str, Any], key: str) -> Any:
    metrics = row.get("metrics") or {}
    if key == "avg_decision_latency_ms":
        latency = row.get("decision_latency") or {}
        if not latency.get("applicable", True):
            return None
    return metrics.get(key)


def _as_float(value: Any) -> float | None:
    if value is None or value == "" or value == "—":
        return None
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    if math.isnan(number):
        return None
    return number


def improvement_pct(
    metric: str,
    baseline: Any,
    value: Any,
) -> tuple[Any, str | None]:
    base = _as_float(baseline)
    current = _as_float(value)
    if base is None or current is None:
        return "", "missing baseline or algorithm value"
    if abs(base) < 1e-12:
        return "", "baseline is zero; skip divide"
    if metric in LOWER_BETTER_METRICS:
        return round((base - current) / base * 100.0, 4), None
    if metric in HIGHER_BETTER_METRICS:
        return round((current - base) / base * 100.0, 4), None
    return "", f"metric {metric} is not in improvement set"


def write_csv(path: Path, rows: list[dict[str, Any]], fieldnames: list[str]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=fieldnames, extrasaction="ignore")
        writer.writeheader()
        for row in rows:
            writer.writerow(row)


def build_summary_rows(results: list[dict[str, Any]]) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for item in results:
        latency = item.get("decision_latency") or {}
        row = {
            "experiment_id": item.get("experiment_id"),
            "algorithm": item.get("algorithm"),
            "seed": item.get("seed"),
            "status": item.get("status"),
            "session_id": item.get("session_id"),
            "decision_latency_n": latency.get("n"),
            "decision_latency_mean_ms": (
                "—" if not latency.get("applicable", True) else latency.get("mean")
            ),
            "decision_latency_median_ms": (
                "—" if not latency.get("applicable", True) else latency.get("median")
            ),
            "decision_latency_p95_ms": (
                "—" if not latency.get("applicable", True) else latency.get("p95")
            ),
            "decision_latency_max_ms": (
                "—" if not latency.get("applicable", True) else latency.get("max")
            ),
        }
        for key in CORE_METRIC_KEYS:
            value = _metric_value(item, key)
            if key == "avg_decision_latency_ms" and not latency.get("applicable", True):
                row[key] = "—"
            else:
                row[key] = value
        rows.append(row)
    return rows


def build_improvement_rows(results: list[dict[str, Any]]) -> tuple[list[dict[str, Any]], list[str]]:
    by_group: dict[str, dict[str, dict[str, Any]]] = {}
    for item in results:
        by_group.setdefault(str(item["experiment_id"]), {})[str(item["algorithm"])] = item
    rows: list[dict[str, Any]] = []
    warnings: list[str] = []
    for experiment_id, algorithms in by_group.items():
        fixed = algorithms.get("fixed")
        if fixed is None:
            warnings.append(f"{experiment_id}: missing fixed baseline")
            continue
        for algorithm, item in algorithms.items():
            row = {
                "experiment_id": experiment_id,
                "algorithm": algorithm,
                "status": item.get("status"),
            }
            for metric in IMPROVEMENT_METRICS:
                if algorithm == "fixed":
                    row[f"{metric}_improvement_pct"] = 0.0
                    row[f"{metric}_warning"] = ""
                    continue
                pct, warning = improvement_pct(
                    metric,
                    _metric_value(fixed, metric),
                    _metric_value(item, metric),
                )
                row[f"{metric}_improvement_pct"] = pct
                row[f"{metric}_warning"] = warning or ""
                if warning:
                    warnings.append(f"{experiment_id}/{algorithm}/{metric}: {warning}")
            rows.append(row)
    return rows, warnings


def build_robustness_rows(results: list[dict[str, Any]]) -> tuple[list[dict[str, Any]], list[str]]:
    by_key = {
        (str(item["experiment_id"]), str(item["algorithm"])): item
        for item in results
    }
    rows: list[dict[str, Any]] = []
    warnings: list[str] = []
    for disturbed_id, baseline_id in ROBUSTNESS_PAIRS:
        algorithms = sorted(
            {
                str(item["algorithm"])
                for item in results
                if item["experiment_id"] in {disturbed_id, baseline_id}
            }
        )
        for algorithm in algorithms:
            baseline = by_key.get((baseline_id, algorithm))
            disturbed = by_key.get((disturbed_id, algorithm))
            if baseline is None or disturbed is None:
                continue
            row = {
                "algorithm": algorithm,
                "baseline_group": baseline_id,
                "disturbed_group": disturbed_id,
                "baseline_status": baseline.get("status"),
                "disturbed_status": disturbed.get("status"),
            }
            for metric in IMPROVEMENT_METRICS:
                base_value = _metric_value(baseline, metric)
                dist_value = _metric_value(disturbed, metric)
                row[f"{metric}_baseline"] = base_value
                row[f"{metric}_disturbed"] = dist_value
                base_num = _as_float(base_value)
                dist_num = _as_float(dist_value)
                if base_num is None or dist_num is None:
                    row[f"{metric}_delta"] = ""
                    row[f"{metric}_change_pct"] = ""
                    row[f"{metric}_warning"] = "missing value"
                    warnings.append(
                        f"{algorithm} {disturbed_id} vs {baseline_id} / {metric}: missing value"
                    )
                    continue
                row[f"{metric}_delta"] = round(dist_num - base_num, 6)
                if abs(base_num) < 1e-12:
                    row[f"{metric}_change_pct"] = ""
                    row[f"{metric}_warning"] = "baseline is zero; skip divide"
                    warnings.append(
                        f"{algorithm} {disturbed_id} vs {baseline_id} / {metric}: baseline is zero"
                    )
                else:
                    row[f"{metric}_change_pct"] = round(
                        (dist_num - base_num) / base_num * 100.0, 4
                    )
                    row[f"{metric}_warning"] = ""
            rows.append(row)
    return rows, warnings


def _fmt_cell(value: Any) -> str:
    if value is None or value == "":
        return "—"
    if isinstance(value, float):
        return f"{value:.4g}" if abs(value) < 1 else f"{value:.2f}"
    return str(value)


def write_summary_md(
    path: Path,
    *,
    results: list[dict[str, Any]],
    improvement_rows: list[dict[str, Any]],
    warnings: list[str],
) -> None:
    by_group: dict[str, list[dict[str, Any]]] = {}
    for item in results:
        by_group.setdefault(str(item["experiment_id"]), []).append(item)
    mapping = group_by_id()
    lines = [
        "# 第四章附加实验结果",
        "",
        "本文件由 `experiments/chapter4/run_chapter4_experiments.py` 自动生成，可直接复制到 Word。",
        "",
    ]
    table_metrics = [
        "path_avg_speed_kmh",
        "avg_travel_time",
        "avg_waiting_time",
        "throughput",
        "regional_max_queue_length_m",
        "spillback_rate",
        "hard_braking_rate",
        "fuel_consumption",
        "travel_time_index",
        "delay_time_proportion",
        "traffic_performance_index",
        "avg_decision_latency_ms",
    ]
    for experiment_id in [group.experiment_id for group in EXPERIMENT_GROUPS]:
        rows = by_group.get(experiment_id)
        if not rows:
            continue
        group = mapping[experiment_id]
        lines.append(f"## {experiment_id} {group.label}")
        lines.append("")
        headers = ["算法", "状态", *[METRIC_LABELS_ZH[key] for key in table_metrics]]
        lines.append("| " + " | ".join(headers) + " |")
        lines.append("| " + " | ".join("---" for _ in headers) + " |")
        for item in rows:
            cells = [item.get("algorithm"), item.get("status")]
            for key in table_metrics:
                cells.append(_fmt_cell(_metric_value(item, key)))
            lines.append("| " + " | ".join(str(cell) for cell in cells) + " |")
        lines.append("")

    cov2x_rows = [row for row in improvement_rows if row.get("algorithm") == "cov2x"]
    if cov2x_rows:
        lines.append("## CoV2X 相对 Fixed 的改善百分比")
        lines.append("")
        lines.append("越小越好：`(fixed-algorithm)/fixed*100%`；越大越好：`(algorithm-fixed)/fixed*100%`。")
        lines.append("")
        headers = ["实验组", *[METRIC_LABELS_ZH[key] for key in IMPROVEMENT_METRICS]]
        lines.append("| " + " | ".join(headers) + " |")
        lines.append("| " + " | ".join("---" for _ in headers) + " |")
        for row in cov2x_rows:
            cells = [row["experiment_id"]]
            for metric in IMPROVEMENT_METRICS:
                cells.append(_fmt_cell(row.get(f"{metric}_improvement_pct")))
            lines.append("| " + " | ".join(str(cell) for cell in cells) + " |")
        lines.append("")

    if warnings:
        lines.append("## Warnings")
        lines.append("")
        for item in warnings:
            lines.append(f"- {item}")
        lines.append("")
    path.write_text("\n".join(lines), encoding="utf-8")


def merge_manifest(existing: Mapping[str, Any] | None, incoming: dict[str, Any]) -> dict[str, Any]:
    if not existing:
        return incoming
    experiments = {
        str(item.get("experiment_id")): item
        for item in existing.get("experiments") or []
        if item.get("experiment_id")
    }
    for item in incoming.get("experiments") or []:
        experiments[str(item["experiment_id"])] = item
    modes = list(
        dict.fromkeys(
            [*(existing.get("modes") or []), *(incoming.get("modes") or [])]
        )
    )
    mode_resolution = dict(existing.get("mode_resolution") or {})
    mode_resolution.update(incoming.get("mode_resolution") or {})
    merged = dict(incoming)
    merged["modes"] = modes
    merged["mode_resolution"] = mode_resolution
    merged["experiments"] = list(experiments.values())
    merged["planned_runs"] = len(experiments) * len(modes)
    return merged


def write_all_outputs(
    output_dir: Path,
    *,
    manifest: dict[str, Any],
    results: list[dict[str, Any]],
    statuses: list[dict[str, Any]],
) -> None:
    atomic_write_json(output_dir / "experiment_manifest.json", manifest)
    atomic_write_json(output_dir / "results_raw.json", {"results": results})
    atomic_write_json(output_dir / "run_status.json", {"runs": statuses})

    summary_rows = build_summary_rows(results)
    summary_fields = list(summary_rows[0].keys()) if summary_rows else [
        "experiment_id",
        "algorithm",
        "seed",
        "status",
    ]
    write_csv(output_dir / "results_summary.csv", summary_rows, summary_fields)

    improvement_rows, improvement_warnings = build_improvement_rows(results)
    improvement_fields = (
        ["experiment_id", "algorithm", "status"]
        + [f"{metric}_improvement_pct" for metric in IMPROVEMENT_METRICS]
        + [f"{metric}_warning" for metric in IMPROVEMENT_METRICS]
    )
    write_csv(output_dir / "improvement_vs_fixed.csv", improvement_rows, improvement_fields)

    robustness_rows, robustness_warnings = build_robustness_rows(results)
    robustness_fields = [
        "algorithm",
        "baseline_group",
        "disturbed_group",
        "baseline_status",
        "disturbed_status",
    ]
    for metric in IMPROVEMENT_METRICS:
        robustness_fields.extend(
            [
                f"{metric}_baseline",
                f"{metric}_disturbed",
                f"{metric}_delta",
                f"{metric}_change_pct",
                f"{metric}_warning",
            ]
        )
    write_csv(output_dir / "robustness_vs_normal.csv", robustness_rows, robustness_fields)

    write_summary_md(
        output_dir / "summary.md",
        results=results,
        improvement_rows=improvement_rows,
        warnings=improvement_warnings + robustness_warnings,
    )


def build_manifest(
    *,
    output_dir: Path,
    modes: list[str],
    groups: list[ExperimentGroup],
    resolved_by_group: dict[str, list[ResolvedEvent]],
    seed: int,
) -> dict[str, Any]:
    experiments = []
    for group in groups:
        preset = require_scenario_preset(group.preset)
        resolved = resolved_by_group.get(group.experiment_id, [])
        experiments.append(
            {
                "experiment_id": group.experiment_id,
                "label": group.label,
                "preset": group.preset,
                "period": group.period,
                "intersection_ids": list(preset.intersection_ids),
                "duration_seconds": DEFAULT_DURATION_SECONDS,
                "seed": seed,
                "step_length": DEFAULT_STEP_LENGTH,
                "decision_interval": DEFAULT_DECISION_INTERVAL,
                "gui": False,
                "realtime": False,
                "events": [item.to_manifest() for item in resolved],
            }
        )
    return {
        "created_at": datetime.now(timezone.utc).isoformat(),
        "output_dir": str(output_dir),
        "defaults": {
            "duration_seconds": DEFAULT_DURATION_SECONDS,
            "seed": seed,
            "step_length": DEFAULT_STEP_LENGTH,
            "decision_interval": DEFAULT_DECISION_INTERVAL,
            "snapshot_interval_seconds": DEFAULT_SNAPSHOT_INTERVAL,
            "gui": False,
            "realtime": False,
        },
        "modes": modes,
        "mode_resolution": {
            mode: {
                "kernel_mode": require_control_mode(mode).kernel_mode,
                "algorithm_module": require_control_mode(mode).algorithm_module,
            }
            for mode in modes
        },
        "experiments": experiments,
        "planned_runs": len(groups) * len(modes),
    }


def print_matrix(
    groups: list[ExperimentGroup],
    modes: list[str],
    resolved_by_group: dict[str, list[ResolvedEvent]],
    seed: int,
) -> None:
    print("======== Chapter 4 experiment matrix ========")
    print(
        f"defaults: duration={DEFAULT_DURATION_SECONDS}s seed={seed} "
        f"step_length={DEFAULT_STEP_LENGTH}s decision_interval={DEFAULT_DECISION_INTERVAL}s "
        f"gui=False realtime=False"
    )
    print(f"modes ({len(modes)}): {','.join(modes)}")
    print(f"groups ({len(groups)}): {','.join(group.experiment_id for group in groups)}")
    print(f"planned runs: {len(groups) * len(modes)}")
    print("")
    for group in groups:
        preset = require_scenario_preset(group.preset)
        print(f"[{group.experiment_id}] {group.label}")
        print(f"  preset={group.preset} period={group.period}")
        print(f"  intersections={list(preset.intersection_ids)}")
        resolved = resolved_by_group.get(group.experiment_id, [])
        if not resolved:
            print("  events: none")
            continue
        for item in resolved:
            print(
                f"  event {item.event_id}: {item.event_type} "
                f"{item.start_seconds:g}~{item.end_seconds:g}s "
                f"intersection={item.intersection_id} lane={item.lane_id} "
                f"original_speed={item.original_max_speed_kmh:g}km/h "
                f"params={item.parameters}"
            )
        print("")


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="第四章附加实验一键批跑（不启动 Backend）",
    )
    parser.add_argument(
        "--modes",
        default="all",
        help="逗号分隔 control_mode，或 all（从 traffic_control.registry 读取）",
    )
    parser.add_argument(
        "--groups",
        default="all",
        help="逗号分隔实验组，或 all。允许 B1,B2,C1,C2,C3,C4",
    )
    parser.add_argument(
        "--output-dir",
        default="",
        help="输出目录；默认 outputs/chapter4_experiments/<timestamp>/",
    )
    parser.add_argument(
        "--force",
        action="store_true",
        help="忽略已有 SUCCESS，强制重跑",
    )
    parser.add_argument(
        "--list-only",
        action="store_true",
        help="只打印实验矩阵和次数，不启动 SUMO",
    )
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    modes = parse_modes(args.modes)
    groups = parse_groups(args.groups)
    seed = DEFAULT_SEED
    unexpected = [mode for mode in modes if mode not in ALL_MODE_NAMES]
    if unexpected:
        print(f"warning: modes not in chapter-4 default set: {unexpected}", file=sys.stderr)

    _ensure_sumo_env()
    generated_dir = DEFAULT_GENERATED_DIR
    manager = SimulationManager(generated_dir=generated_dir)
    catalog = manager.catalog()
    resolved_by_group: dict[str, list[ResolvedEvent]] = {}
    for group in groups:
        resolved_by_group[group.experiment_id] = resolve_group_events(
            group,
            catalog,
            generated_dir=generated_dir,
        )

    print_matrix(groups, modes, resolved_by_group, seed)
    if args.list_only:
        return 0

    if args.output_dir:
        output_dir = Path(args.output_dir)
        if not output_dir.is_absolute():
            output_dir = PROJECT_ROOT / output_dir
    else:
        stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        output_dir = PROJECT_ROOT / "outputs" / "chapter4_experiments" / stamp
    output_dir.mkdir(parents=True, exist_ok=True)
    session_root = output_dir / "sessions"
    session_root.mkdir(parents=True, exist_ok=True)

    existing_manifest = load_json(output_dir / "experiment_manifest.json", {})
    manifest = merge_manifest(
        existing_manifest,
        build_manifest(
            output_dir=output_dir,
            modes=modes,
            groups=groups,
            resolved_by_group=resolved_by_group,
            seed=seed,
        ),
    )
    existing_raw = load_json(output_dir / "results_raw.json", {"results": []})
    existing_status = load_json(output_dir / "run_status.json", {"runs": []})
    results_by_key = {
        str(item.get("run_key")): item
        for item in existing_raw.get("results") or []
        if item.get("run_key")
    }
    status_by_key = {
        str(item.get("run_key")): item
        for item in existing_status.get("runs") or []
        if item.get("run_key")
    }

    print(f"output: {output_dir}")
    for group in groups:
        for mode in modes:
            key = run_key(group.experiment_id, mode, seed)
            previous = status_by_key.get(key)
            if (
                not args.force
                and previous
                and previous.get("status") == STATUS_SUCCESS
            ):
                print(f"SKIP {key} (already SUCCESS)")
                continue
            print(f"RUN  {key}")
            result = run_one_episode(
                group=group,
                control_mode=mode,
                resolved_events=resolved_by_group[group.experiment_id],
                manager=manager,
                session_root=session_root,
                generated_dir=generated_dir,
                seed=seed,
            )
            results_by_key[key] = result
            status_by_key[key] = {
                "run_key": key,
                "experiment_id": group.experiment_id,
                "algorithm": mode,
                "seed": seed,
                "status": result["status"],
                "session_id": result.get("session_id"),
                "error": result.get("error"),
                "elapsed_wall_s": result.get("elapsed_wall_s"),
            }
            if result["status"] == STATUS_FAILED:
                print(f"FAIL {key}: {result.get('error')}", file=sys.stderr)
            else:
                print(f"OK   {key} session={result.get('session_id')}")
            write_all_outputs(
                output_dir,
                manifest=manifest,
                results=list(results_by_key.values()),
                statuses=list(status_by_key.values()),
            )

    write_all_outputs(
        output_dir,
        manifest=manifest,
        results=list(results_by_key.values()),
        statuses=list(status_by_key.values()),
    )
    failed = [
        item for item in status_by_key.values() if item.get("status") == STATUS_FAILED
    ]
    print(f"done. output={output_dir} failed={len(failed)}/{len(status_by_key)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
