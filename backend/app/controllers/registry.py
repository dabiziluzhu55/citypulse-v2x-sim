"""管控模式注册表：只读 traffic_control.registry 元数据，不实例化算法。"""

from __future__ import annotations

from traffic_control.registry import (
    CONTROL_MODE_REGISTRY,
    ControlModeSpec,
    get_control_mode,
    is_supported_control_mode,
    list_control_modes,
    list_local_algorithm_modules,
    require_control_mode,
    validate_enabled_modes,
)

__all__ = [
    "CONTROL_MODE_REGISTRY",
    "ControlModeSpec",
    "get_control_mode",
    "is_supported_control_mode",
    "list_control_modes",
    "list_local_algorithm_modules",
    "require_control_mode",
    "validate_enabled_modes",
]
