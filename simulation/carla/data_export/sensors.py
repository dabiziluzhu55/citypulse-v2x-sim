"""Sensor farm: spawns CARLA sensor actors and bridges their asynchronous
data stream into a small pool of writer threads per sensor.

Threading model (keeps the simulation loop free of I/O)::

    CARLA worker thread                writer workers (N per sensor)
    ─────────────────                  ─────────────────────────────
    listen() callback                  get() → save file → results[seq]
      └─ assign seq, put_nowait(queue)  (N threads, completion may be
                                         out of order — that is fine)

    simulation loop: on_sim_tick → consume results by capture sequence,
    strictly in order (the consumer waits on the current seq; dropped or
    failed seqs are released and recorded as ``no_data``).

Correctness invariants:

* File names are a pure function of the capture sequence
  (``frame_%06d.png`` / ``.ply``), so any worker may compute them without
  a race and out-of-order completion is harmless.
* The per-sensor manifest is written by the *consumer* (single thread), so
  JSONL lines stay ordered and atomic.
* The bounded queue drops the *newest* frame when the workers cannot keep
  up (drop-newest, counters kept for the run meta).

PNG/PLY encoding is pure CPU and embarrassingly parallel across frames, so
``sensors[i].write_threads`` (default 2, per-sensor override — the old
``output.write_threads`` is gone) scales encode throughput on multi-core
servers — the first knob to turn when the "sensor queue full" warning
appears.
"""

from __future__ import annotations

import logging
import queue
import threading
import time
from dataclasses import dataclass, field
from typing import Any, Callable, Dict, List, Optional, Set, Tuple

from .config import DEFAULT_WRITE_THREADS, SensorSpec

logger = logging.getLogger("cosim.export")

QUEUE_MAXSIZE = 4          # ≈ 4× step of buffering per sensor
DRAIN_TIMEOUT_S = 10.0     # worker shutdown grace period


@dataclass
class ManagedSensor:
    """One spawned CARLA sensor plus its worker pool, queues and stats.

    Thread ownership:

    * ``seq``            — callback thread only (capture counter)
    * ``results``/``dropped_seqs`` — written by callback/workers under
      ``lock``, read-and-popped by the consumer under ``lock``
    * ``next_seq``       — consumer (simulation thread) only
    * ``drops``/``errors``/``written`` — GIL-atomic counters (logging only)
    """

    name: str
    spec: SensorSpec
    actor: Any = None
    queue: queue.Queue = field(default_factory=lambda: queue.Queue(maxsize=QUEUE_MAXSIZE))
    stop_event: threading.Event = field(default_factory=threading.Event)
    # 暂停门控(由联仿控制通道设置):置位期间 _on_data 静默丢弃数据,
    # seq 不递增 → 不写盘、队列/results 不堆积,恢复后捕获序号连续。
    pause_event: threading.Event = field(default_factory=threading.Event)
    workers: List[threading.Thread] = field(default_factory=list)
    lock: threading.Lock = field(default_factory=threading.Lock)
    seq: int = 0                    # next capture sequence (callback only)
    next_seq: int = 1               # consumer's cursor (consumer only)
    # seq → result dict; failed saves store {"error": True, "sim_time": ts}
    results: Dict[int, Dict[str, Any]] = field(default_factory=dict)
    dropped_seqs: Dict[int, float] = field(default_factory=dict)  # seq → sim_ts
    drops: int = 0                  # frames dropped because the queue was full
    errors: int = 0                 # failed file writes
    written: int = 0                # successfully saved frames
    save_time_total: float = 0.0    # wall time spent in save() (workers)
    save_time_count: int = 0

    def pending(self) -> bool:
        """True while data is buffered (queue or unconsumed results)."""
        return (not self.queue.empty()) or bool(self.results)

    def stop_and_drain(self, timeout: float = DRAIN_TIMEOUT_S) -> int:
        """Signal all workers to stop and wait for them to drain the queue.

        Returns the number of frames left unsaved (only if the workers were
        still busy when the timeout expired — the queue keeps its items).
        """
        self.stop_event.set()
        for worker in self.workers:
            if worker.is_alive():
                worker.join(timeout)
        remaining = 0
        while True:
            try:
                self.queue.get_nowait()
                remaining += 1
            except queue.Empty:
                break
        return remaining


def _save_one(ms: ManagedSensor,
              save: Callable[[ManagedSensor, int, float, Any, int], Dict[str, Any]],
              frame: int, ts: float, data: Any, seq: int) -> None:
    """Save one queued frame; called on a writer worker."""
    t0 = time.monotonic()
    try:
        result = save(ms, frame, ts, data, seq)
    except Exception as exc:  # OSError incl. disk-full, encoding errors, ...
        ms.errors += 1
        if ms.errors <= 5 or ms.errors % 100 == 0:
            logger.warning("[export] %s: frame save failed (%d×): %s",
                           ms.name, ms.errors, exc)
        with ms.lock:
            ms.results[seq] = {"error": True, "sim_time": ts}
        return
    with ms.lock:
        ms.written += 1
        ms.save_time_total += time.monotonic() - t0
        ms.save_time_count += 1
        ms.results[seq] = result


def _worker_loop(ms: ManagedSensor,
                 save: Callable[[ManagedSensor, int, float, Any, int], Dict[str, Any]]) -> None:
    """Writer worker body: save queued frames until stopped, then drain."""
    while True:
        try:
            item = ms.queue.get(timeout=0.5)
        except queue.Empty:
            if ms.stop_event.is_set():
                break
            continue
        _save_one(ms, save, *item)
    # Drain whatever is left after the stop signal.
    while True:
        try:
            item = ms.queue.get_nowait()
        except queue.Empty:
            break
        _save_one(ms, save, *item)


def _on_data(ms: ManagedSensor, data: Any) -> None:
    """CARLA sensor callback (runs on the client's worker thread): assign a
    capture sequence and enqueue the raw data object; drop-newest when the
    workers are behind (the dropped seq is recorded so the consumer can
    release it as ``no_data``)."""
    try:
        frame = int(data.frame)
        ts = float(data.timestamp)
    except (AttributeError, TypeError, ValueError):
        logger.warning("[export] %s: unexpected sensor data %r — ignored",
                       ms.name, type(data).__name__)
        return
    if ms.pause_event.is_set():
        return  # 暂停:不分配 seq、不写盘、不产生 results(静默丢弃)
    ms.seq += 1
    seq = ms.seq
    try:
        ms.queue.put_nowait((frame, ts, data, seq))
    except queue.Full:
        ms.drops += 1
        with ms.lock:
            ms.dropped_seqs[seq] = ts
        if ms.drops <= 5 or ms.drops % 100 == 0:
            logger.warning(
                "[export] %s: sensor queue full — dropping frame (drops=%d). "
                "Encoder/disk cannot keep up — increase the sensor's "
                "write_threads in the export config or reduce "
                "resolution/fps.", ms.name, ms.drops)


class SensorFarm:
    """Spawns and owns all sensor actors of an export run."""

    def __init__(self, world: Any, logger: logging.Logger = logger) -> None:
        self._world = world
        self._logger = logger
        self._sensors: List[ManagedSensor] = []

    def spawn_all(self, specs: List[SensorSpec],
                  save: Callable[[ManagedSensor, int, float, Any, int], Dict[str, Any]],
                  workers_per_sensor: int = DEFAULT_WRITE_THREADS,
                  ) -> List[ManagedSensor]:
        """Spawn one actor per spec, attach listeners and start ``workers_per_sensor``
        writer workers each.  Failures are logged and skipped — never fatal."""
        spawned: List[ManagedSensor] = []
        for spec in specs:
            ms = self._spawn_one(spec, save, workers_per_sensor)
            if ms is not None:
                spawned.append(ms)
                self._sensors.append(ms)
        return spawned

    def _spawn_one(self, spec: SensorSpec,
                   save: Callable[[ManagedSensor, int, float, Any, int], Dict[str, Any]],
                   workers_per_sensor: int,
                   ) -> Optional[ManagedSensor]:
        try:
            import carla  # lazy import — only needed at runtime
        except ImportError as exc:
            self._logger.warning("[export] carla Python API unavailable (%s) "
                                 "— cannot spawn sensor '%s'", exc, spec.name)
            return None

        try:
            bp = self._world.get_blueprint_library().find(spec.blueprint)
        except Exception as exc:
            self._logger.warning("[export] sensor '%s': blueprint '%s' not "
                                 "found (%s) — skipped", spec.name,
                                 spec.blueprint, exc)
            return None
        for key, value in spec.attributes.items():
            bp.set_attribute(key, str(value))
        try:
            actor = self._world.spawn_actor(
                bp,
                carla.Transform(
                    carla.Location(x=spec.transform["x"],
                                   y=spec.transform["y"],
                                   z=spec.transform["z"]),
                    carla.Rotation(pitch=spec.transform["pitch"],
                                   yaw=spec.transform["yaw"],
                                   roll=spec.transform["roll"])))
        except Exception as exc:
            self._logger.warning("[export] sensor '%s' spawn failed (%s) — "
                                 "skipped", spec.name, exc)
            return None

        ms = ManagedSensor(name=spec.name, spec=spec, actor=actor)
        actor.listen(lambda data: _on_data(ms, data))
        # Per-sensor write_threads overrides the caller's fallback (the old
        # global output.write_threads); the spawn log below prints the count.
        n = spec.write_threads or workers_per_sensor
        for idx in range(max(1, n)):
            worker = threading.Thread(target=_worker_loop, args=(ms, save),
                                      name=f"export-writer-{ms.name}-{idx}",
                                      daemon=True)
            ms.workers.append(worker)
            worker.start()
        self._logger.info("[export] spawned '%s' (%s, %d worker(s)) at "
                          "(%.2f, %.2f, %.2f)", ms.name, spec.blueprint,
                          len(ms.workers), spec.transform["x"],
                          spec.transform["y"], spec.transform["z"])
        return ms

    def destroy_all(self) -> None:
        """Destroy all sensor actors (idempotent)."""
        for ms in self._sensors:
            if ms.actor is not None:
                try:
                    ms.actor.destroy()
                except Exception as exc:
                    self._logger.debug("[export] %s: destroy error: %s",
                                       ms.name, exc)
                ms.actor = None

    # -- pause / resume (runtime control, see export_control.py) --------

    def pause_all(self) -> None:
        """Gate capture on every sensor (idempotent).  In-flight frames
        finish writing; no new frames are captured or saved."""
        for ms in self._sensors:
            ms.pause_event.set()

    def resume_all(self) -> None:
        """Open the capture gate on every sensor (idempotent)."""
        for ms in self._sensors:
            ms.pause_event.clear()

    def any_paused(self) -> bool:
        return any(ms.pause_event.is_set() for ms in self._sensors)
