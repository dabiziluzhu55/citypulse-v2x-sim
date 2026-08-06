"""Traffic-control metrics computed outside the simulator."""

from __future__ import annotations

import xml.etree.ElementTree as ET
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Dict, List, Mapping, Optional, Sequence

from .collector import EvalResult, FUEL_POWERTRAINS, _resolve_fuel_telemetry_unit


def _rounded(value: Optional[float], digits: int) -> Optional[float]:
    return None if value is None else round(value, digits)


@dataclass
class BenchmarkResult:
    """Metrics for one completed benchmark run."""

    algorithm: str = ""
    scenario: str = ""
    total_departed: int = 0
    total_arrived: int = 0
    total_planned: int = 0
    eval_duration_s: float = 0.0
    avg_travel_time_s: Optional[float] = None
    avg_waiting_time_s: Optional[float] = None
    avg_queue_length_veh: Optional[float] = None
    throughput_veh_per_h: Optional[float] = None
    avg_decision_latency_ms: Optional[float] = None
    decision_latency_p95_ms: Optional[float] = None
    fuel_intensity_L_per_100km: Optional[float] = None
    emergency_braking_exposure_per_1000: Optional[float] = None
    controlled_intersection_passages: int = 0
    emergency_braking_events: int = 0
    avg_queue_per_step: List[float] = field(default_factory=list)
    metric_sources: Dict[str, str] = field(default_factory=dict)
    warnings: List[str] = field(default_factory=list)

    def to_dict(self) -> Dict[str, Any]:
        return {
            "avg_travel_time_s": _rounded(self.avg_travel_time_s, 2),
            "avg_waiting_time_s": _rounded(self.avg_waiting_time_s, 2),
            "avg_queue_length_veh": _rounded(self.avg_queue_length_veh, 2),
            "throughput_veh_per_h": _rounded(self.throughput_veh_per_h, 1),
            "avg_decision_latency_ms": _rounded(
                self.avg_decision_latency_ms, 3
            ),
            "decision_latency_p95_ms": _rounded(
                self.decision_latency_p95_ms, 3
            ),
            "fuel_intensity_L_per_100km": _rounded(
                self.fuel_intensity_L_per_100km, 2
            ),
            "emergency_braking_exposure_per_1000": _rounded(
                self.emergency_braking_exposure_per_1000, 2
            ),
            "controlled_intersection_passages": (
                self.controlled_intersection_passages
            ),
            "emergency_braking_events": self.emergency_braking_events,
            "departed": self.total_departed,
            "arrived": self.total_arrived,
            "metric_sources": dict(self.metric_sources),
            "warnings": list(self.warnings),
        }


def compute_from_tripinfo(
    tripinfo_path: str,
    *,
    eval_duration_s: float = 0.0,
    queue_timeseries: Optional[Sequence[float]] = None,
    lane_count: int = 0,
    decision_latencies_ms: Optional[Sequence[float]] = None,
    emission_path: Optional[str] = None,
    vehicle_type_metadata: Optional[Mapping[str, Any]] = None,
    emission_step_scaled: bool = False,
    emission_step_length_s: Optional[float] = None,
    emission_fuel_unit: str = "auto",
    total_planned: int = 0,
    algorithm: str = "",
    scenario: str = "",
) -> BenchmarkResult:
    """Compute metrics from TripInfo plus optional live auxiliary sources.

    TripInfo provides travel/waiting times for all departed vehicles
    (completed plus truncated unfinished ones, avoiding survivor bias).  Queue length,
    algorithm latency and fuel intensity cannot be inferred from TripInfo alone;
    missing auxiliary inputs therefore produce ``None`` (rendered as ``N/A``).

    Standard SUMO emission output stores ``fuel`` in mg/s.  Set
    ``emission_step_scaled=True`` only when SUMO was run with
    ``--emission-output.step-scaled``.  ``vehicle_type_metadata`` must expose
    ``powertrain`` and ``fuel_density_mg_per_ml`` for every emitted type.
    ``emission_fuel_unit`` accepts ``auto``, ``mg_per_s`` or ``ml_per_s``;
    ``auto`` follows the locally installed SUMO version (1.14 changed units).
    ``lane_count`` remains accepted for backward compatibility; queue samples
    are expected to already be per-incoming-lane means.
    """

    del lane_count
    result = BenchmarkResult(algorithm=algorithm, scenario=scenario)
    root = ET.parse(tripinfo_path).getroot()
    all_tripinfos = list(root.findall("tripinfo"))
    completed = []
    total_waiting = 0.0
    total_travel = 0.0

    # 全口径：平均行程/平均等待统计全部已出发车辆（completed + 截断未完成，
    # 即 vaporized="end" / arrival<0 的残余车辆），避免只统计成功到达车辆
    # 造成的幸存者偏差（长时域下完成率常低于 10%）。completed 仅用于吞吐量。
    for trip in all_tripinfos:
        arrival = float(trip.get("arrival", -1))
        if arrival >= 0 and str(trip.get("vaporized", "false")).lower() != "true":
            completed.append(trip)
        total_travel += float(trip.get("duration", 0.0))
        total_waiting += float(trip.get("waitingTime", 0.0))

    arrived = len(completed)
    result.total_arrived = arrived
    result.total_departed = len(all_tripinfos)
    result.total_planned = total_planned if total_planned > 0 else len(all_tripinfos)
    result.eval_duration_s = float(eval_duration_s)

    departed_all = len(all_tripinfos)
    if departed_all:
        result.avg_travel_time_s = total_travel / departed_all
        result.avg_waiting_time_s = total_waiting / departed_all
        result.metric_sources["avg_travel_time_s"] = "tripinfo_all_departed"
        result.metric_sources["avg_waiting_time_s"] = "tripinfo_all_departed"
    else:
        result.warnings.append("TripInfo 中没有车辆记录。")

    if queue_timeseries is not None and len(queue_timeseries) > 0:
        result.avg_queue_per_step = [float(value) for value in queue_timeseries]
        result.avg_queue_length_veh = sum(result.avg_queue_per_step) / len(
            result.avg_queue_per_step
        )
        result.metric_sources[
            "avg_queue_length_veh"
        ] = "incoming_lane_queue_timeseries"
    else:
        result.warnings.append("缺少进口车道排队时序，平均排队长度不可用。")

    if eval_duration_s > 0:
        result.throughput_veh_per_h = arrived / eval_duration_s * 3600.0
        result.metric_sources["throughput_veh_per_h"] = "tripinfo_arrivals"
    elif arrived >= 2:
        span = max(float(trip.get("arrival", 0.0)) for trip in completed) - min(
            float(trip.get("depart", 0.0)) for trip in completed
        )
        if span > 0:
            result.throughput_veh_per_h = arrived / span * 3600.0
            result.metric_sources["throughput_veh_per_h"] = "tripinfo_time_span"
    if result.throughput_veh_per_h is None:
        result.warnings.append("缺少有效评估时长，吞吐量不可用。")

    if decision_latencies_ms is not None and len(decision_latencies_ms) > 0:
        samples = [float(value) for value in decision_latencies_ms]
        result.avg_decision_latency_ms = sum(samples) / len(samples)
        result.metric_sources[
            "avg_decision_latency_ms"
        ] = "algorithm_latency_samples"
    else:
        result.warnings.append("缺少算法决策耗时时序，平均决策耗时不可用。")

    if emission_path:
        if not Path(emission_path).exists():
            raise FileNotFoundError(emission_path)
        if not vehicle_type_metadata:
            result.warnings.append(
                "缺少车辆 powertrain/燃油密度元数据，燃油强度不可用。"
            )
        else:
            fuel_ml, distance_m = _parse_emission(
                emission_path,
                vehicle_type_metadata,
                step_scaled=emission_step_scaled,
                step_length_s=emission_step_length_s,
                fuel_unit=emission_fuel_unit,
            )
            if distance_m > 0:
                result.fuel_intensity_L_per_100km = (fuel_ml / 1000.0) / (
                    distance_m / 100000.0
                )
                result.metric_sources[
                    "fuel_intensity_L_per_100km"
                ] = "fuel_powertrain_emission_output"
            else:
                result.warnings.append("排放文件中没有燃油车辆行驶里程。")
    else:
        result.warnings.append("缺少排放或实时燃油数据，燃油强度不可用。")

    return result


def apply_tripinfo_completed_metrics(
    result: EvalResult, tripinfo_path: str
) -> EvalResult:
    """Replace sampled travel/waiting metrics with exact TripInfo values.

    口径（2026-08-06 更新）：平均行程/平均等待统计全部已出发车辆（completed
    + 截断未完成 vaporized="end" / arrival<0 的残余车辆），避免只统计成功
    到达车辆产生的幸存者偏差（长时域下完成率常低于 10%）。未完成车辆的
    duration 为评估截断时刻的已行驶时长（下限），waitingTime 为累计等待。
    函数名保留历史命名以兼容既有导入；arrived 一致性门禁保留，防止 TripInfo
    与实时统计不一致时发布假数据。
    """

    root = ET.parse(tripinfo_path).getroot()
    all_records = list(root.findall("tripinfo"))
    completed = [
        trip
        for trip in all_records
        if float(trip.get("arrival", -1)) >= 0
        and str(trip.get("vaporized", "false")).lower() != "true"
    ]
    if len(completed) != result.arrived:
        result.avg_travel_time_s = None
        result.avg_waiting_time_s = None
        result.metric_sources.pop("avg_travel_time_s", None)
        result.metric_sources.pop("avg_waiting_time_s", None)
        warning = (
            "TripInfo 已完成车辆数与 finish arrived_vehicles 不一致；"
            "平均行程时间和等待时间不可用。"
        )
        if warning not in result.warnings:
            result.warnings.append(warning)
        return result
    if not all_records:
        return result

    result.avg_travel_time_s = sum(
        float(trip.get("duration", 0.0)) for trip in all_records
    ) / len(all_records)
    result.avg_waiting_time_s = sum(
        float(trip.get("waitingTime", 0.0)) for trip in all_records
    ) / len(all_records)
    result.metric_sources["avg_travel_time_s"] = "tripinfo_all_departed"
    result.metric_sources["avg_waiting_time_s"] = "tripinfo_all_departed"
    result.warnings = [
        warning
        for warning in result.warnings
        if "等待 TripInfo 回填" not in warning
    ]
    return result


def _metadata_value(metadata: Any, name: str, default: Any = None) -> Any:
    if isinstance(metadata, Mapping):
        return metadata.get(name, default)
    return getattr(metadata, name, default)


def _parse_emission(
    emission_path: str,
    vehicle_type_metadata: Mapping[str, Any],
    *,
    step_scaled: bool = False,
    step_length_s: Optional[float] = None,
    fuel_unit: str = "auto",
) -> tuple[float, float]:
    """Return fuel mL and distance m for the same fuel-consuming vehicles."""

    timesteps = list(ET.parse(emission_path).getroot().findall("timestep"))
    if not timesteps:
        return 0.0, 0.0
    times = [float(timestep.get("time", 0.0)) for timestep in timesteps]
    resolved_fuel_unit = str(fuel_unit).strip().lower()
    if resolved_fuel_unit == "auto":
        telemetry_unit, _ = _resolve_fuel_telemetry_unit("auto")
        resolved_fuel_unit = (
            "ml_per_s"
            if telemetry_unit == "legacy_ml_as_mg"
            else "mg_per_s"
        )
    if resolved_fuel_unit not in {"mg_per_s", "ml_per_s"}:
        raise ValueError("emission_fuel_unit must be auto, mg_per_s or ml_per_s")

    inferred_step = float(step_length_s) if step_length_s is not None else None
    if inferred_step is not None and inferred_step <= 0:
        raise ValueError("emission_step_length_s must be positive")
    if inferred_step is None and len(times) >= 2:
        positive_deltas = [
            later - earlier
            for earlier, later in zip(times, times[1:])
            if later - earlier > 0
        ]
        if positive_deltas:
            inferred_step = positive_deltas[0]
    if inferred_step is None:
        raise ValueError(
            "Cannot infer emission step length from a single timestep; "
            "pass emission_step_length_s."
        )

    total_fuel_ml = 0.0
    total_distance_m = 0.0
    for index, timestep in enumerate(timesteps):
        if index + 1 < len(times):
            delta = times[index + 1] - times[index]
            if delta <= 0:
                raise ValueError("Emission timestep times must be increasing")
        else:
            delta = inferred_step

        for vehicle in timestep.findall("vehicle"):
            type_id = str(vehicle.get("type", ""))
            if type_id not in vehicle_type_metadata:
                raise ValueError(
                    f"Emission vehicle type {type_id!r} has no profile metadata"
                )
            metadata = vehicle_type_metadata[type_id]
            powertrain = str(_metadata_value(metadata, "powertrain", "")).lower()
            if powertrain not in FUEL_POWERTRAINS:
                continue
            fuel_value = max(0.0, float(vehicle.get("fuel", 0.0)))
            fuel_per_step = fuel_value if step_scaled else fuel_value * delta
            if resolved_fuel_unit == "ml_per_s":
                total_fuel_ml += fuel_per_step
            else:
                density = float(
                    _metadata_value(metadata, "fuel_density_mg_per_ml", 0.0)
                )
                if density <= 0:
                    raise ValueError(
                        f"Fuel vehicle type {type_id!r} has invalid fuel density"
                    )
                total_fuel_ml += fuel_per_step / density
            total_distance_m += max(
                0.0, float(vehicle.get("speed", 0.0))
            ) * delta

    return total_fuel_ml, total_distance_m


def _format_metric(value: Optional[float], width: int, precision: int) -> str:
    if value is None:
        return f"{'N/A':>{width}}"
    return f"{value:>{width}.{precision}f}"


def print_comparison_table(results: List[BenchmarkResult]) -> str:
    header = (
        f"{'算法':<20} {'行程(s)':>8} {'等待(s)':>8} {'排队':>6} "
        f"{'路网吞吐':>8} {'延迟(ms)':>8} {'油耗':>8} "
        f"{'紧急制动/千次':>12}"
    )
    lines = [header, "-" * len(header)]
    for result in results:
        lines.append(
            f"{result.algorithm:<20} "
            f"{_format_metric(result.avg_travel_time_s, 8, 1)} "
            f"{_format_metric(result.avg_waiting_time_s, 8, 1)} "
            f"{_format_metric(result.avg_queue_length_veh, 6, 2)} "
            f"{_format_metric(result.throughput_veh_per_h, 8, 1)} "
            f"{_format_metric(result.avg_decision_latency_ms, 8, 3)} "
            f"{_format_metric(result.fuel_intensity_L_per_100km, 8, 2)} "
            f"{_format_metric(result.emergency_braking_exposure_per_1000, 12, 2)}"
        )
    return "\n".join(lines)


def _markdown_metric(value: Optional[float], precision: int) -> str:
    return "N/A" if value is None else f"{value:.{precision}f}"


def print_markdown_table(results: List[BenchmarkResult]) -> str:
    lines = [
        "| 算法 | 行程时间(s) | 等待时间(s) | 排队长度 | 路网吞吐量(veh/h) | 延迟(ms) | 油耗(L/100km) | 紧急制动暴露率(次/千次通行) |",
        "|------|------------|------------|---------|-------------------|---------|--------------|------------------------------|",
    ]
    for result in results:
        lines.append(
            f"| {result.algorithm} | "
            f"{_markdown_metric(result.avg_travel_time_s, 1)} | "
            f"{_markdown_metric(result.avg_waiting_time_s, 1)} | "
            f"{_markdown_metric(result.avg_queue_length_veh, 2)} | "
            f"{_markdown_metric(result.throughput_veh_per_h, 1)} | "
            f"{_markdown_metric(result.avg_decision_latency_ms, 3)} | "
            f"{_markdown_metric(result.fuel_intensity_L_per_100km, 2)} | "
            f"{_markdown_metric(result.emergency_braking_exposure_per_1000, 2)} |"
        )
    return "\n".join(lines)
