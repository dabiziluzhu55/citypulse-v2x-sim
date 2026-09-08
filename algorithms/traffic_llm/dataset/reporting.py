"""Dataset summary statistics written after generate/select/build-sft."""

from __future__ import annotations

from collections import Counter, defaultdict
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
    phase_hist: Counter[int] = Counter()
    n_controlled: list[int] = []
    # filled by caller via sft optional stats
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
        "fixed_as_best_share": expert_counts.get("fixed", 0) / n_select,
        "fallback_to_baseline_share": fallback_n / max(1, len(selections)),
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
        "avg_plan_intersections": _mean(n_controlled),
        "phase_histogram": dict(phase_hist),
    }


def render_markdown(summary: Mapping[str, Any]) -> str:
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
    lines.append(f"- fixed_as_best_share: {summary.get('fixed_as_best_share')}")
    lines.append(f"- fallback_to_baseline_share: {summary.get('fallback_to_baseline_share')}")
    lines.append(f"- high_confidence: {summary.get('n_high_confidence')}")
    lines.append(f"- ambiguous: {summary.get('n_ambiguous')}")
    lines.append(f"- rejected: {summary.get('n_rejected')}")
    lines.append("")
    lines.append("## SFT")
    lines.append(f"- {summary.get('sft_counts')}")
    lines.append(f"- action_space_counts: `{summary.get('action_space_counts')}`")
    return "\n".join(lines) + "\n"
