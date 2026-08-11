"""Unified per-run schema for the VRC pre-registration protocol (Task A).

Every evaluation method emits one normalized run dict per seed.  This module
owns the frozen field contract, the INVALID determination, and the
availability/provenance assembly.  No metric values are computed here; the
live collector and the TripInfo backfill provide them.
"""

from __future__ import annotations

from typing import Any, Dict, Mapping

MANDATORY_NUMERIC_FIELDS = (
    "all_waiting_total_s",
    "unfinished_waiting_total_s",
    "departed_count",
    "end_waiting_total_s",
    "end_queue_veh",
    "avg_travel_time_s",
    "avg_waiting_time_s",
    "avg_queue_length_veh",
    "arrived_count",
    "simulation_duration_s",
    "emergency_braking_event_count",
    "passage_count",
    "ai_frame_interval_seconds",
)

# Fuel is a schema field but its value may legitimately be missing (no
# emission output); that must NOT invalidate the run.  The fuel gate only
# activates after the four-check audit passes and data is available.
OPTIONAL_VALUE_FIELDS = frozenset({"fuel_intensity_l_per_100km"})

ALL_FIELDS = MANDATORY_NUMERIC_FIELDS + ("fuel_intensity_l_per_100km",)

REQUIRED_INPUT_FIELDS = ("method", "seed")

DEFAULT_PROVENANCE: Dict[str, str] = {
    "all_waiting_total_s": "tripinfo_all_departed",
    "unfinished_waiting_total_s": "tripinfo_unfinished",
    "departed_count": "tripinfo_records",
    "end_waiting_total_s": (
        "tripinfo_all_departed (== all_waiting_total_s, frozen redundancy)"
    ),
    "end_queue_veh": "collector_last_observer_frame",
    "avg_travel_time_s": "tripinfo_all_departed",
    "avg_waiting_time_s": "tripinfo_all_departed",
    "avg_queue_length_veh": "incoming_lane_halting_count",
    "fuel_intensity_l_per_100km": "tripinfo_fuel_totals",
    "arrived_count": "tripinfo_completed",
    "simulation_duration_s": "collector_finish_summary",
    "emergency_braking_event_count": "protocol_hard_braking_onset_delta",
    "passage_count": "protocol_controlled_intersection_passages",
    "ai_frame_interval_seconds": "observed_frame_delta_s",
}

AI_FRAME_INTERVAL_S = 1.0  # frozen observation interval (spec v1.1)


def schema_keys() -> set[str]:
    """Return the frozen top-level key set of one normalized run dict."""
    return (
        set(ALL_FIELDS)
        | set(REQUIRED_INPUT_FIELDS)
        | {"availability", "provenance"}
    )


def _availability_for(field: str, value: Any) -> str:
    if value is None:
        return "missing" if field in OPTIONAL_VALUE_FIELDS else "unavailable"
    return "available"


def normalize(row: Mapping[str, Any]) -> Dict[str, Any]:
    """Normalize one per-run row into the frozen schema.

    Returns a dict with ``valid`` (bool), ``invalid_reasons`` (list), the
    original fields, plus assembled ``availability`` and ``provenance``.
    Any mandatory field that is absent or None invalidates the run; the only
    exception is fuel intensity, whose absence is ``missing`` but valid.
    """
    invalid_reasons: list[str] = []
    data: Dict[str, Any] = dict(row)

    for key in REQUIRED_INPUT_FIELDS:
        if key not in data:
            invalid_reasons.append(f"missing top-level field: {key}")

    for field in ALL_FIELDS:
        if field not in data:
            invalid_reasons.append(f"missing field: {field}")
            continue
        value = data[field]
        if value is None and field not in OPTIONAL_VALUE_FIELDS:
            invalid_reasons.append(f"field is None: {field}")

    departed_count = data.get("departed_count")
    trip_records = data.get("trip_records")
    if (
        departed_count is not None
        and trip_records is not None
        and int(departed_count) != int(trip_records)
    ):
        invalid_reasons.append(
            f"departed_count={departed_count} != trip_records={trip_records}"
        )

    interval = data.get("ai_frame_interval_seconds")
    if interval is None:
        invalid_reasons.append("ai_frame_interval_seconds is None")
    elif abs(float(interval) - AI_FRAME_INTERVAL_S) > 1e-9:
        invalid_reasons.append(
            f"ai_frame_interval_seconds={interval} != {AI_FRAME_INTERVAL_S}"
        )

    availability: Dict[str, str] = {}
    provenance: Dict[str, str] = {}
    metric_sources = data.get("metric_sources") or {}
    for field in ALL_FIELDS:
        if field not in data:
            continue
        availability[field] = _availability_for(field, data[field])
        provenance[field] = str(
            metric_sources.get(field) or DEFAULT_PROVENANCE[field]
        )
    data["availability"] = availability
    data["provenance"] = provenance
    data["valid"] = not invalid_reasons
    data["invalid_reasons"] = invalid_reasons
    return data


def is_valid(normalized: Mapping[str, Any]) -> bool:
    """Return True only for a normalized run with no invalid reasons."""
    return bool(normalized.get("valid")) and not normalized.get(
        "invalid_reasons"
    )
