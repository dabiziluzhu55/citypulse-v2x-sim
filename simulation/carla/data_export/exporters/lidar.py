"""Roadside lidar exporter: one PLY point cloud per captured scan."""

from __future__ import annotations

from typing import Any, Dict

from ..base import register
from ..sensors import ManagedSensor
from ._common import FileSinkExporter


@register("lidar")
class LidarExporter(FileSinkExporter):
    """Saves ``sensor.lidar.ray_cast`` measurements as PLY point clouds
    (x/y/z + intensity, written by CARLA's ``save_to_disk``)."""

    ext = ".ply"

    def _save(self, data: Any, path) -> None:
        data.save_to_disk(str(path))

    def _manifest_extra(self, ms: ManagedSensor, frame: int, ts: float,
                        data: Any) -> Dict[str, Any]:
        try:
            return {"points": len(data)}  # LidarMeasurement: number of points
        except (TypeError, ValueError):
            return {}
