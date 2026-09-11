"""Shared scenario preset catalog for backend, traffic_eval, and workers."""

from .presets import (
    ALL_DEMO_INTERSECTION_IDS,
    SCENARIO_PRESET_REGISTRY,
    ScenarioPreset,
    list_scenario_presets,
    require_scenario_preset,
    supported_intersection_ids,
)

__all__ = [
    "ALL_DEMO_INTERSECTION_IDS",
    "SCENARIO_PRESET_REGISTRY",
    "ScenarioPreset",
    "list_scenario_presets",
    "require_scenario_preset",
    "supported_intersection_ids",
]
