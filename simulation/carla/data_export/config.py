"""Export configuration: JSON schema, validation and sensor attribute
resolution.

The configuration file (see ``config/export.example.json`` in this repo)
and the files written by ``spectator_coords.py --save`` share one schema::

    {
      "version": 1,
      "output": {"fps": 30, "export_dir": "../../data/exports"},
      "sensors": [
        {"name": "cam_01", "type": "rgb_camera",
         "transform": {"x": ..., "y": ..., "z": ...,
                       "pitch": ..., "yaw": ..., "roll": ...},
         "width": 1920, "height": 1080, "fov": 90.0, "fps": 30},
        {"name": "lidar_01", "type": "lidar",
         "transform": {...},
         "channels": 32, "range": 80.0, "points_per_second": 500000,
         "rotation_frequency": 20, "upper_fov": 10.0, "lower_fov": -30.0}
      ]
    }

Unknown top-level keys (e.g. the spectator file's ``map``/``saved_at``) are
ignored, so a file produced by ``spectator_coords.py --save`` can be used
directly as an export configuration.

The ``transform`` is the sensor's absolute CARLA world pose — exactly what
``spectator_coords.py`` records from the spectator camera, no conversion
needed (both use the same ``carla.Transform`` pitch/yaw/roll convention).
"""

from __future__ import annotations

import json
import math
import os
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional, Set, Tuple

import toolchain_env  # 统一环境/路径配置:导出根目录默认 ../../data/exports
from .base import ExportConfigError

# Registry kind (== config sensors[].type) → CARLA blueprint id.  Extend this
# mapping when adding new sensor types.
BLUEPRINTS: Dict[str, str] = {
    "rgb_camera": "sensor.camera.rgb",
    "lidar": "sensor.lidar.ray_cast",
    # reserved for future exporters:
    # "semantic_segmentation": "sensor.camera.semantic_segmentation",
    # "depth": "sensor.camera.depth",
}

CONFIG_VERSION = 1

# Default directory for per-map export configurations (one file per map,
# e.g. config/export_configs/WestZone.json).  Files starting with "_" or "."
# are treated as templates/examples and excluded from name resolution.
EXPORT_CONFIG_DIR = "config/export_configs"

# Per-type parameter ranges: (key, lo, hi, must_be_int, default)
_CAMERA_PARAMS: List[Tuple[str, float, float, bool, Any]] = [
    ("width", 64.0, 4096.0, True, 1920),
    ("height", 64.0, 4096.0, True, 1080),
    ("fov", 1.0, 180.0, False, 90.0),
    ("fps", 1e-9, 120.0, False, None),  # default taken from output.fps
]

_LIDAR_PARAMS: List[Tuple[str, float, float, bool, Any]] = [
    ("channels", 1.0, 256.0, True, 32),
    ("range", 1.0, 1000.0, False, 80.0),
    ("points_per_second", 10000.0, 10_000_000.0, True, 500_000),
    ("rotation_frequency", 1.0, 120.0, False, 20.0),
    ("upper_fov", -90.0, 90.0, False, 10.0),
    ("lower_fov", -90.0, 90.0, False, -30.0),
]


@dataclass
class SensorSpec:
    """One validated sensor definition from the export configuration."""

    name: str
    type: str                       # registry kind, e.g. "rgb_camera"
    blueprint: str                  # CARLA blueprint id, e.g. "sensor.camera.rgb"
    transform: Dict[str, float]     # x, y, z, pitch, yaw, roll (CARLA world)
    attributes: Dict[str, Any] = field(default_factory=dict)  # resolved CARLA blueprint attrs
    params: Dict[str, Any] = field(default_factory=dict)      # original high-level params
    note: str = ""                  # alignment note (effective fps etc.)


# Default ZeroMQ bind address for the stream exporter (localhost only).
DEFAULT_STREAM_BIND = "tcp://127.0.0.1:19091"


@dataclass
class ExportConfig:
    """Validated export configuration."""

    version: int
    sensors: List[SensorSpec]
    output_fps: float
    output_dir: str
    write_threads: int = 2  # parallel encoder workers per sensor (1 = single-threaded)
    stream: Dict[str, Any] = field(default_factory=dict)  # output.stream block (see _validate_stream)


# ---------------------------------------------------------------------------
# Validation helpers
# ---------------------------------------------------------------------------

def _num(entry: Dict[str, Any], key: str, ctx: str) -> Optional[float]:
    """Read ``entry[key]`` as a finite float; ``None`` when absent."""
    v = entry.get(key)
    if v is None:
        return None
    try:
        f = float(v)
    except (TypeError, ValueError):
        raise ExportConfigError(f"{ctx}.{key}: expected a number, got {v!r}")
    if not math.isfinite(f):
        raise ExportConfigError(f"{ctx}.{key}: must be finite, got {v!r}")
    return f


def _check_param(entry: Dict[str, Any], key: str, lo: float, hi: float,
                 is_int: bool, default: Any, ctx: str) -> Any:
    """Validate one parameter against its range; return the resolved value."""
    v = _num(entry, key, ctx)
    if v is None:
        return default
    if not (lo <= v <= hi):
        raise ExportConfigError(f"{ctx}.{key}: out of range [{lo:g}, {hi:g}], got {v:g}")
    if is_int and v != int(v):
        raise ExportConfigError(f"{ctx}.{key}: must be an integer, got {v:g}")
    return int(v) if is_int else v


def _validate_transform(entry: Dict[str, Any], ctx: str) -> Dict[str, float]:
    tf = entry.get("transform")
    if not isinstance(tf, dict):
        raise ExportConfigError(f"{ctx}.transform: object with x/y/z required")
    transform: Dict[str, float] = {}
    for key in ("x", "y", "z"):
        v = _num(tf, key, ctx + ".transform")
        if v is None:
            raise ExportConfigError(f"{ctx}.transform.{key}: required number")
        transform[key] = v
    for key in ("pitch", "yaw", "roll"):
        transform[key] = _num(tf, key, ctx + ".transform") or 0.0
    return transform


def validate_sensor_entry(entry: Any, idx: int, output_fps: float,
                          seen: Set[str]) -> SensorSpec:
    """Validate one ``sensors[]`` entry, returning its :class:`SensorSpec`.

    Every error message is prefixed with ``sensors[N].<field>`` so the user
    can locate the offending entry without a schema lookup.
    """
    ctx = f"sensors[{idx}]"
    if not isinstance(entry, dict):
        raise ExportConfigError(f"{ctx}: expected an object, got {type(entry).__name__}")

    name = entry.get("name")
    if not isinstance(name, str) or not name.strip():
        raise ExportConfigError(f"{ctx}.name: non-empty string required")
    name = name.strip()
    if name in seen:
        raise ExportConfigError(f"{ctx}.name: duplicate sensor name '{name}'")
    seen.add(name)

    stype = entry.get("type")
    if stype not in BLUEPRINTS:
        raise ExportConfigError(
            f"{ctx}.type: unknown sensor type {stype!r} "
            f"(available: {', '.join(sorted(BLUEPRINTS))})")

    transform = _validate_transform(entry, ctx)

    params: Dict[str, Any] = {}
    if stype == "rgb_camera":
        for key, lo, hi, is_int, default in _CAMERA_PARAMS:
            if default is None:
                default = output_fps
            params[key] = _check_param(entry, key, lo, hi, is_int, default, ctx)
    else:  # lidar
        for key, lo, hi, is_int, default in _LIDAR_PARAMS:
            params[key] = _check_param(entry, key, lo, hi, is_int, default, ctx)
        if not (params["upper_fov"] > params["lower_fov"]):
            raise ExportConfigError(
                f"{ctx}: upper_fov ({params['upper_fov']:g}) must be > "
                f"lower_fov ({params['lower_fov']:g})")

    return SensorSpec(name=name, type=stype,
                      blueprint=BLUEPRINTS[stype], transform=transform,
                      params=params)


def _validate_stream(stream_raw: Any) -> Dict[str, Any]:
    """Validate the optional ``output.stream`` block of an export config.

    Recognised keys (all optional)::

        {"bind": "tcp://127.0.0.1:19091",   # PUB bind address (tcp:// or ipc://)
         "jpeg_quality": 85,                 # camera JPEG quality, 1-100
         "lidar_compress": true}             # zlib-compress lidar payloads

    Returns the validated block (defaults filled in).
    """
    if not isinstance(stream_raw, dict):
        raise ExportConfigError("output.stream: must be an object")
    bind = stream_raw.get("bind", DEFAULT_STREAM_BIND)
    if not isinstance(bind, str) or not bind.strip():
        raise ExportConfigError("output.stream.bind: non-empty string required")
    if not (bind.startswith("tcp://") or bind.startswith("ipc://")):
        raise ExportConfigError(
            "output.stream.bind: must start with 'tcp://' or 'ipc://', "
            f"got {bind!r}")
    return {
        "bind": bind.strip(),
        "jpeg_quality": _check_param(stream_raw, "jpeg_quality", 1.0, 100.0,
                                     True, 85, "output.stream"),
        "lidar_compress": bool(stream_raw.get("lidar_compress", True)),
    }


# ---------------------------------------------------------------------------
# Step-length alignment
# ---------------------------------------------------------------------------

def effective_fps(step_length: float, requested_fps: float) -> Tuple[float, str]:
    """Real capture rate given the simulation step length.

    In CARLA synchronous mode a sensor can fire at most once per ``world.tick``,
    so the actual rate is ``min(requested, 1/step_length)``.  Returns the
    effective rate and a human-readable note explaining the limitation (empty
    when the request is already achievable).
    """
    max_fps = 1.0 / step_length
    if requested_fps > max_fps + 1e-9:
        note = (f"step={step_length:g}s → one frame per tick ≈ {max_fps:.1f}fps; "
                f"{requested_fps:g}fps requires --step-length {1.0 / requested_fps:.6f}")
        return max_fps, note
    if requested_fps >= max_fps - 1e-9:
        return requested_fps, f"step-aligned — one frame per tick ({max_fps:.1f}fps)"
    return requested_fps, ""


def resolve_sensor_attributes(spec: SensorSpec, step_length: float) -> None:
    """Compute the final CARLA blueprint attributes for ``spec`` (in place),
    aligning the capture rate to ``step_length``.  Also fills ``spec.note``."""
    if spec.type == "rgb_camera":
        fps = float(spec.params["fps"])
        eff, note = effective_fps(step_length, fps)
        max_fps = 1.0 / step_length
        # sensor_tick semantics: ≤ step_length fires every tick; only below
        # 1/step does it skip ticks.  Use the exact values so metadata and
        # disk estimates stay truthful (1/sensor_tick == effective fps).
        tick = step_length if fps >= max_fps - 1e-9 else 1.0 / fps
        spec.attributes = {
            "image_size_x": int(spec.params["width"]),
            "image_size_y": int(spec.params["height"]),
            "fov": float(spec.params["fov"]),
            "sensor_tick": tick,
        }
        spec.note = note or f"capturing every tick ≈ {1.0 / tick:.1f}fps"
    else:  # lidar
        max_rf = 1.0 / step_length
        rf = float(spec.params["rotation_frequency"])
        if rf > max_rf:
            rf = max_rf
            spec.note = f"rotation_frequency clamped to 1/step ({max_rf:.1f} Hz)"
        spec.attributes = {
            "channels": int(spec.params["channels"]),
            "range": float(spec.params["range"]),
            "points_per_second": int(spec.params["points_per_second"]),
            "rotation_frequency": rf,
            "upper_fov": float(spec.params["upper_fov"]),
            "lower_fov": float(spec.params["lower_fov"]),
        }


# ---------------------------------------------------------------------------
# Load / dump / merge
# ---------------------------------------------------------------------------

def list_export_configs(config_dir: str = EXPORT_CONFIG_DIR) -> List[str]:
    """Names of the per-map export configurations available in ``config_dir``
    (files starting with "_" or "." are excluded)."""
    names: List[str] = []
    if os.path.isdir(config_dir):
        for fname in sorted(os.listdir(config_dir)):
            if fname.startswith(("_", ".")):
                continue
            if fname.endswith(".json"):
                names.append(fname[:-len(".json")])
    return names


def resolve_export_config_path(path_or_name: Optional[str] = None,
                               map_name: Optional[str] = None,
                               config_dir: str = EXPORT_CONFIG_DIR,
                               ) -> Optional[str]:
    """Resolve the ``--export-config`` argument to an actual file path.

    Resolution rules (per-map config management — see README section 6):

    1. ``path_or_name`` is an existing file path → returned unchanged
       (explicit paths keep working).
    2. ``path_or_name`` is a bare name (e.g. ``"WestZone"``) → tried as
       ``<config_dir>/<name>.json`` and then ``<name>.json`` in the working
       directory.  If neither exists an :class:`ExportConfigError` is raised
       listing the available per-map configs.
    3. ``path_or_name`` is None and ``map_name`` is given → the per-map
       default ``<config_dir>/<map_name>.json`` is returned when it exists,
       otherwise ``None`` (export mode simply stays disabled).
    """
    if path_or_name:
        if os.path.isfile(path_or_name):
            return path_or_name
        for cand in (os.path.join(config_dir, path_or_name + ".json"),
                     path_or_name if path_or_name.endswith(".json")
                     else path_or_name + ".json"):
            if os.path.isfile(cand):
                return cand
        available = list_export_configs(config_dir)
        hint = (f" (available: {', '.join(available)})" if available
                else f" (directory '{config_dir}' is empty — save camera "
                      f"points with spectator_coords.py --save first)")
        raise ExportConfigError(
            f"export config '{path_or_name}' not found as a file or a "
            f"per-map config name{hint}")
    if map_name:
        cand = os.path.join(config_dir, map_name + ".json")
        if os.path.isfile(cand):
            return cand
        # Legacy fallback: a single export_config.json in the working dir.
        if os.path.isfile("export_config.json"):
            return "export_config.json"
    return None


def load_export_config(path: str, step_length: float = 0.05) -> ExportConfig:
    """Load and validate an export configuration file.

    Raises:
        ExportConfigError: file missing, malformed or invalid.
    """
    path = os.fspath(path)
    try:
        with open(path, "r", encoding="utf-8") as fh:
            doc = json.load(fh)
    except FileNotFoundError:
        raise ExportConfigError(f"export config file not found: {path}")
    except json.JSONDecodeError as exc:
        raise ExportConfigError(f"export config '{path}' is not valid JSON: {exc}")

    if not isinstance(doc, dict):
        raise ExportConfigError(f"export config '{path}': top level must be an object")

    version = doc.get("version", 1)
    if version != CONFIG_VERSION:
        raise ExportConfigError(
            f"unsupported export config version {version!r} (expected {CONFIG_VERSION})")

    output = doc.get("output", {})
    if not isinstance(output, dict):
        raise ExportConfigError(f"export config '{path}': 'output' must be an object")
    output_fps = _check_param(output, "fps", 1e-9, 120.0, False, 30.0, "output")
    # 导出根目录:导出配置显式指定 > config/toolchain.json exports_dir > 默认 ../../data/exports
    output_dir = output.get("export_dir") or toolchain_env.resolve_exports_dir("")
    if not isinstance(output_dir, str) or not output_dir.strip():
        raise ExportConfigError("output.export_dir: non-empty string required")
    # 每传感器并行编码 worker 数(多核服务器提升硬件利用率;1 = 单线程)
    write_threads = _check_param(output, "write_threads", 1.0, 16.0, True, 2, "output")
    # 实时流导出器配置(output.stream,可选;无 stream 配置时导出器使用默认值)
    stream = _validate_stream(output.get("stream", {}))

    sensors_raw = doc.get("sensors", [])
    if not isinstance(sensors_raw, list):
        raise ExportConfigError(f"export config '{path}': 'sensors' must be a list")
    seen: Set[str] = set()
    sensors: List[SensorSpec] = []
    for idx, entry in enumerate(sensors_raw):
        spec = validate_sensor_entry(entry, idx, output_fps, seen)
        resolve_sensor_attributes(spec, step_length)
        sensors.append(spec)
    if not sensors:
        raise ExportConfigError(
            f"export config '{path}': 'sensors' is empty — nothing to export")

    return ExportConfig(version=version, sensors=sensors,
                        output_fps=output_fps, output_dir=output_dir,
                        write_threads=write_threads, stream=stream)


def export_config_to_dict(cfg: ExportConfig) -> Dict[str, Any]:
    """Serialise a resolved config (used for the run_config.json copy)."""
    output = {"fps": cfg.output_fps, "export_dir": cfg.output_dir,
              "write_threads": cfg.write_threads}
    if cfg.stream:
        output["stream"] = dict(cfg.stream)
    return {
        "version": cfg.version,
        "output": output,
        "sensors": [
            {"name": s.name, "type": s.type, "blueprint": s.blueprint,
             "transform": dict(s.transform), "attributes": dict(s.attributes),
             "params": dict(s.params), "note": s.note}
            for s in cfg.sensors
        ],
    }


def merge_spectator_entry(existing: Dict[str, Any], entry: Dict[str, Any]) -> Dict[str, Any]:
    """Append a spectator-recorded sensor entry to an existing config document
    (in place), preserving all other top-level keys (map, saved_at, ...)."""
    existing.setdefault("version", CONFIG_VERSION)
    sensors = existing.get("sensors")
    if not isinstance(sensors, list):
        sensors = []
        existing["sensors"] = sensors
    sensors.append(entry)
    return existing
