"""兼容层：实现见 traffic_eval.powertrain"""

from traffic_eval.powertrain import (  # noqa: F401
    VehicleTypeFuelMeta,
    load_fuel_meta_by_type,
    load_powertrain_by_type,
)

__all__ = [
    "VehicleTypeFuelMeta",
    "load_fuel_meta_by_type",
    "load_powertrain_by_type",
]
