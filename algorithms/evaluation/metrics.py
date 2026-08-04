"""竞赛算法评估指标 -- 6 大指标纯计算，与仿真端解耦。
适用于所有算法：FixedTime / MaxPressure / SOTL / IPPO / 自研RL。
"""

from __future__ import annotations

import xml.etree.ElementTree as ET
from dataclasses import dataclass, field
from pathlib import Path
from typing import Dict, List, Optional


@dataclass
class BenchmarkResult:
    """单次仿真的全部指标。"""

    algorithm: str = ""
    scenario: str = ""

    # 原始数据
    total_departed: int = 0
    total_arrived: int = 0
    total_planned: int = 0
    eval_duration_s: float = 0.0

    # 6 大指标
    avg_travel_time_s: float = 0.0            # 1. 平均行程时间
    avg_waiting_time_s: float = 0.0           # 2. 平均等待时间
    avg_queue_length_veh: float = 0.0         # 3. 平均排队长度
    throughput_veh_per_h: float = 0.0         # 4. 有效吞吐量
    avg_decision_latency_ms: float = 0.0      # 5. 平均决策耗时
    fuel_intensity_L_per_100km: float = 0.0   # 6. 单位车公里燃油消耗

    # 辅助（队列时序数据来源）
    avg_queue_per_step: List[float] = field(default_factory=list)

    def to_dict(self) -> Dict[str, float]:
        return {
            "avg_travel_time_s": round(self.avg_travel_time_s, 2),
            "avg_waiting_time_s": round(self.avg_waiting_time_s, 2),
            "avg_queue_length_veh": round(self.avg_queue_length_veh, 2),
            "throughput_veh_per_h": round(self.throughput_veh_per_h, 1),
            "avg_decision_latency_ms": round(self.avg_decision_latency_ms, 3),
            "fuel_intensity_L_per_100km": round(self.fuel_intensity_L_per_100km, 2),
            "departed": self.total_departed,
            "arrived": self.total_arrived,
        }


# ======================================================================
# 指标计算
# ======================================================================


def compute_from_tripinfo(
    tripinfo_path: str,
    *,
    eval_duration_s: float = 0.0,
    queue_timeseries: Optional[List[float]] = None,
    lane_count: int = 0,
    emission_path: Optional[str] = None,
    total_planned: int = 0,
    algorithm: str = "",
    scenario: str = "",
) -> BenchmarkResult:
    """从 SUMO tripinfo.xml 计算全部 6 大指标。

    Parameters
    ----------
    tripinfo_path : str
        SUMO 的 tripinfo 输出文件路径。
    eval_duration_s : float
        仿真评估时长（秒），用于吞吐量计算。
    queue_timeseries : list[float] or None
        每步的平均进口道排队长度（已按步采样），用于计算平均排队长度。
    lane_count : int
        进口道数量，用于排队长度归一化。queue_timeseries 已归一化时可传 1。
    emission_path : str or None
        SUMO 排放输出文件路径（可选，无排放数据时燃油指标为 0）。
    total_planned : int
        计划出发车辆数，用于完成率计算。
    algorithm : str
        算法名称（仅用于标记结果）。
    scenario : str
        场景名称（仅用于标记结果）。
    """
    result = BenchmarkResult(algorithm=algorithm, scenario=scenario)

    tree = ET.parse(tripinfo_path)
    root = tree.getroot()

    # ── 遍历 tripinfo ──
    trips = []
    total_waiting = 0.0
    total_travel = 0.0
    total_distance = 0.0

    for trip in root.findall("tripinfo"):
        depart = float(trip.get("depart", 0))
        arrival = float(trip.get("arrival", -1))

        if arrival < 0:
            continue  # 未到达，跳过（不参与行程/等待计算）

        duration = float(trip.get("duration", 0))
        wait = float(trip.get("waitingTime", 0))
        length = float(trip.get("routeLength", 0))

        total_travel += duration
        total_waiting += wait
        total_distance += length
        trips.append(trip)

    arrived = len(trips)
    departed = sum(1 for t in root.findall("tripinfo"))

    result.total_arrived = arrived
    result.total_departed = departed
    result.total_planned = total_planned if total_planned > 0 else departed
    result.eval_duration_s = eval_duration_s

    # 1. 平均行程时间
    result.avg_travel_time_s = total_travel / arrived if arrived > 0 else 0.0

    # 2. 平均等待时间
    result.avg_waiting_time_s = total_waiting / arrived if arrived > 0 else 0.0

    # 3. 平均排队长度（从时序数据计算）
    if queue_timeseries:
        result.avg_queue_per_step = queue_timeseries
        result.avg_queue_length_veh = sum(queue_timeseries) / len(queue_timeseries)
    elif lane_count > 0:
        result.avg_queue_length_veh = 0.0  # 无时序数据时无法计算

    # 4. 有效吞吐量（veh/h）
    if eval_duration_s > 0:
        result.throughput_veh_per_h = (arrived / eval_duration_s) * 3600.0
    else:
        # 用到达时间跨度估算
        if arrived >= 2 and trips:
            span = max(float(t.get("arrival", 0)) for t in trips) - \
                   min(float(t.get("depart", 0)) for t in trips)
            if span > 0:
                result.throughput_veh_per_h = (arrived / span) * 3600.0

    # 5. 单位车公里燃油消耗（L/100km）
    if emission_path and Path(emission_path).exists():
        fuel_total_ml, emission_distance_m = _parse_emission(emission_path)
        if emission_distance_m > 0:
            # fuel_total_ml → L, distance_m → 100km
            fuel_L = fuel_total_ml / 1000.0
            dist_100km = emission_distance_m / 100000.0
            result.fuel_intensity_L_per_100km = fuel_L / dist_100km if dist_100km > 0 else 0.0
    elif total_distance > 0:
        # 用 tripinfo 的距离估算（无排放文件时燃油消耗无法精确计算，填 0）
        # SUMO 用 HBEFA3 模型，近似：汽油 8L/100km 作为常数参考
        # 此处不做估算，如有排放文件再填充
        result.fuel_intensity_L_per_100km = 0.0

    return result


# ======================================================================
# 排放文件解析
# ======================================================================


def _parse_emission(emission_path: str) -> tuple:
    """从 SUMO emission 输出提取总燃油消耗(mL)和总行驶距离(m)。

    Returns
    -------
    (fuel_total_ml, distance_total_m)
    """
    tree = ET.parse(emission_path)
    root = tree.getroot()

    fuel_ml = 0.0
    distance_m = 0.0

    for timestep in root.findall("timestep"):
        for vehicle in timestep.findall("vehicle"):
            fuel_ml += float(vehicle.get("fuel", 0))       # SUMO 排放单位: mL
            distance_m += float(vehicle.get("routeLength", 0))

    return fuel_ml, distance_m


# ======================================================================
# 对比表输出
# ======================================================================


def print_comparison_table(results: List[BenchmarkResult]) -> str:
    """生成对比表格字符串。"""
    header = (
        f"{'算法':<20} {'行程(s)':>8} {'等待(s)':>8} {'排队':>6} "
        f"{'吞吐':>8} {'延迟(ms)':>8} {'油耗':>8}"
    )
    sep = "-" * len(header)

    lines = [header, sep]
    for r in results:
        lines.append(
            f"{r.algorithm:<20} {r.avg_travel_time_s:>8.1f} {r.avg_waiting_time_s:>8.1f} "
            f"{r.avg_queue_length_veh:>6.2f} {r.throughput_veh_per_h:>8.1f} "
            f"{r.avg_decision_latency_ms:>8.3f} {r.fuel_intensity_L_per_100km:>7.2f}"
        )

    return "\n".join(lines)


def print_markdown_table(results: List[BenchmarkResult]) -> str:
    """生成 Markdown 对比表格。"""
    lines = [
        "| 算法 | 行程时间(s) | 等待时间(s) | 排队长度 | 吞吐量(veh/h) | 延迟(ms) | 油耗(L/100km) |",
        "|------|------------|------------|---------|-------------|---------|--------------|",
    ]
    for r in results:
        lines.append(
            f"| {r.algorithm} | {r.avg_travel_time_s:.1f} | {r.avg_waiting_time_s:.1f} | "
            f"{r.avg_queue_length_veh:.2f} | {r.throughput_veh_per_h:.1f} | "
            f"{r.avg_decision_latency_ms:.3f} | {r.fuel_intensity_L_per_100km:.2f} |"
        )
    return "\n".join(lines)
