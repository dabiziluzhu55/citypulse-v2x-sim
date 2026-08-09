# algorithms/v2x/coverage.py
"""RSU 感知覆盖：covered_lanes + 可选半径；next_signal 仅作数据缺失 fallback。"""
from __future__ import annotations

from typing import Optional

from .entities import RSU


def is_in_rsu_coverage(
    lane_id: Optional[str],
    position: Optional[tuple[float, float]],
    rsu: RSU,
    detection_radius_m: Optional[float] = None,
) -> bool:
    lane_covered = lane_id is not None and lane_id in rsu.covered_lane_ids
    distance_covered = False
    if (
        detection_radius_m is not None
        and rsu.position is not None
        and position is not None
    ):
        dx = position[0] - rsu.position[0]
        dy = position[1] - rsu.position[1]
        distance_covered = (dx * dx + dy * dy) ** 0.5 <= detection_radius_m
    return lane_covered or distance_covered


def should_use_next_signal_fallback(
    lane_id: Optional[str],
    position: Optional[tuple[float, float]],
    rsu: RSU,
    next_signal_intersection_id: Optional[str],
    detection_radius_m: Optional[float],
) -> bool:
    """仅在 lane_id 缺失且半径判定无法执行时，才用 next_signal 兜底。"""
    radius_available = (
        detection_radius_m is not None
        and rsu.position is not None
        and position is not None
    )
    if lane_id is not None or radius_available:
        return False
    return next_signal_intersection_id == rsu.rsu_id
