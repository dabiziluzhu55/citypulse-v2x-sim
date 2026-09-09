"""Dataset summary statistics written after generate/select/build-sft."""

from __future__ import annotations

from collections import Counter, defaultdict
from pathlib import Path
from typing import Any, Mapping, Sequence


TRAFFIC_EVAL_KEYS = (
    "path_avg_speed_kmh",
    "travel_time_index",
    "delay_time_proportion",
    "traffic_performance_index",
    "avg_stops_per_vehicle",
    "regional_max_queue_length_m",
    "spillback_rate",
    "fuel_intensity_L_per_100km",
    "avg_travel_time_s",
    "avg_waiting_time_s",
    "avg_queue_length_veh",
    "throughput_veh_per_h",
    "hard_braking_events",
    "hard_braking_rate",
    "completion_rate",
    "avg_decision_latency_ms",
)


def _mean(values: Sequence[Any]) -> float | None:
    present = []
    for item in values:
        if item is None:
            continue
        try:
            present.append(float(item))
        except (TypeError, ValueError):
            continue
    if not present:
        return None
    return sum(present) / len(present)


def _percentile(values: Sequence[float], pct: float) -> float | None:
    if not values:
        return None
    ordered = sorted(float(item) for item in values)
    if len(ordered) == 1:
        return ordered[0]
    rank = (len(ordered) - 1) * (pct / 100.0)
    lo = int(rank)
    hi = min(lo + 1, len(ordered) - 1)
    frac = rank - lo
    return ordered[lo] * (1.0 - frac) + ordered[hi] * frac


def _stats(values: Sequence[Any]) -> dict[str, float | None]:
    present = []
    for item in values:
        if item is None:
            continue
        try:
            present.append(float(item))
        except (TypeError, ValueError):
            continue
    return {
        "n": len(present),
        "mean": _mean(present),
        "median": _percentile(present, 50),
        "p95": _percentile(present, 95),
        "min": min(present) if present else None,
        "max": max(present) if present else None,
    }


def file_size(path: Path) -> int:
    if not path.is_file():
        return 0
    return int(path.stat().st_size)


def dir_size(path: Path) -> int:
    if not path.exists():
        return 0
    if path.is_file():
        return int(path.stat().st_size)
    total = 0
    for item in path.rglob("*"):
        if item.is_file():
            total += item.stat().st_size
    return total


def _local_vs_global_conflict(run: Mapping[str, Any]) -> bool | None:
    local = dict(run.get("local_event_window") or {})
    global_w = dict(run.get("event_window") or {})
    pairs = (
        ("local_avg_queue_veh", "window_avg_queue_veh", True),
        ("local_spillback_pct", "window_spillback_pct", True),
        ("local_mean_speed_mps", "window_mean_speed_mps", False),
    )
    signs = []
    for local_key, global_key, higher_worse in pairs:
        a = local.get(local_key)
        b = global_w.get(global_key)
        try:
            if a is None or b is None:
                continue
            fa = float(a)
            fb = float(b)
        except (TypeError, ValueError):
            continue
        if abs(fa) + abs(fb) < 1e-9:
            continue
        # conflict: local looks worse while global looks better, or vice versa,
        # using relative gap of 25% when both positive.
        if fb == 0:
            continue
        ratio = fa / fb if fb else None
        if ratio is None:
            continue
        if higher_worse and ratio >= 1.5:
            signs.append(True)
        elif (not higher_worse) and 0 < ratio <= 0.67:
            signs.append(True)
        else:
            signs.append(False)
    if not signs:
        return None
    return any(signs)


def summarize_dataset(
    *,
    scenarios: Sequence[Mapping[str, Any]],
    runs: Sequence[Mapping[str, Any]],
    selections: Sequence[Mapping[str, Any]],
    sft_counts: Mapping[str, int],
    ambiguous: Sequence[Mapping[str, Any]],
    rejected: Sequence[Mapping[str, Any]],
) -> dict[str, Any]:
    n_scenarios = len(scenarios)
    event_types = Counter(item["event"]["event_type"] for item in scenarios)
    periods = Counter(item["period"] for item in scenarios)
    scopes = Counter(item["scope"] for item in scenarios)
    seeds = Counter(item["seed"] for item in scenarios)
    intersections = Counter(
        item["event"]["intersection_id"] for item in scenarios if item.get("event")
    )
    by_mode: dict[str, list[Mapping[str, Any]]] = defaultdict(list)
    for run in runs:
        by_mode[str(run.get("control_mode"))].append(run)
    success_by_mode = {
        mode: {
            "n": len(items),
            "completed": sum(1 for item in items if item.get("state") == "COMPLETED"),
            "failed": sum(1 for item in items if item.get("state") != "COMPLETED"),
        }
        for mode, items in by_mode.items()
    }
    expert_counts = Counter(
        item.get("winner") for item in selections if item.get("winner")
    )
    n_select = max(1, sum(1 for item in selections if item.get("winner")))
    fallback_n = sum(1 for item in selections if item.get("fallback_to_baseline"))
    action_spaces = Counter(str(run.get("teacher_action_space")) for run in runs)
    metric_means: dict[str, dict[str, float | None]] = {}
    for mode, items in by_mode.items():
        metric_means[mode] = {
            key: _mean([(item.get("traffic_eval") or {}).get(key) for item in items])
            for key in TRAFFIC_EVAL_KEYS
        }
    by_event: dict[str, dict[str, Any]] = {}
    scenario_by_id = {item["scenario_id"]: item for item in scenarios}
    for run in runs:
        event_type = (scenario_by_id.get(run.get("scenario_id")) or {}).get("event", {}).get("event_type")
        if not event_type:
            continue
        slot = by_event.setdefault(event_type, defaultdict(list))
        slot[run.get("control_mode")].append(
            (run.get("traffic_eval") or {}).get("traffic_performance_index")
        )
    event_tpi = {
        event_type: {
            mode: _mean(values) for mode, values in modes.items()
        }
        for event_type, modes in by_event.items()
    }
    expert_by_event: dict[str, Counter[str]] = defaultdict(Counter)
    expert_by_period: dict[str, Counter[str]] = defaultdict(Counter)
    expert_by_scope: dict[str, Counter[str]] = defaultdict(Counter)
    for selection in selections:
        winner = selection.get("winner")
        if not winner:
            continue
        spec = scenario_by_id.get(selection.get("scenario_id")) or {}
        expert_by_event[str(spec.get("event", {}).get("event_type") or "unknown")][str(winner)] += 1
        expert_by_period[str(spec.get("period") or "unknown")][str(winner)] += 1
        expert_by_scope[str(spec.get("scope") or "unknown")][str(winner)] += 1

    completion_by_mode = {
        mode: _stats([(item.get("traffic_eval") or {}).get("completion_rate") for item in items])
        for mode, items in by_mode.items()
    }
    gated = 0
    gated_modes = Counter()
    for selection in selections:
        modes = selection.get("trip_reliability_gated_modes") or []
        if modes:
            gated += 1
        for mode in modes:
            gated_modes[str(mode)] += 1
    vehicle_blocked = sum(
        1
        for item in selections
        if item.get("winner") == "cov2x"
        and not item.get("signal_sft_eligible", True)
    )
    cov2x_wins = sum(1 for item in selections if item.get("winner") == "cov2x")
    conflicts = [run for run in runs if _local_vs_global_conflict(run)]
    monopoly = None
    if expert_counts:
        top_mode, top_n = expert_counts.most_common(1)[0]
        share = top_n / n_select
        monopoly = {
            "mode": top_mode,
            "count": top_n,
            "share": share,
            "flag": share >= 0.80 and n_select >= 5,
        }
    phase_hist: Counter[int] = Counter()
    n_controlled: list[int] = []
    return {
        "n_scenarios": n_scenarios,
        "n_success_scenarios": len({run["scenario_id"] for run in runs if run.get("state") == "COMPLETED"}),
        "n_failed_scenarios": n_scenarios
        - len({run["scenario_id"] for run in runs if run.get("state") == "COMPLETED"}),
        "event_type_counts": dict(event_types),
        "period_counts": dict(periods),
        "scope_counts": dict(scopes),
        "seed_counts": dict(seeds),
        "intersection_counts": dict(intersections),
        "algorithm_success": success_by_mode,
        "expert_counts": dict(expert_counts),
        "expert_share": {
            mode: count / n_select for mode, count in expert_counts.items()
        },
        "expert_counts_by_event_type": {
            key: dict(value) for key, value in expert_by_event.items()
        },
        "expert_counts_by_period": {
            key: dict(value) for key, value in expert_by_period.items()
        },
        "expert_counts_by_scope": {
            key: dict(value) for key, value in expert_by_scope.items()
        },
        "fixed_as_best_share": expert_counts.get("fixed", 0) / n_select,
        "fallback_to_baseline_share": fallback_n / max(1, len(selections)),
        "ambiguous_share": len(ambiguous) / max(1, len(selections)),
        "cov2x_wins": cov2x_wins,
        "cov2x_vehicle_action_blocks_signal_sft": vehicle_blocked,
        "cov2x_vehicle_action_block_share": (
            vehicle_blocked / cov2x_wins if cov2x_wins else 0.0
        ),
        "action_space_counts": dict(action_spaces),
        "n_high_confidence": sum(
            1
            for item in selections
            if item.get("winner") and not item.get("ambiguous") and item.get("signal_sft_eligible")
        ),
        "n_ambiguous": len(ambiguous),
        "n_rejected": len(rejected),
        "sft_counts": dict(sft_counts),
        "traffic_eval_means": metric_means,
        "tpi_by_event_and_algorithm": event_tpi,
        "completion_rate_by_algorithm": completion_by_mode,
        "trip_reliability_gate_scenario_share": gated / max(1, len(selections)),
        "trip_reliability_gated_modes": dict(gated_modes),
        "local_vs_global_conflict_runs": len(conflicts),
        "local_vs_global_conflict_share": len(conflicts) / max(1, len(runs)),
        "expert_monopoly": monopoly,
        "avg_plan_intersections": _mean(n_controlled),
        "phase_histogram": dict(phase_hist),
    }


def wall_and_disk_report(
    *,
    scenarios: Sequence[Mapping[str, Any]],
    runs: Sequence[Mapping[str, Any]],
    output_dir: Path,
    workers: int = 1,
    full_grid_episodes: int = 8748,
    target_episodes: int | None = None,
) -> dict[str, Any]:
    scenario_by_id = {item["scenario_id"]: item for item in scenarios}
    completed = [run for run in runs if run.get("state") == "COMPLETED"]
    walls = [float(run.get("elapsed_wall_s") or 0.0) for run in completed]
    by_mode: dict[str, list[float]] = defaultdict(list)
    by_scope: dict[str, list[float]] = defaultdict(list)
    trace_sizes: list[int] = []
    scope_disk: dict[str, list[int]] = defaultdict(list)
    traces_dir = output_dir / "traces"
    for run in completed:
        mode = str(run.get("control_mode"))
        spec = scenario_by_id.get(run.get("scenario_id")) or {}
        scope = str(spec.get("scope") or "unknown")
        wall = float(run.get("elapsed_wall_s") or 0.0)
        by_mode[mode].append(wall)
        by_scope[scope].append(wall)
        run_id = str(run.get("run_id"))
        size = file_size(traces_dir / f"{run_id}.jsonl.gz") + file_size(
            traces_dir / f"{run_id}.snapshots.jsonl.gz"
        )
        trace_sizes.append(size)
        scope_disk[scope].append(size)
    workers = max(1, int(workers))
    n_completed = len(completed)
    mean_wall = _mean(walls)
    target = int(target_episodes or n_completed)
    estimate_target = None if mean_wall is None else mean_wall * target / workers
    estimate_full = None if mean_wall is None else mean_wall * full_grid_episodes / workers
    scope_weighted = 0.0
    scope_weighted_ok = False
    if by_scope and mean_wall is not None:
        # Assume the official 1458-scenario grid is balanced across the three scopes.
        n_scopes = max(1, len(by_scope))
        per_scope_full = full_grid_episodes / n_scopes
        acc = 0.0
        for scope, values in by_scope.items():
            scope_mean = _mean(values)
            if scope_mean is None:
                continue
            acc += scope_mean * per_scope_full
            scope_weighted_ok = True
        if scope_weighted_ok:
            scope_weighted = acc / workers
    return {
        "n_completed_runs": n_completed,
        "workers": workers,
        "wall_time_s": {
            "overall": _stats(walls),
            "by_algorithm": {mode: _stats(values) for mode, values in by_mode.items()},
            "by_scope": {scope: _stats(values) for scope, values in by_scope.items()},
        },
        "trace_bytes": {
            "overall": _stats(trace_sizes),
            "by_scope": {
                scope: _stats(values) for scope, values in scope_disk.items()
            },
        },
        "dataset_bytes": dir_size(output_dir),
        "estimates": {
            "source": "actual completed episode wall time / disk, not theoretical FLOPs",
            "target_episodes": target,
            "target_wall_seconds": estimate_target,
            "full_grid_episodes": full_grid_episodes,
            "full_grid_wall_seconds": estimate_full,
            "full_grid_scope_weighted_wall_seconds": scope_weighted if scope_weighted_ok else estimate_full,
            "target_disk_bytes": None
            if not trace_sizes
            else (_mean(trace_sizes) or 0) * target,
            "full_grid_disk_bytes": None
            if not trace_sizes
            else (_mean(trace_sizes) or 0) * full_grid_episodes,
        },
    }


def render_markdown(summary: Mapping[str, Any], cost: Mapping[str, Any] | None = None) -> str:
    lines = ["# Traffic-Qwen dataset summary", ""]
    lines.append(f"- 总场景数: {summary.get('n_scenarios')}")
    lines.append(f"- 成功场景数: {summary.get('n_success_scenarios')}")
    lines.append(f"- 失败场景数: {summary.get('n_failed_scenarios')}")
    lines.append("")
    lines.append("## 分布")
    for key in ("event_type_counts", "period_counts", "scope_counts", "seed_counts"):
        lines.append(f"- {key}: `{summary.get(key)}`")
    lines.append("")
    lines.append("## 算法成功率")
    for mode, item in dict(summary.get("algorithm_success") or {}).items():
        lines.append(f"- {mode}: {item}")
    lines.append("")
    lines.append("## Expert 选择")
    lines.append(f"- expert_counts: `{summary.get('expert_counts')}`")
    lines.append(f"- by event_type: `{summary.get('expert_counts_by_event_type')}`")
    lines.append(f"- by period: `{summary.get('expert_counts_by_period')}`")
    lines.append(f"- by scope: `{summary.get('expert_counts_by_scope')}`")
    lines.append(f"- fixed_as_best_share: {summary.get('fixed_as_best_share')}")
    lines.append(f"- fallback_to_baseline_share: {summary.get('fallback_to_baseline_share')}")
    lines.append(f"- ambiguous_share: {summary.get('ambiguous_share')}")
    lines.append(f"- high_confidence: {summary.get('n_high_confidence')}")
    lines.append(f"- ambiguous: {summary.get('n_ambiguous')}")
    lines.append(f"- rejected: {summary.get('n_rejected')}")
    lines.append(f"- CoV2X wins / vehicle-action block Signal SFT: {summary.get('cov2x_wins')} / {summary.get('cov2x_vehicle_action_blocks_signal_sft')}")
    lines.append(f"- trip_reliability_gate_scenario_share: {summary.get('trip_reliability_gate_scenario_share')}")
    lines.append(f"- completion_rate_by_algorithm: `{summary.get('completion_rate_by_algorithm')}`")
    lines.append(f"- local_vs_global_conflict_share: {summary.get('local_vs_global_conflict_share')}")
    lines.append(f"- expert_monopoly: `{summary.get('expert_monopoly')}`")
    gate_audit = dict(summary.get("tripinfo_gate_audit") or {})
    if gate_audit:
        lines.append("")
        lines.append("## TripInfo reliability gate")
        for scope, item in gate_audit.items():
            if scope.startswith("_"):
                continue
            lines.append(
                f"- {scope}: gate=`{item.get('tripinfo_gate_rate')}` "
                f"completion_rate_mean=`{(item.get('completion_rate') or {}).get('mean')}` "
                f"tripinfo_excluded=`{item.get('tripinfo_metrics_excluded_from_scoring')}` "
                f"override=`{item.get('override')}`"
            )
    lines.append("")
    lines.append("## SFT")
    lines.append(f"- {summary.get('sft_counts')}")
    lines.append(f"- action_space_counts: `{summary.get('action_space_counts')}`")
    if cost:
        lines.append("")
        lines.append("## 运行成本（来自真实 wall time / 磁盘）")
        lines.append(f"- wall_time: `{cost.get('wall_time_s')}`")
        lines.append(f"- trace_bytes: `{cost.get('trace_bytes')}`")
        lines.append(f"- dataset_bytes: {cost.get('dataset_bytes')}")
        lines.append(f"- estimates: `{cost.get('estimates')}`")
    return "\n".join(lines) + "\n"


def expert_diagnostics(
    *,
    scenarios: Sequence[Mapping[str, Any]],
    selections: Sequence[Mapping[str, Any]],
    ambiguous: Sequence[Mapping[str, Any]],
    rejected: Sequence[Mapping[str, Any]],
    scoring: Mapping[str, Any] | None = None,
    runs: Sequence[Mapping[str, Any]] | None = None,
) -> dict[str, Any]:
    """Formal expert-selection health checks. Does not change scoring."""

    scoring = dict(scoring or {})
    override = dict(scoring.get("tripinfo_gate_override") or {})
    allowed_override_scopes = {str(item) for item in override.get("allowed_scopes") or ()}
    override_reason = str(override.get("reason") or "")
    scenario_by_id = {item["scenario_id"]: item for item in scenarios}
    selected = [item for item in selections if item.get("winner") and not item.get("ambiguous")]
    n_selected = len(selected)
    n_ambiguous = len(ambiguous)
    n_compared = max(1, n_selected + n_ambiguous)
    winners = Counter(str(item.get("winner")) for item in selected)
    by_event = defaultdict(Counter)
    by_period = defaultdict(Counter)
    by_scope = defaultdict(Counter)
    sft_by_event: Counter[str] = Counter()
    event_totals: Counter[str] = Counter()
    scope_gate: Counter[str] = Counter()
    scope_totals: Counter[str] = Counter()
    signal_vehicle = 0
    signal_sft_ok = 0
    fallback = 0
    trip_gated = 0
    for item in selected:
        spec = scenario_by_id.get(item.get("scenario_id")) or {}
        event_type = str((spec.get("event") or {}).get("event_type") or "unknown")
        period = str(spec.get("period") or "unknown")
        scope = str(spec.get("scope") or "unknown")
        winners_key = str(item.get("winner"))
        by_event[event_type][winners_key] += 1
        by_period[period][winners_key] += 1
        by_scope[scope][winners_key] += 1
        event_totals[event_type] += 1
        scope_totals[scope] += 1
        if item.get("fallback_to_baseline"):
            fallback += 1
        if item.get("signal_sft_eligible"):
            signal_sft_ok += 1
            sft_by_event[event_type] += 1
        if item.get("winner_action_space") == "signal_vehicle" or not item.get("signal_sft_eligible", True):
            signal_vehicle += 1
        gated_modes = item.get("trip_reliability_gated_modes") or []
        if gated_modes:
            trip_gated += 1
            scope_gate[scope] += 1
    for item in ambiguous:
        spec = scenario_by_id.get(item.get("scenario_id")) or {}
        event_type = str((spec.get("event") or {}).get("event_type") or "unknown")
        scope = str(spec.get("scope") or "unknown")
        event_totals[event_type] += 1
        scope_totals[scope] += 1
        gated_modes = item.get("trip_reliability_gated_modes") or []
        if gated_modes:
            trip_gated += 1
            scope_gate[scope] += 1
    top_share = (winners.most_common(1)[0][1] / n_selected) if n_selected else 0.0
    events_without_sft = [
        event_type
        for event_type, total in event_totals.items()
        if sft_by_event.get(event_type, 0) == 0
    ]
    high_gate_scopes = {
        scope: (scope_gate[scope] / n) if n else 0.0
        for scope, n in scope_totals.items()
        if n and (scope_gate[scope] / n) >= 0.50
    }
    unexpected_high_gate = {
        scope: rate
        for scope, rate in high_gate_scopes.items()
        if scope not in allowed_override_scopes
    }
    completion_by_scope: dict[str, dict[str, float | None]] = {}
    if runs:
        by_scope_rates: dict[str, list[float]] = defaultdict(list)
        for run in runs:
            spec = scenario_by_id.get(run.get("scenario_id")) or run.get("scenario") or {}
            if not isinstance(spec, Mapping):
                spec = {}
            scope = str(spec.get("scope") or "unknown")
            rate = (run.get("traffic_eval") or {}).get("completion_rate")
            try:
                if rate is not None:
                    by_scope_rates[scope].append(float(rate))
            except (TypeError, ValueError):
                continue
        for scope, values in by_scope_rates.items():
            completion_by_scope[scope] = {
                "n": len(values),
                "mean": sum(values) / len(values) if values else None,
                "min": min(values) if values else None,
                "max": max(values) if values else None,
            }
    flags = {
        "single_expert_over_80pct": bool(n_selected and top_share >= 0.80),
        "event_without_signal_sft": events_without_sft,
        "scope_tripinfo_gate_ge_50pct": high_gate_scopes,
        "unexpected_tripinfo_gate_ge_50pct": unexpected_high_gate,
    }
    acknowledged = {
        scope: {
            "tripinfo_gate_rate": high_gate_scopes.get(scope),
            "completion_rate": completion_by_scope.get(scope),
            "reason": override_reason,
            "tripinfo_metrics_excluded_from_scoring": True,
        }
        for scope in sorted(allowed_override_scopes & set(high_gate_scopes))
    }
    return {
        "n_selected": n_selected,
        "n_ambiguous": n_ambiguous,
        "n_rejected": len(rejected),
        "winner_counts": dict(winners),
        "winner_share": {mode: count / n_selected for mode, count in winners.items()} if n_selected else {},
        "winner_by_event": {key: dict(value) for key, value in by_event.items()},
        "winner_by_period": {key: dict(value) for key, value in by_period.items()},
        "winner_by_scope": {key: dict(value) for key, value in by_scope.items()},
        "ambiguous_rate": n_ambiguous / n_compared,
        "signal_vehicle_rate": signal_vehicle / max(1, n_selected),
        "signal_sft_eligible_rate": signal_sft_ok / max(1, n_selected),
        "fixed_fallback_rate": fallback / max(1, n_selected),
        "tripinfo_gate_rate": trip_gated / max(1, n_selected + n_ambiguous),
        "tripinfo_gate_by_scope": {
            scope: (scope_gate[scope] / n) if n else 0.0 for scope, n in scope_totals.items()
        },
        "completion_rate_by_scope": completion_by_scope,
        "tripinfo_gate_override": {
            "allowed_scopes": sorted(allowed_override_scopes),
            "reason": override_reason,
            "acknowledged_scopes": acknowledged,
        },
        "flags": flags,
        "block_training": bool(
            flags["single_expert_over_80pct"]
            or flags["event_without_signal_sft"]
            or flags["unexpected_tripinfo_gate_ge_50pct"]
        ),
    }


def tripinfo_gate_audit(diagnostics: Mapping[str, Any]) -> dict[str, Any]:
    """Auditable per-scope TripInfo gate record for manifest / final reports."""

    override = dict(diagnostics.get("tripinfo_gate_override") or {})
    allowed = {str(item) for item in override.get("allowed_scopes") or ()}
    gates = dict(diagnostics.get("tripinfo_gate_by_scope") or {})
    completion = dict(diagnostics.get("completion_rate_by_scope") or {})
    acknowledged = dict(override.get("acknowledged_scopes") or {})
    scopes = sorted(set(gates) | set(completion) | allowed)
    payload: dict[str, Any] = {}
    for scope in scopes:
        rate = gates.get(scope)
        payload[scope] = {
            "tripinfo_gate_rate": rate,
            "completion_rate": completion.get(scope),
            "tripinfo_metrics_excluded_from_scoring": bool(
                scope in allowed and rate is not None and float(rate) >= 0.50
            ),
            "override": scope in allowed,
            "override_reason": override.get("reason") if scope in allowed else None,
            "acknowledged": acknowledged.get(scope),
        }
    payload["_notes"] = {
        "xiongan_20_expected": "300s episode causes systematic TripInfo truncation; expert selection uses local event/recovery metrics",
        "east_west": "continue using reliable TripInfo when completion_rate >= 0.30",
    }
    return payload
