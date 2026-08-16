"""Run output directory: layout, JSONL streams and atomic JSON writes.

Layout of one export run::

    <export-dir>/<map>/<run-ts=YYYYmmdd-HHMMSS>/
    ├── meta.json          # run summary (started at init, finalised at end)
    ├── run_config.json    # copy of the resolved export configuration
    ├── manifest.jsonl     # run-level per-tick index (one JSON object per line)
    └── sensors/<name>/    # per-sensor data files + manifest.jsonl

All writes are line-buffered and flushed per line, so a crash loses at most
one line; ``meta.json`` is written atomically (temp file + rename).
"""

from __future__ import annotations

import json
import os
import time
from pathlib import Path
from typing import Any, Dict, Optional, TextIO

from .base import ExportConfigError
from .config import ExportConfig, export_config_to_dict


class RunOutput:
    """Creates and manages one export run's output directory."""

    def __init__(self, root: str, map_name: str, config: ExportConfig) -> None:
        self._root = Path(root)
        self._map_name = map_name
        run_ts = time.strftime("%Y%m%d-%H%M%S")
        run_id = run_ts
        # 同秒同地图的多段导出(运行时 stop 后再次 start)会撞目录:以原子
        # mkdir 探测,已存在则追加 -2/-3 后缀,避免 manifest 追加/覆盖。
        suffix = 2
        while True:
            self.dir = self._root / map_name / run_id
            try:
                (self.dir / "sensors").mkdir(parents=True, exist_ok=False)
                break
            except FileExistsError:
                run_id = f"{run_ts}-{suffix}"
                suffix += 1
            except OSError as exc:
                raise ExportConfigError(
                    f"cannot create export output dir '{self.dir}': {exc}")
        self.run_id = run_id
        self._finalized = False

        self._meta_path = self.dir / "meta.json"
        self._meta: Dict[str, Any] = {
            "run_id": run_id,
            "map": map_name,
            "started_at": time.strftime("%Y-%m-%dT%H:%M:%S"),
            "aborted": False,
        }
        self._write_json_atomic(self._meta_path, self._meta)
        self._write_json_atomic(self.dir / "run_config.json",
                                export_config_to_dict(config))

        self._manifest_fh: TextIO = open(
            self.dir / "manifest.jsonl", "a", encoding="utf-8", buffering=1)
        self._sensor_fhs: Dict[str, TextIO] = {}

    # -- paths ----------------------------------------------------------

    def sensor_dir(self, name: str) -> Path:
        """Per-sensor output directory (created on first use)."""
        d = self.dir / "sensors" / name
        d.mkdir(parents=True, exist_ok=True)
        return d

    def kitti_dir(self) -> Path:
        """KITTI 输出根目录(首次使用时创建):``<run>/kitti/``。"""
        d = self.dir / "kitti"
        d.mkdir(parents=True, exist_ok=True)
        return d

    def write_sensor_json(self, name: str, obj: Dict[str, Any],
                          fname: str = "calibration.json") -> Path:
        """Atomically write ``obj`` as ``sensors/<name>/<fname>`` (temp file
        + rename, same mechanism as meta.json).  Returns the written path."""
        path = self.sensor_dir(name) / fname
        self._write_json_atomic(path, obj)
        return path

    # -- streams --------------------------------------------------------

    def write_manifest_line(self, rec: Dict[str, Any]) -> None:
        self._manifest_fh.write(json.dumps(rec, ensure_ascii=False) + "\n")

    def sensor_line(self, name: str, rec: Dict[str, Any]) -> None:
        """Append one line to ``sensors/<name>/manifest.jsonl`` (called from
        the sensor's writer thread)."""
        fh = self._sensor_fhs.get(name)
        if fh is None:
            fh = open(self.dir / "sensors" / name / "manifest.jsonl",
                      "a", encoding="utf-8", buffering=1)
            self._sensor_fhs[name] = fh
        fh.write(json.dumps(rec, ensure_ascii=False) + "\n")

    # -- finalise -------------------------------------------------------

    def finalize(self, extra: Optional[Dict[str, Any]] = None,
                 aborted: bool = False) -> None:
        """Merge run statistics into meta.json and close all streams."""
        if self._finalized:
            return
        self._finalized = True
        self._meta["aborted"] = bool(aborted)
        self._meta["ended_at"] = time.strftime("%Y-%m-%dT%H:%M:%S")
        if extra:
            self._meta.update(extra)
        self._write_json_atomic(self._meta_path, self._meta)
        for fh in self._sensor_fhs.values():
            try:
                fh.close()
            except Exception:
                pass
        self._sensor_fhs.clear()
        try:
            self._manifest_fh.close()
        except Exception:
            pass

    @staticmethod
    def _write_json_atomic(path: Path, obj: Dict[str, Any]) -> None:
        tmp = path.with_suffix(path.suffix + ".tmp")
        tmp.write_text(json.dumps(obj, ensure_ascii=False, indent=2),
                       encoding="utf-8")
        os.replace(str(tmp), str(path))

    @staticmethod
    def estimate_bytes(config: ExportConfig, duration_s: float) -> int:
        """Rough disk-space estimate for a run of ``duration_s`` seconds
        (PNG ≈ raw BGRA/4, PLY ≈ 40 B/point).  Used by ``--check-env``."""
        total = 0
        for s in config.sensors:
            if s.type == "rgb_camera":
                w = int(s.attributes.get("image_size_x", 1920))
                h = int(s.attributes.get("image_size_y", 1080))
                tick = float(s.attributes.get("sensor_tick", 0.05))
                total += int(w * h * 4 / 4.0 * (1.0 / tick) * duration_s)
            elif s.type == "lidar":
                pps = float(s.attributes.get("points_per_second", 500_000))
                total += int(pps * 40.0 * duration_s)
        return int(total)
