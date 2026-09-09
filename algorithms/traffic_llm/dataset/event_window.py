"""Dataset-specific snapshot window and recovery metrics (not traffic_eval)."""

from __future__ import annotations

from typing import Any, Mapping, Sequence

from simulation.sumo.engine.session import SimulationSnapshot

from .catalog import expand_scope


def _finite(value: Any) -> float | None:
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    if number != number:  # NaN
        return None
    return number


def resolve_local_intersection_ids(
    intersection_ids: Sequence[str],
    event_intersection_id: str | None,
    neighbors: Mapping[str, Sequence[str]] | None,
    *,
    hops: int = 1,
    small_preset_max_intersections: int = 6,
) -> tuple[str, ...]:
    allowed = {str(item) for item in intersection_ids}
    if not allowed:
        return ()
    if len(allowed) <= int(small_preset_max_intersections):
        return tuple(sorted(allowed))
    seed = str(event_intersection_id or "")
    seeds = (seed,) if seed in allowed else tuple(sorted(allowed)[:1])
    local = expand_scope(seeds, int(hops), dict(neighbors or {}), allowed)
    return local or tuple(sorted(allowed))


def _as_mapping(snapshot: Any) -> dict[str, Any]:
    if isinstance(snapshot, SimulationSnapshot):
        return {
            "elapsed_seconds": float(snapshot.elapsed_seconds),
            "intersections": snapshot.intersections,
            "metrics": snapshot.metrics,
            "vehicles": snapshot.vehicles,
            "intersection_stats": None,
        }
    if not isinstance(snapshot, Mapping):
        return {}
    summary = snapshot.get("summary") if isinstance(snapshot.get("summary"), Mapping) else snapshot
    if not isinstance(summary, Mapping):
        summary = {}
    elapsed = snapshot.get("elapsed_seconds")
    if elapsed is None:
        elapsed = summary.get("elapsed_seconds")
    return {
        "elapsed_seconds": float(elapsed or 0.0),
        "intersections": dict(summary.get("intersections") or {}),
        "metrics": summary.get("metrics") or snapshot.get("metrics") or {},
        "vehicles": summary.get("vehicles") or snapshot.get("vehicles") or (),
        "intersection_stats": dict(summary.get("intersection_stats") or {}),
    }


def _metric_value(metrics: Any, key: str) -> float | None:
    if metrics is None:
        return None
    if isinstance(metrics, Mapping):
        return _finite(metrics.get(key))
    return _finite(getattr(metrics, key, None))


def _lane_value(lane: Any, key: str) -> Any:
    if isinstance(lane, Mapping):
        return lane.get(key)
    return getattr(lane, key, None)


def _vehicle_value(vehicle: Any, key: str) -> Any:
    if isinstance(vehicle, Mapping):
        return vehicle.get(key)
    return getattr(vehicle, key, None)


def _intersection_lanes(intersection: Any) -> Mapping[str, Any]:
    if isinstance(intersection, Mapping):
        return dict(intersection.get("lanes") or {})
    return dict(getattr(intersection, "lanes", {}) or {})


def snapshot_region_stats(
    snapshot: Any,
    intersection_ids: Sequence[str] | None = None,
) -> dict[str, float | None]:
    frame = _as_mapping(snapshot)
    intersections = dict(frame.get("intersections") or {})
    allowed = {str(item) for item in intersection_ids} if intersection_ids is not None else None
    incoming_halting: list[float] = []
    queue_m: list[float] = []
    speed_w: list[tuple[float, float]] = []
    waiting_sum = 0.0
    waiting_n = 0
    spill_overflow = 0.0
    spill_exposed = 0.0
    incoming_count = 0.0
    outgoing_count = 0.0
    local_lane_ids: set[str] = set()
    local_iids: set[str] = set()

    for intersection_id, intersection in intersections.items():
        iid = str(intersection_id)
        if allowed is not None and iid not in allowed:
            continue
        local_iids.add(iid)
        for lane_id, lane in _intersection_lanes(intersection).items():
            local_lane_ids.add(str(lane_id))
            role = str(_lane_value(lane, "role") or "")
            vehicle_count = float(_lane_value(lane, "vehicle_count") or 0)
            if role in {"outgoing"}:
                outgoing_count += vehicle_count
            if role and role not in {"incoming", "both"}:
                continue
            incoming_count += vehicle_count
            incoming_halting.append(float(_lane_value(lane, "halting_count") or 0))
            q = _finite(_lane_value(lane, "queue_length_m"))
            length = _finite(_lane_value(lane, "lane_length_m"))
            if q is not None:
                queue_m.append(q)
            if length is not None and length > 0:
                spill_exposed += 1.0
                if q is not None and q + 1e-9 >= length:
                    spill_overflow += 1.0
            speed = _finite(_lane_value(lane, "mean_speed"))
            if speed is not None:
                speed_w.append((speed, max(vehicle_count, 1.0)))
            wait = _finite(_lane_value(lane, "waiting_time"))
            if wait is not None:
                waiting_sum += wait
                waiting_n += 1

    avg_queue = (
        sum(incoming_halting) / len(incoming_halting) if incoming_halting else None
    )
    max_queue = max(queue_m) if queue_m else None
    spillback = (
        100.0 * spill_overflow / spill_exposed if spill_exposed > 0 else None
    )
    weighted_speed = None
    if speed_w:
        weighted_speed = sum(speed * weight for speed, weight in speed_w) / sum(
            weight for _, weight in speed_w
        )
    if allowed is None:
        mean_speed = _metric_value(frame.get("metrics"), "mean_speed")
        if mean_speed is None:
            mean_speed = weighted_speed
    else:
        mean_speed = weighted_speed

    waiting_time = waiting_sum if waiting_n else None
    if waiting_time is None and allowed is None:
        waiting_time = _metric_value(frame.get("metrics"), "total_waiting_time")

    if allowed is None:
        hard_braking = _metric_value(frame.get("metrics"), "hard_braking_events")
    else:
        hard_braking = _local_hard_braking(frame, local_iids, local_lane_ids)

    arrived = _metric_value(frame.get("metrics"), "arrived_vehicles")

    extra_stats = frame.get("intersection_stats") or {}
    if extra_stats and allowed is not None:
        wait_extra = 0.0
        wait_n = 0
        brake_extra = 0.0
        brake_n = 0
        for iid in local_iids:
            item = extra_stats.get(iid) or {}
            wait = _finite(item.get("incoming_waiting_time"))
            if wait is not None:
                wait_extra += wait
                wait_n += 1
            brake = _finite(item.get("hard_braking_events"))
            if brake is not None:
                brake_extra += brake
                brake_n += 1
        if waiting_time is None and wait_n:
            waiting_time = wait_extra
        if hard_braking is None and brake_n:
            hard_braking = brake_extra

    return {
        "avg_queue_veh": avg_queue,
        "max_queue_m": max_queue,
        "spillback_pct": spillback,
        "mean_speed_mps": mean_speed,
        "arrived_vehicles": arrived,
        "waiting_time_s": waiting_time,
        "hard_braking_events": hard_braking,
        "halting_vehicles": _metric_value(frame.get("metrics"), "halting_vehicles")
        if allowed is None
        else (sum(incoming_halting) if incoming_halting else None),
        "incoming_vehicle_count": incoming_count,
        "outgoing_vehicle_count": outgoing_count,
        "elapsed_seconds": float(frame.get("elapsed_seconds") or 0.0),
        "local_vehicle_ids": _local_vehicle_ids(frame, local_iids, local_lane_ids),
        "all_vehicle_ids": _all_vehicle_ids(frame),
    }


def _all_vehicle_ids(frame: Mapping[str, Any]) -> set[str] | None:
    vehicles = frame.get("vehicles") or ()
    if not vehicles:
        return None
    ids = set()
    for vehicle in vehicles:
        vid = _vehicle_value(vehicle, "vehicle_id")
        if vid:
            ids.add(str(vid))
    return ids


def _local_vehicle_ids(
    frame: Mapping[str, Any],
    local_iids: set[str],
    local_lane_ids: set[str],
) -> set[str] | None:
    vehicles = frame.get("vehicles") or ()
    if not vehicles:
        return None
    ids: set[str] = set()
    for vehicle in vehicles:
        lane_id = str(_vehicle_value(vehicle, "lane_id") or "")
        next_iid = _vehicle_value(vehicle, "next_intersection_id")
        if lane_id in local_lane_ids or (next_iid and str(next_iid) in local_iids):
            vid = _vehicle_value(vehicle, "vehicle_id")
            if vid:
                ids.add(str(vid))
    return ids


def _local_hard_braking(
    frame: Mapping[str, Any],
    local_iids: set[str],
    local_lane_ids: set[str],
) -> float | None:
    vehicles = frame.get("vehicles") or ()
    if not vehicles:
        return None
    total = 0.0
    found = False
    for vehicle in vehicles:
        lane_id = str(_vehicle_value(vehicle, "lane_id") or "")
        next_iid = _vehicle_value(vehicle, "next_intersection_id")
        if lane_id in local_lane_ids or (next_iid and str(next_iid) in local_iids):
            value = _finite(_vehicle_value(vehicle, "hard_braking_events"))
            if value is not None:
                total += value
                found = True
    return total if found else 0.0


def _snapshots_in_window(
    snapshots: Sequence[Any],
    start: float,
    end: float,
) -> list[Any]:
    selected = []
    for snap in snapshots:
        elapsed = _as_mapping(snap).get("elapsed_seconds")
        if elapsed is None:
            continue
        if start - 1e-9 <= float(elapsed) <= end + 1e-9:
            selected.append(snap)
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


def _throughput_delta(last: Mapping[str, Any], baseline: Mapping[str, Any]) -> float | None:
    last_ids = last.get("local_vehicle_ids")
    base_ids = baseline.get("local_vehicle_ids")
    all_last = last.get("all_vehicle_ids")
    if isinstance(base_ids, set) and isinstance(last_ids, set) and isinstance(all_last, set):
        left_network = base_ids - all_last
        left_region = (base_ids & all_last) - last_ids
        return float(len(left_network) + len(left_region))
    arrived_delta = _delta(last.get("arrived_vehicles"), baseline.get("arrived_vehicles"))
    if arrived_delta is not None:
        return arrived_delta
    return None


def compute_event_window_metrics(
    snapshots: Sequence[Any],
    *,
    event_start: float,
    event_end: float,
    offsets_seconds: Sequence[float],
    include_active_span: bool = True,
    intersection_ids: Sequence[str] | None = None,
) -> dict[str, Any]:
    if not snapshots:
        return {"windows": {}, "note": "no snapshots"}
    first = snapshot_region_stats(snapshots[0], intersection_ids)
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
    baseline = (
        snapshot_region_stats(baseline_snaps[-1], intersection_ids)
        if baseline_snaps
        else first
    )
    windows: dict[str, Any] = {}
    for label, start, end in zip(labels, starts, ends):
        selected = _snapshots_in_window(snapshots, start, end)
        if not selected:
            windows[label] = None
            continue
        stats = [snapshot_region_stats(item, intersection_ids) for item in selected]
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
            "throughput_delta_veh": (
                _throughput_delta(last, baseline)
                if intersection_ids is not None
                else _delta(last["arrived_vehicles"], baseline["arrived_vehicles"])
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
        "intersection_ids": list(intersection_ids) if intersection_ids is not None else None,
    }


def compute_local_event_window_metrics(
    snapshots: Sequence[Any],
    *,
    event_start: float,
    event_end: float,
    offsets_seconds: Sequence[float],
    include_active_span: bool = True,
    intersection_ids: Sequence[str],
) -> dict[str, Any]:
    raw = compute_event_window_metrics(
        snapshots,
        event_start=event_start,
        event_end=event_end,
        offsets_seconds=offsets_seconds,
        include_active_span=include_active_span,
        intersection_ids=intersection_ids,
    )
    return {
        "windows": raw.get("windows") or {},
        "local_intersection_ids": list(intersection_ids),
        "local_avg_queue_veh": raw.get("window_avg_queue_veh"),
        "local_max_queue_m": raw.get("window_max_queue_m"),
        "local_spillback_pct": raw.get("window_spillback_pct"),
        "local_mean_speed_mps": raw.get("window_mean_speed_mps"),
        "local_throughput_delta": raw.get("window_throughput_delta_veh"),
        "local_waiting_time_delta": raw.get("window_waiting_time_delta_s"),
        "local_hard_braking_delta": raw.get("window_hard_braking_delta"),
        "note": raw.get("note"),
    }


def compute_recovery_metrics(
    snapshots: Sequence[Any],
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

    episode_end = float(_as_mapping(snapshots[-1])["elapsed_seconds"])
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
                recovered_at = float(_as_mapping(snap)["elapsed_seconds"])
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
