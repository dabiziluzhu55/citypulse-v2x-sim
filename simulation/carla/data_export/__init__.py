"""Pluggable data-export framework for the SUMO+CARLA co-simulation.

Export mode is activated from ``run_cosimulation.py`` via ``--export`` /
``--export-config``; the export configuration and the sensor placement files
written by ``spectator_coords.py --save`` share one JSON schema (see
``data_export.config``).  New output types are added by creating an exporter
class registered with ``@register("kind")`` and importing it from
``data_export.exporters`` — no changes to the co-simulation entry point are
required.

IMPORTANT: this package must stay importable WITHOUT the ``carla`` module
(no module-level ``import carla``) so ``run_cosimulation.py`` can import it
early and so the self-check can run on machines without CARLA installed.
"""

from .base import (Exporter, EXPORTER_REGISTRY, ExportError,
                   ExportConfigError, SensorSpawnError, OutputWriteError,
                   register)
from .config import (BLUEPRINTS, SensorSpec, ExportConfig,
                     load_export_config, merge_spectator_entry, effective_fps,
                     resolve_export_config_path, list_export_configs,
                     EXPORT_CONFIG_DIR)
from .context import ExportContext, FrameRegistry
from .output import RunOutput
from .sensors import SensorFarm
from .manager import ExporterManager
from . import exporters  # noqa: F401  (registers the built-in exporters)

__all__ = [
    "Exporter", "EXPORTER_REGISTRY", "ExportError", "ExportConfigError",
    "SensorSpawnError", "OutputWriteError", "register",
    "BLUEPRINTS", "SensorSpec", "ExportConfig", "load_export_config",
    "merge_spectator_entry", "effective_fps",
    "resolve_export_config_path", "list_export_configs", "EXPORT_CONFIG_DIR",
    "ExportContext", "FrameRegistry", "RunOutput", "SensorFarm",
    "ExporterManager",
]
