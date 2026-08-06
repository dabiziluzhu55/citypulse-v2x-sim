# algorithms/v2x/collab/scope.py
"""算法控制/协同范围解析结果（spec §7.2/§7.3）。"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Literal, Optional


@dataclass(frozen=True, slots=True)
class ResolvedScenarioScope:
    """CLI 解析结果：算法控制 == 协同 managed 范围（spec §7.2/§7.3）。"""

    source: Literal["preset", "custom", "default"]
    preset_id: Optional[str] = None
    managed_ids: tuple[str, ...] = ()
