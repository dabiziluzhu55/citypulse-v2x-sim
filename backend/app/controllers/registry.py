"""管控算法注册表：业务control_mode → SUMO内核模式/算法名

新增基线算法时只改此处，Schema、catalog、Service、Runtime均从此读取
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Callable

from .max_pressure import MaxPressureController


@dataclass(frozen=True)
class ControlModeSpec:
    """单个业务管控模式的静态描述"""

    name: str
    kernel_mode: str  # SUMO SimulationConfig.control_mode: fixed | algorithm
    algorithm_name: str | None = None

    @property
    def needs_algorithm(self) -> bool:
        return self.kernel_mode == "algorithm" and self.algorithm_name is not None


ControllerFactory = Callable[[dict[str, Any]], Any]

CONTROL_MODE_REGISTRY: dict[str, ControlModeSpec] = {
    "fixed": ControlModeSpec(
        name="fixed",
        kernel_mode="fixed",
        algorithm_name=None,
    ),
    "max_pressure": ControlModeSpec(
        name="max_pressure",
        kernel_mode="algorithm",
        algorithm_name="max_pressure",
    ),
}

# 算法名（仅算法型模式需要）
CONTROLLER_FACTORIES: dict[str, ControllerFactory] = {
    "max_pressure": MaxPressureController,
}


def list_control_modes() -> list[str]:
    return list(CONTROL_MODE_REGISTRY.keys())


def get_control_mode(name: str) -> ControlModeSpec | None:
    return CONTROL_MODE_REGISTRY.get(name)


def require_control_mode(name: str) -> ControlModeSpec:
    spec = CONTROL_MODE_REGISTRY.get(name)
    if spec is None:
        raise ValueError(
            f"Unsupported control_mode={name!r}. "
            f"Allowed: {sorted(CONTROL_MODE_REGISTRY)}"
        )
    return spec


def is_supported_control_mode(name: str) -> bool:
    return name in CONTROL_MODE_REGISTRY


def list_algorithm_names() -> list[str]:
    return sorted(
        {
            spec.algorithm_name
            for spec in CONTROL_MODE_REGISTRY.values()
            if spec.algorithm_name
        }
    )


def is_supported_algorithm(name: str) -> bool:
    return name in CONTROLLER_FACTORIES


def create_controller(algorithm_name: str, metadata: dict[str, Any]) -> Any:
    factory = CONTROLLER_FACTORIES.get(algorithm_name)
    if factory is None:
        raise ValueError(f"No controller factory for algorithm={algorithm_name!r}")
    return factory(metadata)


def validate_enabled_modes(enabled: tuple[str, ...] | list[str]) -> tuple[str, ...]:
    """校验启用白名单必须是注册表子集，返回规范化元组。"""
    modes = tuple(enabled)
    unknown = [m for m in modes if m not in CONTROL_MODE_REGISTRY]
    if unknown:
        raise ValueError(
            f"enabled_control_modes contains unknown modes {unknown}; "
            f"registry has {sorted(CONTROL_MODE_REGISTRY)}"
        )
    if not modes:
        raise ValueError("enabled_control_modes must not be empty")
    return modes
