"""Offline self-check for the data-export framework (no CARLA required).

Exercises everything that does not need a live CARLA server: exporter
registration, configuration validation, step-length alignment, the output
directory layout, and — using fake world/sensor/data stubs — the full
exporter lifecycle end to end (spawn → tick → files on disk → teardown →
meta.json).  Runs on the development machine as::

    python -m data_export.selfcheck
"""

from __future__ import annotations

import json
import logging
import math
import os
import struct
import sys
import tempfile
import time
from pathlib import Path
from types import SimpleNamespace

from . import (ExporterManager, ExportContext, FrameRegistry, RunOutput,
               SensorFarm, effective_fps, load_export_config)
from .base import EXPORTER_REGISTRY, ExportConfigError
from .config import merge_spectator_entry

logging.basicConfig(level=logging.WARNING, format="%(levelname)s | %(message)s")
logger = logging.getLogger("cosim.export.selfcheck")

STEP = 0.05  # default simulation step


# ---------------------------------------------------------------------------
# CARLA stubs (duck-typed)
# ---------------------------------------------------------------------------

class FakeSensorData:
    """Duck-typed carla.Image / LidarMeasurement."""

    def __init__(self, frame: int, timestamp: float, kind: str = "rgb",
                 delay: float = 0.0) -> None:
        self.frame = frame
        self.timestamp = timestamp
        self.width, self.height, self.fov = 1920, 1080, 90.0
        self.kind = kind
        self.points = 1234
        self.delay = delay  # simulate slow encode (out-of-order completion)

    def save_to_disk(self, path: str) -> None:
        if self.delay:
            time.sleep(self.delay)
        Path(path).write_bytes(b"fake-data:" + os.fsencode(path))

    def __len__(self) -> int:
        return self.points

    def __getitem__(self, i: int):
        """Deterministic fake lidar point: [x, y, z, intensity]."""
        return [float(i), 0.0, 0.0, 0.5]

    @property
    def raw_data(self) -> bytes:
        """Flat 4×float32 LE buffer, like carla.LidarMeasurement.raw_data
        (camera images raise AttributeError, as in CARLA)."""
        if self.kind != "lidar":
            raise AttributeError("camera images have no raw_data")
        return b"".join(struct.pack("<4f", *self[i])
                        for i in range(self.points))


class FakeActor:
    def __init__(self, actor_id: int) -> None:
        self.id = actor_id
        self._listener = None

    def listen(self, cb) -> None:
        self._listener = cb

    def emit(self, data) -> None:
        self._listener(data)

    def destroy(self) -> None:
        self._listener = None


class FakeBlueprintLibrary:
    def __init__(self, blueprint_ids) -> None:
        self._bps = {bid: SimpleNamespace(id=bid, _attrs={})
                     for bid in blueprint_ids}

    def find(self, bid: str):
        if bid not in self._bps:
            raise RuntimeError(f"blueprint '{bid}' not found")
        bp = self._bps[bid]
        return SimpleNamespace(id=bid,
                               set_attribute=lambda k, v: bp._attrs.__setitem__(k, v))


class FakeWorld:
    def __init__(self, blueprint_ids) -> None:
        self._bl = FakeBlueprintLibrary(blueprint_ids)
        self.actors = []

    def get_blueprint_library(self):
        return self._bl

    def spawn_actor(self, bp, transform):
        actor = FakeActor(len(self.actors) + 1)
        actor.transform = transform
        self.actors.append(actor)
        return actor

    def get_weather(self):
        return SimpleNamespace(cloudiness=0.0, precipitation=0.0,
                               wind_intensity=10.0, sun_azimuth_angle=120.0,
                               sun_altitude_angle=45.0, fog_density=0.0,
                               wetness=0.0)


# ---------------------------------------------------------------------------
# Checks
# ---------------------------------------------------------------------------

def _check_registry() -> None:
    missing = {"rgb_camera", "lidar", "kitti", "manifest"} - set(EXPORTER_REGISTRY)
    assert not missing, f"exporters not registered: {sorted(missing)}"


def _check_config() -> None:
    doc = {
        "version": 1,
        "output": {"fps": 30, "export_dir": "exports"},
        "sensors": [
            {"name": "cam_01", "type": "rgb_camera",
             "transform": {"x": 1.0, "y": 2.0, "z": 6.0, "pitch": -20.0,
                           "yaw": 90.0, "roll": 0.0},
             "width": 1920, "height": 1080, "fov": 90.0, "fps": 30},
            {"name": "lidar_01", "type": "lidar",
             "transform": {"x": 1.0, "y": 2.0, "z": 5.0},
             "channels": 32, "range": 80.0, "points_per_second": 500000,
             "rotation_frequency": 20},
        ],
    }
    with tempfile.TemporaryDirectory() as tmp:
        path = os.path.join(tmp, "cfg.json")
        with open(path, "w", encoding="utf-8") as fh:
            json.dump(doc, fh)
        cfg = load_export_config(path, STEP)
        assert len(cfg.sensors) == 2
        cam = cfg.sensors[0]
        assert cam.blueprint == "sensor.camera.rgb"
        assert cam.attributes["sensor_tick"] == STEP  # 30fps > 20fps → every tick
        assert "requires --step-length" in cam.note

        # Validation failures carry sensors[N].<field> markers.
        bad = [
            ({"name": "", "type": "rgb_camera", "transform": {"x": 0, "y": 0, "z": 0}}, ".name"),
            ({"name": "a", "type": "nope", "transform": {"x": 0, "y": 0, "z": 0}}, ".type"),
            ({"name": "a", "type": "rgb_camera", "transform": {"x": 0, "y": 0}}, ".transform"),
            ({"name": "a", "type": "rgb_camera", "transform": {"x": 0, "y": 0, "z": 0},
              "width": 99999}, ".width"),
            ({"name": "a", "type": "lidar", "transform": {"x": 0, "y": 0, "z": 0},
              "upper_fov": -5, "lower_fov": 5}, ""),
        ]
        from .config import validate_sensor_entry
        for entry, marker in bad:
            try:
                validate_sensor_entry(entry, 0, 30.0, set())
                raise AssertionError(f"should have been rejected: {entry}")
            except ExportConfigError as exc:
                assert marker in str(exc), f"expected marker {marker!r} in: {exc}"

        # merge_spectator_entry preserves foreign top-level keys.
        merged = merge_spectator_entry(
            {"version": 1, "map": "Demo_1", "saved_at": "x", "sensors": []},
            {"name": "cam_09", "type": "rgb_camera", "transform": {"x": 0, "y": 0, "z": 0}})
        assert merged["map"] == "Demo_1" and len(merged["sensors"]) == 1


def _check_fps_alignment() -> None:
    eff, note = effective_fps(0.05, 30.0)
    assert abs(eff - 20.0) < 1e-9 and note
    eff, note = effective_fps(0.05, 10.0)
    assert abs(eff - 10.0) < 1e-9 and not note
    eff, _ = effective_fps(1.0 / 30.0, 30.0)
    assert abs(eff - 30.0) < 1e-6


def _check_output_layout() -> None:
    doc = {"version": 1, "output": {"fps": 30, "export_dir": "exports"},
           "sensors": [
               {"name": "cam_01", "type": "rgb_camera",
                "transform": {"x": 0, "y": 0, "z": 6}}]}
    with tempfile.TemporaryDirectory() as tmp:
        cfg = _load_from_doc(doc)
        out = RunOutput(os.path.join(tmp, "out"), "test_map", cfg)
        assert out.dir.is_dir() and (out.dir / "meta.json").is_file()
        assert (out.dir / "run_config.json").is_file()
        assert RunOutput.estimate_bytes(cfg, 10.0) > 0
        out.finalize({"sensors": []}, aborted=True)
        meta = json.loads((out.dir / "meta.json").read_text(encoding="utf-8"))
        assert meta["aborted"] is True and "ended_at" in meta


def _load_from_doc(doc):
    import tempfile as _tf
    fd, path = _tf.mkstemp(suffix=".json")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as fh:
            json.dump(doc, fh)
        return load_export_config(path, STEP)
    finally:
        os.unlink(path)


def _install_carla_stub() -> None:
    """Minimal fake ``carla`` module so the farm's lazy ``import carla``
    works without a CARLA install (Transform/Location/Rotation only)."""
    import types as _types
    stub = _types.ModuleType("carla")

    class _Loc:
        def __init__(self, x=0.0, y=0.0, z=0.0):
            self.x, self.y, self.z = x, y, z

    class _Rot:
        def __init__(self, pitch=0.0, yaw=0.0, roll=0.0):
            self.pitch, self.yaw, self.roll = pitch, yaw, roll

    class _Transform:
        """Duck-typed ``carla.Transform`` with the real rotation formula
        (CARLA ``Rotation.get_matrix()``, left-handed) so calibration can be
        exercised offline — test-only copy of the runtime implementation."""

        def __init__(self, loc, rot):
            self.loc, self.rot = loc, rot

        def get_matrix(self):
            cy, sy = math.cos(math.radians(self.rot.yaw)), \
                     math.sin(math.radians(self.rot.yaw))
            cr, sr = math.cos(math.radians(self.rot.roll)), \
                     math.sin(math.radians(self.rot.roll))
            cp, sp = math.cos(math.radians(self.rot.pitch)), \
                     math.sin(math.radians(self.rot.pitch))
            r = [[cy * cp, cy * sp * sr - sy * cr, cy * sp * cr + sy * sr],
                 [sy * cp, sy * sp * sr + cy * cr, sy * sp * cr - cy * sr],
                 [-sp, cp * sr, cp * cr]]
            t = (self.loc.x, self.loc.y, self.loc.z)
            return [r[0] + [t[0]], r[1] + [t[1]], r[2] + [t[2]],
                    [0.0, 0.0, 0.0, 1.0]]

        def get_inverse_matrix(self):
            m = self.get_matrix()
            r = [m[i][:3] for i in range(3)]
            t = [m[i][3] for i in range(3)]
            inv_t = [-(r[0][i] * t[0] + r[1][i] * t[1] + r[2][i] * t[2])
                     for i in range(3)]
            return [[r[0][0], r[1][0], r[2][0], inv_t[0]],
                    [r[0][1], r[1][1], r[2][1], inv_t[1]],
                    [r[0][2], r[1][2], r[2][2], inv_t[2]],
                    [0.0, 0.0, 0.0, 1.0]]

    stub.Location = _Loc
    stub.Rotation = _Rot
    stub.Transform = _Transform
    sys.modules["carla"] = stub


def _spectator_args(**overrides):
    """A Namespace shaped exactly like spectator_coords.py's argparse output
    (dest names, not hand-picked ones) — guards against dest-rename bugs."""
    defaults = dict(
        save=None, save_name=None, save_type="rgb_camera",
        width=1920, height=1080, fov=90.0, fps=30.0,
        channels=32, range=80.0, points_per_second=500_000,
        rotation_frequency=20.0, upper_fov=10.0, lower_fov=-30.0,
    )
    defaults.update(overrides)
    return SimpleNamespace(**defaults)


def _fake_transform(x, y, z, pitch, yaw, roll):
    return SimpleNamespace(location=SimpleNamespace(x=x, y=y, z=z),
                           rotation=SimpleNamespace(pitch=pitch, yaw=yaw, roll=roll))


def _check_spectator_save() -> None:
    """spectator_coords.py --save pipeline: entry schema, auto-naming
    (first free slot), same-name upsert, origin refusal and the per-map
    default path (config/export_configs/<map>.json)."""
    import spectator_coords as sc

    class FakeWorld:
        def __init__(self):
            self.map = SimpleNamespace(name="/Game/Maps/WestZone/WestZone")

        def get_map(self):
            return self.map

    with tempfile.TemporaryDirectory() as tmp:
        old_cwd = os.getcwd()
        os.chdir(tmp)
        try:
            world = FakeWorld()
            transform = _fake_transform(1200.5, -800.3, 6.0, -20.0, 90.0, 0.0)

            # Explicit path + auto name; first save appends.
            args = _spectator_args(save="cameras.json")
            entry = sc._build_sensor_entry(world, transform, args)
            assert entry["name"] == "cam_01" and entry["type"] == "rgb_camera"
            assert entry["transform"]["yaw"] == 90.0
            name, path, replaced = sc._save_reading(world, transform, args)
            assert (name, path, replaced) == ("cam_01", "cameras.json", False)

            # Same name again → replaced in place, single entry, new values.
            args1b = _spectator_args(save="cameras.json", save_name="cam_01")
            name, path, replaced = sc._save_reading(
                world, _fake_transform(1.0, 2.0, 3.0, -10.0, 45.0, 0.0), args1b)
            assert replaced is True
            doc = json.load(open("cameras.json", encoding="utf-8"))
            assert len(doc["sensors"]) == 1
            assert doc["sensors"][0]["transform"]["yaw"] == 45.0

            # Auto naming fills the first free slot: cam_01, cam_03 exist →
            # the next auto name is cam_02 (not cam_03).
            args2 = _spectator_args(save="cameras.json", save_name="cam_03")
            sc._save_reading(world, transform, args2)
            args2b = _spectator_args(save="cameras.json")
            entry = sc._build_sensor_entry(world, transform, args2b)
            assert entry["name"] == "cam_02", entry["name"]

            # No --save path → per-map default file, directory auto-created.
            args3 = _spectator_args(save=None, save_type="lidar")
            name, path, replaced = sc._save_reading(world, transform, args3)
            assert path == os.path.join("config", "export_configs", "WestZone.json")
            assert os.path.isfile(path)
            doc = json.load(open(path, encoding="utf-8"))
            assert doc["map"] == "WestZone"
            assert doc["sensors"][0]["type"] == "lidar"

            # Origin reading → refused (None), no file created.
            args4 = _spectator_args(save="origin.json")
            assert sc._save_reading(world, _fake_transform(0, 0, 0, 0, 0, 0), args4) is None
            assert not os.path.isfile("origin.json")

            # _is_origin helper.
            assert sc._is_origin(_fake_transform(0, 0, 0, 0, 0, 0))
            assert not sc._is_origin(_fake_transform(0, 0, 1, 0, 0, 0))
        finally:
            os.chdir(old_cwd)


def _check_freshness_wait() -> None:
    """_wait_for_fresh_frame: must never tick the world, and must return True
    once a frame arrives — via wait_for_tick when available, via snapshot
    polling otherwise."""
    import spectator_coords as sc

    class _FakeSnap:
        def __init__(self, frame):
            self.frame = frame

    # Case 1: wait_for_tick exists and a frame is flowing (the co-sim case) —
    # blocks until the next frame, returns the snapshot.
    class _WorldWaitTick:
        def __init__(self):
            self.ticked = False

        def wait_for_tick(self, timeout=None):
            return _FakeSnap(100)

        def get_snapshot(self):
            return _FakeSnap(0)

        def tick(self):
            self.ticked = True

    w1 = _WorldWaitTick()
    assert sc._wait_for_fresh_frame(w1, timeout=1.0) is True
    assert not w1.ticked, "_wait_for_fresh_frame must never call world.tick()"

    # Case 2: no wait_for_tick (older API) → polling fallback via frame change.
    class _WorldPolling:
        def __init__(self):
            self.ticked = False
            self.calls = 0

        def get_snapshot(self):
            self.calls += 1
            return _FakeSnap(self.calls)  # frame advances on every poll

        def tick(self):
            self.ticked = True

    w2 = _WorldPolling()
    assert sc._wait_for_fresh_frame(w2, timeout=1.0) is True
    assert not w2.ticked

    # Case 3: nobody ever produces a frame → False after the timeout.
    class _WorldSilent:
        def __init__(self):
            self.ticked = False

        def wait_for_tick(self, timeout=None):
            return None

        def get_snapshot(self):
            return _FakeSnap(0)

        def tick(self):
            self.ticked = True

    w3 = _WorldSilent()
    assert sc._wait_for_fresh_frame(w3, timeout=0.15) is False
    assert not w3.ticked


def _check_config_resolution() -> None:
    """Per-map config resolution: explicit path / bare name / auto by map
    name / missing-name error / legacy export_config.json fallback."""
    from .config import (resolve_export_config_path, list_export_configs,
                         EXPORT_CONFIG_DIR)
    with tempfile.TemporaryDirectory() as tmp:
        old_cwd = os.getcwd()
        os.chdir(tmp)
        try:
            # Explicit existing file → unchanged.
            Path("explicit.json").write_text("{}", encoding="utf-8")
            assert resolve_export_config_path("explicit.json") == "explicit.json"

            # Per-map dir + bare name.
            os.makedirs(EXPORT_CONFIG_DIR)
            Path(EXPORT_CONFIG_DIR, "WestZone.json").write_text("{}", encoding="utf-8")
            Path(EXPORT_CONFIG_DIR, "_example.json").write_text("{}", encoding="utf-8")
            assert resolve_export_config_path("WestZone") == \
                os.path.join(EXPORT_CONFIG_DIR, "WestZone.json")
            assert list_export_configs() == ["WestZone"]  # _example excluded

            # Auto by map name.
            assert resolve_export_config_path(None, "WestZone") == \
                os.path.join(EXPORT_CONFIG_DIR, "WestZone.json")
            assert resolve_export_config_path(None, "OtherMap") is None

            # Unknown bare name → ExportConfigError listing available configs.
            try:
                resolve_export_config_path("Nope")
                raise AssertionError("should have raised ExportConfigError")
            except ExportConfigError as exc:
                assert "WestZone" in str(exc)

            # Legacy fallback: export_config.json in cwd.
            Path("export_config.json").write_text("{}", encoding="utf-8")
            assert resolve_export_config_path(None, "OtherMap") == "export_config.json"
        finally:
            os.chdir(old_cwd)


def _check_calibration() -> None:
    """Intrinsic/extrinsic calibration math: known K values, T_cw @ T_wc == I,
    pure-rotation transpose case, input rejection (the carla stub provides
    the matrices, so this runs without a CARLA install)."""
    _install_carla_stub()
    from .calibration import intrinsic_matrix, extrinsic_matrices

    # fov=90, 1920x1080 -> fx=fy≈960, cx=960, cy=540 (tan(45°) is not
    # exactly 1.0 in float, so fx carries a ~1e-13 tail — compare closely).
    K = intrinsic_matrix(1920, 1080, 90.0)
    assert abs(K[0][0] - 960.0) < 1e-9 and abs(K[1][1] - 960.0) < 1e-9
    assert K[0][2] == 960.0 and K[1][2] == 540.0 and K[2] == [0.0, 0.0, 1.0]

    def mat_mul(a, b):  # small local helper
        return [[sum(a[i][k] * b[k][j] for k in range(len(b)))
                 for j in range(len(b[0]))] for i in range(len(a))]

    # Arbitrary pose: T_cw @ T_wc must be the 4x4 identity.
    T_wc, T_cw = extrinsic_matrices(
        {"x": 10.0, "y": 20.0, "z": 6.0, "pitch": -20.0, "yaw": 90.0,
         "roll": 15.0})
    prod = mat_mul(T_cw, T_wc)
    assert all(abs(prod[i][j] - (1.0 if i == j else 0.0)) < 1e-12
               for i in range(4) for j in range(4))

    # Pure rotation: T_cw is exactly the transpose of T_wc.
    Twc2, Tcw2 = extrinsic_matrices(
        {"x": 0.0, "y": 0.0, "z": 0.0, "pitch": 30.0, "yaw": 45.0, "roll": 0.0})
    assert Tcw2 == [[Twc2[j][i] for j in range(4)] for i in range(4)]

    for bad in [(0, 1080, 90.0), (1920, -1, 90.0), (1920, 1080, 180.0),
                (1920, 1080, float("nan")), (1920.5, 1080, 90.0)]:
        try:
            intrinsic_matrix(*bad)
            raise AssertionError(f"should have been rejected: {bad}")
        except ValueError:
            pass

    try:
        extrinsic_matrices({"x": 1.0, "y": 2.0, "z": 3.0})
        raise AssertionError("missing transform keys should raise KeyError")
    except KeyError:
        pass


def _check_parallel_writers() -> None:
    """Parallel writer workers (write_threads=2): out-of-order completion must
    not lose or reorder rows; dropped sequences must be released as no_data
    (the consumer must never deadlock on a missing sequence)."""
    _install_carla_stub()
    import queue as _queue

    doc = {"version": 1, "output": {"fps": 30, "export_dir": "exports"},
           "sensors": [{"name": "cam_01", "type": "rgb_camera",
                        "transform": {"x": 10, "y": 20, "z": 6}}]}

    # -- Scenario A: out-of-order completion (seq1 slow, seq2 instant) --
    with tempfile.TemporaryDirectory() as tmp:
        cfg = _load_from_doc(doc)
        world = FakeWorld(["sensor.camera.rgb"])
        run_output = RunOutput(os.path.join(tmp, "out"), "test_map", cfg)
        ctx = ExportContext(
            world=world, client=None, step_length=STEP, max_sim_time=10.0,
            map_name="test_map", run_output=run_output,
            sensor_farm=SensorFarm(world, logger),
            frame_registry=FrameRegistry(STEP), logger=logger,
            write_threads=2)
        mgr = ExporterManager(cfg, ctx, kinds=["rgb_camera"])
        mgr.setup_all()
        cam_actor = world.actors[0]

        cam_actor.emit(FakeSensorData(frame=1001, timestamp=1 * STEP,
                                      delay=0.25))       # seq1: slow
        mgr.on_sim_tick(1, 1 * STEP)                     # seq1 in flight → waits
        cam_actor.emit(FakeSensorData(frame=1002, timestamp=2 * STEP,
                                      delay=0))          # seq2: instant
        time.sleep(0.4)                                  # both done, order mixed
        mgr.on_sim_tick(2, 2 * STEP)
        mgr.teardown_all()

        cam_dir = run_output.dir / "sensors" / "cam_01"
        pngs = sorted(cam_dir.glob("frame_*.png"))
        assert [p.name for p in pngs] == ["frame_000001.png", "frame_000002.png"], \
            [p.name for p in pngs]
        lines = [json.loads(l) for l in
                 (run_output.dir / "manifest.jsonl").read_text(encoding="utf-8").splitlines()]
        assert [l["export_frame"] for l in lines] == [1, 2]
        assert all(l["sensors"]["cam_01"]["status"] == "written" for l in lines)

    # -- Scenario B: dropped-seq release (no deadlock, no_data row) --
    with tempfile.TemporaryDirectory() as tmp:
        cfg = _load_from_doc(doc)
        world = FakeWorld(["sensor.camera.rgb"])
        run_output = RunOutput(os.path.join(tmp, "out"), "test_map", cfg)
        ctx = ExportContext(
            world=world, client=None, step_length=STEP, max_sim_time=10.0,
            map_name="test_map", run_output=run_output,
            sensor_farm=SensorFarm(world, logger),
            frame_registry=FrameRegistry(STEP), logger=logger,
            write_threads=2)
        mgr = ExporterManager(cfg, ctx, kinds=["rgb_camera"])
        mgr.setup_all()
        cam_actor = world.actors[0]
        ms = ctx.sensor_farm._sensors[0]

        # Inject a dropped capture at tick 1, then a normal one at tick 2.
        # (ms.seq is the callback counter — advance it so the next capture
        #  gets seq 2, consistent with the injected drop of seq 1.)
        with ms.lock:
            ms.dropped_seqs[1] = 1 * STEP
        ms.seq = 1
        mgr.on_sim_tick(1, 1 * STEP)                     # releases seq1 → no_data
        cam_actor.emit(FakeSensorData(frame=1002, timestamp=2 * STEP))
        mgr.on_sim_tick(2, 2 * STEP)
        mgr.teardown_all()

        lines = [json.loads(l) for l in
                 (run_output.dir / "manifest.jsonl").read_text(encoding="utf-8").splitlines()]
        assert [l["export_frame"] for l in lines] == [1, 2]
        assert lines[0]["sensors"]["cam_01"]["status"] == "no_data"
        assert lines[1]["sensors"]["cam_01"]["status"] == "written"
        cam_dir = run_output.dir / "sensors" / "cam_01"
        # The dropped seq1 has no file; the surviving capture is seq2.
        assert sorted(p.name for p in cam_dir.glob("frame_*.png")) == ["frame_000002.png"]

    # -- Scenario C: callback drop path (bounded queue, no workers) --
    from .sensors import ManagedSensor, _on_data
    ms2 = ManagedSensor(name="cam_x", spec=cfg.sensors[0], actor=FakeActor(9))
    ms2.queue = _queue.Queue(maxsize=1)
    _on_data(ms2, FakeSensorData(frame=1, timestamp=1 * STEP))  # seq1 queued
    _on_data(ms2, FakeSensorData(frame=2, timestamp=2 * STEP))  # full → dropped
    assert ms2.drops == 1
    with ms2.lock:
        assert ms2.dropped_seqs == {2: 2 * STEP}
    assert ms2.queue.qsize() == 1


def _check_stream() -> None:
    """Real-time stream exporter: JPEG/lidar encoding round-trips plus a
    ZeroMQ PUB loopback (meta + camera + lidar messages).  Skipped with a
    warning when pyzmq or Pillow is not installed — never a failure."""
    try:
        import zmq  # noqa: F401
    except ImportError:
        print("  ⚠ stream check skipped: pyzmq not installed "
              "(pip install pyzmq)")
        return
    try:
        from PIL import Image as PILImage
    except ImportError:
        print("  ⚠ stream check skipped: Pillow not installed "
              "(pip install pillow)")
        return
    import io
    import zlib
    from .exporters.stream import (STREAM_CODECS, _StreamPub, stream_codec,
                                   estimate_stream_bytes_per_sec)

    # Codec registry integrity: the built-in types are registered with the
    # right capability flags.
    assert set(STREAM_CODECS) >= {"rgb_camera", "lidar"}, \
        f"stream codecs not registered: {sorted(STREAM_CODECS)}"
    assert STREAM_CODECS["rgb_camera"].calibration is True
    assert STREAM_CODECS["rgb_camera"].needs_pillow is True
    assert STREAM_CODECS["lidar"].calibration is False

    # Duck-typed sensor data (camera BGRA frame + lidar point cloud).
    class _FakeLidar:
        frame, timestamp = 7, 0.35

        def __len__(self) -> int:
            return 1234

        @property
        def raw_data(self) -> bytes:
            return b"".join(struct.pack("<4f", float(i), 0.0, 0.0, 0.5)
                            for i in range(1234))

    class _FakeImage:
        frame, timestamp = 7, 0.35
        width, height, fov = 320, 240, 90.0

        @property
        def raw_data(self) -> bytes:
            return bytes(320 * 240 * 4)  # BGRA8

    # Encoding round-trips THROUGH the registered codecs (the exporter
    # delegates to these, so this exercises the actual encode path).
    lidar_meta, lidar_payload = STREAM_CODECS["lidar"].encode(
        _FakeLidar(), None, SimpleNamespace(lidar_compress=True))
    assert lidar_meta["format"] == "lidar_f32" and lidar_meta["compressed"]
    assert lidar_meta["points"] == 1234
    assert len(zlib.decompress(lidar_payload)) == 1234 * 16
    rgb_meta, jpeg = STREAM_CODECS["rgb_camera"].encode(
        _FakeImage(), None, SimpleNamespace(jpeg_quality=85))
    assert rgb_meta["format"] == "jpeg" and rgb_meta["width"] == 320
    assert PILImage.open(io.BytesIO(jpeg)).size == (320, 240)

    # Extensibility: registering a third codec is all a new sensor type
    # needs (no StreamExporter / manager / schema changes).  Register a fake
    # type, exercise it, then remove it so the registry is unchanged.
    @stream_codec("fake_stream_type", calibration=True)
    def _encode_fake(data, spec, sconf):  # noqa: F811 (unused name on purpose)
        return {"kind": "fake_stream_type", "format": "raw"}, b"FAKE"
    try:
        assert "fake_stream_type" in STREAM_CODECS
        fmeta, fpay = STREAM_CODECS["fake_stream_type"].encode(None, None, None)
        assert fmeta["format"] == "raw" and fpay == b"FAKE"
        assert STREAM_CODECS["fake_stream_type"].calibration is True
    finally:
        del STREAM_CODECS["fake_stream_type"]
    assert "fake_stream_type" not in STREAM_CODECS

    # ZMQ loopback over ipc (no port conflicts).
    addr = (f"ipc://{tempfile.gettempdir()}"
            f"/citypulse_selfcheck_stream_{os.getpid()}.sock")
    sub = zmq.Context().socket(zmq.SUB)
    sub.setsockopt(zmq.SUBSCRIBE, b"")
    sub.connect(addr)
    pub = _StreamPub.bind(addr)
    try:
        pub.send_frame("meta", {"kind": "meta", "sensor": "meta"},
                       b'{"map": "W"}')
        pub.send_frame("cam_x", {"kind": "rgb_camera", "sensor": "cam_x",
                                 "seq": 1, "world_frame": 7,
                                 "sim_time": 0.35, "format": "jpeg"}, jpeg)
        pub.send_frame("lidar_x", {"kind": "lidar", "sensor": "lidar_x",
                                   "seq": 1, "world_frame": 7,
                                   "sim_time": 0.35, "format": "lidar_f32",
                                   "compressed": True,
                                   "points": 1234}, lidar_payload)
        topics = set()
        for _ in range(3):
            topic, meta_b, pl = sub.recv_multipart()
            topics.add(topic)
            m = json.loads(meta_b)
            if topic == b"cam_x":
                assert len(pl) == len(jpeg)
            elif topic == b"lidar_x":
                assert len(zlib.decompress(pl)) == 1234 * 16
        assert topics == {b"meta", b"cam_x", b"lidar_x"}, topics
    finally:
        pub.close()
        sub.close()

    # Bandwidth estimate sanity: 3 cameras 1920×1080 @20fps ≈ 25 MB/s.
    spec = SimpleNamespace(type="rgb_camera", attributes={
        "image_size_x": 1920, "image_size_y": 1080, "sensor_tick": 0.05})
    bps = estimate_stream_bytes_per_sec([spec, spec, spec])
    expected = int(1920 * 1080 * 0.2 * 20.0 * 3)
    assert abs(bps - expected) < expected * 0.01, (bps, expected)


def _check_lifecycle() -> None:
    """Drive the full exporter lifecycle with stubs and assert the files."""
    _install_carla_stub()
    N_TICKS = 10
    doc = {"version": 1, "output": {"fps": 30, "export_dir": "exports"},
           "sensors": [
               {"name": "cam_01", "type": "rgb_camera",
                "transform": {"x": 10, "y": 20, "z": 6, "pitch": -20, "yaw": 90}},
               {"name": "lidar_01", "type": "lidar",
                "transform": {"x": 15, "y": 25, "z": 5}}]}
    with tempfile.TemporaryDirectory() as tmp:
        cfg = _load_from_doc(doc)
        world = FakeWorld(["sensor.camera.rgb", "sensor.lidar.ray_cast"])
        run_output = RunOutput(os.path.join(tmp, "out"), "test_map", cfg)
        ctx = ExportContext(
            world=world, client=None, step_length=STEP, max_sim_time=10.0,
            map_name="test_map", run_output=run_output,
            sensor_farm=SensorFarm(world, logger),
            frame_registry=FrameRegistry(STEP), logger=logger)
        mgr = ExporterManager(cfg, ctx, kinds=["rgb_camera", "lidar", "kitti"])
        mgr.setup_all()
        # kitti spawns its own renamed copies (kitti_cam_01/kitti_lidar_01).
        assert len(world.actors) == 4, "expected 4 spawned sensor actors"

        cam_actor, lid_actor, kcam_actor, klid_actor = world.actors
        for i in range(1, N_TICKS + 1):
            cam_actor.emit(FakeSensorData(frame=1000 + i, timestamp=i * STEP, kind="rgb"))
            lid_actor.emit(FakeSensorData(frame=2000 + i, timestamp=i * STEP, kind="lidar"))
            kcam_actor.emit(FakeSensorData(frame=3000 + i, timestamp=i * STEP, kind="rgb"))
            klid_actor.emit(FakeSensorData(frame=4000 + i, timestamp=i * STEP, kind="lidar"))
            # Small pause mimics the real cadence (≈50ms per tick) so the
            # writer threads can save without the bounded queue filling up.
            time.sleep(0.01)
            mgr.on_sim_tick(i, i * STEP)
        mgr.teardown_all()

        # Files on disk: N_TICKS PNGs + PLYs, both sensor manifests, run manifest.
        cam_dir = run_output.dir / "sensors" / "cam_01"
        lid_dir = run_output.dir / "sensors" / "lidar_01"
        pngs = sorted(cam_dir.glob("frame_*.png"))
        plies = sorted(lid_dir.glob("frame_*.ply"))
        assert len(pngs) == N_TICKS, f"expected {N_TICKS} PNGs, got {len(pngs)}"
        assert len(plies) == N_TICKS, f"expected {N_TICKS} PLYs, got {len(plies)}"
        assert (cam_dir / "manifest.jsonl").is_file()
        assert (lid_dir / "manifest.jsonl").is_file()

        # calibration.json: per-camera intrinsics + extrinsics, written once
        # at setup from the config (defaults 1920x1080/90° → fx=960).
        calib = json.loads((cam_dir / "calibration.json").read_text(encoding="utf-8"))
        assert calib["sensor"]["name"] == "cam_01"
        assert abs(calib["intrinsics"]["K"][0][0] - 960.0) < 1e-9  # fx for 1920/90°
        assert len(calib["extrinsics"]["T_wc"]) == 4  # 4x4 homogeneous
        assert not (lid_dir / "calibration.json").exists()  # lidar gets none

        # Run-level manifest: N_TICKS rows, every sensor slot "written".
        lines = [json.loads(l) for l in
                 (run_output.dir / "manifest.jsonl").read_text(encoding="utf-8").splitlines()]
        assert len(lines) == N_TICKS, f"expected {N_TICKS} manifest rows, got {len(lines)}"
        for i, line in enumerate(lines, start=1):
            assert line["export_frame"] == i
            assert line["sensors"]["cam_01"]["status"] == "written"
            assert line["sensors"]["lidar_01"]["status"] == "written"
            assert line["sensors"]["kitti_lidar_01"]["status"] == "written"
            assert line["sensors"]["kitti_lidar_01"]["file"].startswith(
                "kitti/velodyne_lidar_01/")
            assert "weather" in line and "cloudiness" in line["weather"]

        # Per-sensor manifest lines reference real files with poses.
        cam_lines = [json.loads(l) for l in
                     (cam_dir / "manifest.jsonl").read_text(encoding="utf-8").splitlines()]
        assert len(cam_lines) == N_TICKS
        assert cam_lines[0]["pose"]["yaw"] == 90.0
        assert (cam_dir / cam_lines[0]["file"]).is_file()

        # meta.json aggregates per-sensor stats.
        meta = json.loads((run_output.dir / "meta.json").read_text(encoding="utf-8"))
        by_name = {s["name"]: s for s in meta["export"]["sensors"]}
        assert by_name["cam_01"]["frames_written"] == N_TICKS
        assert by_name["lidar_01"]["drops"] == 0
        assert by_name["kitti_lidar_01"]["frames_written"] == N_TICKS
        assert meta["rt_stats"]["rt_samples"] == 0  # observe_rt never called here

        # KITTI layout: calib.txt once, frame numbers from 0, .bin payload
        # is the flat 4×float32 buffer (16 bytes × 1234 fake points).
        kitti_root = run_output.dir / "kitti"
        assert (kitti_root / "calib.txt").is_file()
        imgs = sorted((kitti_root / "image_cam_01").glob("*.png"))
        bins = sorted((kitti_root / "velodyne_lidar_01").glob("*.bin"))
        assert len(imgs) == N_TICKS and len(bins) == N_TICKS
        assert imgs[0].name == "000000.png" and bins[0].name == "000000.bin"
        assert bins[0].stat().st_size == 4 * 4 * 1234
        first = struct.unpack("<4f", bins[0].read_bytes()[:16])
        assert first == (0.0, 0.0, 0.0, 0.5)  # fake point #0

        calib = (kitti_root / "calib.txt").read_text(encoding="utf-8")
        assert "R0_rect: 1 0 0 0 1 0 0 0 1" in calib

        def mat_mul(a, b):  # small local helper
            return [[sum(a[i][k] * b[k][j] for k in range(len(b)))
                     for j in range(len(b[0]))] for i in range(len(a))]

        from .calibration import extrinsic_matrices
        cam_spec = next(s for s in cfg.sensors if s.name == "cam_01")
        lid_spec = next(s for s in cfg.sensors if s.name == "lidar_01")

        # P_cam_01 == K @ T_cw[:3] with the known default K (1920x1080/90°).
        T_wc, T_cw = extrinsic_matrices(cam_spec.transform)
        K = [[960.0, 0.0, 960.0], [0.0, 960.0, 540.0], [0.0, 0.0, 1.0]]
        P_ref = mat_mul(K, [T_cw[i][:4] for i in range(3)])
        p_line = next(l for l in calib.splitlines()
                      if l.startswith("P_cam_01:"))
        p_vals = [float(x) for x in p_line.split()[1:]]
        assert len(p_vals) == 12
        # calib.txt 数值为 %.8e(8 位有效数字),值域 ~1e4 → 舍入误差 ~1e-4。
        assert all(abs(p_vals[j] - P_ref[j // 4][j % 4]) < 5e-4
                   for j in range(12))

        # Tr_velo_lidar_01_to_cam_cam_01 == (T_cw_cam @ T_wc_velo)[:3].
        T_wc_v, _ = extrinsic_matrices(lid_spec.transform)
        Tr_ref = mat_mul([T_cw[i][:4] for i in range(3)], T_wc_v)
        tr_line = next(l for l in calib.splitlines()
                       if l.startswith("Tr_velo_lidar_01_to_cam_cam_01:"))
        tr_vals = [float(x) for x in tr_line.split()[1:]]
        assert all(abs(tr_vals[j] - Tr_ref[j // 4][j % 4]) < 5e-4
                   for j in range(12))


def _check_bin2pcd() -> None:
    """KITTI .bin → PCD: header fields, lossless binary payload, size
    validation (runs offline, no CARLA needed)."""
    import tempfile as _tf
    import bin2pcd as b2p

    with _tf.TemporaryDirectory() as tmp:
        fake_bin = os.path.join(tmp, "fake.bin")
        points = [struct.pack("<4f", float(i), 1.0, 2.0, 0.5)
                  for i in range(3)]
        with open(fake_bin, "wb") as fh:
            fh.write(b"".join(points))

        fake_pcd = os.path.join(tmp, "fake.pcd")
        assert b2p.bin_to_pcd(fake_bin, fake_pcd) == 3
        blob = open(fake_pcd, "rb").read()
        header, _, payload = blob.partition(b"DATA binary\n")
        text = header.decode("ascii")
        for line in ("VERSION 0.7", "FIELDS x y z intensity",
                     "SIZE 4 4 4 4", "TYPE F F F F", "COUNT 1 1 1 1",
                     "WIDTH 3", "HEIGHT 1", "POINTS 3"):
            assert line in text, line
        assert payload == b"".join(points), "binary payload must be lossless"

        bad = os.path.join(tmp, "bad.bin")
        with open(bad, "wb") as fh:
            fh.write(b"\x00" * 17)  # not a multiple of 16
        try:
            b2p.bin_to_pcd(bad, os.path.join(tmp, "bad.pcd"))
            raise AssertionError("17-byte file should have been rejected")
        except ValueError:
            pass


def _check_plan_lidar_points() -> None:
    """Automatic lidar placement: mini net.xml (single junction J at
    (100,100)), one 360° lidar above the junction centre; config merge
    preserves cameras, replaces by name and cleans up stale per-road
    entries (demo_N_lidar_01..04 from the first layout version)."""
    import tempfile as _tf
    import plan_lidar_points as plp

    net = """<?xml version="1.0" encoding="UTF-8"?>
<net version="1.0">
  <junction id="J" type="traffic_light" x="100" y="100"/>
</net>
"""
    with _tf.TemporaryDirectory() as tmp:
        net_path = os.path.join(tmp, "mini.net.xml")
        with open(net_path, "w", encoding="utf-8") as fh:
            fh.write(net)
        junctions = plp.scan_net(net_path)
        assert set(junctions) == {"J"}

        entries = plp.plan_for_junctions(junctions, [("demo_1", "J")],
                                         z=6.0, pitch=-5.0)
        assert [e["name"] for e in entries] == ["demo_1_lidar"]
        assert entries[0]["transform"] == {
            "x": 100.0, "y": -100.0, "z": 6.0,  # CARLA y = -SUMO y
            "pitch": -5.0, "yaw": 0.0, "roll": 0.0}
        assert entries[0]["range"] == 50.0  # default coverage radius

        # Merge: keeps the camera entry and the top-level keys; stale
        # per-road entries (demo_1_lidar_01/02) are cleaned up; running
        # again replaces in place instead of appending.
        cfg_path = os.path.join(tmp, "cfg.json")
        doc = {"version": 1, "map": "WestZone", "saved_at": "x",
               "sensors": [
                   {"name": "cam_01", "type": "rgb_camera",
                    "transform": {"x": 0, "y": 0, "z": 6}},
                   {"name": "demo_1_lidar_01", "type": "lidar",
                    "transform": {"x": 80, "y": -90, "z": 6}},
                   {"name": "demo_1_lidar_02", "type": "lidar",
                    "transform": {"x": 120, "y": -110, "z": 6}}]}
        with open(cfg_path, "w", encoding="utf-8") as fh:
            json.dump(doc, fh)
        assert plp.merge_lidar_entries(cfg_path, "WestZone", entries) == 2
        merged = json.load(open(cfg_path, encoding="utf-8"))
        assert merged["map"] == "WestZone" and merged["version"] == 1
        names = [s["name"] for s in merged["sensors"]]
        assert names == ["cam_01", "demo_1_lidar"], names  # stale cleaned
        assert plp.merge_lidar_entries(cfg_path, "WestZone", entries) == 1
        merged2 = json.load(open(cfg_path, encoding="utf-8"))
        assert len(merged2["sensors"]) == 2, "replaced, not appended"


def main(argv=None) -> int:
    checks = [
        ("exporter registry", _check_registry),
        ("config validation", _check_config),
        ("fps/step alignment", _check_fps_alignment),
        ("output layout", _check_output_layout),
        ("config path resolution", _check_config_resolution),
        ("spectator --save pipeline", _check_spectator_save),
        ("fresh-frame wait (no ticking)", _check_freshness_wait),
        ("calibration math", _check_calibration),
        ("plan lidar points", _check_plan_lidar_points),
        ("bin -> pcd", _check_bin2pcd),
        ("parallel writers (order/drop)", _check_parallel_writers),
        ("stream exporter (ZMQ loopback)", _check_stream),
        ("full lifecycle (stubs)", _check_lifecycle),
    ]
    print("data_export self-check (no CARLA needed)")
    print("─" * 40)
    ok = True
    for name, fn in checks:
        try:
            fn()
            print(f"  ✓ {name}")
        except Exception as exc:
            ok = False
            print(f"  ✗ {name}: {exc}")
    print("─" * 40)
    print("SELF-CHECK PASSED ✓" if ok else "SELF-CHECK FAILED ✗")
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
