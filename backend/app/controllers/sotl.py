"""SOTL兼容导入包装；实现位于traffic_control.sotl"""

from __future__ import annotations

from typing import Any

from traffic_control import sotl as _impl
from traffic_control.sotl import (
    DEFAULT_DECISION_INTERVAL,
    DEFAULT_MINIMUM_GREEN,
    DEFAULT_MU,
    DEFAULT_OMEGA,
    DEFAULT_THRESHOLD,
    SOTLController,
)

__all__ = [
    "DEFAULT_DECISION_INTERVAL",
    "DEFAULT_MINIMUM_GREEN",
    "DEFAULT_MU",
    "DEFAULT_OMEGA",
    "DEFAULT_THRESHOLD",
    "SOTLController",
]


def __getattr__(name: str) -> Any:
    """Expose private helpers for legacy unit tests."""

    return getattr(_impl, name)
