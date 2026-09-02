"""兼容层：实现见 traffic_eval.tripinfo"""

from traffic_eval.tripinfo import (  # noqa: F401
    DTP_SOURCE,
    FUEL_POWERTRAINS,
    FUEL_SOURCE,
    PATH_AVG_SPEED_SOURCE,
    STOPS_SOURCE,
    TRAVEL_WAIT_SOURCE,
    TRIPINFO_READY_DELAY_S,
    TRIPINFO_READY_RETRIES,
    TTI_SOURCE,
    apply_tripinfo_completed_metrics,
    apply_tripinfo_fuel_intensity,
    apply_tripinfo_official_metrics,
    apply_tripinfo_path_metrics,
    parse_completed_tripinfo,
    parse_departed_tripinfo,
    wait_for_readable_tripinfo,
)

__all__ = [
    "DTP_SOURCE",
    "FUEL_POWERTRAINS",
    "FUEL_SOURCE",
    "PATH_AVG_SPEED_SOURCE",
    "STOPS_SOURCE",
    "TRAVEL_WAIT_SOURCE",
    "TRIPINFO_READY_DELAY_S",
    "TRIPINFO_READY_RETRIES",
    "TTI_SOURCE",
    "apply_tripinfo_completed_metrics",
    "apply_tripinfo_fuel_intensity",
    "apply_tripinfo_official_metrics",
    "apply_tripinfo_path_metrics",
    "parse_completed_tripinfo",
    "parse_departed_tripinfo",
    "wait_for_readable_tripinfo",
]
