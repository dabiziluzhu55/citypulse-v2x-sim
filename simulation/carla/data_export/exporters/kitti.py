"""KITTI-format exporter: per-lidar velodyne ``.bin`` clouds, per-camera
PNGs and one ``calib.txt`` linking them.

Reference-KITTI layout with roadside multi-sensor extensions::

    <run>/kitti/
    ├── calib.txt
    ├── image_<cam>/000000.png …        # per camera, frame number from 0
    └── velodyne_<lidar>/000000.bin …   # per lidar, x/y/z/intensity f32 LE

``calib.txt`` (written once at setup — roadside sensors are static)::

    P_<cam>: 12 floats                  # K @ [R_cw | t_cw]   (3x4)
    R0_rect: 1 0 0 0 1 0 0 0 1          # identity, P already includes rectification
    Tr_velo_<lidar>_to_cam_<cam>: 12 floats   # [T_cw_cam @ T_wc_velo][:3]

All matrices are CARLA/Unreal left-handed (camera-local x/y/z =
forward/right/up) and numerically identical to the per-sensor
``calibration.json`` documents — no remapping to the KITTI camera frame;
consumers wanting the standard right-handed convention must apply the
documented rotation themselves.

This is a *consumer* kind: no sensor has ``type == "kitti"``, the exporter
manager feeds it the config's rgb_camera + lidar specs and it spawns its own
copies (names prefixed ``kitti_`` to avoid clashing with other exporters'
sensor names in the frame registry).
"""

from __future__ import annotations

import dataclasses
import struct
from typing import Any, Dict, List, Optional

from ..base import register
from ..calibration import extrinsic_matrices, intrinsic_matrix
from ..sensors import ManagedSensor
from ._common import FileSinkExporter


def _mat_mul(a: List[List[float]], b: List[List[float]]) -> List[List[float]]:
    """Row-major matrix product ``a @ b`` (any compatible sizes)."""
    return [[sum(a[i][k] * b[k][j] for k in range(len(b)))
             for j in range(len(b[0]))] for i in range(len(a))]


def _bin_payload(data: Any) -> bytes:
    """KITTI velodyne payload: x, y, z, intensity as little-endian float32,
    point order preserved.

    Preferred path is zero-copy-ish: ``carla.LidarMeasurement.raw_data`` is
    the flat 4×float32-per-point buffer, byte-compatible with the .bin
    layout.  Falls back to per-point ``struct.pack`` when raw_data is not a
    plain buffer (e.g. older CARLA versions).

    Raises:
        ValueError: the data exposes neither a usable raw_data nor points.
    """
    try:
        return bytes(memoryview(data.raw_data).cast("f"))
    except Exception:
        pass
    try:
        out = bytearray()
        for i in range(len(data)):
            out += struct.pack("<4f", *data[i])  # [x, y, z, intensity]
        return bytes(out)
    except Exception:
        raise ValueError("lidar data has neither raw_data nor point access")


@register("kitti")
class KittiExporter(FileSinkExporter):
    """Spawns its own camera + lidar copies and writes KITTI-format files.

    Frame numbers start at 0 (KITTI convention) while the capture sequence
    starts at 1, so ``_save_frame`` derives the name from ``seq - 1``.  In
    synchronous mode every sensor fires once per world tick, so a PNG and a
    .bin sharing a frame number come from the same tick (cross-checkable via
    the manifest rows' ``export_frame``).
    """

    def setup(self, ctx) -> None:
        # Rename the spawned copies so the frame registry / meta.json never
        # collide with rgb_camera/lidar exporter entries of the same name.
        self._specs = [dataclasses.replace(s, name="kitti_" + s.name)
                       for s in self._specs]
        self._orig: Dict[str, str] = {}      # spawn name → config name
        self._subdir: Dict[str, str] = {}    # spawn name → image_*/velodyne_*
        super().setup(ctx)                   # spawn_all + raise on empty
        for ms in self._managed:
            short = ms.spec.name[len("kitti_"):]
            self._orig[ms.spec.name] = short
            prefix = "velodyne_" if ms.spec.type == "lidar" else "image_"
            self._subdir[ms.spec.name] = prefix + short
            (ctx.run_output.kitti_dir() / self._subdir[ms.spec.name]).mkdir(
                parents=True, exist_ok=True)
            ctx.run_output.sensor_dir(ms.spec.name)  # sensors/<name>/manifest.jsonl
        self._write_calib()

    def _write_calib(self) -> None:
        """One ``kitti/calib.txt`` for the whole run (sensors are static);
        a failure only logs a warning and must not disable the exporter."""
        ctx = self._ctx
        try:
            cams = [ms.spec for ms in self._managed
                    if ms.spec.type != "lidar"]
            velos = [ms.spec for ms in self._managed
                     if ms.spec.type == "lidar"]
            lines = [
                "# KITTI-extension calibration, written once at setup "
                "(roadside sensors are static)",
                "# frame: CARLA left-handed world; camera-local "
                "x/y/z = forward/right/up",
                "# P_<cam> = K @ [R_cw | t_cw]  (KITTI P matrix = "
                "intrinsics x extrinsics)",
                "# Tr_velo_<lidar>_to_cam_<cam> = "
                "[T_cw_cam @ T_wc_velo][:3]  (velodyne local -> cam)",
                "# R0_rect = identity (P already includes rectification)",
            ]
            for cam in cams:
                K = intrinsic_matrix(
                    int(cam.params["width"]), int(cam.params["height"]),
                    float(cam.params["fov"]))
                _, T_cw = extrinsic_matrices(cam.transform)
                P = _mat_mul(K, [T_cw[i][:4] for i in range(3)])
                lines.append("P_{}: {}".format(
                    self._orig[cam.name],
                    " ".join(f"{v:.8e}" for row in P for v in row)))
            lines.append("R0_rect: 1 0 0 0 1 0 0 0 1")
            for velo in velos:
                cam = self._match_cam(velo)
                if cam is None:
                    ctx.logger.warning(
                        "[export] kitti: no camera to associate "
                        "'%s' with in calib.txt", velo.name)
                    continue
                T_wc_v, _ = extrinsic_matrices(velo.transform)
                _, T_cw_c = extrinsic_matrices(cam.transform)
                Tr = _mat_mul([T_cw_c[i][:4] for i in range(3)], T_wc_v)
                lines.append("Tr_velo_{}_to_cam_{}: {}".format(
                    self._orig[velo.name], self._orig[cam.name],
                    " ".join(f"{v:.8e}" for row in Tr for v in row)))
            path = ctx.run_output.kitti_dir() / "calib.txt"
            path.write_text("\n".join(lines) + "\n", encoding="utf-8")
        except Exception as exc:
            ctx.logger.warning(
                "[export] kitti: could not write calib.txt — %s", exc)

    def _match_cam(self, velo_spec) -> Optional[Any]:
        """Camera spec associated with a lidar: longest prefix match on the
        config name (``demo_19_lidar_01`` → ``demo_19``; the trailing
        underscore avoids demo_1/demo_14 confusion), else the first camera.
        Returns None when the config has no camera at all."""
        cams = [ms.spec for ms in self._managed if ms.spec.type != "lidar"]
        short = self._orig[velo_spec.name]
        best = None
        for cam in cams:
            if short.startswith(self._orig[cam.name] + "_") and \
                    (best is None or len(self._orig[cam.name]) >
                     len(self._orig[best.name])):
                best = cam
        return best or (cams[0] if cams else None)

    # -- per-frame plumbing ---------------------------------------------

    def _result_path_prefix(self, ms: ManagedSensor) -> str:
        """Rows reference the kitti/ sub-directories, not sensors/."""
        return f"kitti/{self._subdir[ms.name]}"

    def _save_frame(self, ms: ManagedSensor, frame: int, ts: float,
                    data: Any, seq: int) -> Dict[str, Any]:
        """Worker-thread entry: KITTI file name from the capture sequence
        (0-based, per KITTI convention — ``seq - 1``)."""
        ext = ".bin" if ms.spec.type == "lidar" else ".png"
        fname = f"{seq - 1:06d}{ext}"   # KITTI: frame number from 0
        path = self._ctx.run_output.kitti_dir() / self._subdir[ms.name] / fname
        if ms.spec.type == "lidar":
            path.write_bytes(_bin_payload(data))
        else:
            data.save_to_disk(str(path))
        result: Dict[str, Any] = {
            "world_frame": frame,
            "sim_time": ts,
            "file": fname,
        }
        extra = self._manifest_extra(ms, frame, ts, data)
        if extra:
            result["extra"] = extra
        return result

    def _manifest_extra(self, ms: ManagedSensor, frame: int, ts: float,
                        data: Any) -> Dict[str, Any]:
        try:
            if ms.spec.type == "lidar":
                return {"points": len(data)}
            return {"width": int(data.width), "height": int(data.height),
                    "fov": float(data.fov)}
        except (AttributeError, TypeError, ValueError):
            return {}
