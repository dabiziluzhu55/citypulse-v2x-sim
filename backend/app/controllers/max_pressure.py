"""Max Pressure兼容导入；实现位于traffic_control.max_pressure"""

from __future__ import annotations

from typing import Any

from traffic_control import max_pressure as _impl
from traffic_control.max_pressure import (
    DEFAULT_PERMISSIVE_WEIGHT,
    DEFAULT_PROTECTED_WEIGHT,
    HALTING_SPEED_MPS,
    MaxPressureController,
)

__all__ = [
    "DEFAULT_PERMISSIVE_WEIGHT",
    "DEFAULT_PROTECTED_WEIGHT",
    "HALTING_SPEED_MPS",
    "MaxPressureController",
]


def __getattr__(name: str) -> Any:
    """Expose private helpers for legacy unit tests."""

    return getattr(_impl, name)
