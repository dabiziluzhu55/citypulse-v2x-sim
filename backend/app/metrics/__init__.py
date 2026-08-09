"""Backend 指标层：薄封装，转发至部署侧公共包 traffic_eval。

产品指标公式的唯一实现在仓库根目录 traffic_eval/；本包仅保持既有导入路径兼容。
"""

from traffic_eval import (  # noqa: F401
    EvalResult,
    MetricsCollector,
    SessionMetricsHub,
    TrafficMetricsCollector,
    VehicleTypeFuelMeta,
    apply_tripinfo_completed_metrics,
    apply_tripinfo_fuel_intensity,
    load_fuel_meta_by_type,
    load_powertrain_by_type,
)
from traffic_eval.collector import HARD_BRAKING_RATE_SOURCE  # noqa: F401
from traffic_eval.tripinfo import FUEL_POWERTRAINS, FUEL_SOURCE  # noqa: F401

__all__ = [
    "EvalResult",
    "FUEL_POWERTRAINS",
    "FUEL_SOURCE",
    "HARD_BRAKING_RATE_SOURCE",
    "MetricsCollector",
    "SessionMetricsHub",
    "TrafficMetricsCollector",
    "VehicleTypeFuelMeta",
    "apply_tripinfo_completed_metrics",
    "apply_tripinfo_fuel_intensity",
    "load_fuel_meta_by_type",
    "load_powertrain_by_type",
]
