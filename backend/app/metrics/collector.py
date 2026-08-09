"""兼容层：实现见 traffic_eval.collector"""

from traffic_eval.collector import (  # noqa: F401
    FUEL_POWERTRAINS,
    HARD_BRAKING_RATE_SOURCE,
    MetricsCollector,
    TrafficMetricsCollector,
)

__all__ = [
    "FUEL_POWERTRAINS",
    "HARD_BRAKING_RATE_SOURCE",
    "MetricsCollector",
    "TrafficMetricsCollector",
]
