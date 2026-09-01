"""当前交通和历史交通共同使用的车道/路口指标口径。"""

from __future__ import annotations

from typing import Any, Mapping, Sequence


CONGESTION_RANK = {"free": 0, "slow": 1, "congested": 2, "severe": 3}


def field(value: Any, name: str, default: Any = None) -> Any:
    if isinstance(value, Mapping):
        return value.get(name, default)
    return getattr(value, name, default)


def mapping_field(value: Any, name: str | None = None) -> Mapping[str, Any]:
    target = field(value, name, {}) if name is not None else value
    return target if isinstance(target, Mapping) else {}


def number_or_zero(value: Any) -> float:
    if value is None or value == "":
        return 0.0
    try:
        return float(value)
    except (TypeError, ValueError):
        return 0.0


def string_values(value: Any) -> list[str]:
    if value is None:
        return []
    if isinstance(value, str):
        values = [value]
    elif isinstance(value, Sequence) and not isinstance(value, (bytes, str)):
        values = list(value)
    else:
        return []
    return [str(item) for item in values if str(item)]


def lane_edge_id(lane_id: str, lane: Any) -> str:
    explicit = str(field(lane, "edge_id", "") or "")
    if explicit:
        return explicit
    if lane_id and "_" in lane_id:
        return lane_id.rsplit("_", 1)[0]
    return lane_id


def occupancy_pct(lane: Any) -> float:
    value = number_or_zero(field(lane, "occupancy_pct", field(lane, "occupancy", 0.0)))
    return max(0.0, min(100.0, value))


def lane_payload(
    *,
    intersection_id: str,
    lane_id: str,
    lane: Any,
    style: Any = None,
) -> dict[str, Any]:
    vehicle_count = int(round(number_or_zero(field(lane, "vehicle_count", 0))))
    halting_count = int(round(number_or_zero(field(lane, "halting_count", 0))))
    mean_speed = number_or_zero(
        field(lane, "mean_speed", field(lane, "mean_speed_mps", 0.0))
    )
    edge_id = lane_edge_id(lane_id, lane)
    style_level = field(style, "level") if style is not None else None
    row = {
        "intersection_id": intersection_id,
        "lane_id": lane_id,
        "edge_id": edge_id,
        "approach_id": field(lane, "approach_id"),
        "role": field(lane, "role", ""),
        "vehicle_count": vehicle_count,
        "halting_count": halting_count,
        "mean_speed_mps": mean_speed,
        "mean_speed_kmh": mean_speed * 3.6,
        "waiting_time_seconds": number_or_zero(
            field(lane, "waiting_time", field(lane, "waiting_time_seconds", 0.0))
        ),
        "occupancy_pct": occupancy_pct(lane),
        "lane_has_green": field(lane, "lane_has_green"),
        "signal_state": field(lane, "signal_state"),
        "downstream_lane_ids": string_values(
            field(lane, "downstream_lane_ids", ())
        ),
    }
    if style_level is not None:
        row["congestion_level"] = str(style_level)
        row["congestion_score"] = number_or_zero(field(style, "score", 0.0))
    return row


def aggregate_lane_rows(rows: Sequence[Mapping[str, Any]]) -> dict[str, Any]:
    vehicle_count = sum(int(number_or_zero(row.get("vehicle_count", 0))) for row in rows)
    halting_count = sum(int(number_or_zero(row.get("halting_count", 0))) for row in rows)
    speed_numerator = sum(
        number_or_zero(row.get("mean_speed_mps", 0.0))
        * int(number_or_zero(row.get("vehicle_count", 0)))
        for row in rows
    )
    occupancy = [number_or_zero(row.get("occupancy_pct", 0.0)) for row in rows]
    levels = [
        str(row["congestion_level"])
        for row in rows
        if row.get("congestion_level")
    ]
    highest_level = max(
        levels,
        key=lambda level: CONGESTION_RANK.get(level, -1),
        default="free",
    )
    return {
        "lane_count": len(rows),
        "vehicle_count": vehicle_count,
        "halting_count": halting_count,
        "mean_speed_mps": speed_numerator / vehicle_count if vehicle_count else 0.0,
        "mean_speed_kmh": speed_numerator / vehicle_count * 3.6
        if vehicle_count
        else 0.0,
        "waiting_time_seconds": sum(
            number_or_zero(row.get("waiting_time_seconds", 0.0)) for row in rows
        ),
        "occupancy_pct": sum(occupancy) / len(occupancy) if occupancy else 0.0,
        "congestion_level": highest_level,
        "congestion_levels": sorted(
            set(levels),
            key=lambda level: CONGESTION_RANK.get(level, -1),
            reverse=True,
        ),
    }


__all__ = [
    "CONGESTION_RANK",
    "aggregate_lane_rows",
    "field",
    "lane_edge_id",
    "lane_payload",
    "mapping_field",
    "number_or_zero",
    "occupancy_pct",
    "string_values",
]
