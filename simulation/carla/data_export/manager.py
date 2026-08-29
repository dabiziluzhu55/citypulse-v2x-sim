"""ExporterManager: instantiates exporters from the configuration, drives
their lifecycle from the simulation loop and enforces the isolation policy
(an exporter failure must never crash the simulation)."""

from __future__ import annotations

import time
from typing import Any, Dict, List, Optional, Set

from .base import EXPORTER_REGISTRY, Exporter, ExportConfigError
from .config import ExportConfig
from .context import ExportContext

# Number of consecutive on_sim_tick errors after which an exporter is
# disabled for the rest of the run.
MAX_CONSECUTIVE_ERRORS = 5


class ExporterManager:
    """Owns the exporters of one run.

    Lifecycle: ``setup_all()`` → ``on_sim_tick()`` × N → ``teardown_all()``.
    ``observe_rt()`` is called once per simulated second by the sync loop so
    the manager can warn when the export pipeline falls behind real time.
    """

    def __init__(self, config: ExportConfig, ctx: ExportContext,
                 kinds: Optional[List[str]] = None) -> None:
        self._config = config
        self._ctx = ctx
        self._kinds = kinds  # None → activate every kind present in the config
        self._exporters: List[Exporter] = []
        self._disabled: Set[str] = set()
        self._consecutive_errors: Dict[str, int] = {}
        self._rt_stats: Dict[str, float] = {
            "rt_ratio_mean": 0.0, "rt_ratio_min": 1.0,
            "rt_samples": 0, "slowdown_warnings": 0,
        }
        self._rt_slow_since: Optional[float] = None
        self._last_rt_warn = 0.0

    # ------------------------------------------------------------------
    # Setup
    # ------------------------------------------------------------------

    def _sensor_specs_for(self, kind: str,
                          by_type: Dict[str, List[Any]]) -> List[Any]:
        """Sensor specs handed to an exporter of ``kind``.

        Most kinds consume the config sensors of their own ``type``; ``kitti``
        and ``stream`` are consumer kinds — no sensor has ``type == "kitti"``
        / ``"stream"``, they spawn the config's rgb_camera + lidar specs
        (renamed internally by the exporters, see
        ``data_export.exporters.kitti`` / ``stream``).
        """
        if kind in ("kitti", "stream"):
            return [s for s in self._config.sensors
                    if s.type in ("rgb_camera", "lidar")]
        return by_type.get(kind, [])

    def setup_all(self) -> None:
        """Instantiate the requested exporters, group their sensor specs and
        run their setup with per-exporter fault isolation."""
        by_type: Dict[str, List[Any]] = {}
        for spec in self._config.sensors:
            by_type.setdefault(spec.type, []).append(spec)

        kinds = self._kinds if self._kinds is not None else sorted(by_type)
        for kind in kinds:
            if kind not in EXPORTER_REGISTRY:
                raise ExportConfigError(
                    f"unknown exporter kind '{kind}' "
                    f"(available: {', '.join(sorted(EXPORTER_REGISTRY))})")
            if kind not in by_type and kind not in ("kitti", "stream"):
                # kitti/stream 是消费型 kind:没有 type==kind 的传感器,它们
                # 消费配置里的相机+lidar specs(见 _sensor_specs_for)。
                self._ctx.logger.warning(
                    "[export] kind '%s' selected but the config has no "
                    "sensors of that type — nothing to do for it", kind)

        for kind in kinds:
            specs = self._sensor_specs_for(kind, by_type)
            if not specs:
                continue
            self._exporters.append(EXPORTER_REGISTRY[kind](kind, specs))

        # The run-level manifest is always written when any exporter is active.
        if self._exporters:
            self._exporters.append(EXPORTER_REGISTRY["manifest"]("manifest", []))

        active: List[Exporter] = []
        for exporter in self._exporters:
            try:
                exporter.setup(self._ctx)
                active.append(exporter)
                self._ctx.frame_registry.expected_sensors.update(
                    ms.name for ms in exporter.managed())
            except Exception as exc:
                self._ctx.logger.warning(
                    "[export] exporter '%s' setup failed — disabled: %s",
                    exporter.kind, exc)
        self._exporters = active

        if not active:
            self._ctx.logger.warning(
                "[export] no exporter could be activated — data export "
                "disabled, simulation continues")
            return

        n_cam = sum(1 for s in self._config.sensors if s.type == "rgb_camera")
        n_lidar = sum(1 for s in self._config.sensors if s.type == "lidar")
        self._ctx.logger.info(
            "[export] data export active: %d sensor(s) (%d camera, %d lidar) "
            "→ %s",
            len(self._ctx.frame_registry.expected_sensors), n_cam, n_lidar,
            self._ctx.run_output.dir)
        for spec in self._config.sensors:
            if spec.note:
                self._ctx.logger.info(
                    "[export]   %s (%s): %s", spec.name, spec.type, spec.note)

    # ------------------------------------------------------------------
    # Per-tick dispatch
    # ------------------------------------------------------------------

    # ------------------------------------------------------------------
    # Runtime pause / resume (set by the co-simulation control channel)
    # ------------------------------------------------------------------

    def pause(self) -> None:
        """Gate sensor capture on every managed sensor (idempotent).

        Also freezes every row that is still incomplete (ticks before the
        pause with no capture — e.g. fps < 1/step, or the pause itself):
        missing slots are backfilled ``no_data`` so the manifest can keep
        streaming.  In-flight frames overwrite their slot with ``written``
        when consumed (``record_sensor`` is overwrite-safe), so no data is
        lost.
        """
        self._ctx.sensor_farm.pause_all()
        reg = self._ctx.frame_registry
        for row in reg.ordered_rows():
            for name in reg.expected_sensors:
                if name not in row["sensors"]:
                    reg.record_sensor(row, name, "no_data")

    def resume(self) -> None:
        """Re-open sensor capture (idempotent)."""
        self._ctx.sensor_farm.resume_all()

    def is_paused(self) -> bool:
        return self._ctx.sensor_farm.any_paused()

    def is_active(self) -> bool:
        """True while at least one exporter is registered."""
        return bool(self._exporters)

    def on_sim_tick(self, export_frame: int, sim_time: float) -> None:
        """Create the tick's registry row and dispatch to every active
        exporter; a failing exporter is disabled after repeated errors.

        Row keys are derived from simulation time (``round(sim_time /
        step_length)``), matching ``FileSinkExporter._consume`` — a segment
        started mid-run (via the control channel) therefore keys its rows at
        the current sim frame, and its frames land in the right rows.
        """
        # 行号必须由 sim 时间推导(与 FileSinkExporter._consume 的
        # round(sim_ts/step_length) 一致);首段 sim 从 0 起步时与原 tick 计数
        # 相等,运行中途 start 的新段(如 sim=300s)行号从 6000 起,帧才落对行。
        sim_frame = round(sim_time / self._ctx.step_length)
        self._ctx.frame_registry.begin_frame(sim_frame, sim_time)
        if self._ctx.sensor_farm.any_paused():
            # 暂停期间行照建、槽位填 no_data:manifest 持续流式输出,
            # 帧号不跳不重,FrameRegistry 内存有界(与 _common 的 gap-fill 语义一致)
            row = self._ctx.frame_registry.get(sim_frame)
            for name in self._ctx.frame_registry.expected_sensors:
                if name not in row["sensors"]:
                    self._ctx.frame_registry.record_sensor(row, name, "no_data")
        for exporter in list(self._exporters):
            if exporter.kind in self._disabled:
                continue
            try:
                exporter.on_sim_tick(export_frame, sim_time)
            except Exception as exc:
                n = self._consecutive_errors.get(exporter.kind, 0) + 1
                self._consecutive_errors[exporter.kind] = n
                self._ctx.logger.warning(
                    "[export] exporter '%s' on_sim_tick failed (%d×): %s",
                    exporter.kind, n, exc)
                if n >= MAX_CONSECUTIVE_ERRORS:
                    self._disabled.add(exporter.kind)
                    self._ctx.logger.warning(
                        "[export] exporter '%s' disabled after %d "
                        "consecutive errors", exporter.kind, n)
            else:
                self._consecutive_errors.pop(exporter.kind, None)

    # ------------------------------------------------------------------
    # Real-time health (called ~1/s from the sync loop)
    # ------------------------------------------------------------------

    def observe_rt(self, rt_ratio: float) -> None:
        """Feed the wall-clock/real-time ratio into the manager's stats and
        warn (throttled) when the export pipeline falls behind."""
        st = self._rt_stats
        st["rt_samples"] += 1
        st["rt_ratio_min"] = min(st["rt_ratio_min"], rt_ratio)
        st["rt_ratio_mean"] += (rt_ratio - st["rt_ratio_mean"]) / st["rt_samples"]

        if rt_ratio < 0.8 and self.any_behind():
            now = time.time()
            if self._rt_slow_since is None:
                self._rt_slow_since = now
            elif now - self._rt_slow_since >= 5.0 and \
                    now - self._last_rt_warn >= 10.0:
                self._last_rt_warn = now
                st["slowdown_warnings"] += 1
                self._ctx.logger.warning(
                    "[export] pipeline behind real time (rt=%.1fx) with "
                    "buffered sensor data — reduce camera count, lower the "
                    "resolution (1280×720), lower fps, or raise "
                    "--step-length", rt_ratio)
        else:
            self._rt_slow_since = None

    def any_behind(self) -> bool:
        """True when any sensor has buffered (unsaved) data."""
        return any(ms.pending()
                   for exporter in self._exporters
                   for ms in exporter.managed())

    # ------------------------------------------------------------------
    # Teardown
    # ------------------------------------------------------------------

    def teardown_all(self, aborted: bool = False) -> None:
        """Stop all exporters (data exporters first, manifest last, so every
        row is final before the index is written), then finalise meta.json."""
        for exporter in reversed(list(self._exporters)):
            if exporter.kind == "manifest":
                continue
            try:
                exporter.teardown()
            except Exception as exc:
                self._ctx.logger.warning(
                    "[export] exporter '%s' teardown error: %s",
                    exporter.kind, exc)
        # Per-sensor run summary: quantify dropped frames and encoder speed,
        # so the operator can judge whether the write pipeline kept up.
        for exporter in self._exporters:
            for ms in exporter.managed():
                total = ms.written + ms.drops
                drop_rate = f"{ms.drops / total * 100:.1f}%" if total else "0.0%"
                avg_ms = (ms.save_time_total /
                          max(ms.save_time_count, 1)) * 1000.0
                self._ctx.logger.info(
                    "[export] %s: %d written, %d dropped (%s), "
                    "avg save %.0f ms/frame",
                    ms.name, ms.written, ms.drops, drop_rate, avg_ms)
        for exporter in self._exporters:
            if exporter.kind == "manifest":
                try:
                    exporter.teardown()
                except Exception as exc:
                    self._ctx.logger.warning(
                        "[export] manifest teardown error: %s", exc)
        try:
            self._ctx.run_output.finalize(self._final_meta(), aborted=aborted)
        except Exception as exc:
            self._ctx.logger.warning(
                "[export] could not finalise run output: %s", exc)

    def _final_meta(self) -> Dict[str, Any]:
        sensors_meta = []
        for exporter in self._exporters:
            for ms in exporter.managed():
                sensors_meta.append({
                    "name": ms.name,
                    "type": ms.spec.type,
                    "blueprint": ms.spec.blueprint,
                    "transform": dict(ms.spec.transform),
                    "note": ms.spec.note,
                    "frames_written": ms.written,
                    "drops": ms.drops,
                    "errors": ms.errors,
                    "avg_save_ms": round(
                        ms.save_time_total / max(ms.save_time_count, 1) * 1000.0, 1),
                })
        try:
            carla_version = self._ctx.client.get_server_version()
        except Exception:
            carla_version = None
        return {
            "step_length": self._ctx.step_length,
            "max_sim_time": self._ctx.max_sim_time,
            "carla_version": carla_version,
            "export": {
                "output_fps": self._config.output_fps,
                "sensors": sensors_meta,
            },
            "rt_stats": dict(self._rt_stats),
            "exporter_errors": dict(self._consecutive_errors),
        }
