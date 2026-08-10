"""Run-level manifest exporter: one JSON line per simulation tick listing
every sensor's status for that tick, plus sampled weather.

Rows are written oldest-first once complete (every spawned sensor has a
slot); a row usually becomes complete one tick after its data was captured
(the write pipeline lags by one step by design).
"""

from __future__ import annotations

from typing import Any, Dict

from ..base import register, Exporter

_WEATHER_KEYS = ("cloudiness", "precipitation", "wind_intensity",
                 "sun_azimuth_angle", "sun_altitude_angle",
                 "fog_density", "wetness")


@register("manifest")
class ManifestExporter(Exporter):
    """Serialises the frame registry into ``manifest.jsonl``."""

    def __init__(self, name: str, specs) -> None:
        super().__init__(name, [])
        self._next_to_write = 1
        self._last_weather_at = -1.0
        self._weather: Dict[str, float] = {}

    def setup(self, ctx) -> None:
        super().setup(ctx)
        self._sample_weather(force=True)

    def on_sim_tick(self, export_frame: int, sim_time: float) -> None:
        self._sample_weather(sim_time)
        self._flush_complete()

    def teardown(self) -> None:
        if self._ctx is None:
            return
        # Backfill slots the data exporters could not fill (writer lost
        # frames on shutdown) so every row is complete, then flush.
        for row in self._ctx.frame_registry.ordered_rows():
            for name in self._ctx.frame_registry.expected_sensors:
                if name not in row["sensors"]:
                    self._ctx.frame_registry.record_sensor(row, name, "no_data")
        self._flush_complete()
        # Belt & braces: rows still not serialised get a pending marker.
        for row in self._ctx.frame_registry.ordered_rows():
            if row["export_frame"] >= self._next_to_write:
                self._write_row(row)

    # -- internals ------------------------------------------------------

    def _row_complete(self, row: Dict[str, Any]) -> bool:
        return all(name in row["sensors"]
                   for name in self._ctx.frame_registry.expected_sensors)

    def _flush_complete(self) -> None:
        reg = self._ctx.frame_registry
        while True:
            row = reg.get(self._next_to_write)
            if row is None or not self._row_complete(row):
                break
            self._write_row(row)
            self._next_to_write += 1

    def _write_row(self, row: Dict[str, Any]) -> None:
        self._ctx.run_output.write_manifest_line({
            "export_frame": row["export_frame"],
            "sim_time": row["sim_time"],
            "weather": dict(self._weather),
            "sensors": row["sensors"],
        })

    def _sample_weather(self, sim_time: float | None = None,
                        force: bool = False) -> None:
        if not force and sim_time is not None and \
                (sim_time - self._last_weather_at) < 1.0:
            return
        try:
            w = self._ctx.world.get_weather()
            self._weather = {k: float(getattr(w, k, 0.0)) for k in _WEATHER_KEYS}
        except Exception as exc:  # weather unavailable on some maps
            self._ctx.logger.debug("[export] weather sampling failed: %s", exc)
        self._last_weather_at = sim_time if sim_time is not None else -1.0
