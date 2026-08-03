"""指标数据模型：交通运行指标与对外序列化格式。"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Optional


def _rounded(value: Optional[float], digits: int) -> Optional[float]:
    return None if value is None else round(value, digits)


@dataclass
class EvalResult:
    """单次仿真（或当前滚动窗口）的评估指标"""

    algorithm: str = ""

    avg_travel_time_s: Optional[float] = None
    avg_waiting_time_s: Optional[float] = None
    avg_queue_length_veh: Optional[float] = None
    throughput_veh_per_h: Optional[float] = None
    avg_decision_latency_ms: Optional[float] = None
    fuel_intensity_L_per_100km: Optional[float] = None

    departed: int = 0
    arrived: int = 0
    completion_rate: Optional[float] = None
    metric_sources: dict[str, str] = field(default_factory=dict)
    warnings: list[str] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return {
            "algorithm": self.algorithm,
            "avg_travel_time_s": _rounded(self.avg_travel_time_s, 2),
            "avg_waiting_time_s": _rounded(self.avg_waiting_time_s, 2),
            "avg_queue_length_veh": _rounded(self.avg_queue_length_veh, 2),
            "throughput_veh_per_h": _rounded(self.throughput_veh_per_h, 1),
            "avg_decision_latency_ms": _rounded(self.avg_decision_latency_ms, 3),
            "fuel_intensity_L_per_100km": _rounded(
                self.fuel_intensity_L_per_100km, 2
            ),
            "departed": self.departed,
            "arrived": self.arrived,
            "completion_rate": _rounded(self.completion_rate, 4),
            "metric_sources": dict(self.metric_sources),
            "warnings": list(self.warnings),
        }

    def to_frontend_metrics(self) -> dict[str, Any]:
        """映射为 MetricsResponse / evaluation 兼容字段名。"""
        return {
            "algorithm": self.algorithm,
            "avg_waiting_time": _rounded(self.avg_waiting_time_s, 2),
            "avg_travel_time": _rounded(self.avg_travel_time_s, 2),
            "avg_queue_length": _rounded(self.avg_queue_length_veh, 2),
            "throughput": _rounded(self.throughput_veh_per_h, 1),
            "fuel_consumption": _rounded(self.fuel_intensity_L_per_100km, 2),
            "avg_decision_latency_ms": _rounded(self.avg_decision_latency_ms, 3),
            "departed": self.departed,
            "arrived": self.arrived,
            "completion_rate": _rounded(self.completion_rate, 4),
            "metric_sources": dict(self.metric_sources),
            "warnings": list(self.warnings),
        }
