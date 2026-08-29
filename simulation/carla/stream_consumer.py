#!/usr/bin/env python3
"""Reference consumer for the co-simulation's real-time sensor stream
(``data_export.exporters.stream`` — ZeroMQ PUB/SUB).

Run it in another terminal while the co-simulation exports with ``stream``::

    # 联仿端(先启动,再启动本消费端,见下):
    python run_cosimulation.py --sumocfg … --carla-map WestZone \
        --export rgb_camera,lidar,stream

    # 消费端(另一个终端;可先于联仿启动——ZMQ SUB 会自动等待 PUB):
    python stream_consumer.py --connect tcp://127.0.0.1:19091
    python stream_consumer.py --sensors demo_19,lidar_01   # 只订阅部分传感器
    python stream_consumer.py --save-dir ./recv           # 落盘 JPEG/.bin 验证

Wire protocol (3-part messages, see the stream exporter docstring)::

    [topic, meta_json, payload]
    topic "meta"  → payload holds the run metadata document (map/run_id/
                    step_length/sensor calibration & poses); meta_json is
                    just the standard header — sent once at setup
    topic <sensor>→ meta_json holds {kind, sensor, seq, world_frame,
                    sim_time, format, …}; payload is the frame bytes
                    (JPEG for cameras, x/y/z/intensity f32 LE for lidar,
                    zlib-compressed when meta.compressed is true)

Semantics every consumer must implement (see 数据导出接口.md §3.3):

* Consume by ``sim_time`` — frames are simulation-tick driven; wall-clock
  rate = 1/step_length × rt (rt = realtime ratio).  Never assume a constant
  wall-clock fps.
* Detect drops via ``seq`` gaps — the framework drops the newest frame when
  the send side is behind (drop-newest), so a gap in seq IS the drop
  evidence.  This consumer prints ``DROPPED n`` when it sees one.
* Reconnect — ZeroMQ SUB re-connects automatically; a stream that restarts
  (new run_id in meta) just resumes with new seq numbers.

Requires ``pyzmq`` (pip install pyzmq).  ``--save-dir`` additionally needs
nothing else: JPEG files are written as-is, lidar payloads are decoded back
to the 16 B/point f32 layout and written as ``.bin`` (KITTI-compatible).
"""

from __future__ import annotations

import argparse
import json
import os
import sys
import time
import zlib
from collections import defaultdict
from pathlib import Path
from typing import Any, Dict, List, Optional

DEFAULT_CONNECT = "tcp://127.0.0.1:19091"
FRAME_BYTES_PER_POINT = 16  # x/y/z/intensity float32 LE


def decode_lidar(payload: bytes, compressed: bool) -> bytes:
    """Stream lidar payload → 16 B/point f32 bytes (KITTI .bin compatible)."""
    if compressed:
        payload = zlib.decompress(payload)
    return payload


def save_frame(topic: str, meta: Dict[str, Any], payload: bytes,
               save_dir: Path, seq_by_topic: Dict[str, int]) -> None:
    """Write one frame under ``save_dir`` (camera → JPEG, lidar → .bin)."""
    sensor_dir = save_dir / topic
    sensor_dir.mkdir(parents=True, exist_ok=True)
    if meta.get("kind") == "lidar":
        data = decode_lidar(payload, bool(meta.get("compressed")))
        path = sensor_dir / f"frame_{meta['seq']:06d}.bin"
        path.write_bytes(data)
    else:
        path = sensor_dir / f"frame_{meta['seq']:06d}.jpg"
        path.write_bytes(payload)


class Consumer:
    """Subscribes to the stream, reports rate/drops and optionally saves."""

    def __init__(self, connect: str, sensors: Optional[List[str]],
                 save_dir: Optional[Path]) -> None:
        self._connect = connect
        self._wanted = set(sensors or [])
        self._save_dir = save_dir
        self._last_seq: Dict[str, int] = {}
        self._frames = 0
        self._drops = 0
        self._t0 = time.monotonic()

    def run(self, max_frames: int = 0, quiet: bool = False) -> int:
        import zmq  # lazily imported — optional dependency
        ctx = zmq.Context()
        sock = ctx.socket(zmq.SUB)
        sock.setsockopt(zmq.SUBSCRIBE, b"")  # everything; filter ourselves
        sock.setsockopt(zmq.RCVHWM, 32)
        sock.connect(self._connect)
        # Line-buffer stdout so progress is visible when piped/redirected.
        sys.stdout.reconfigure(line_buffering=True)
        if not quiet:
            print(f"subscribing to {self._connect} (Ctrl-C to stop)…")
        try:
            while True:
                parts = sock.recv_multipart()
                if len(parts) != 3:
                    continue  # unexpected message shape — ignore
                topic, meta_b, payload = parts
                try:
                    meta = json.loads(meta_b)
                except ValueError:
                    continue
                if topic == b"meta":
                    # Run-level metadata document lives in the payload part
                    # (the meta part is just the standard message header).
                    if not quiet:
                        self._show_meta(json.loads(payload))
                    continue
                if self._wanted and topic.decode() not in self._wanted:
                    continue
                self._on_frame(topic.decode(), meta, payload)
                self._frames += 1
                if not quiet and self._frames % 100 == 0:
                    self._report()
                if max_frames and self._frames >= max_frames:
                    break
        except KeyboardInterrupt:
            pass
        finally:
            sock.close()
            ctx.destroy(linger=0)
        if not quiet:
            self._report(final=True)
        return 0

    # -- internals ------------------------------------------------------

    def _on_frame(self, topic: str, meta: Dict[str, Any], payload: bytes) -> None:
        seq = int(meta.get("seq", 0))
        prev = self._last_seq.get(topic)
        # Writer threads complete out of order (documented), so a frame can
        # arrive with a LOWER seq than one already seen — that is reordering,
        # not a drop.  Only a forward gap (seq jumps past prev) is evidence
        # of a dropped frame (drop-newest on the send side).
        if prev is not None and seq > prev + 1:
            gap = seq - prev - 1
            self._drops += gap
            print(f"  DROPPED {gap} frame(s) on '{topic}' "
                  f"(seq {prev} → {seq}) — send side behind")
        if prev is None or seq > prev:
            self._last_seq[topic] = seq
        if self._save_dir is not None:
            save_frame(topic, meta, payload, self._save_dir, self._last_seq)

    def _report(self, final: bool = False) -> None:
        elapsed = time.monotonic() - self._t0
        fps = self._frames / max(elapsed, 1e-9)
        tag = "FINAL" if final else ""
        print(f"  {tag} {self._frames} frames, {fps:.1f} fps (wall-clock), "
              f"{self._drops} dropped, {elapsed:.1f}s")
        if final:
            for topic, seq in sorted(self._last_seq.items()):
                print(f"    - {topic}: last seq {seq}")

    def _show_meta(self, meta: Dict[str, Any]) -> None:
        print(f"meta: run_id={meta.get('run_id')} map={meta.get('map')} "
              f"step_length={meta.get('step_length')}s")
        for s in meta.get("sensors", []):
            print(f"  sensor {s['name']} ({s['type']}) "
                  f"pose={s.get('transform')}")


def main(argv: Optional[List[str]] = None) -> int:
    ap = argparse.ArgumentParser(
        description="Consume the co-simulation's real-time sensor stream "
                    "(see data_export.exporters.stream for the protocol).",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=("Examples:\n"
                "  python stream_consumer.py\n"
                "  python stream_consumer.py --sensors demo_19,lidar_01\n"
                "  python stream_consumer.py --save-dir ./recv --max-frames 600\n"))
    ap.add_argument("--connect", default=DEFAULT_CONNECT,
                    help=f"ZMQ connect address (default: {DEFAULT_CONNECT})")
    ap.add_argument("--sensors", metavar="NAMES",
                    help="comma-separated sensor names to subscribe "
                         "(default: all)")
    ap.add_argument("--save-dir", help="save received frames here "
                                       "(JPEG files + KITTI .bin lidar)")
    ap.add_argument("--max-frames", type=int, default=0,
                    help="stop after N frames (0 = run until Ctrl-C)")
    ap.add_argument("--quiet", action="store_true",
                    help="only print drops and the final summary")
    args = ap.parse_args(argv)

    try:
        import zmq  # noqa: F401
    except ImportError as exc:
        print(f"error: pyzmq is required (pip install pyzmq): {exc}",
              file=sys.stderr)
        return 2

    sensors = [s.strip() for s in args.sensors.split(",") if s.strip()] \
        if args.sensors else None
    save_dir = Path(args.save_dir) if args.save_dir else None
    if save_dir:
        save_dir.mkdir(parents=True, exist_ok=True)
    return Consumer(args.connect, sensors, save_dir).run(
        max_frames=args.max_frames, quiet=args.quiet)


if __name__ == "__main__":
    sys.exit(main())
