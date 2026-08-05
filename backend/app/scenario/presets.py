"""场景预设注册表：单一事实源在 config/scenario_presets.py，此处透传导入。

保持既有导出名（SCENARIO_PRESET_REGISTRY / ScenarioPreset /
list_scenario_presets / require_scenario_preset / supported_intersection_ids /
ALL_DEMO_INTERSECTION_IDS）不变，避免破坏 backend 其它模块与前端契约。
"""

from __future__ import annotations

from config.scenario_presets import (
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
