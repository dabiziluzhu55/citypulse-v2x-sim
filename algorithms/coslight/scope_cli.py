# algorithms/coslight/scope_cli.py
"""场景范围 CLI 解析（spec §7.2）：纯函数，便于单测。

--scenario-preset {xiongan_20,east_dense,west_dense}
--intersections demo_3,demo_5,...（单个整数 N 兼容为 demo_1..N）
两者互斥（由 evaluate.py 的 argparse mutually exclusive group 保证）。
"""
from __future__ import annotations

import re
from typing import Optional, Sequence

from config.scenario_presets import (
    ALL_DEMO_INTERSECTION_IDS, ResolvedScenarioScope,
    require_scenario_preset,
)

_DEMO_ID_RE = re.compile(r"demo_\d+")


def parse_intersections(value: str) -> tuple[str, ...]:
    """解析 --intersections：单个整数 N → demo_1..N；逗号列表保序。

    空项、重复 ID、非法格式均抛 ValueError（启动阶段报错，不静默去重）。
    """
    stripped = value.strip()
    if stripped.isdigit():
        n = int(stripped)
        if not 1 <= n <= len(ALL_DEMO_INTERSECTION_IDS):
            raise ValueError(
                f"--intersections N must be in [1, {len(ALL_DEMO_INTERSECTION_IDS)}], "
                f"got {n}")
        return tuple(f"demo_{i}" for i in range(1, n + 1))
    parts = [part.strip() for part in stripped.split(",")]
    if not parts or any(not part for part in parts):
        raise ValueError(
            "--intersections must be a positive integer or comma-separated demo_N ids")
    seen: set[str] = set()
    ordered: list[str] = []
    for part in parts:
        if not _DEMO_ID_RE.fullmatch(part):
            raise ValueError(f"invalid intersection id: {part!r} (expected demo_N)")
        if part in seen:
            raise ValueError(f"duplicate intersection id: {part}")
        seen.add(part)
        ordered.append(part)
    return tuple(ordered)


def resolve_scope(preset_id: Optional[str],
                  custom_ids: Optional[tuple[str, ...]]) -> ResolvedScenarioScope:
    """preset → custom → default 三选一（§7.2 解析规则）。"""
    if preset_id is not None:
        preset = require_scenario_preset(preset_id)
        return ResolvedScenarioScope(
            source="preset", preset_id=preset.preset_id,
            managed_ids=preset.intersection_ids)
    if custom_ids is not None:
        return ResolvedScenarioScope(
            source="custom", preset_id=None, managed_ids=custom_ids)
    return ResolvedScenarioScope(
        source="default", preset_id=None,
        managed_ids=ALL_DEMO_INTERSECTION_IDS)


def build_scope_block(scope: ResolvedScenarioScope,
                      registered_ids: Sequence[str]) -> dict:
    """§7.4 scope 块（与 collab.stats.scope_block 保持同构；一致性测试保证）。"""
    managed = list(scope.managed_ids)
    registered = list(registered_ids)
    return {
        "source": scope.source,
        "preset_id": scope.preset_id,
        "registered_intersections": len(registered),
        "algorithm_controlled_intersections": len(managed),
        "fixed_intersections": len(registered) - len(managed),
        "managed_ids": managed,
    }