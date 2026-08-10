"""部署侧公共交通评估

Backend API与无后端命令行评估共用本包，保证指标公式一致

依赖：
- simulation.sumo.engine.session.SimulationSnapshot（采集输入）
- session / traffic manifest（燃油元数据）

"""

from .collector import MetricsCollector, TrafficMetricsCollector
from .models import EvalResult
from .powertrain import (
    VehicleTypeFuelMeta,
    load_fuel_meta_by_type,
    load_powertrain_by_type,
)
from .runner import LocalEvalRunResult, run_local_episode
from .session_hub import SessionMetricsHub
from .tripinfo import apply_tripinfo_completed_metrics, apply_tripinfo_fuel_intensity

__all__ = [
    "EvalResult",
    "LocalEvalRunResult",
    "MetricsCollector",
    "SessionMetricsHub",
    "TrafficMetricsCollector",
    "VehicleTypeFuelMeta",
    "apply_tripinfo_completed_metrics",
    "apply_tripinfo_fuel_intensity",
    "load_fuel_meta_by_type",
    "load_powertrain_by_type",
    "run_local_episode",
]
