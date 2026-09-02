"""指标数据模型：交通运行指标与对外序列化格式"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Optional


def _rounded(value: Optional[float], digits: int) -> Optional[float]:
    return None if value is None else round(value, digits)


@dataclass
class EvalResult:
    """单次仿真（或当前滚动窗口）的评估指标

    比例类字段单位约定：
    - delay_time_proportion：0~1的比例
    - spillback_rate：0~100的百分数
    - completion_rate：0~1的比例
    - hard_braking_rate：次/100辆
    None表示不可计算
    """

    algorithm: str = ""

    # ===== 标准核心指标 =====
    path_avg_speed_kmh: Optional[float] = None  # km/h，总距离/总时间
    travel_time_index: Optional[float] = None  # 无量纲，仿真等效 TTI
    delay_time_proportion: Optional[float] = None  # 0~1，DTP
    traffic_performance_index: Optional[float] = None  # 0~10，TPI
    traffic_state: Optional[str] = None
    tpi_method: Optional[str] = None
    avg_stops_per_vehicle: Optional[float] = None  # 次/车
    regional_max_queue_length_m: Optional[float] = None  # m
    regional_max_queue_intersection_id: Optional[str] = None
    regional_max_queue_lane_id: Optional[str] = None
    regional_max_queue_sim_time_s: Optional[float] = None
    spillback_rate: Optional[float] = None  # 0~100，进口车道-时间暴露率 %
    fuel_intensity_L_per_100km: Optional[float] = None  # L/100km

    # ===== 辅助指标 =====
    avg_travel_time_s: Optional[float] = None  # s，平均行程时间
    avg_waiting_time_s: Optional[float] = None  # s，平均停车等待时间
    avg_queue_length_veh: Optional[float] = None  # veh/lane，进口车道平均排队车辆数
    throughput_veh_per_h: Optional[float] = None  # veh/h，网络实际吞吐流率
    hard_braking_events: Optional[int] = None
    hard_braking_rate: Optional[float] = None
    avg_decision_latency_ms: Optional[float] = None  # ms，工程性能

    departed: int = 0
    arrived: int = 0
    completion_rate: Optional[float] = None  # 0~1
    metric_sources: dict[str, str] = field(default_factory=dict)
    warnings: list[str] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return {
            "algorithm": self.algorithm,
            "path_avg_speed_kmh": _rounded(self.path_avg_speed_kmh, 2),
            "travel_time_index": _rounded(self.travel_time_index, 4),
            "delay_time_proportion": _rounded(self.delay_time_proportion, 4),
            "traffic_performance_index": _rounded(
                self.traffic_performance_index, 2
            ),
            "traffic_state": self.traffic_state,
            "tpi_method": self.tpi_method,
            "avg_stops_per_vehicle": _rounded(self.avg_stops_per_vehicle, 2),
            "regional_max_queue_length_m": _rounded(
                self.regional_max_queue_length_m, 2
            ),
            "regional_max_queue_intersection_id": (
                self.regional_max_queue_intersection_id
            ),
            "regional_max_queue_lane_id": self.regional_max_queue_lane_id,
            "regional_max_queue_sim_time_s": _rounded(
                self.regional_max_queue_sim_time_s, 3
            ),
            "spillback_rate": _rounded(self.spillback_rate, 2),
            "fuel_intensity_L_per_100km": _rounded(
                self.fuel_intensity_L_per_100km, 2
            ),
            "avg_travel_time_s": _rounded(self.avg_travel_time_s, 2),
            "avg_waiting_time_s": _rounded(self.avg_waiting_time_s, 2),
            "avg_queue_length_veh": _rounded(self.avg_queue_length_veh, 2),
            "throughput_veh_per_h": _rounded(self.throughput_veh_per_h, 1),
            "avg_decision_latency_ms": _rounded(self.avg_decision_latency_ms, 3),
            "hard_braking_events": self.hard_braking_events,
            "hard_braking_rate": _rounded(self.hard_braking_rate, 2),
            "departed": self.departed,
            "arrived": self.arrived,
            "completion_rate": _rounded(self.completion_rate, 4),
            "metric_sources": dict(self.metric_sources),
            "warnings": list(self.warnings),
        }

    def to_frontend_metrics(self) -> dict[str, Any]:
        """映射为MetricsResponse / evaluation兼容字段名；保留全部旧 key。"""
        return {
            "algorithm": self.algorithm,
            "path_avg_speed_kmh": _rounded(self.path_avg_speed_kmh, 2),
            "travel_time_index": _rounded(self.travel_time_index, 4),
            "delay_time_proportion": _rounded(self.delay_time_proportion, 4),
            "traffic_performance_index": _rounded(
                self.traffic_performance_index, 2
            ),
            "traffic_state": self.traffic_state,
            "tpi_method": self.tpi_method,
            "avg_stops_per_vehicle": _rounded(self.avg_stops_per_vehicle, 2),
            "regional_max_queue_length_m": _rounded(
                self.regional_max_queue_length_m, 2
            ),
            "regional_max_queue_intersection_id": (
                self.regional_max_queue_intersection_id
            ),
            "regional_max_queue_lane_id": self.regional_max_queue_lane_id,
            "regional_max_queue_sim_time_s": _rounded(
                self.regional_max_queue_sim_time_s, 3
            ),
            "spillback_rate": _rounded(self.spillback_rate, 2),
            "avg_waiting_time": _rounded(self.avg_waiting_time_s, 2),
            "avg_travel_time": _rounded(self.avg_travel_time_s, 2),
            "avg_queue_length": _rounded(self.avg_queue_length_veh, 2),
            "throughput": _rounded(self.throughput_veh_per_h, 1),
            "fuel_consumption": _rounded(self.fuel_intensity_L_per_100km, 2),
            "fuel_intensity_L_per_100km": _rounded(
                self.fuel_intensity_L_per_100km, 2
            ),
            "hard_braking_events": self.hard_braking_events,
            "hard_braking_rate": _rounded(self.hard_braking_rate, 2),
            "avg_decision_latency_ms": _rounded(self.avg_decision_latency_ms, 3),
            "departed": self.departed,
            "arrived": self.arrived,
            "completion_rate": _rounded(self.completion_rate, 4),
            "metric_sources": dict(self.metric_sources),
            "warnings": list(self.warnings),
        }
