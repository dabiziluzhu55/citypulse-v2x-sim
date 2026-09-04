"""场景预设与启动请求解析
"""

from .presets import (
    SCENARIO_PRESET_REGISTRY,
    ScenarioPreset,
    list_scenario_presets,
    require_scenario_preset,
    supported_intersection_ids,
)

__all__ = [
    "SCENARIO_PRESET_REGISTRY",
    "ScenarioPreset",
    "list_scenario_presets",
    "require_scenario_preset",
    "resolve_start_simulation",
    "supported_intersection_ids",
]


def __getattr__(name: str):
    if name == "resolve_start_simulation":
        from .resolver import resolve_start_simulation

        return resolve_start_simulation
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
