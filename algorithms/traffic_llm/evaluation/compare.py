"""Compare closed-loop LLM episodes against existing algorithm runs."""

from __future__ import annotations

from collections import defaultdict
from pathlib import Path
from typing import Any, Mapping, Sequence

from algorithms.traffic_llm.dataset.io_utils import load_json, read_jsonl
from algorithms.traffic_llm.dataset.scorer import ignored_trip_metrics_for


PRIMARY_METRICS: tuple[tuple[str, str, str], ...] = (
    ("local_event_window", "local_avg_queue_veh", "minimize"),
    ("local_event_window", "local_max_queue_m", "minimize"),
    ("local_event_window", "local_spillback_pct", "minimize"),
    ("local_event_window", "local_mean_speed_mps", "maximize"),
    ("local_event_window", "local_throughput_delta", "maximize"),
    ("recovery", "recovery_time_s", "minimize"),
)

SNAPSHOT_EVAL_METRICS: tuple[tuple[str, str], ...] = (
    ("spillback_rate", "minimize"),
    ("regional_max_queue_length_m", "minimize"),
    ("hard_braking_rate", "minimize"),
)

COMPLETION_METRIC = ("traffic_eval", "completion_rate", "maximize")

REL_EPS = 0.01


def _finite(value: Any) -> float | None:
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    if number != number:
        return None
    return number


def _lookup(run: Mapping[str, Any], source: str, metric_id: str) -> float | None:
    if source == "traffic_eval":
        return _finite((run.get("traffic_eval") or {}).get(metric_id))
    if source == "local_event_window":
        return _finite((run.get("local_event_window") or {}).get(metric_id))
    if source == "recovery":
        return _finite((run.get("recovery") or {}).get(metric_id))
    return _finite(run.get(metric_id))


def relative_improvement(llm: float | None, other: float | None, direction: str) -> float | None:
    if llm is None or other is None:
        return None
    denom = abs(other) if abs(other) > 1e-12 else None
    if denom is None:
        return 0.0 if abs(llm - other) <= 1e-12 else None
    if direction == "minimize":
        return (other - llm) / denom * 100.0
    return (llm - other) / denom * 100.0


def outcome(improvement: float | None) -> str:
    if improvement is None:
        return "na"
    if improvement > REL_EPS * 100.0:
        return "win"
    if improvement < -REL_EPS * 100.0:
        return "loss"
    return "tie"


def scenario_verdict(improvements: Sequence[float | None]) -> str:
    wins = sum(1 for item in improvements if item is not None and item > REL_EPS * 100.0)
    losses = sum(1 for item in improvements if item is not None and item < -REL_EPS * 100.0)
    if wins > losses:
        return "win"
    if losses > wins:
        return "loss"
    return "tie"


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


def load_algorithm_run(dataset_dir: Path, scenario_id: str, mode: str) -> dict[str, Any] | None:
    path = dataset_dir / "runs" / f"{scenario_id}_{mode}.json"
    if not path.is_file():
        return None
    return load_json(path)


def load_closed_loop_run(root: Path, policy_name: str, scenario_id: str) -> dict[str, Any] | None:
    path = root / policy_name / "runs" / f"{scenario_id}_{policy_name}.json"
    if not path.is_file():
        return None
    return load_json(path)


def load_selected_experts(dataset_dir: Path) -> dict[str, str]:
    path = dataset_dir / "teacher_selection" / "selected_experts.jsonl"
    winners: dict[str, str] = {}
    if not path.is_file():
        return winners
    for item in read_jsonl(path):
        sid = str(item.get("scenario_id") or "")
        winner = item.get("winner")
        if sid and winner and not item.get("ambiguous"):
            winners[sid] = str(winner)
    return winners


def _metric_pack(run: Mapping[str, Any], scoring: Mapping[str, Any]) -> dict[str, float | None]:
    ignored = set(ignored_trip_metrics_for(run, scoring))
    payload: dict[str, float | None] = {}
    for source, metric_id, _direction in PRIMARY_METRICS:
        payload[metric_id] = _lookup(run, source, metric_id)
    payload["completion_rate"] = _lookup(run, "traffic_eval", "completion_rate")
    for metric_id, _direction in SNAPSHOT_EVAL_METRICS:
        payload[metric_id] = _lookup(run, "traffic_eval", metric_id)
    payload["traffic_performance_index"] = (
        None
        if "traffic_performance_index" in ignored
        else _lookup(run, "traffic_eval", "traffic_performance_index")
    )
    payload["path_avg_speed_kmh"] = (
        None
        if "path_avg_speed_kmh" in ignored
        else _lookup(run, "traffic_eval", "path_avg_speed_kmh")
    )
    payload["tripinfo_gated"] = bool(ignored)
    return payload


def _pair_row(
    llm: Mapping[str, Any],
    other: Mapping[str, Any] | None,
    scoring: Mapping[str, Any],
    *,
    other_name: str,
) -> dict[str, Any] | None:
    if other is None or other.get("state") != "COMPLETED":
        return None
    llm_metrics = _metric_pack(llm, scoring)
    other_metrics = _metric_pack(other, scoring)
    improvements: dict[str, float | None] = {}
    outcomes: dict[str, str] = {}
    primary_vals: list[float | None] = []
    for source, metric_id, direction in PRIMARY_METRICS:
        rel = relative_improvement(llm_metrics.get(metric_id), other_metrics.get(metric_id), direction)
        improvements[metric_id] = rel
        outcomes[metric_id] = outcome(rel)
        primary_vals.append(rel)
    return {
        "other": other_name,
        "improvements_pct": improvements,
        "outcomes": outcomes,
        "verdict": scenario_verdict(primary_vals),
        "llm_metrics": llm_metrics,
        "other_metrics": other_metrics,
        "other_state": other.get("state"),
    }


def _tally(verdicts: Sequence[str]) -> dict[str, int]:
    return {
        "win": sum(1 for item in verdicts if item == "win"),
        "tie": sum(1 for item in verdicts if item == "tie"),
        "loss": sum(1 for item in verdicts if item == "loss"),
        "n": len(verdicts),
    }


def _mean(values: Sequence[float | None]) -> float | None:
    present = [float(item) for item in values if item is not None]
    if not present:
        return None
    return sum(present) / len(present)


def summarize_group(rows: Sequence[Mapping[str, Any]], comparison: str) -> dict[str, Any]:
    pairs = [item["comparisons"].get(comparison) for item in rows]
    pairs = [item for item in pairs if item]
    verdicts = [str(item["verdict"]) for item in pairs]
    metric_means: dict[str, float | None] = {}
    metric_wtl: dict[str, dict[str, int]] = {}
    if pairs:
        metric_ids = list(pairs[0]["improvements_pct"])
        for metric_id in metric_ids:
            metric_means[metric_id] = _mean([item["improvements_pct"].get(metric_id) for item in pairs])
            metric_wtl[metric_id] = _tally([item["outcomes"].get(metric_id, "na") for item in pairs])
    return {
        "n": len(pairs),
        "win_tie_loss": _tally(verdicts),
        "mean_improvement_pct": metric_means,
        "metric_win_tie_loss": metric_wtl,
    }


def compare_closed_loop(
    *,
    dataset_dir: Path,
    closed_loop_root: Path,
    scoring: Mapping[str, Any],
    scenarios: Sequence[Mapping[str, Any]],
    llm_policy: str = "traffic_qwen",
    base_policy: str = "base_qwen",
) -> dict[str, Any]:
    experts = load_selected_experts(dataset_dir)
    rows: list[dict[str, Any]] = []
    latencies: list[float] = []
    json_ok = 0
    schema_ok = 0
    phase_ok = 0
    region_ok = 0
    n_plans = 0
    n_fallback = 0
    n_invalid = 0
    n_llm_ok = 0
    n_base_ok = 0
    for spec in scenarios:
        sid = str(spec["scenario_id"])
        llm = load_closed_loop_run(closed_loop_root, llm_policy, sid)
        base = load_closed_loop_run(closed_loop_root, base_policy, sid)
        if llm and llm.get("state") == "COMPLETED":
            n_llm_ok += 1
        if base and base.get("state") == "COMPLETED":
            n_base_ok += 1
        if not llm or llm.get("state") != "COMPLETED":
            continue
        for ms in llm.get("inference_latency_ms") or ():
            try:
                latencies.append(float(ms))
            except (TypeError, ValueError):
                continue
        n_plans += int(llm.get("n_plans") or 0)
        n_fallback += int(llm.get("n_fallback") or 0)
        n_invalid += int(llm.get("n_invalid_plans") or 0)
        for decision in llm.get("decisions") or ():
            if decision.get("skipped"):
                continue
            json_ok += int(bool(decision.get("json_ok")))
            schema_ok += int(bool(decision.get("schema_ok")))
            phase_ok += int(bool(decision.get("phase_ok")))
            region_ok += int(bool(decision.get("region_ok")))
        expert_mode = experts.get(sid)
        comparisons = {
            "base_qwen": _pair_row(llm, base, scoring, other_name="base_qwen") if base else None,
            "fixed": _pair_row(llm, load_algorithm_run(dataset_dir, sid, "fixed"), scoring, other_name="fixed"),
            "max_pressure": _pair_row(
                llm,
                load_algorithm_run(dataset_dir, sid, "max_pressure"),
                scoring,
                other_name="max_pressure",
            ),
            "selected_expert": (
                _pair_row(
                    llm,
                    load_algorithm_run(dataset_dir, sid, expert_mode),
                    scoring,
                    other_name=expert_mode,
                )
                if expert_mode
                else None
            ),
        }
        rows.append(
            {
                "scenario_id": sid,
                "event_type": spec.get("event", {}).get("event_type") if isinstance(spec.get("event"), Mapping) else spec.get("event_type"),
                "period": spec.get("period"),
                "scope": spec.get("scope"),
                "seed": spec.get("seed"),
                "llm_state": llm.get("state"),
                "selected_expert": expert_mode,
                "n_plans": llm.get("n_plans"),
                "tripinfo_gated": bool((_metric_pack(llm, scoring)).get("tripinfo_gated")),
                "comparisons": comparisons,
                "llm_local": dict(llm.get("local_event_window") or {}),
                "llm_recovery": dict(llm.get("recovery") or {}),
                "completion_rate": (llm.get("traffic_eval") or {}).get("completion_rate"),
                "fallback_rate": llm.get("fallback_rate"),
                "invalid_plan_rate": llm.get("invalid_plan_rate"),
            }
        )

    def _group(field: str) -> dict[str, dict[str, Any]]:
        buckets: dict[str, list[dict[str, Any]]] = defaultdict(list)
        for row in rows:
            buckets[str(row.get(field) or "unknown")].append(row)
        return {
            key: {
                comparison: summarize_group(items, comparison)
                for comparison in ("base_qwen", "fixed", "max_pressure", "selected_expert")
            }
            for key, items in sorted(buckets.items())
        }

    overall = {
        comparison: summarize_group(rows, comparison)
        for comparison in ("base_qwen", "fixed", "max_pressure", "selected_expert")
    }
    return {
        "n_scenarios": len(scenarios),
        "n_llm_completed": n_llm_ok,
        "n_base_completed": n_base_ok,
        "n_compared": len(rows),
        "n_zero_plan_episodes": sum(1 for item in rows if not int(item.get("n_plans") or 0)),
        "n_plans": n_plans,
        "json_ok_rate": (json_ok / n_plans) if n_plans else None,
        "schema_ok_rate": (schema_ok / n_plans) if n_plans else None,
        "phase_ok_rate": (phase_ok / n_plans) if n_plans else None,
        "region_ok_rate": (region_ok / n_plans) if n_plans else None,
        "fallback_rate": (n_fallback / n_plans) if n_plans else None,
        "invalid_plan_rate": (n_invalid / n_plans) if n_plans else None,
        "inference_latency_ms": {
            "n": len(latencies),
            "p50": _percentile(latencies, 50),
            "p95": _percentile(latencies, 95),
            "max": max(latencies) if latencies else None,
        },
        "overall": overall,
        "by_event": _group("event_type"),
        "by_period": _group("period"),
        "by_scope": _group("scope"),
        "scenarios": rows,
    }
