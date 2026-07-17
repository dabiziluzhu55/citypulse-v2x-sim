"""指标数据模型：定义评估结果结构与前端可用的序列化格式"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any


@dataclass
class EvalResult:
    """单次仿真（或当前滚动窗口）的评估指标。"""

    algorithm: str = ""

    avg_travel_time_s: float = 0.0
    avg_waiting_time_s: float = 0.0
    avg_queue_length_veh: float = 0.0
    throughput_veh_per_h: float = 0.0
    avg_decision_latency_ms: float = 0.0
    fuel_intensity_L_per_100km: float = 0.0

    departed: int = 0
    arrived: int = 0

    def to_dict(self) -> dict[str, Any]:
        return {
            "algorithm": self.algorithm,
            "avg_travel_time_s": round(self.avg_travel_time_s, 2),
            "avg_waiting_time_s": round(self.avg_waiting_time_s, 2),
            "avg_queue_length_veh": round(self.avg_queue_length_veh, 2),
            "throughput_veh_per_h": round(self.throughput_veh_per_h, 1),
            "avg_decision_latency_ms": round(self.avg_decision_latency_ms, 3),
            "fuel_intensity_L_per_100km": round(self.fuel_intensity_L_per_100km, 2),
            "departed": self.departed,
            "arrived": self.arrived,
        }

    def to_frontend_metrics(self) -> dict[str, Any]:
        """映射为前端时序图更易展示的字段名"""
        return {
            "algorithm": self.algorithm,
            "avg_waiting_time": round(self.avg_waiting_time_s, 2),
            "avg_travel_time": round(self.avg_travel_time_s, 2),
            "avg_queue_length": round(self.avg_queue_length_veh, 2),
            "throughput": round(self.throughput_veh_per_h, 1),
            "fuel_consumption": round(self.fuel_intensity_L_per_100km, 2),
            "avg_decision_latency_ms": round(self.avg_decision_latency_ms, 3),
            "departed": self.departed,
            "arrived": self.arrived,
        }
