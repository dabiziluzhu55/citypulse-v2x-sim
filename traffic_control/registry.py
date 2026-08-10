"""API和SUMO workers的唯一控制模式注册表
"""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class ControlModeSpec:
    """Static description of one product ``control_mode``."""

    name: str
    kernel_mode: str  # SUMO SimulationConfig.control_mode: fixed | algorithm
    algorithm_transport: str = ""  # "" | "local"
    algorithm_module: str = ""
    supported_presets: tuple[str, ...] | None = None

    @property
    def needs_algorithm(self) -> bool:
        return self.kernel_mode == "algorithm"

    @property
    def algorithm_name(self) -> str | None:
        """Legacy short name used by in-process controller factories / HTTP routes."""

        if not self.needs_algorithm:
            return None
        return self.name

    def allows_preset(self, preset_id: str) -> bool:
        if self.supported_presets is None:
            return True
        return preset_id in self.supported_presets


CONTROL_MODE_REGISTRY: dict[str, ControlModeSpec] = {
    "fixed": ControlModeSpec(
        name="fixed",
        kernel_mode="fixed",
    ),
    "max_pressure": ControlModeSpec(
        name="max_pressure",
        kernel_mode="algorithm",
        algorithm_transport="local",
        algorithm_module="traffic_control.max_pressure",
    ),
    "sotl": ControlModeSpec(
        name="sotl",
        kernel_mode="algorithm",
        algorithm_transport="local",
        algorithm_module="traffic_control.sotl",
    ),
    "ippo": ControlModeSpec(
        name="ippo",
        kernel_mode="algorithm",
        algorithm_transport="local",
        algorithm_module="traffic_control.ippo",
        supported_presets=("xiongan_20", "east_dense", "west_dense"),
    ),
    "mappo": ControlModeSpec(
        name="mappo",
        kernel_mode="algorithm",
        algorithm_transport="local",
        algorithm_module="traffic_control.mappo",
        supported_presets=("xiongan_20", "east_dense", "west_dense"),
    ),
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


def list_local_algorithm_modules() -> dict[str, str]:
    """Return ``{control_mode: algorithm_module}`` for local Protocol 2.0 algorithms."""

    return {
        name: spec.algorithm_module
        for name, spec in CONTROL_MODE_REGISTRY.items()
        if spec.needs_algorithm and spec.algorithm_module
    }


def validate_enabled_modes(enabled: tuple[str, ...] | list[str]) -> tuple[str, ...]:
    """Ensure the enabled whitelist is a non-empty registry subset."""

    modes = tuple(enabled)
    unknown = [mode for mode in modes if mode not in CONTROL_MODE_REGISTRY]
    if unknown:
        raise ValueError(
            f"enabled_control_modes contains unknown modes {unknown}; "
            f"registry has {sorted(CONTROL_MODE_REGISTRY)}"
        )
    if not modes:
        raise ValueError("enabled_control_modes must not be empty")
    return modes
