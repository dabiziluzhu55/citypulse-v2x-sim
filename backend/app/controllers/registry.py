"""管控算法注册表：从 traffic_control.registry 读取模式配置

新增基线算法时只改 traffic_control.registry；Schema、catalog、Service、Runtime
均从此读取。本模块保留 create_controller 等旧接口兼容性。
"""

from __future__ import annotations

from typing import Any, Callable

from traffic_control.registry import (
    CONTROL_MODE_REGISTRY,
    ControlModeSpec,
    get_control_mode,
    is_supported_control_mode,
    list_control_modes,
    require_control_mode,
    validate_enabled_modes,
)

ControllerFactory = Callable[[dict[str, Any]], Any]

# 仅进程内Controller类（HTTP 内部协议 / 单元测试兼容）
# IPPO使用local Protocol 2.0模块，不进入工厂表，避免FastAPI启动导入torch
_CONTROLLER_IMPORTS: dict[str, tuple[str, str]] = {
    "max_pressure": ("traffic_control.max_pressure", "MaxPressureController"),
    "sotl": ("traffic_control.sotl", "SOTLController"),
}


def list_algorithm_names() -> list[str]:
    return sorted(_CONTROLLER_IMPORTS)


def is_supported_algorithm(name: str) -> bool:
    return name in _CONTROLLER_IMPORTS


def create_controller(algorithm_name: str, metadata: dict[str, Any]) -> Any:
    spec = _CONTROLLER_IMPORTS.get(algorithm_name)
    if spec is None:
        raise ValueError(f"No controller factory for algorithm={algorithm_name!r}")
    module_name, attr_name = spec
    import importlib

    module = importlib.import_module(module_name)
    factory = getattr(module, attr_name)
    return factory(metadata)

CONTROLLER_FACTORIES: dict[str, ControllerFactory] = {
    name: (lambda metadata, _n=name: create_controller(_n, metadata))
    for name in _CONTROLLER_IMPORTS
}


__all__ = [
    "CONTROL_MODE_REGISTRY",
    "CONTROLLER_FACTORIES",
    "ControlModeSpec",
    "create_controller",
    "get_control_mode",
    "is_supported_algorithm",
    "is_supported_control_mode",
    "list_algorithm_names",
    "list_control_modes",
    "require_control_mode",
    "validate_enabled_modes",
]
