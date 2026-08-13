"""RGB camera exporter: one PNG per captured frame, the "street surveillance
camera" footage of the co-simulation, plus one ``calibration.json`` per
camera (intrinsics + extrinsics) written once at setup."""

from __future__ import annotations

from typing import Any, Dict

from ..base import register
from ..calibration import calibration_document
from ..sensors import ManagedSensor
from ._common import FileSinkExporter


@register("rgb_camera")
class RgbCameraExporter(FileSinkExporter):
    """Saves ``sensor.camera.rgb`` frames as PNG files (BGRA8 pixel data,
    encoded by CARLA's ``carla.Image.save_to_disk`` — no extra dependencies)."""

    ext = ".png"

    def setup(self, ctx) -> None:
        super().setup(ctx)  # spawn_all + raise when nothing was spawned
        self._write_calibration_files()

    def _write_calibration_files(self) -> None:
        """One ``calibration.json`` per spawned camera (intrinsic K + both
        extrinsic matrices).  Roadside cameras are static, so the document
        is written once at setup; a failure only logs a warning and must not
        disable the exporter."""
        ctx = self._ctx
        for ms in self._managed:
            spec = ms.spec
            try:
                doc = calibration_document(
                    spec.name, spec.type, spec.blueprint,
                    int(spec.params["width"]), int(spec.params["height"]),
                    float(spec.params["fov"]), spec.transform)
                ctx.run_output.write_sensor_json(spec.name, doc)
            except Exception as exc:
                ctx.logger.warning(
                    "[export] %s: could not write calibration.json — %s",
                    spec.name, exc)

    def _save(self, data: Any, path) -> None:
        data.save_to_disk(str(path))

    def _manifest_extra(self, ms: ManagedSensor, frame: int, ts: float,
                        data: Any) -> Dict[str, Any]:
        try:  # image size/fov are handy for downstream geometry
            return {"width": int(data.width), "height": int(data.height),
                    "fov": float(data.fov)}
        except (AttributeError, TypeError, ValueError):
            return {}
