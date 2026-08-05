"""Product traffic-control algorithms (Protocol 2.0 local modules).

Importing this package must remain free of torch. IPPO / MAPPO are loaded only
when the SUMO worker imports ``traffic_control.ippo`` / ``traffic_control.mappo``.
"""

from traffic_control.registry import (
    CONTROL_MODE_REGISTRY,
    ControlModeSpec,
    list_control_modes,
    require_control_mode,
)

__all__ = [
    "CONTROL_MODE_REGISTRY",
    "ControlModeSpec",
    "list_control_modes",
    "require_control_mode",
]
