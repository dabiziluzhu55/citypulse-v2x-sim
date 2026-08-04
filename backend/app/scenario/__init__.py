"""场景预设与启动请求解析"""

from .presets import (
    SCENARIO_PRESET_REGISTRY,
    ScenarioPreset,
    list_scenario_presets,
    require_scenario_preset,
    supported_intersection_ids,
)
from .resolver import resolve_start_simulation

__all__ = [
    "SCENARIO_PRESET_REGISTRY",
    "ScenarioPreset",
    "list_scenario_presets",
    "require_scenario_preset",
    "resolve_start_simulation",
    "supported_intersection_ids",
]
