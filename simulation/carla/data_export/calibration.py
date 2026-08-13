"""Camera calibration: intrinsic K and extrinsic T_wc/T_cw matrices.

``carla`` is imported lazily (only inside the functions that need it) so
this package stays importable without CARLA installed; the import fallback
adds the CARLA Python API egg via ``toolchain_env.add_carla_pythonapi_to_path()``
(env var ``CARLA_ROOT`` > ``config/toolchain.json`` ``carla_root``).

Conventions (CARLA / Unreal, left-handed): camera-local axes are X forward,
Y right, Z up; rotation yaw is around Z, pitch around Y, roll around X
(degrees).  ``T_wc`` maps camera-local to CARLA world and is taken verbatim
from ``carla.Transform.get_matrix()``; ``T_cw`` is its inverse
(``get_inverse_matrix()``), the standard world→camera extrinsic
(P_cam = T_cw @ P_world).  Matrices are row-major nested lists, directly
JSON-serialisable.

The intrinsic K has no CARLA API equivalent — CARLA cameras are modelled by
width/height/fov, so K follows from the pinhole formula in
:func:`intrinsic_matrix`.
"""

from __future__ import annotations

import math
from typing import Any, Dict, List, Tuple

import toolchain_env  # CARLA PythonAPI 路径(惰性 import carla 时使用)

SCHEMA_VERSION = "camera-calibration/v1"

Mat = List[List[float]]           # row-major matrix (3x3 or 4x4)
TransformDict = Dict[str, float]  # x/y/z/pitch/yaw/roll


def _require_finite(value: Any, what: str) -> float:
    """Coerce ``value`` to a finite float or raise ValueError."""
    v = float(value)  # TypeError/ValueError from bad types
    if not math.isfinite(v):
        raise ValueError(f"{what}: must be finite, got {value!r}")
    return v


def _import_carla():
    """Lazy ``import carla``: add the PythonAPI egg via toolchain_env first
    (mirrors ``data_export.sensors``' lazy import; re-raises ImportError)."""
    try:
        import carla
    except ImportError:
        toolchain_env.add_carla_pythonapi_to_path()
        import carla
    return carla


# ---------------------------------------------------------------------------
# Intrinsics
# ---------------------------------------------------------------------------

def intrinsic_matrix(width: int, height: int, fov: float) -> Mat:
    """3x3 intrinsic matrix K for the CARLA pinhole camera model.

    Square pixels: ``fx = fy = (w/2) / tan(fov_rad/2)``; principal point at
    the image centre (``cx = w/2``, ``cy = h/2``).  ``fov`` in degrees, must
    lie in the open interval (0, 180).

    Raises:
        ValueError: non-finite / non-positive / non-integer width or height,
            or fov outside (0, 180).
    """
    w = _require_finite(width, "width")
    h = _require_finite(height, "height")
    fov_deg = _require_finite(fov, "fov")
    if w <= 0 or h <= 0:
        raise ValueError(f"width/height must be positive, got {width!r}x{height!r}")
    if w != int(w) or h != int(h):
        raise ValueError(f"width/height must be integers, got {width!r}/{height!r}")
    if not (0.0 < fov_deg < 180.0):
        raise ValueError(f"fov must be in (0, 180) degrees, got {fov_deg:g}")
    w, h = int(w), int(h)
    f = (w / 2.0) / math.tan(math.radians(fov_deg) / 2.0)
    return [[f, 0.0, w / 2.0],
            [0.0, f, h / 2.0],
            [0.0, 0.0, 1.0]]


# ---------------------------------------------------------------------------
# Extrinsics
# ---------------------------------------------------------------------------

def extrinsic_matrices(transform: TransformDict) -> Tuple[Mat, Mat]:
    """4x4 homogeneous (T_wc, T_cw) for a CARLA world pose.

    ``T_wc`` maps camera-local 4-vectors to CARLA world (verbatim from
    ``carla.Transform(...).get_matrix()``); ``T_cw`` maps world to camera
    (``get_inverse_matrix()``), so ``P_cam = T_cw @ P_world``.  Requires the
    ``carla`` Python API (imported lazily; see module docstring).

    Raises:
        ValueError: a non-finite component.
        KeyError: a required transform key is missing (config validation
            guarantees them in normal use).
    """
    x = _require_finite(transform["x"], "transform.x")
    y = _require_finite(transform["y"], "transform.y")
    z = _require_finite(transform["z"], "transform.z")
    pitch = _require_finite(transform["pitch"], "transform.pitch")
    yaw = _require_finite(transform["yaw"], "transform.yaw")
    roll = _require_finite(transform["roll"], "transform.roll")
    carla = _import_carla()
    t = carla.Transform(carla.Location(x=x, y=y, z=z),
                        carla.Rotation(pitch=pitch, yaw=yaw, roll=roll))
    return t.get_matrix(), t.get_inverse_matrix()


# ---------------------------------------------------------------------------
# Calibration document
# ---------------------------------------------------------------------------

def calibration_document(name: str, spec_type: str, blueprint: str,
                         width: int, height: int, fov: float,
                         transform: TransformDict) -> Dict[str, Any]:
    """The full ``calibration.json`` document for one camera.

    Keeps the original inputs (``input``) for traceability and spells out
    the coordinate conventions (``convention``) so downstream consumers do
    not have to guess them.
    """
    K = intrinsic_matrix(width, height, fov)
    T_wc, T_cw = extrinsic_matrices(transform)
    return {
        "schema": SCHEMA_VERSION,
        "sensor": {"name": name, "type": spec_type, "blueprint": blueprint},
        "input": {"width": width, "height": height, "fov": fov,
                  "transform": dict(transform)},
        "convention": {
            "coordinate_system": "carla (Unreal left-handed)",
            "local_camera_axes": {"x": "forward", "y": "right", "z": "up"},
            "rotation": {"yaw": "around z", "pitch": "around y", "roll": "around x"},
            "angle_unit": "degrees", "length_unit": "meters",
            "matrix_layout": "row-major nested lists (JSON arrays)",
        },
        "intrinsics": {"model": "carla-pinhole (square pixels, principal "
                                 "point at image centre)", "K": K},
        "extrinsics": {"T_wc": T_wc,   # camera local -> CARLA world
                       "T_cw": T_cw},  # world -> camera; P_cam = T_cw @ P_world
    }
