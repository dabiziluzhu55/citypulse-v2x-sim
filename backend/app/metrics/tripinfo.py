"""兼容层：实现见 traffic_eval.tripinfo"""

from traffic_eval.tripinfo import (  # noqa: F401
    FUEL_POWERTRAINS,
    FUEL_SOURCE,
    TRAVEL_WAIT_SOURCE,
    TRIPINFO_READY_DELAY_S,
    TRIPINFO_READY_RETRIES,
    apply_tripinfo_completed_metrics,
    apply_tripinfo_fuel_intensity,
    parse_completed_tripinfo,
    parse_departed_tripinfo,
    wait_for_readable_tripinfo,
)

__all__ = [
    "FUEL_POWERTRAINS",
    "FUEL_SOURCE",
    "TRAVEL_WAIT_SOURCE",
    "TRIPINFO_READY_DELAY_S",
    "TRIPINFO_READY_RETRIES",
    "apply_tripinfo_completed_metrics",
    "apply_tripinfo_fuel_intensity",
    "parse_completed_tripinfo",
    "parse_departed_tripinfo",
    "wait_for_readable_tripinfo",
]
