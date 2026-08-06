# config/scenario_presets.py
"""算法无关的场景预设注册表（单一事实源；backend 与 evaluate 共同导入）。

字段契约与既有 backend.app.scenario.presets 完全一致：
preset_id / label / intersection_ids / map_template。
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Literal, Optional


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


@dataclass(frozen=True, slots=True)
class ResolvedScenarioScope:
    """CLI 解析结果：算法控制 == 协同 managed 范围（spec §7.2/§7.3）。"""

    source: Literal["preset", "custom", "default"]
    preset_id: Optional[str] = None
    managed_ids: tuple[str, ...] = ()


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
