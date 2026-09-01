"""Shared congestion-level rules used by route styling and event detection."""

from __future__ import annotations

from typing import Literal


CongestionLevel = Literal["free", "slow", "congested", "severe"]

CONGESTION_LEVEL_RANK: dict[CongestionLevel, int] = {
    "free": 0,
    "slow": 1,
    "congested": 2,
    "severe": 3,
}


def classify_congestion(
    *,
    vehicle_count: int,
    halting_count: int,
    mean_speed: float,
    occupancy: float,
) -> tuple[CongestionLevel, float]:
    """Return the same level and score used by backend ``traffic_style``.

    SUMO/TraCI occupancy is expressed as a percentage in the current snapshot
    contract (for example, ``35.0`` means 35 percent).
    """

    if vehicle_count <= 0:
        return "free", 0.0
    halt_ratio = halting_count / max(vehicle_count, 1)
    if mean_speed <= 1.0 and (occupancy >= 35.0 or halt_ratio >= 0.6):
        return "severe", 1.0
    if mean_speed <= 3.0 and (occupancy >= 20.0 or halt_ratio >= 0.4):
        return "congested", 0.75
    if mean_speed <= 8.0 and occupancy >= 10.0:
        return "slow", 0.45
    return "free", 0.15
