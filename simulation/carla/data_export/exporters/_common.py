"""Shared machinery for exporters that save one file per sensor frame
(RGB PNGs, lidar PLYs, ...): spawning the sensors through the farm with a
small worker pool per sensor, and consuming completed writes into the frame
registry strictly in capture-sequence order.

With parallel workers, frames complete out of order — correctness rests on
three invariants (see ``data_export.sensors``):

1. file names are a pure function of the capture sequence;
2. the consumer releases sequences strictly in order (waiting on the current
   one; dropped/failed sequences are skipped and recorded as ``no_data``);
3. the per-sensor manifest is written by the consumer (single thread), so
   JSONL lines are ordered and atomic.
"""

from __future__ import annotations

from typing import Any, Dict, List

from ..base import Exporter, SensorSpawnError
from ..sensors import ManagedSensor

_MISSING = object()  # sentinel for dict.get on locked results


class FileSinkExporter(Exporter):
    """Base class: one data file per captured frame.

    Subclasses define :attr:`ext` (file suffix) and :meth:`_save` (how the
    frame is written) plus :meth:`_manifest_extra` (per-sensor manifest
    metadata such as image size or point count).  Rows in the frame registry
    are keyed by simulation time (``round(sim_ts / step_length)``); ticks
    without a capture are marked ``no_data``.
    """

    ext: str = ""  # e.g. ".png"

    def __init__(self, name: str, specs) -> None:
        super().__init__(name, specs)
        self._managed: List[ManagedSensor] = []
        self._last_ef: Dict[str, int] = {}  # per-sensor last row filled

    # -- lifecycle ------------------------------------------------------

    def setup(self, ctx) -> None:
        super().setup(ctx)
        self._managed = ctx.sensor_farm.spawn_all(
            self._specs, save=self._save_frame,
            workers_per_sensor=ctx.write_threads)
        if not self._managed:
            raise SensorSpawnError(
                f"no '{self._name}' sensors could be spawned (see warnings)")

    def managed(self) -> List[ManagedSensor]:
        return list(self._managed)

    def on_sim_tick(self, export_frame: int, sim_time: float) -> None:
        for ms in self._managed:
            self._consume(ms)

    def teardown(self) -> None:
        ctx = self._ctx
        if ctx is None:
            return
        for ms in self._managed:
            lost = ms.stop_and_drain()
            if lost:
                ctx.logger.warning(
                    "[export] %s: %d frame(s) left unsaved on shutdown",
                    ms.name, lost)
            self._consume(ms)
        # Rows this sensor never filled (ticks without a capture, or frames
        # lost on shutdown) become explicit "no_data" entries.
        for ms in self._managed:
            for row in ctx.frame_registry.ordered_rows():
                if ms.name not in row["sensors"]:
                    ctx.frame_registry.record_sensor(row, ms.name, "no_data")
        ctx.sensor_farm.destroy_all()

    # -- ordered consumption ----------------------------------------------

    def _export_frame(self, sim_ts: float) -> int:
        """The 1-based tick index a frame belongs to (rows are tick-indexed)."""
        return round(sim_ts / self._ctx.step_length)

    def _consume(self, ms: ManagedSensor) -> None:
        """Release completed saves strictly in capture-sequence order.

        The sequence cursor blocks while the current sequence is still in
        flight (out-of-order completion is fine); sequences that were dropped
        at the callback or failed to save are released and recorded as
        ``no_data``, so the cursor never deadlocks.
        """
        reg = self._ctx.frame_registry
        last_ef = self._last_ef.get(ms.name, 0)
        while True:
            with ms.lock:
                result = ms.results.pop(ms.next_seq, _MISSING)
                dropped_ts = ms.dropped_seqs.pop(ms.next_seq, _MISSING)
            if result is _MISSING and dropped_ts is _MISSING:
                break  # current sequence still in flight (or never captured)

            if result is _MISSING:
                ef = self._export_frame(dropped_ts)
                status, slot, line = "no_data", {}, None
            elif result.get("error"):
                ef = self._export_frame(result["sim_time"])
                status, slot, line = "no_data", {}, None
            else:
                ef = self._export_frame(result["sim_time"])
                if ef <= last_ef:  # defensive: should not happen (seq monotonic)
                    ms.next_seq += 1
                    continue
                slot = {"file": f"{self._result_path_prefix(ms)}/{result['file']}",
                        "world_frame": result["world_frame"]}
                status = "written"
                line = {
                    "export_frame": ef,
                    "world_frame": result["world_frame"],
                    "sim_time": result["sim_time"],
                    "file": result["file"],
                    "pose": ms.spec.transform,
                    **result.get("extra", {}),
                }

            # Gap ticks between the last filled row and this one never had a
            # capture (e.g. fps < 1/step) → explicit "no_data".
            for gap in range(last_ef + 1, ef):
                row = reg.get(gap)
                if row is not None:
                    reg.record_sensor(row, ms.name, "no_data")
            row = reg.get(ef)
            if row is not None:
                reg.record_sensor(row, ms.name, status, **slot)
                if line is not None:
                    self._ctx.run_output.sensor_line(ms.name, line)
            last_ef = ef
            ms.next_seq += 1
        self._last_ef[ms.name] = last_ef

    # -- worker hook ------------------------------------------------------

    def _result_path_prefix(self, ms: ManagedSensor) -> str:
        """Run-relative path prefix recorded for written files in the frame
        registry (``kitti`` overrides to point into ``kitti/``)."""
        return f"sensors/{ms.name}"

    def _save_frame(self, ms: ManagedSensor, frame: int, ts: float,
                    data: Any, seq: int) -> Dict[str, Any]:
        """Worker-thread entry: save the frame to its sequence-derived file
        name and return the result record.  May raise — the worker then marks
        the sequence as failed."""
        fname = f"frame_{seq:06d}{self.ext}"
        path = self._ctx.run_output.sensor_dir(ms.name) / fname
        self._save(data, path)  # may raise → handled by the worker
        result: Dict[str, Any] = {
            "world_frame": frame,
            "sim_time": ts,
            "file": fname,
        }
        extra = self._manifest_extra(ms, frame, ts, data)
        if extra:
            result["extra"] = extra
        return result

    # -- subclass hooks -------------------------------------------------

    def _save(self, data: Any, path) -> None:
        """Persist one frame to ``path`` (runs on a writer worker)."""
        raise NotImplementedError

    def _manifest_extra(self, ms: ManagedSensor, frame: int, ts: float,
                        data: Any) -> Dict[str, Any]:
        """Extra per-sensor manifest metadata for one saved frame."""
        return {}
