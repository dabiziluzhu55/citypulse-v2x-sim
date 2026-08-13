#!/usr/bin/env python3
"""
Output the CARLA spectator (observer) position to the terminal.

Runs against a live CARLA server and continuously prints the spectator's
world transform, plus (optionally) the corresponding SUMO coordinates under
this project's identity mapping (sumo_x = carla_x, sumo_y = -carla_y — see
README coordinate table).

Works both standalone and while a co-simulation is running.  Note: CARLA's
``get_transform()`` returns the client's cached transform from the last
received frame (not a server query); this script waits for a fresh frame
before each reading, which in synchronous mode requires the co-simulation to
be ticking (frames only exist while it runs).  The connection line shows
``sync_mode=ON/OFF`` and the last received frame number.

Usage:
    python spectator_coords.py                 # live update every 1s
    python spectator_coords.py --once          # print a single reading
    python spectator_coords.py --interval 0.2  # faster refresh
    python spectator_coords.py --host 192.168.1.100 --port 2000
    python spectator_coords.py --geo           # also print lat/lon/altitude

Placing "street cameras" (data-export mode):
    Move the spectator in CARLA to the wanted camera spot, then save the
    reading as a sensor entry for the export configuration.  Without a
    --save path the entry goes to the per-map default config/export_configs/<map>.json
    (one config file per map), which run_cosimulation.py resolves by map name:

    python spectator_coords.py --once --save --save-name cam_01
    python spectator_coords.py --once --save --save-type lidar --save-name lidar_01
    python spectator_coords.py --once --save cameras.json --save-name cam_01   # explicit path

    Without --save-name the name auto-increments (cam_01, cam_02, ...); in
    live mode (no --once) a new entry is appended on every refresh interval,
    which is convenient for sweeping several spots quickly.
"""

from __future__ import annotations

import argparse
import json
import os
import sys
import time

import toolchain_env  # 统一环境配置:env > config/toolchain.json > 自动探测
from typing import Any, Dict, List, Optional, Tuple

# CARLA source trees whose Python API egg (PythonAPI/carla/dist/*.egg) is added
# to sys.path automatically, so `import carla` works without a manual PYTHONPATH.
# CARLA 源码目录仅来自:环境变量 ``CARLA_ROOT`` > ``config/toolchain.json`` 的
# ``carla_root``(不做任何硬编码路径猜测,见 toolchain_env.py)。

# Camera/lidar parameter ranges — mirrors data_export.config validation.
_CAMERA_RANGES = {
    "width": (64, 4096), "height": (64, 4096), "fov": (1.0, 180.0), "fps": (1e-9, 120.0),
}
_LIDAR_RANGES = {
    "channels": (1, 256), "range": (1.0, 1000.0), "points_per_second": (10000, 10_000_000),
    "rotation_frequency": (1.0, 120.0), "upper_fov": (-90.0, 90.0), "lower_fov": (-90.0, 90.0),
}


def _get_map_name(world) -> str:
    """Short CARLA map name (last path segment)."""
    name = world.get_map().name
    return name.split("/")[-1] if "/" in name else name


def _is_origin(transform) -> bool:
    """True when the transform sits exactly at the world origin."""
    loc = transform.location
    return loc.x == 0.0 and loc.y == 0.0 and loc.z == 0.0


def _wait_for_fresh_frame(world, timeout: float) -> bool:
    """Wait until the client receives a new frame from the server, WITHOUT
    advancing the simulation.

    ``actor.get_transform()`` does not ask the server — it returns the
    transform from the client's cache of the last received frame (per CARLA
    docs: "returns the transform received in the last tick").  A freshly
    connected client has an empty cache and reads (0, 0, 0) until a frame
    arrives; in synchronous mode frames only exist while the co-simulation
    ticks.

    This function must never call ``world.tick()``: a second client ticking a
    synchronous-mode world would advance CARLA independently of SUMO and
    desynchronise the co-simulation.  ``world.wait_for_tick`` only waits for
    the next broadcast frame — it does not tick.

    Returns:
        True when a fresh frame was received within ``timeout`` seconds.
    """
    try:
        snapshot = world.wait_for_tick(timeout=timeout)
        if snapshot is not None:
            return True
    except (AttributeError, TypeError):
        pass  # older API without wait_for_tick → polling grace below

    # Short polling grace for missing/buggy wait_for_tick: frames flowing at
    # ~20Hz arrive within 50ms, so 0.5s is plenty.
    try:
        base_frame = world.get_snapshot().frame
    except Exception:
        base_frame = None
    deadline = time.time() + min(timeout, 0.5)
    while time.time() < deadline:
        try:
            frame = world.get_snapshot().frame
            if base_frame is None or frame != base_frame:
                return True
        except Exception:
            pass
        time.sleep(0.05)
    return False


def _read_spectator_transform(world, spectator):
    """Read the spectator's transform, retrying while frames may still be
    arriving.  A (0,0,0) reading right after connect usually means the
    client's frame cache is empty (see ``_wait_for_fresh_frame``), so retry
    a few times — the next broadcast frame usually restores the value.
    Returns the final transform (all-zero when the spectator genuinely sits
    at the world origin)."""
    transform = spectator.get_transform()
    if not _is_origin(transform):
        return transform
    for _ in range(3):
        time.sleep(0.1)
        transform = spectator.get_transform()
        if not _is_origin(transform):
            break
    return transform


def _print_reading(world, transform, show_geo: bool) -> None:
    loc = transform.location
    rot = transform.rotation

    # Project's SUMO <-> CARLA mapping: carla.x = sumo.x, carla.y = -sumo.y
    sumo_x, sumo_y = loc.x, -loc.y

    parts = [
        f"xyz=({loc.x:8.2f}, {loc.y:8.2f}, {loc.z:6.2f})",
        f"rot=(pitch={rot.pitch:6.2f}, yaw={rot.yaw:6.2f}, roll={rot.roll:6.2f})",
        f"sumo=({sumo_x:8.2f}, {sumo_y:8.2f})",
    ]
    if show_geo:
        try:
            geo = world.get_map().transform_to_geolocation(loc)
            parts.append(f"geo=(lat={geo.latitude:.7f}, lon={geo.longitude:.7f}, "
                         f"alt={geo.altitude:.2f})")
        except Exception as exc:  # broken/incomplete geoReference etc.
            parts.append(f"geo=(unavailable: {exc})")

    print(f"[{time.strftime('%H:%M:%S')}] map={_get_map_name(world)}  "
          + "  ".join(parts), flush=True)


# ---------------------------------------------------------------------------
# Saving sensor placements (--save) for the data-export mode
# ---------------------------------------------------------------------------

def _build_sensor_entry(world, transform, args) -> Dict[str, Any]:
    """One ``sensors[]`` entry for the export configuration, built from the
    spectator's current absolute CARLA world pose (no coordinate conversion —
    the spectator and camera/lidar sensors share the same Transform
    convention)."""
    loc = transform.location
    rot = transform.rotation
    entry: Dict[str, Any] = {
        "name": args.save_name or _auto_sensor_name(_save_target_path(args, world),
                                                    args.save_type),
        "type": args.save_type,
        "transform": {
            "x": loc.x, "y": loc.y, "z": loc.z,
            "pitch": rot.pitch, "yaw": rot.yaw, "roll": rot.roll,
        },
    }
    if args.save_type == "rgb_camera":
        entry.update(width=args.width, height=args.height, fov=args.fov, fps=args.fps)
    else:  # lidar
        entry.update(channels=args.channels, range=args.range,
                     points_per_second=args.points_per_second,
                     rotation_frequency=args.rotation_frequency,
                     upper_fov=args.upper_fov, lower_fov=args.lower_fov)
    return entry


def _auto_sensor_name(path: Optional[str], stype: str) -> str:
    """First free name (cam_01, cam_02, ... / lidar_01, ...) among the
    entries already saved to ``path``.  Scanning instead of counting avoids
    colliding with a hole in the numbering (e.g. only cam_02 exists → the
    next name is cam_01, not cam_02)."""
    prefix = "cam" if stype == "rgb_camera" else "lidar"
    used = set()
    if path and os.path.isfile(path):
        try:
            with open(path, "r", encoding="utf-8") as fh:
                doc = json.load(fh)
            used = {str(s.get("name")) for s in doc.get("sensors", [])
                    if isinstance(s, dict) and str(s.get("name", "")).startswith(prefix)}
        except Exception:
            pass
    n = 1
    while f"{prefix}_{n:02d}" in used:
        n += 1
    return f"{prefix}_{n:02d}"


def _save_target_path(args, world) -> str:
    """Where the next sensor entry is saved: the explicit ``--save`` path, or
    the per-map default ``config/export_configs/<map>.json`` (one config per
    map — see README section 6)."""
    if args.save:
        return args.save
    return os.path.join("config", "export_configs", _get_map_name(world) + ".json")


def _upsert_sensor_entry(path: str, map_name: str, entry: Dict[str, Any]) -> bool:
    """Write a sensor entry to the config file, REPLACING an existing entry
    with the same name in place (or appending when the name is new), and
    preserving all other top-level keys.  Written atomically so Ctrl+C or a
    crash cannot corrupt the file.

    Returns:
        True when an existing entry was replaced, False when appended.
    """
    parent = os.path.dirname(path)
    if parent:
        os.makedirs(parent, exist_ok=True)
    if os.path.isfile(path):
        with open(path, "r", encoding="utf-8") as fh:
            doc = json.load(fh)
        if not isinstance(doc, dict):
            raise ValueError(f"{path} is not a JSON object")
    else:
        doc = {"version": 1}
    doc.setdefault("map", map_name)
    doc.setdefault("saved_at", time.strftime("%Y-%m-%dT%H:%M:%S"))
    sensors = doc.setdefault("sensors", [])
    if not isinstance(sensors, list):
        raise ValueError(f"{path}: 'sensors' is not a list")

    replaced = False
    for idx, existing in enumerate(sensors):
        if isinstance(existing, dict) and existing.get("name") == entry["name"]:
            sensors[idx] = entry
            replaced = True
            break
    if not replaced:
        sensors.append(entry)

    tmp = path + ".tmp"
    with open(tmp, "w", encoding="utf-8") as fh:
        json.dump(doc, fh, ensure_ascii=False, indent=2)
    os.replace(tmp, path)
    return replaced


def _save_reading(world, transform, args) -> Optional[Tuple[str, str, bool]]:
    """Save the current spectator pose as a sensor entry.

    A reading at the world origin (0, 0, 0) is almost always a mistake
    (spectator never moved / stale frame), so it is NOT saved.

    Returns:
        ``(entry_name, path, replaced)`` when saved (``replaced`` True when
        an existing entry with the same name was overwritten), ``None`` when
        the reading was refused (world origin).
    """
    entry = _build_sensor_entry(world, transform, args)
    t = entry["transform"]
    if t["x"] == 0.0 and t["y"] == 0.0 and t["z"] == 0.0:
        return None  # caller prints the origin warning (once per run)
    path = _save_target_path(args, world)
    replaced = _upsert_sensor_entry(path, _get_map_name(world), entry)
    return entry["name"], path, replaced


def _validate_save_args(parser: argparse.ArgumentParser, args) -> None:
    """Range-check the --save parameter block (mirrors the export config
    validation in data_export.config)."""
    if args.save is None:
        return
    if args.save_type == "rgb_camera":
        for key, (lo, hi) in _CAMERA_RANGES.items():
            v = getattr(args, key)
            if not (lo <= v <= hi):
                parser.error(f"--{key} out of range [{lo}, {hi}]: {v}")
    else:
        for key, (lo, hi) in _LIDAR_RANGES.items():
            v = getattr(args, key)
            if not (lo <= v <= hi):
                parser.error(f"--{key} out of range [{lo}, {hi}]: {v}")
        if not (args.upper_fov > args.lower_fov):
            parser.error("--upper-fov must be > --lower-fov")


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(
        description=__doc__,
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    parser.add_argument("--host", default="127.0.0.1", help="CARLA server host (default: 127.0.0.1)")
    parser.add_argument("--port", type=int, default=2000, help="CARLA server port (default: 2000)")
    parser.add_argument("--interval", type=float, default=1.0,
                        help="refresh interval in seconds (default: 1.0)")
    parser.add_argument("--once", action="store_true",
                        help="print a single reading and exit (instead of continuous mode)")
    parser.add_argument("--geo", action="store_true",
                        help="also print geolocation (lat/lon/altitude)")

    # ── Sensor placement recording (data-export mode) ──
    parser.add_argument("--save", nargs="?", metavar="PATH",
                        help="append the current spectator pose as a sensor "
                             "entry to PATH, or to the per-map default "
                             "config/export_configs/<map>.json when no path is given")
    parser.add_argument("--save-name",
                        help="entry name (default: auto-incrementing cam_01 / lidar_01)")
    parser.add_argument("--save-type", choices=("rgb_camera", "lidar"),
                        default="rgb_camera",
                        help="sensor type of the saved entry (default: rgb_camera)")
    parser.add_argument("--width", type=float, default=1920, help="camera width px (default: 1920)")
    parser.add_argument("--height", type=float, default=1080, help="camera height px (default: 1080)")
    parser.add_argument("--fov", type=float, default=90.0, help="camera FOV degrees (default: 90)")
    parser.add_argument("--fps", type=float, default=30.0, help="camera fps (default: 30)")
    parser.add_argument("--channels", type=float, default=32, help="lidar channels (default: 32)")
    parser.add_argument("--range", type=float, default=80.0, help="lidar range m (default: 80)")
    parser.add_argument("--points-per-second", type=float, default=500_000,
                        help="lidar points per second (default: 500000)")
    parser.add_argument("--rotation-frequency", type=float, default=20.0,
                        help="lidar rotation frequency Hz (default: 20)")
    parser.add_argument("--upper-fov", type=float, default=10.0,
                        help="lidar upper FOV deg (default: 10)")
    parser.add_argument("--lower-fov", type=float, default=-30.0,
                        help="lidar lower FOV deg (default: -30)")
    args = parser.parse_args(argv)

    if args.interval <= 0:
        parser.error("--interval must be > 0")
    _validate_save_args(parser, args)

    toolchain_env.add_carla_pythonapi_to_path()
    try:
        import carla  # lazy import — only needed at runtime
    except ImportError:
        print("ERROR: carla Python API not found. "
              "CARLA 源码目录仅从环境变量 CARLA_ROOT 或 config/toolchain.json 的 "
              "carla_root 解析(无硬编码路径)。\n"
              "Add CARLA's PythonAPI/carla/dist/carla-*.egg to PYTHONPATH, "
              "or: pip install carla==<your-carla-version>", file=sys.stderr)
        return 1

    try:
        client = carla.Client(args.host, args.port, worker_threads=1)
        client.set_timeout(10.0)
        world = client.get_world()
    except Exception as exc:
        print(f"ERROR: cannot connect to CARLA at {args.host}:{args.port}: {exc}",
              file=sys.stderr)
        return 1

    spectator = world.get_spectator()

    # ── Diagnostics: world sync mode + last frame the client has received ──
    sync_mode = False
    try:
        sync_mode = bool(world.get_settings().synchronous_mode)
    except Exception:
        pass
    last_frame = None
    try:
        last_frame = world.get_snapshot().frame
    except Exception:
        pass
    diag = f"sync_mode={'ON' if sync_mode else 'OFF'}"
    if last_frame is not None:
        diag += f" | last_frame={last_frame}"
    print(f"Connected to CARLA {client.get_server_version()} | "
          f"map: {world.get_map().name} | spectator: {spectator.type_id} | {diag}",
          flush=True)

    # ── Wait for a fresh frame before reading.  get_transform() returns the
    #    client's cache of the last received frame; in synchronous mode a
    #    frame exists only while the co-simulation is ticking.  We only wait —
    #    never tick: a second client ticking would desynchronise SUMO↔CARLA. ──
    fresh = _wait_for_fresh_frame(world, timeout=2.0 if args.once else 1.0)
    if not fresh and sync_mode:
        print("⚠ no fresh frame received — the world is in synchronous mode "
              "and nobody is ticking it (is the co-simulation running?). "
              "Readings may be stale.", flush=True)

    warned = False  # warn once per run in live mode

    def _warn_if_origin() -> None:
        nonlocal warned
        if not _is_origin(transform) or warned:
            return
        warned = True
        if not fresh:
            print("⚠ reading is (0, 0, 0) with no fresh frame received "
                  "(synchronous mode, co-sim not ticking?) — the value may "
                  "be stale.", flush=True)
        else:
            print("⚠ spectator is at the world origin (0, 0, 0) — move the "
                  "camera in the CARLA window first (WASD + mouse + Q/E, "
                  "Shift to speed up), then re-save the point.", flush=True)

    if args.once:
        transform = _read_spectator_transform(world, spectator)
        _print_reading(world, transform, args.geo)
        _warn_if_origin()
        if args.save is not None:
            result = _save_reading(world, transform, args)
            if result is not None:
                name, path, replaced = result
                print(f"Saved sensor entry '{name}' to {path}"
                      + (" (updated)" if replaced else ""), flush=True)
            else:
                print(f"Not saved — see the warning above.", flush=True)
        return 0

    print("Live mode (Ctrl+C to stop):", flush=True)
    try:
        while True:
            transform = _read_spectator_transform(world, spectator)
            _print_reading(world, transform, args.geo)
            _warn_if_origin()
            if args.save is not None:
                result = _save_reading(world, transform, args)
                if result is not None:
                    name, path, replaced = result
                    print(f"Saved sensor entry '{name}' to {path}"
                          + (" (updated)" if replaced else ""), flush=True)
                else:
                    print(f"Not saved — see the warning above.", flush=True)
            time.sleep(args.interval)
    except KeyboardInterrupt:
        print("\nStopped.", flush=True)
        return 0


if __name__ == "__main__":
    sys.exit(main())
