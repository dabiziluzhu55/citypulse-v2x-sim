"""Exporter plugin contract, registry and error types for the data-export
framework.

A data exporter turns one *kind* of simulation data (RGB camera frames,
lidar point clouds, ...) into files under the run output directory.  New
exporters are added by subclassing :class:`Exporter`, decorating the class
with :func:`register` and importing it from :mod:`data_export.exporters` —
no changes to ``run_cosimulation.py`` are required.

This module (and the whole package) must stay importable WITHOUT the
``carla`` module, so the co-simulation entry point can import it early and
so the self-check can run on machines without CARLA installed.
"""

from __future__ import annotations

import abc
from typing import TYPE_CHECKING, Any, Callable, Dict, List, Optional, Type

if TYPE_CHECKING:  # importable only for type checkers (avoids cycles)
    from .config import SensorSpec
    from .context import ExportContext


class ExportError(Exception):
    """Base class for all data-export errors."""


class ExportConfigError(ExportError):
    """Invalid export configuration (schema, ranges, file format)."""


class SensorSpawnError(ExportError):
    """A CARLA sensor could not be spawned (blueprint missing, spawn failed)."""


class OutputWriteError(ExportError):
    """A data file could not be written to the output directory."""


EXPORTER_REGISTRY: Dict[str, Type["Exporter"]] = {}


def register(kind: str) -> Callable[[Type["Exporter"]], Type["Exporter"]]:
    """Class decorator registering an exporter under ``kind``.

    ``kind`` is also the value used in the export configuration's
    ``sensors[].type`` field (see :data:`data_export.config.BLUEPRINTS`).
    """

    def deco(cls: Type["Exporter"]) -> Type["Exporter"]:
        if kind in EXPORTER_REGISTRY:
            raise ExportError(f"duplicate exporter kind '{kind}'")
        cls.kind = kind
        EXPORTER_REGISTRY[kind] = cls
        return cls

    return deco


class Exporter(abc.ABC):
    """Base class for all data exporters.

    Lifecycle (orchestrated by ``ExporterManager``)::

        exporter = Cls(kind, specs)
        exporter.setup(ctx)                       # after CARLA + SUMO are up
        exporter.on_sim_tick(frame, sim_time)     # once per sync-loop step
        exporter.teardown()                       # before CARLA sync is closed

    Contract:

    * ``__init__`` must not touch CARLA or the filesystem (the world may not
      be ready yet) — it only stores configuration.
    * ``setup`` may fail; the manager catches the exception, disables this
      exporter and lets the simulation continue.
    * ``on_sim_tick`` must be cheap (memory-only, < 1 ms) — all file I/O
      happens on writer threads.
    * ``teardown`` must never raise.
    """

    kind: str = ""

    def __init__(self, name: str, specs: List["SensorSpec"]) -> None:
        self._name = name
        self._specs = list(specs)
        self._ctx: Optional[ExportContext] = None

    def setup(self, ctx: "ExportContext") -> None:
        """Prepare the exporter (spawn sensors, open outputs)."""
        self._ctx = ctx

    @abc.abstractmethod
    def on_sim_tick(self, export_frame: int, sim_time: float) -> None:
        """Called once per simulation step after the CARLA tick."""

    def teardown(self) -> None:
        """Stop writers, destroy sensors, flush any buffered data."""

    def managed(self) -> List[Any]:
        """The :class:`data_export.sensors.ManagedSensor` instances owned by
        this exporter (used by the manager for statistics)."""
        return []
