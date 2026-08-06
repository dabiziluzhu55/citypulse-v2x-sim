# algorithms/presets.py
"""算法端场景预设注册表（训练/评估用，自包含，不依赖后端）。

与后端 ``backend/app/scenario/presets.py`` 按 preset_id 协议保持一致。
评估/测试的统一入口由后端提供脚本驱动，届时本注册表可按其接口精简。
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Optional


@dataclass(frozen=True, slots=True)
class ScenarioPreset:
    preset_id: str
    label: str
    intersection_ids: tuple[str, ...]
    map_template: str


SCENARIO_PRESET_REGISTRY: dict[str, ScenarioPreset] = {
    "xiongan_20": ScenarioPreset(
        preset_id="xiongan_20",
        label="雄安20路口路网",
        intersection_ids=tuple(f"demo_{i}" for i in range(1, 21)),
        map_template="xiongan20",
    ),
    "east_dense": ScenarioPreset(
        preset_id="east_dense",
        label="东部密集路口场景",
        intersection_ids=("demo_3", "demo_5", "demo_6", "demo_9"),
        map_template="east_dense",
    ),
    "west_dense": ScenarioPreset(
        preset_id="west_dense",
        label="西部密集路口场景",
        intersection_ids=("demo_14", "demo_15", "demo_19"),
        map_template="west_dense",
    ),
}

# evaluate 侧别名
SCENARIO_PRESETS: dict[str, ScenarioPreset] = SCENARIO_PRESET_REGISTRY

ALL_DEMO_INTERSECTION_IDS: tuple[str, ...] = tuple(
    f"demo_{i}" for i in range(1, 21)
)


def list_scenario_presets() -> list[ScenarioPreset]:
    return [SCENARIO_PRESET_REGISTRY[key] for key in sorted(SCENARIO_PRESET_REGISTRY)]


def require_scenario_preset(preset_id: str) -> ScenarioPreset:
    preset = SCENARIO_PRESET_REGISTRY.get(preset_id)
    if preset is None:
        allowed = sorted(SCENARIO_PRESET_REGISTRY)
        raise ValueError(
            f"scenario_preset_id must be one of {allowed}, got {preset_id!r}."
        )
    return preset


def supported_intersection_ids() -> tuple[str, ...]:
    seen: set[str] = set()
    ordered: list[str] = []
    for preset in list_scenario_presets():
        for intersection_id in preset.intersection_ids:
            if intersection_id not in seen:
                seen.add(intersection_id)
                ordered.append(intersection_id)
    return tuple(ordered)
