"""Configurable composite scoring. None is never coerced to 0."""

from __future__ import annotations

from typing import Any, Mapping, Sequence


DEFAULT_UNRELIABLE_TRIP_METRICS = (
    "traffic_performance_index",
    "path_avg_speed_kmh",
    "fuel_intensity_L_per_100km",
    "avg_travel_time_s",
    "delay_time_proportion",
    "travel_time_index",
    "avg_stops_per_vehicle",
    "avg_waiting_time_s",
    "throughput_veh_per_h",
)


def _finite(value: Any) -> float | None:
    if value is None:
        return None
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    if number != number:
        return None
    return number


def trip_reliability_config(scoring: Mapping[str, Any]) -> dict[str, Any]:
    raw = dict(scoring.get("trip_metric_reliability") or {})
    metrics = tuple(raw.get("unreliable_metrics") or DEFAULT_UNRELIABLE_TRIP_METRICS)
    return {
        "enabled": bool(raw.get("enabled", False)),
        "min_completion_rate": float(raw.get("min_completion_rate", 0.30)),
        "unreliable_metrics": metrics,
    }


def ignored_trip_metrics_for(
    candidate: Mapping[str, Any],
    scoring: Mapping[str, Any],
) -> tuple[str, ...]:
    cfg = trip_reliability_config(scoring)
    if not cfg["enabled"]:
        return ()
    rate = _finite((candidate.get("traffic_eval") or {}).get("completion_rate"))
    threshold = float(cfg["min_completion_rate"])
    if rate is not None and rate + 1e-12 >= threshold:
        return ()
    return tuple(str(item) for item in cfg["unreliable_metrics"])


def _lookup_metric(
    candidate: Mapping[str, Any],
    metric_id: str,
    source: str,
    *,
    ignored: set[str] | frozenset[str] = frozenset(),
) -> float | None:
    if metric_id in ignored:
        return None
    traffic_eval = dict(candidate.get("traffic_eval") or {})
    event_window = dict(candidate.get("event_window") or {})
    local_event_window = dict(candidate.get("local_event_window") or {})
    recovery = dict(candidate.get("recovery") or {})
    if source == "traffic_eval":
        return _finite(traffic_eval.get(metric_id))
    if source == "event_window":
        return _finite(event_window.get(metric_id))
    if source == "local_event_window":
        return _finite(local_event_window.get(metric_id))
    if source == "recovery":
        return _finite(recovery.get(metric_id))
    return _finite(candidate.get(metric_id))


def _normalize(
    values: Sequence[float | None],
    *,
    direction: str,
    relative_range_epsilon: float = 0.05,
) -> list[float | None]:
    present = [item for item in values if item is not None]
    if not present:
        return [None for _ in values]
    lo = min(present)
    hi = max(present)
    span = hi - lo
    scale = max(abs(hi), abs(lo), 1.0)
    out: list[float | None] = []
    if span <= 1e-12 or span / scale < float(relative_range_epsilon):
        for item in values:
            out.append(None if item is None else 0.5)
        return out
    for item in values:
        if item is None:
            out.append(None)
            continue
        unit = (item - lo) / span
        if direction == "minimize":
            unit = 1.0 - unit
        elif direction != "maximize":
            raise ValueError(f"Unknown metric direction {direction!r}")
        out.append(float(unit))
    return out


def score_candidates(
    candidates: Sequence[Mapping[str, Any]],
    scoring: Mapping[str, Any],
) -> list[dict[str, Any]]:
    groups = dict(scoring.get("groups") or {})
    if not groups:
        raise ValueError("scoring config must declare groups")
    relative_eps = float(scoring.get("relative_range_epsilon", 0.05))
    names = [str(item.get("control_mode") or item.get("name") or idx) for idx, item in enumerate(candidates)]
    ignored_by_idx = [set(ignored_trip_metrics_for(item, scoring)) for item in candidates]
    raw_by_metric: dict[tuple[str, str, str], list[float | None]] = {}
    metric_meta: dict[tuple[str, str, str], dict[str, Any]] = {}
    for group_name, group in groups.items():
        for metric in group.get("metrics") or ():
            key = (str(group_name), str(metric["id"]), str(metric["source"]))
            metric_meta[key] = {
                "direction": str(metric["direction"]),
                "weight": float(metric.get("weight", 1.0)),
                "source": str(metric["source"]),
                "group": str(group_name),
            }
            raw_by_metric[key] = [
                _lookup_metric(
                    candidate,
                    str(metric["id"]),
                    str(metric["source"]),
                    ignored=ignored_by_idx[idx],
                )
                for idx, candidate in enumerate(candidates)
            ]

    normalized: dict[tuple[str, str, str], list[float | None]] = {}
    for key, raw_values in raw_by_metric.items():
        normalized[key] = _normalize(
            raw_values,
            direction=metric_meta[key]["direction"],
            relative_range_epsilon=relative_eps,
        )

    results: list[dict[str, Any]] = []
    group_weights = {
        name: float(group.get("weight", 0.0)) for name, group in groups.items()
    }
    weight_sum = sum(group_weights.values()) or 1.0
    for idx, candidate in enumerate(candidates):
        group_scores: dict[str, float | None] = {}
        raw_metrics: dict[str, float | None] = {}
        norm_metrics: dict[str, float | None] = {}
        for key, meta in metric_meta.items():
            metric_id = key[1]
            raw_metrics[f"{meta['source']}.{metric_id}"] = raw_by_metric[key][idx]
            norm_metrics[f"{meta['source']}.{metric_id}"] = normalized[key][idx]
        for group_name, group in groups.items():
            weighted = 0.0
            total = 0.0
            for metric in group.get("metrics") or ():
                key = (str(group_name), str(metric["id"]), str(metric["source"]))
                value = normalized[key][idx]
                if value is None:
                    continue
                w = float(metric.get("weight", 1.0))
                weighted += w * value
                total += w
            group_scores[group_name] = None if total <= 0 else weighted / total
        composite = 0.0
        used = 0.0
        for group_name, score in group_scores.items():
            if score is None:
                continue
            w = group_weights[group_name]
            composite += w * score
            used += w
        composite_score = None if used <= 0 else composite / (used if used > 0 else weight_sum)
        results.append(
            {
                "control_mode": names[idx],
                "raw_metrics": raw_metrics,
                "normalized_metrics": norm_metrics,
                "group_scores": group_scores,
                "composite_score": composite_score,
                "ignored_trip_metrics": sorted(ignored_by_idx[idx]),
                "trip_reliability_gated": bool(ignored_by_idx[idx]),
                "completion_rate": _finite((candidate.get("traffic_eval") or {}).get("completion_rate")),
            }
        )
    return results


def pareto_ranks(scored: Sequence[Mapping[str, Any]]) -> list[int]:
    ranks = [0] * len(scored)
    remaining = set(range(len(scored)))
    rank = 1
    while remaining:
        front: list[int] = []
        for idx in remaining:
            dominated = False
            for other in remaining:
                if other == idx:
                    continue
                if _dominates(scored[other]["group_scores"], scored[idx]["group_scores"]):
                    dominated = True
                    break
            if not dominated:
                front.append(idx)
        if not front:
            for idx in remaining:
                ranks[idx] = rank
            break
        for idx in front:
            ranks[idx] = rank
            remaining.remove(idx)
        rank += 1
    return ranks


def _dominates(left: Mapping[str, Any], right: Mapping[str, Any]) -> bool:
    keys = sorted(set(left) | set(right))
    strictly_better = False
    comparable = False
    for key in keys:
        a = _finite(left.get(key))
        b = _finite(right.get(key))
        if a is None or b is None:
            continue
        comparable = True
        if a + 1e-12 < b:
            return False
        if a > b + 1e-12:
            strictly_better = True
    return comparable and strictly_better
