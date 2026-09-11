"""Backend control-mode registry (metadata only; algorithms run in SUMO worker)."""

from .registry import (
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
