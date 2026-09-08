"""Dataset-specific snapshot window and recovery metrics (not traffic_eval)."""

from __future__ import annotations

from typing import Any, Mapping, Sequence

from simulation.sumo.engine.session import SimulationSnapshot


def _finite(value: Any) -> float | None:
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    if number != number:  # NaN
        return None
    return number


def snapshot_region_stats(snapshot: SimulationSnapshot) -> dict[str, float | None]:
    incoming_halting: list[float] = []
    queue_m: list[float] = []
    spill_overflow = 0.0
    spill_exposed = 0.0
    mean_speed = _finite(getattr(snapshot.metrics, "mean_speed", None))
    for intersection_id, intersection in snapshot.intersections.items():
        for lane_id, lane in intersection.lanes.items():
            role = str(getattr(lane, "role", "") or "")
            if role and role not in {"incoming", "both"}:
                continue
            incoming_halting.append(float(getattr(lane, "halting_count", 0) or 0))
            q = _finite(getattr(lane, "queue_length_m", None))
            length = _finite(getattr(lane, "lane_length_m", None))
            if q is not None:
                queue_m.append(q)
            if length is not None and length > 0:
                spill_exposed += 1.0
                if q is not None and q + 1e-9 >= length:
                    spill_overflow += 1.0
            _ = (intersection_id, lane_id)
    avg_queue = (
        sum(incoming_halting) / len(incoming_halting) if incoming_halting else None
    )
    max_queue = max(queue_m) if queue_m else None
    spillback = (
        100.0 * spill_overflow / spill_exposed if spill_exposed > 0 else None
    )
    return {
        "avg_queue_veh": avg_queue,
        "max_queue_m": max_queue,
        "spillback_pct": spillback,
        "mean_speed_mps": mean_speed,
        "arrived_vehicles": float(snapshot.metrics.arrived_vehicles),
        "waiting_time_s": float(snapshot.metrics.total_waiting_time),
        "hard_braking_events": float(snapshot.metrics.hard_braking_events),
        "halting_vehicles": float(snapshot.metrics.halting_vehicles),
        "elapsed_seconds": float(snapshot.elapsed_seconds),
    }


def _snapshots_in_window(
    snapshots: Sequence[SimulationSnapshot],
    start: float,
    end: float,
) -> list[SimulationSnapshot]:
    selected = [
        snap
        for snap in snapshots
        if start - 1e-9 <= float(snap.elapsed_seconds) <= end + 1e-9
    ]
    return selected


def _mean(values: Sequence[float | None]) -> float | None:
    present = [float(item) for item in values if item is not None]
    if not present:
        return None
    return sum(present) / len(present)


def _delta(later: float | None, earlier: float | None) -> float | None:
    if later is None or earlier is None:
        return None
    return later - earlier


def compute_event_window_metrics(
    snapshots: Sequence[SimulationSnapshot],
    *,
    event_start: float,
    event_end: float,
    offsets_seconds: Sequence[float],
    include_active_span: bool = True,
) -> dict[str, Any]:
    if not snapshots:
        return {"windows": {}, "note": "no snapshots"}
    first = snapshot_region_stats(snapshots[0])
    labels = [f"event_plus_{int(offset)}s" for offset in offsets_seconds]
    starts = [float(event_start)] * len(offsets_seconds)
    ends = [float(event_start) + float(offset) for offset in offsets_seconds]
    if include_active_span:
        labels.append("event_active")
        starts.append(float(event_start))
        ends.append(float(event_end))
    baseline_snaps = _snapshots_in_window(
        snapshots, max(0.0, event_start - 1.0), event_start
    )
    baseline = snapshot_region_stats(baseline_snaps[-1]) if baseline_snaps else first
    windows: dict[str, Any] = {}
    for label, start, end in zip(labels, starts, ends):
        selected = _snapshots_in_window(snapshots, start, end)
        if not selected:
            windows[label] = None
            continue
        stats = [snapshot_region_stats(item) for item in selected]
        last = stats[-1]
        windows[label] = {
            "avg_queue_veh": _mean([item["avg_queue_veh"] for item in stats]),
            "max_queue_m": max(
                (item["max_queue_m"] for item in stats if item["max_queue_m"] is not None),
                default=None,
            ),
            "spillback_pct": _mean([item["spillback_pct"] for item in stats]),
            "mean_speed_mps": _mean([item["mean_speed_mps"] for item in stats]),
            "halting_vehicles": _mean([item["halting_vehicles"] for item in stats]),
            "throughput_delta_veh": _delta(
                last["arrived_vehicles"], baseline["arrived_vehicles"]
            ),
            "waiting_time_delta_s": _delta(
                last["waiting_time_s"], baseline["waiting_time_s"]
            ),
            "hard_braking_delta": _delta(
                last["hard_braking_events"], baseline["hard_braking_events"]
            ),
            "n_samples": len(selected),
            "window_start_s": start,
            "window_end_s": end,
        }
    active = windows.get("event_active") or next(
        (item for item in windows.values() if item),
        {},
    ) or {}
    return {
        "windows": windows,
        "window_avg_queue_veh": active.get("avg_queue_veh"),
        "window_max_queue_m": active.get("max_queue_m"),
        "window_spillback_pct": active.get("spillback_pct"),
        "window_mean_speed_mps": active.get("mean_speed_mps"),
        "window_throughput_delta_veh": active.get("throughput_delta_veh"),
        "window_waiting_time_delta_s": active.get("waiting_time_delta_s"),
        "window_hard_braking_delta": active.get("hard_braking_delta"),
    }



def compute_recovery_metrics(
    snapshots: Sequence[SimulationSnapshot],
    *,
    event_start: float,
    event_end: float,
    config: Mapping[str, Any],
) -> dict[str, Any]:
    version = str(config.get("version") or "recovery_v1")
    reference_s = float(config.get("reference_window_before_event_s", 20.0))
    clear_n = int(config.get("consecutive_clear_samples", 3))
    min_horizon = float(config.get("min_post_event_horizon_s", 20.0))
    tolerance = dict(config.get("tolerance") or {})
    avg_rel = float(tolerance.get("avg_queue_rel", 0.15))
    max_rel = float(tolerance.get("max_queue_rel", 0.20))
    spill_abs = float(tolerance.get("spillback_abs", 2.0))

    if not snapshots:
        return {
            "recovery_version": version,
            "recovery_time_s": None,
            "post_event_avg_queue": None,
            "post_event_max_queue": None,
            "post_event_spillback": None,
            "post_event_throughput": None,
            "reason": "no snapshots",
        }

    episode_end = float(snapshots[-1].elapsed_seconds)
    if episode_end + 1e-9 < event_end + min_horizon:
        post = _snapshots_in_window(snapshots, event_end, episode_end)
        post_stats = [snapshot_region_stats(item) for item in post] if post else []
        return {
            "recovery_version": version,
            "recovery_time_s": None,
            "post_event_avg_queue": _mean([item["avg_queue_veh"] for item in post_stats]) if post_stats else None,
            "post_event_max_queue": max(
                (item["max_queue_m"] for item in post_stats if item.get("max_queue_m") is not None),
                default=None,
            ) if post_stats else None,
            "post_event_spillback": _mean([item["spillback_pct"] for item in post_stats]) if post_stats else None,
            "post_event_throughput": _delta(
                snapshot_region_stats(snapshots[-1])["arrived_vehicles"],
                snapshot_region_stats(post[0])["arrived_vehicles"] if post else None,
            ),
            "reason": "insufficient post-event horizon",
        }

    ref_snaps = _snapshots_in_window(
        snapshots, max(0.0, event_start - reference_s), event_start
    )
    if not ref_snaps:
        return {
            "recovery_version": version,
            "recovery_time_s": None,
            "post_event_avg_queue": None,
            "post_event_max_queue": None,
            "post_event_spillback": None,
            "post_event_throughput": None,
            "reason": "no pre-event reference window",
        }
    ref = snapshot_region_stats(ref_snaps[-1])
    post = _snapshots_in_window(snapshots, event_end, episode_end)
    post_stats = [snapshot_region_stats(item) for item in post]
    recovered_at: float | None = None
    streak = 0
    for snap, stats in zip(post, post_stats):
        ok = True
        ref_q = ref["avg_queue_veh"]
        cur_q = stats["avg_queue_veh"]
        if ref_q is not None and cur_q is not None:
            threshold = ref_q * (1.0 + avg_rel) + 1e-6
            if cur_q > threshold:
                ok = False
        ref_m = ref["max_queue_m"]
        cur_m = stats["max_queue_m"]
        if ok and ref_m is not None and cur_m is not None:
            if cur_m > ref_m * (1.0 + max_rel) + 1e-6:
                ok = False
        ref_s = ref["spillback_pct"]
        cur_s = stats["spillback_pct"]
        if ok and ref_s is not None and cur_s is not None:
            if cur_s > ref_s + spill_abs + 1e-6:
                ok = False
        if ok:
            streak += 1
            if streak >= clear_n:
                recovered_at = float(snap.elapsed_seconds)
                break
        else:
            streak = 0

    recovery_time = (
        None if recovered_at is None else max(0.0, recovered_at - float(event_end))
    )
    arrived_start = snapshot_region_stats(post[0])["arrived_vehicles"] if post else None
    arrived_end = snapshot_region_stats(post[-1])["arrived_vehicles"] if post else None
    return {
        "recovery_version": version,
        "recovery_time_s": recovery_time,
        "post_event_avg_queue": _mean([item["avg_queue_veh"] for item in post_stats]),
        "post_event_max_queue": max(
            (item["max_queue_m"] for item in post_stats if item.get("max_queue_m") is not None),
            default=None,
        ),
        "post_event_spillback": _mean([item["spillback_pct"] for item in post_stats]),
        "post_event_throughput": _delta(arrived_end, arrived_start),
        "reason": None if recovery_time is not None else "did not recover within episode",
        "reference": {
            "avg_queue_veh": ref["avg_queue_veh"],
            "max_queue_m": ref["max_queue_m"],
            "spillback_pct": ref["spillback_pct"],
        },
        "tolerance": {
            "avg_queue_rel": avg_rel,
            "max_queue_rel": max_rel,
            "spillback_abs": spill_abs,
            "consecutive_clear_samples": clear_n,
        },
    }
