"""空间排队长度估算"""

from __future__ import annotations

from collections.abc import Sequence

STOPPED_SPEED_MPS = 0.1
DEFAULT_VEHICLE_SPACE_M = 7.5

LaneVehicleSample = tuple[float, float, float, float]


def estimate_queue_length_m(
    *,
    lane_length_m: float,
    halting_count: int,
    occupancy: float,
    samples: Sequence[LaneVehicleSample] = (),
    default_vehicle_space: float = DEFAULT_VEHICLE_SPACE_M,
) -> float:
    """由停车车辆空间范围、停车数×车位、占有率三者取上界，并截断到车道长度

    ``samples`` 为已缓存的 (lane_position, speed, length, min_gap)，
    来自VehicleTelemetryTracker
    """

    if halting_count <= 0:
        return 0.0
    stopped = [item for item in samples if item[1] <= STOPPED_SPEED_MPS]
    spatial_extent = 0.0
    if stopped:
        queue_tail = min(
            max(0.0, position - length) for position, _, length, _ in stopped
        )
        spatial_extent = lane_length_m - queue_tail
        average_space = sum(length + gap for _, _, length, gap in stopped) / len(
            stopped
        )
    else:
        average_space = default_vehicle_space
    count_extent = halting_count * average_space
    occupancy_extent = lane_length_m * max(0.0, occupancy) / 100.0
    return min(
        lane_length_m,
        max(0.0, spatial_extent, count_extent, occupancy_extent),
    )
