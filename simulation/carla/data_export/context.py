"""Runtime context shared by all exporters of one simulation run, plus the
per-tick frame registry that ties sensor results to simulation time."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Dict, List, Optional, Set

from .base import Exporter  # noqa: F401  (re-exported for convenience)


@dataclass
class ExportContext:
    """Everything an exporter needs at runtime.  Constructed by
    ``run_cosimulation.py`` once CARLA and SUMO are up."""

    world: Any                      # carla.World
    client: Any                     # carla.Client
    step_length: float              # simulation step (seconds per tick)
    max_sim_time: float             # --max-time
    map_name: str                   # short CARLA map name
    run_output: Any                 # data_export.output.RunOutput
    sensor_farm: Any                # data_export.sensors.SensorFarm
    frame_registry: Any             # FrameRegistry
    logger: Any                     # logging.Logger
    write_threads: int = 2          # parallel encoder workers per sensor


class FrameRegistry:
    """Ordered per-tick record of every sensor's export state.

    One row exists per simulation tick (created by the manager on every
    ``on_sim_tick`` call).  Sensor exporters fill their slot in a row once the
    frame's file has been written (keyed by the frame's simulation time —
    rows are tick indices, ``round(sim_time / step_length)``); gaps between
    two captured frames are marked ``no_data``.  The run-level manifest
    exporter serialises rows oldest-first once every expected sensor has a
    slot.
    """

    def __init__(self, step_length: float) -> None:
        self._step = step_length
        self._rows: Dict[int, Dict[str, Any]] = {}
        # Names of sensors that were actually spawned (set by the manager
        # after setup).  A row is complete when all of these have a slot.
        self.expected_sensors: Set[str] = set()

    def begin_frame(self, export_frame: int, sim_time: float) -> Dict[str, Any]:
        """Ensure a row exists for ``export_frame`` and return it."""
        row = self._rows.get(export_frame)
        if row is None:
            row = {"export_frame": export_frame, "sim_time": sim_time, "sensors": {}}
            self._rows[export_frame] = row
        return row

    def get(self, export_frame: int) -> Optional[Dict[str, Any]]:
        return self._rows.get(export_frame)

    def get_by_sim_time(self, sim_ts: float) -> Optional[Dict[str, Any]]:
        """The row whose simulation time is nearest ``sim_ts``."""
        return self._rows.get(round(sim_ts / self._step))

    def record_sensor(self, row: Dict[str, Any], sensor_name: str,
                      status: str, **extra: Any) -> None:
        """Fill (or overwrite) a sensor's slot in ``row``."""
        slot = {"status": status}
        slot.update(extra)
        row["sensors"][sensor_name] = slot

    def ordered_rows(self) -> List[Dict[str, Any]]:
        return [self._rows[k] for k in sorted(self._rows)]
