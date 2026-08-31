"""Algorithm-side versioned SPaT v2 and MAP helpers for Safe-MP.

Only the current/next transition envelope is exposed.  The module never
fabricates a future committed cycle and does not depend on the backend.
"""

from __future__ import annotations

import math
from typing import Any, Mapping


SCHEMA_VERSION = "2.0"
SOURCE = "road/RSU"


def _finite(value: Any, default: float = 0.0) -> float:
    try:
        result = float(value)
    except (TypeError, ValueError):
        return default
    return result if math.isfinite(result) else default


def _phase(meta: Mapping[str, Any], phase_id: int) -> Mapping[str, Any]:
    phases = meta.get("phases", {})
    if not isinstance(phases, Mapping):
        return {}
    value = phases.get(str(phase_id), phases.get(phase_id, {}))
    return value if isinstance(value, Mapping) else {}


def _phase_order(meta: Mapping[str, Any]) -> list[int]:
    result: list[int] = []
    for value in meta.get("phase_order", ()):
        try:
            result.append(int(value))
        except (TypeError, ValueError):
            continue
    return result


def _transition_envelope(
    *,
    state: Mapping[str, Any],
    phase_meta: Mapping[str, Any],
    sim_time: float,
) -> tuple[dict[str, float], float, int | None]:
    stage = str(state.get("stage", "GREEN")).upper()
    elapsed = max(0.0, _finite(state.get("stage_elapsed")))
    green = max(
        0.0,
        _finite(phase_meta.get("green_seconds", phase_meta.get("green", 0.0))),
    )
    yellow = max(
        0.0,
        _finite(phase_meta.get("yellow_seconds", phase_meta.get("yellow", 0.0))),
    )
    clearance = max(
        0.0,
        _finite(phase_meta.get("clearance_seconds", phase_meta.get("all_red", 0.0))),
    )
    if stage == "GREEN":
        remaining = max(0.0, green - elapsed)
        next_start = sim_time + remaining + yellow + clearance
        likely = remaining
    elif stage in {"YELLOW", "CLEARANCE"}:
        transition_total = yellow if stage == "YELLOW" else clearance
        remaining = max(0.0, transition_total - elapsed)
        next_start = sim_time + remaining
        likely = remaining
    else:
        remaining = 0.0
        next_start = sim_time
        likely = 0.0
    maximum = likely + yellow + clearance
    envelope = {
        "min_s": max(0.0, min(likely, remaining)),
        "likely_s": max(0.0, likely),
        "max_s": max(0.0, maximum),
    }
    pending = state.get("pending_phase")
    try:
        pending_phase = None if pending is None else int(pending)
    except (TypeError, ValueError):
        pending_phase = None
    return envelope, next_start, pending_phase


def build_spat_v2_payload(
    intersection_id: str,
    state: Mapping[str, Any],
    metadata: Mapping[str, Any],
    *,
    sim_time: float,
    controller_epoch: int,
    plan_id: str,
    confidence: float = 1.0,
    source: str = SOURCE,
) -> dict[str, Any]:
    order = _phase_order(metadata)
    try:
        current_phase = int(
            state.get("current_phase", order[0] if order else 0)
        )
    except (TypeError, ValueError):
        current_phase = order[0] if order else 0
    phase_meta = _phase(metadata, current_phase)
    envelope, next_start, pending_phase = _transition_envelope(
        state=state,
        phase_meta=phase_meta,
        sim_time=float(sim_time),
    )
    if pending_phase is None and order:
        try:
            next_phase = order[(order.index(current_phase) + 1) % len(order)]
        except ValueError:
            next_phase = order[0]
    else:
        next_phase = pending_phase

    lanes = state.get("lanes", {})
    conn_states: list[dict[str, Any]] = []
    if isinstance(lanes, Mapping):
        for lane in lanes.values():
            if not isinstance(lane, Mapping):
                continue
            values = lane.get("connection_signal_states", ())
            if isinstance(values, list):
                conn_states.extend(
                    dict(value)
                    for value in values
                    if isinstance(value, Mapping)
                )

    now = float(sim_time)
    valid_until = now + 5.0
    return {
        "schema_version": SCHEMA_VERSION,
        "source": source,
        "intersection_id": str(intersection_id),
        "controller_epoch": int(controller_epoch),
        "plan_id": str(plan_id),
        "program_id": str(
            metadata.get("program_id", metadata.get("period", "unknown"))
        ),
        "phase_order": list(order),
        "current_phase": current_phase,
        "stage": str(state.get("stage", "GREEN")),
        "stage_elapsed": max(0.0, _finite(state.get("stage_elapsed"))),
        "pending_phase": pending_phase,
        "connection_signal_states": conn_states,
        # Keep legacy fields populated for the existing v2 draft validator.
        "remaining_time_s": envelope["likely_s"],
        "next_stage": next_phase,
        "next_stage_start_time": next_start,
        "schedule_status": "predicted",
        "transition_envelope": envelope,
        "generated_at": now,
        "valid_from": now,
        "valid_until": valid_until,
        "spat_valid_until": valid_until,
        "advisory_valid_until": valid_until,
        "confidence": min(1.0, max(0.0, _finite(confidence))),
        "vtc_eligible": False,
        # Explicitly absent in the control sense; this None is not a claimed
        # future schedule and is rejected by any VTC eligibility check.
        "committed_movement_windows": None,
    }


def build_map_v2_payload(
    intersection_id: str,
    metadata: Mapping[str, Any],
    *,
    topology_version: str = "unknown",
) -> dict[str, Any]:
    lanes_meta = metadata.get("lanes", {})
    lanes: list[dict[str, Any]] = []
    if isinstance(lanes_meta, Mapping):
        for raw_id, raw_lane in lanes_meta.items():
            lane = raw_lane if isinstance(raw_lane, Mapping) else {}
            try:
                lane_index = int(lane.get("lane_index", 0))
            except (TypeError, ValueError):
                lane_index = 0
            lanes.append(
                {
                    "lane_id": str(raw_id),
                    "edge_id": str(lane.get("edge_id", "")),
                    "lane_index": lane_index,
                    "role": str(lane.get("role", "")),
                    "approach_id": lane.get("approach_id"),
                    "movements": [str(v) for v in lane.get("movements", ())],
                    "downstream_lane_ids": [
                        str(v) for v in lane.get("downstream_lane_ids", ())
                    ],
                }
            )
    connections = [
        dict(value)
        for value in metadata.get("connections", ())
        if isinstance(value, Mapping)
    ]
    phases = metadata.get("phases", {})
    return {
        "schema_version": SCHEMA_VERSION,
        "source": SOURCE,
        "intersection_id": str(intersection_id),
        "topology_version": str(topology_version),
        "phase_order": _phase_order(metadata),
        "phases": phases,
        "lanes": lanes,
        "connections": connections,
        "direct_neighbors": [
            str(v) for v in metadata.get("direct_neighbors", ())
        ],
    }


def build_invalidation(
    *,
    intersection_id: str,
    controller_epoch: int,
    plan_id: str,
    sim_time: float,
    reason: str,
) -> dict[str, Any]:
    return {
        "schema_version": SCHEMA_VERSION,
        "source": SOURCE,
        "message_type": "SPaT_INVALIDATION",
        "intersection_id": str(intersection_id),
        "controller_epoch": int(controller_epoch),
        "plan_id": str(plan_id),
        "generated_at": float(sim_time),
        "valid": False,
        "schedule_status": "invalidated",
        "reason": str(reason),
        "vtc_eligible": False,
    }


def validate_spat_v2(payload: Mapping[str, Any]) -> None:
    required = {
        "schema_version", "source", "intersection_id", "controller_epoch",
        "plan_id", "phase_order", "current_phase", "stage",
        "transition_envelope", "generated_at", "valid_until", "confidence",
        "schedule_status", "vtc_eligible",
    }
    missing = sorted(required - set(payload))
    if missing:
        raise ValueError(f"SPaT v2 missing fields: {missing}")
    if payload["schema_version"] != SCHEMA_VERSION or payload["source"] != SOURCE:
        raise ValueError("SPaT v2 schema/source mismatch")
    if payload["schedule_status"] != "predicted" or payload["vtc_eligible"]:
        raise ValueError("SPaT v2 must remain predicted and VTC-ineligible")
    envelope = payload["transition_envelope"]
    if not isinstance(envelope, Mapping):
        raise ValueError("SPaT v2 transition_envelope must be a mapping")
    try:
        values = [float(envelope[key]) for key in ("min_s", "likely_s", "max_s")]
    except (KeyError, TypeError, ValueError) as exc:
        raise ValueError("SPaT v2 envelope is incomplete") from exc
    if any(not math.isfinite(value) or value < 0.0 for value in values):
        raise ValueError("SPaT v2 envelope values must be finite and non-negative")
    if values != sorted(values):
        raise ValueError("SPaT v2 envelope must satisfy min <= likely <= max")


def validate_map_v2(payload: Mapping[str, Any]) -> None:
    required = {
        "schema_version", "source", "intersection_id", "phase_order",
        "lanes", "connections",
    }
    missing = sorted(required - set(payload))
    if missing:
        raise ValueError(f"MAP v2 missing fields: {missing}")
    if payload["schema_version"] != SCHEMA_VERSION or payload["source"] != SOURCE:
        raise ValueError("MAP v2 schema/source mismatch")
