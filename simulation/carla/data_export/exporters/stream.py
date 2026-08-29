"""ZeroMQ stream exporter: publishes sensor frames (camera JPEG, lidar point
clouds) to a PUB socket in real time — the "AD interface" (roadside sensor →
processing system) of the co-simulation.  See ``数据导出接口.md`` for the
feasibility/risk analysis and the industry comparison that led to ZMQ.

Wire protocol (ZeroMQ PUB/SUB, one 3-part message per frame)::

    [topic, meta_json, payload]

    topic    : UTF-8 sensor name from the export config (e.g. ``demo_19``)
    meta_json: UTF-8 JSON object, always:
                 {kind, sensor, seq, world_frame, sim_time, map, run_id,
                  format, ...}
               camera: format="jpeg", width/height/jpeg_quality
               lidar : format="lidar_f32", compressed=bool, points
    payload  : camera → JPEG bytes (BGRA → RGB, quality configurable)
               lidar  → x/y/z/intensity little-endian float32 (16 B/point,
                         point order preserved), optionally zlib-compressed

One extra message is published once at setup with topic ``meta`` (same
3-part shape: header in ``meta_json``, run metadata document in ``payload``
— map, run_id, step_length, sensor calibration/poses) so a fresh subscriber
can configure itself before the frame stream starts.

Design notes (matches the exporter contract in ``base.py``):

* This is a *consumer* kind: no sensor has ``type == "stream"``, the manager
  feeds it the config's rgb_camera + lidar specs (like ``kitti``) and it
  spawns its own copies named ``stream_<name>`` so the frame registry never
  collides with file exporters.
* Sending happens on the sensor writer threads (``SensorFarm`` worker pool):
  the simulation tick stays free of I/O, and the existing bounded-queue
  back-pressure (drop-newest) applies unchanged.  The PUB socket never
  blocks the caller — when the send high-water mark (``SNDHWM``) is reached,
  ZeroMQ silently drops the oldest buffered message, which is exactly the
  drop-newest semantics the framework already documents.
* ``on_sim_tick`` only marks the per-tick registry row as ``sent`` for each
  sensor (the manifest must stay complete so the run-level index keeps
  streaming); no frame is consumed on the simulation thread.
* setup failures (pyzmq/Pillow missing, bind error) disable this exporter
  only — the simulation and other exporters continue.

Extending to a new sensor type — a codec registry (same idea as
``@register`` for exporters).  How one frame of a given sensor type is
encoded is *not* hard-coded here; each type registers a codec::

    from .stream import stream_codec

    @stream_codec("semantic_segmentation", calibration=True)
    def _encode_semseg(data, spec, sconf):
        ...  # -> (meta_extra: dict, payload: bytes)

Adding a new data type therefore touches only:
  1. ``config.BLUEPRINTS``  (kind → CARLA blueprint id) and, if it is a
     camera, its parameter validation (``_CAMERA_PARAMS``);
  2. a ``@stream_codec(...)`` registration (here or in the new exporter's
     module, imported from ``data_export.exporters``);
  3. optionally ``estimate_bytes_per_sec`` for the ``--check-env``
     bandwidth estimate.

``StreamExporter`` / ``manager`` / the config schema need no changes.

Optional dependencies (imported lazily — this module stays importable and
self-checkable without them):

* ``pyzmq`` — required for the PUB socket.
* ``Pillow`` — required by codecs that declare ``needs_pillow`` (the RGB
  camera JPEG codec); when absent those sensor types are skipped, others
  keep streaming.

Runtime configuration lives in the export config's ``output.stream`` block
(validated by ``data_export.config``)::

    "output": {
      "fps": 30, "export_dir": "...", "write_threads": 2,
      "stream": {"bind": "tcp://127.0.0.1:19091",
                 "jpeg_quality": 85,
                 "lidar_compress": true}
    }

``bind`` accepts ``tcp://<host>:<port>`` (cross-machine; default
127.0.0.1:19091 — localhost only) or ``ipc://<path>`` (same-machine
zero-copy transport).
"""

from __future__ import annotations

import dataclasses
import io
import json
import threading
import time
import zlib
from typing import Any, Callable, Dict, List, Optional

from ..base import ExportError, Exporter, SensorSpawnError, register
from ..sensors import ManagedSensor

# PUB socket send high-water mark (frames buffered per sensor before the
# oldest is dropped).  With ~20 fps per sensor this is ≈ 0.8 s of buffering;
# a slow consumer loses old frames instead of starving the simulation.
SNDHWM = 16
# ZMQ slow-joiner mitigation: wait this long after bind() before publishing,
# so a subscriber that is already connected receives the meta message.
# Frames published before a subscriber finishes its handshake are silently
# lost (that is the documented drop-newest semantics — a consumer joining
# mid-run simply sees its first seq > 1).
PUB_WARMUP_S = 1.0
# Camera JPEG quality range is enforced by config validation (1-100, default 85).
DEFAULT_BIND = "tcp://127.0.0.1:19091"
# Rough payload sizes for the --check-env bandwidth estimate.
JPEG_BYTES_PER_PIXEL = 0.2      # ≈ quality-85 JPEG, ~2 MB/frame @1080p
LIDAR_ZLIB_RATIO = 0.35         # ≈ f32 raw → zlib level 3


def _jpeg_payload(data: Any, quality: int) -> bytes:
    """Camera frame → JPEG bytes (BGRA8 → RGB, quality ∈ [1, 100])."""
    from PIL import Image as PILImage  # lazily imported; setup() verified it
    w, h = int(data.width), int(data.height)
    img = PILImage.frombuffer(
        "RGBA", (w, h), bytes(data.raw_data), "raw", "BGRA", 0, 1)
    buf = io.BytesIO()
    img.convert("RGB").save(buf, "JPEG", quality=quality)
    return buf.getvalue()


def _lidar_payload(data: Any, compress: bool) -> tuple:
    """Lidar scan → payload bytes (x/y/z/intensity f32 LE, 16 B/point, point
    order preserved) plus the compression flag for the message meta."""
    raw = bytes(memoryview(data.raw_data).cast("f"))  # zero-copy-ish, like kitti
    if compress:
        return zlib.compress(raw, 3), True
    return raw, False


# ---------------------------------------------------------------------------
# Stream codec registry — one codec per sensor type (see module docstring)
# ---------------------------------------------------------------------------

@dataclasses.dataclass(frozen=True)
class StreamCodec:
    """How one sensor type is encoded for the stream.

    ``encode(data, spec, sconf) -> (meta_extra, payload)`` runs on a sensor
    writer thread; ``meta_extra`` is merged into the frame header and
    ``payload`` is the frame bytes.  ``calibration`` / ``needs_pillow`` /
    ``estimate_bytes_per_sec`` let the exporter decide meta content, the
    Pillow dependency and the bandwidth estimate per type without touching
    its code.
    """

    encode: Callable                 # (data, spec, sconf) -> (meta_extra, payload)
    calibration: bool = False        # attach a calibration document to meta
    needs_pillow: bool = False       # encoding needs Pillow (skip if absent)
    estimate_bytes_per_sec: Optional[Callable] = None  # (spec) -> int


STREAM_CODECS: Dict[str, StreamCodec] = {}


def stream_codec(sensor_type: str, *, calibration: bool = False,
                 needs_pillow: bool = False,
                 estimate_bytes_per_sec: Optional[Callable] = None
                 ) -> Callable[[Callable], Callable]:
    """Registration decorator for one sensor type's stream codec (same idea
    as ``@register`` for exporters)."""
    def deco(fn: Callable) -> Callable:
        if sensor_type in STREAM_CODECS:
            raise ExportError(f"duplicate stream codec '{sensor_type}'")
        STREAM_CODECS[sensor_type] = StreamCodec(
            encode=fn, calibration=calibration, needs_pillow=needs_pillow,
            estimate_bytes_per_sec=estimate_bytes_per_sec)
        return fn
    return deco


def _estimate_rgb_camera(spec: Any) -> int:
    w = int(spec.attributes.get("image_size_x", 1920))
    h = int(spec.attributes.get("image_size_y", 1080))
    tick = float(spec.attributes.get("sensor_tick", 0.05))
    return int(w * h * JPEG_BYTES_PER_PIXEL * (1.0 / tick))


def _estimate_lidar(spec: Any) -> int:
    pps = float(spec.attributes.get("points_per_second", 500_000))
    return int(pps * 16.0 * LIDAR_ZLIB_RATIO)


@stream_codec("rgb_camera", calibration=True, needs_pillow=True,
              estimate_bytes_per_sec=_estimate_rgb_camera)
def _encode_rgb_camera(data: Any, spec: Any, sconf: "_StreamConfig") -> tuple:
    """Camera frame → JPEG payload (BGRA8 → RGB, quality from config)."""
    payload = _jpeg_payload(data, sconf.jpeg_quality)
    meta = {"kind": "rgb_camera", "format": "jpeg",
            "width": int(data.width), "height": int(data.height),
            "jpeg_quality": sconf.jpeg_quality}
    return meta, payload


@stream_codec("lidar", estimate_bytes_per_sec=_estimate_lidar)
def _encode_lidar(data: Any, spec: Any, sconf: "_StreamConfig") -> tuple:
    """Lidar scan → zlib-compressed x/y/z/intensity f32 payload (point order
    preserved) plus the compression flag for the message meta."""
    payload, compressed = _lidar_payload(data, sconf.lidar_compress)
    meta = {"kind": "lidar", "format": "lidar_f32", "compressed": compressed,
            "points": len(data)}
    return meta, payload


def estimate_stream_bytes_per_sec(specs: List[Any]) -> int:
    """Rough real-time bandwidth estimate (bytes/s) for the given sensor
    specs — used by ``--check-env``.  Delegates to each type's codec
    estimate hook, so new types are covered automatically."""
    total = 0
    for s in specs:
        codec = STREAM_CODECS.get(s.type)
        if codec is not None and codec.estimate_bytes_per_sec is not None:
            total += codec.estimate_bytes_per_sec(s)
    return int(total)


@dataclasses.dataclass
class _StreamPub:
    """Thin wrapper around a ZeroMQ PUB socket (lazy import of pyzmq).

    ZeroMQ sockets are NOT thread-safe for concurrent sends — several sensor
    writer threads publish through this one socket, so every multipart send
    is serialised under ``lock`` (otherwise frames from different sensors
    interleave and corrupt the message stream).
    """

    sock: Any
    zmq_ctx: Any
    lock: Any = dataclasses.field(default_factory=threading.Lock)

    @classmethod
    def bind(cls, bind_addr: str, hwm: int = SNDHWM) -> "_StreamPub":
        try:
            import zmq  # lazily imported — optional dependency
        except ImportError as exc:
            raise SensorSpawnError(
                "stream exporter needs pyzmq (pip install pyzmq)") from exc
        zmq_ctx = zmq.Context()
        sock = zmq_ctx.socket(zmq.PUB)
        sock.setsockopt(zmq.SNDHWM, hwm)
        sock.setsockopt(zmq.LINGER, 0)
        try:
            sock.bind(bind_addr)
        except Exception as exc:
            sock.close()
            zmq_ctx.destroy(linger=0)
            raise SensorSpawnError(
                f"cannot bind stream socket '{bind_addr}': {exc}") from exc
        time.sleep(PUB_WARMUP_S)  # slow-joiner mitigation for the meta message
        return cls(sock=sock, zmq_ctx=zmq_ctx)

    def send_frame(self, topic: str, meta: Dict[str, Any], payload: bytes) -> None:
        parts = [
            topic.encode("utf-8"),
            json.dumps(meta, ensure_ascii=False).encode("utf-8"),
            payload]
        with self.lock:  # serialise multipart sends across writer threads
            self.sock.send_multipart(parts)

    def close(self) -> None:
        try:
            self.sock.close()
        except Exception:
            pass
        try:
            self.zmq_ctx.destroy(linger=0)
        except Exception:
            pass


@dataclasses.dataclass
class _SensorChannel:
    """One spawned sensor's stream channel: spawn name ↔ config name."""

    spawn_name: str   # "stream_<config name>" (frame-registry safe)
    topic: str        # config name — what subscribers filter on


@dataclasses.dataclass
class _StreamConfig:
    bind: str = DEFAULT_BIND
    jpeg_quality: int = 85
    lidar_compress: bool = True

    @classmethod
    def from_ctx(cls, ctx) -> "_StreamConfig":
        cfg = getattr(ctx, "export_config", None)
        sconf = getattr(cfg, "stream", None) or {}
        return cls(
            bind=str(sconf.get("bind", DEFAULT_BIND)),
            jpeg_quality=int(sconf.get("jpeg_quality", 85)),
            lidar_compress=bool(sconf.get("lidar_compress", True)))


@register("stream")
class StreamExporter(Exporter):
    """Publishes camera JPEG frames and lidar point clouds over a ZeroMQ
    PUB socket, on the sensor writer threads.  No files are written; the
    frame registry rows are marked ``sent`` per tick so the run-level
    manifest keeps streaming (see module docstring)."""

    def __init__(self, name: str, specs: List[Any]) -> None:
        super().__init__(name, specs)
        self._managed: List[ManagedSensor] = []
        self._channels: Dict[str, _SensorChannel] = {}  # spawn name → channel
        self._pub: _StreamPub | None = None
        self._sconf = _StreamConfig()
        self._pillow_ok = False

    # -- lifecycle ------------------------------------------------------

    def setup(self, ctx) -> None:
        super().setup(ctx)
        self._sconf = _StreamConfig.from_ctx(ctx)

        try:
            import PIL  # noqa: F401  (module-level check for the encoding path)
            self._pillow_ok = True
        except ImportError:
            ctx.logger.warning(
                "[export] stream: Pillow not available — sensor types that "
                "need it (rgb_camera) will NOT be streamed (pip install "
                "pillow); others keep streaming")

        # Keep only sensor types with a usable codec: an unknown type, or a
        # type whose codec needs Pillow while Pillow is absent, is skipped.
        specs, skipped = [], []
        for s in self._specs:
            codec = STREAM_CODECS.get(s.type)
            if codec is None:
                skipped.append(f"{s.name} (no codec for '{s.type}')")
            elif codec.needs_pillow and not self._pillow_ok:
                skipped.append(f"{s.name} (needs Pillow)")
            else:
                specs.append(s)
        if skipped:
            ctx.logger.warning(
                "[export] stream: skipped %d sensor(s): %s",
                len(skipped), ", ".join(skipped))
        if not specs:
            raise SensorSpawnError(
                "stream exporter: nothing to stream "
                f"(no sensor with a usable codec; skipped: {skipped or 'none'})")

        # Rename like kitti so the frame registry never collides with
        # rgb_camera/lidar exporters that share the same config names.
        self._specs = [dataclasses.replace(s, name="stream_" + s.name)
                       for s in specs]
        self._orig: Dict[str, str] = {}  # spawn name → config name
        for s in self._specs:
            self._orig[s.name] = s.name[len("stream_"):]

        self._pub = _StreamPub.bind(self._sconf.bind)
        self._managed = ctx.sensor_farm.spawn_all(
            self._specs, save=self._send_frame,
            workers_per_sensor=ctx.write_threads)
        if not self._managed:
            self._pub.close()
            self._pub = None
            raise SensorSpawnError(
                f"no '{self._name}' sensors could be spawned (see warnings)")
        for ms in self._managed:
            self._channels[ms.name] = _SensorChannel(
                spawn_name=ms.name, topic=self._orig[ms.name])

        self._publish_meta()

    def managed(self) -> List[ManagedSensor]:
        return list(self._managed)

    def on_sim_tick(self, export_frame: int, sim_time: float) -> None:
        """Mark the tick's registry row as ``sent`` for every streamed
        sensor — the manifest needs a slot per expected sensor to keep
        streaming (frames themselves are sent on writer threads)."""
        reg = self._ctx.frame_registry
        row = reg.get(export_frame)
        if row is not None:
            for ms in self._managed:
                reg.record_sensor(row, ms.name, "sent")

    def teardown(self) -> None:
        ctx = self._ctx
        if ctx is None:
            return
        for ms in self._managed:
            lost = ms.stop_and_drain()  # sends any queued frames, then stops
            if lost:
                ctx.logger.warning(
                    "[export] stream: %d frame(s) left unsent on shutdown "
                    "for '%s'", lost, ms.name)
        ctx.sensor_farm.destroy_all()
        if self._pub is not None:
            self._pub.close()
            self._pub = None

    # -- worker hook ----------------------------------------------------

    def _send_frame(self, ms: ManagedSensor, frame: int, ts: float,
                    data: Any, seq: int) -> Dict[str, Any]:
        """Worker-thread entry: encode + publish one frame, delegating the
        encoding to the sensor type's stream codec.  May raise — the worker
        marks the sequence failed and counts an error, like file exporters."""
        if self._pub is None:
            raise RuntimeError("stream socket not open")
        codec = STREAM_CODECS.get(ms.spec.type)
        if codec is None:
            raise RuntimeError(
                f"no stream codec for sensor type '{ms.spec.type}' — "
                f"register one with @stream_codec")
        meta_extra, payload = codec.encode(data, ms.spec, self._sconf)
        chan = self._channels[ms.name]
        meta: Dict[str, Any] = {
            "sensor": chan.topic, "seq": seq,
            "world_frame": frame, "sim_time": ts,
            "map": self._ctx.map_name, "run_id": self._ctx.run_output.run_id,
            **meta_extra,
        }
        self._pub.send_frame(chan.topic, meta, payload)
        return {"world_frame": frame, "sim_time": ts}

    # -- internals ------------------------------------------------------

    def _publish_meta(self) -> None:
        """One run-level metadata message (topic ``meta``) so a subscriber
        can configure itself (calibration, poses, step_length) before the
        frame stream starts."""
        sensors = []
        for ms in self._managed:
            spec = ms.spec
            codec = STREAM_CODECS.get(spec.type)
            calib = None
            if codec is not None and codec.calibration:
                try:
                    from ..calibration import calibration_document
                    calib = calibration_document(
                        self._orig[ms.name], spec.type, spec.blueprint,
                        int(spec.params["width"]), int(spec.params["height"]),
                        float(spec.params["fov"]), spec.transform)
                except Exception as exc:  # meta is advisory — never fatal
                    self._ctx.logger.warning(
                        "[export] stream: calibration for '%s' unavailable — %s",
                        ms.name, exc)
            sensors.append({
                "name": self._orig[ms.name], "type": spec.type,
                "transform": dict(spec.transform),
                "calibration": calib, "params": dict(spec.params)})
        doc = {
            "map": self._ctx.map_name,
            "run_id": self._ctx.run_output.run_id,
            "step_length": self._ctx.step_length,
            "sensors": sensors,
        }
        self._pub.send_frame(
            "meta",
            {"kind": "meta", "sensor": "meta", "seq": 0, "world_frame": 0,
             "sim_time": 0.0, "map": self._ctx.map_name,
             "run_id": self._ctx.run_output.run_id, "format": "json"},
            json.dumps(doc, ensure_ascii=False).encode("utf-8"))

