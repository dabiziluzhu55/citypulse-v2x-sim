"""TripInfo解析与已出发车辆行程/等待/燃油强度指标回填"""

from __future__ import annotations

import logging
import time
import xml.etree.ElementTree as ET
from pathlib import Path
from typing import Mapping, Optional, Sequence

from .models import EvalResult
from .powertrain import VehicleTypeFuelMeta

logger = logging.getLogger(__name__)

TRIPINFO_READY_RETRIES = 8
TRIPINFO_READY_DELAY_S = 0.25
FUEL_POWERTRAINS = frozenset({"gasoline", "diesel", "hybrid"})
FUEL_SOURCE = "tripinfo_completed_fuel_vehicles"
TRAVEL_WAIT_SOURCE = "tripinfo_departed"


def parse_completed_tripinfo(
    tripinfo_path: str | Path,
) -> tuple[list[ET.Element], Optional[str]]:
    """返回已完成且未vaporize的tripinfo节点；失败时返回 ([], warning)"""

    path = Path(tripinfo_path)
    try:
        root = ET.parse(path).getroot()
    except (OSError, ET.ParseError) as exc:
        return [], f"TripInfo 解析失败: {exc}"

    completed: list[ET.Element] = []
    for trip in root.findall("tripinfo"):
        arrival = float(trip.get("arrival", -1))
        if arrival < 0 or str(trip.get("vaporized", "false")).lower() == "true":
            continue
        completed.append(trip)
    return completed, None


def parse_departed_tripinfo(
    tripinfo_path: str | Path,
    *,
    include_vtypes: Optional[Sequence[str]] = None,
) -> tuple[list[ET.Element], Optional[str]]:
    """返回已出发车辆的tripinfo节点（含未完成 / vaporize）

    与仿真终态 departed 对齐：若提供 include_vtypes，仅保留这些 vType；
    否则排除 citypulse_* 扰动车（与 load_tripinfo_totals 跳过未知类型的意图一致）。
    """

    path = Path(tripinfo_path)
    try:
        root = ET.parse(path).getroot()
    except (OSError, ET.ParseError) as exc:
        return [], f"TripInfo 解析失败: {exc}"

    allow = (
        {str(v) for v in include_vtypes} if include_vtypes is not None else None
    )
    departed: list[ET.Element] = []
    for trip in root.findall("tripinfo"):
        type_id = str(trip.get("vType", "") or "")
        if allow is not None:
            if type_id not in allow:
                continue
        elif type_id.startswith("citypulse_"):
            continue
        departed.append(trip)
    return departed, None


def wait_for_readable_tripinfo(
    tripinfo_path: str | Path,
    *,
    retries: int = TRIPINFO_READY_RETRIES,
    delay_s: float = TRIPINFO_READY_DELAY_S,
) -> tuple[Optional[Path], Optional[str]]:
    """终态后等待TripInfo写完并可解析，避免读到未关闭文件"""

    path = Path(tripinfo_path)
    last_error: Optional[str] = None
    for attempt in range(max(1, retries)):
        if not path.is_file():
            last_error = f"TripInfo 文件不存在: {path}"
        else:
            try:
                ET.parse(path)
                return path, None
            except ET.ParseError as exc:
                last_error = f"TripInfo 尚未可读或解析失败: {exc}"
            except OSError as exc:
                last_error = f"TripInfo 读取失败: {exc}"
        if attempt + 1 < retries:
            time.sleep(delay_s)
    return None, last_error or f"TripInfo 文件不可用: {path}"


def apply_tripinfo_completed_metrics(
    result: EvalResult,
    tripinfo_path: str | Path,
    *,
    expected_departed: Optional[int] = None,
    expected_arrived: Optional[int] = None,
    include_vtypes: Optional[Sequence[str]] = None,
    retries: int = TRIPINFO_READY_RETRIES,
    delay_s: float = TRIPINFO_READY_DELAY_S,
) -> EvalResult:
    """用TripInfo覆盖平均行程时间与平均等待时间

    口径：全部已出发车辆（含未到达、vaporize）的 duration / waitingTime 总和
    除以出发车辆数。不再使用“仅已完成车辆”均值。

    文件缺失、解析失败或 TripInfo 出发记录数与 departed 不一致时，对应指标置为None
    并记录warning，不抛出异常。

    ``expected_arrived`` 已废弃，仅兼容旧调用；分母以 ``expected_departed`` /
    ``result.departed`` 为准。
    """

    del expected_arrived  # 兼容旧参数，不再参与校验

    departed_expected = (
        result.departed if expected_departed is None else int(expected_departed)
    )
    path, wait_warning = wait_for_readable_tripinfo(
        tripinfo_path,
        retries=retries,
        delay_s=delay_s,
    )
    if path is None:
        _clear_travel_wait_metric(result)
        _append_warning(result, wait_warning or "TripInfo 不可用")
        return result

    departed_trips, parse_warning = parse_departed_tripinfo(
        path, include_vtypes=include_vtypes
    )
    if parse_warning is not None:
        _clear_travel_wait_metric(result)
        _append_warning(result, parse_warning)
        return result

    if departed_expected <= 0:
        _clear_travel_wait_metric(result)
        _append_warning(result, "出发车辆数为0，平均行程时间和等待时间不可用")
        return result

    if len(departed_trips) != departed_expected:
        _clear_travel_wait_metric(result)
        _append_warning(
            result,
            "TripInfo 已出发车辆数与 finish departed_vehicles 不一致；"
            "平均行程时间和等待时间不可用",
        )
        return result

    if not departed_trips:
        _clear_travel_wait_metric(result)
        _append_warning(result, "TripInfo中没有已出发车辆")
        return result

    travel_total = sum(float(trip.get("duration", 0.0)) for trip in departed_trips)
    waiting_total = sum(
        float(trip.get("waitingTime", 0.0)) for trip in departed_trips
    )
    denom = float(departed_expected)
    result.avg_travel_time_s = travel_total / denom
    result.avg_waiting_time_s = waiting_total / denom
    result.metric_sources["avg_travel_time_s"] = TRAVEL_WAIT_SOURCE
    result.metric_sources["avg_waiting_time_s"] = TRAVEL_WAIT_SOURCE
    _clear_pending_tripinfo_warnings(result)
    return result


def apply_tripinfo_fuel_intensity(
    result: EvalResult,
    tripinfo_path: str | Path,
    fuel_meta_by_type: Mapping[str, VehicleTypeFuelMeta],
    *,
    retries: int = TRIPINFO_READY_RETRIES,
    delay_s: float = TRIPINFO_READY_DELAY_S,
) -> EvalResult:
    """终态百公里油耗：直接解析TripInfo emissions.fuel_abs（mg）与routeLength

    仅统计已完成、未vaporized、且powertrain为gasoline/diesel/hybrid的同一批车辆
    分子分母使用完全相同车辆集合：sum(fuel_ml)/1000 / sum(distance_m)/100000
    """

    _clear_fuel_metric(result)

    if not fuel_meta_by_type:
        _append_warning(result, "缺少车辆powertrain/fuel_density元数据，燃油强度不可计算")
        return result

    path, wait_warning = wait_for_readable_tripinfo(
        tripinfo_path,
        retries=retries,
        delay_s=delay_s,
    )
    if path is None:
        _append_warning(result, wait_warning or "TripInfo不可用，燃油强度不可计算")
        return result

    completed, parse_warning = parse_completed_tripinfo(path)
    if parse_warning is not None:
        _append_warning(result, f"{parse_warning}；燃油强度不可计算")
        return result

    total_fuel_ml = 0.0
    total_distance_m = 0.0
    fuel_vehicle_count = 0

    for trip in completed:
        type_id = str(trip.get("vType", "") or "")
        if not type_id:
            _append_warning(
                result,
                "TripInfo 存在空车辆类型，燃油强度不可计算",
            )
            return result
        meta = fuel_meta_by_type.get(type_id)
        if meta is None:
            # 扰动/活动车使用 citypulse_* 前缀，不计入正式燃油强度
            if type_id.startswith("citypulse_"):
                continue
            _append_warning(
                result,
                f"TripInfo 车辆类型 {type_id!r} 未知，燃油强度不可计算",
            )
            return result
        if meta.powertrain not in FUEL_POWERTRAINS:
            continue

        emissions = trip.find("emissions")
        if emissions is None:
            _append_warning(
                result,
                f"TripInfo 车辆 {trip.get('id')!r} 缺少emissions，燃油强度不可计算",
            )
            return result
        try:
            fuel_abs_mg = float(emissions.get("fuel_abs", "nan"))
        except (TypeError, ValueError):
            _append_warning(
                result,
                f"TripInfo 车辆 {trip.get('id')!r} 的fuel_abs非法，燃油强度不可计算",
            )
            return result
        if fuel_abs_mg < 0 or fuel_abs_mg != fuel_abs_mg:  # NaN check
            _append_warning(
                result,
                f"TripInfo 车辆 {trip.get('id')!r} 的fuel_abs非法，燃油强度不可计算",
            )
            return result

        density = float(meta.fuel_density_mg_per_ml)
        if density <= 0 or density != density:
            _append_warning(
                result,
                f"车辆类型 {type_id!r} 的fuel_density_mg_per_ml非法，燃油强度不可计算",
            )
            return result

        try:
            distance_m = float(trip.get("routeLength", "nan"))
        except (TypeError, ValueError):
            _append_warning(
                result,
                f"TripInfo 车辆 {trip.get('id')!r} 的routeLength非法，燃油强度不可计算",
            )
            return result
        if distance_m < 0 or distance_m != distance_m:
            _append_warning(
                result,
                f"TripInfo 车辆 {trip.get('id')!r} 的routeLength非法，燃油强度不可计算",
            )
            return result

        total_fuel_ml += fuel_abs_mg / density
        total_distance_m += distance_m
        fuel_vehicle_count += 1

    if fuel_vehicle_count == 0:
        _append_warning(result, "TripInfo中没有可用的燃油车辆，燃油强度不可计算")
        return result
    if total_distance_m <= 0:
        _append_warning(result, "燃油车辆总里程为0，燃油强度不可计算")
        return result

    result.fuel_intensity_L_per_100km = (total_fuel_ml / 1000.0) / (
        total_distance_m / 100000.0
    )
    result.metric_sources["fuel_intensity_L_per_100km"] = FUEL_SOURCE
    _clear_pending_fuel_warnings(result)
    return result


def _clear_travel_wait_metric(result: EvalResult) -> None:
    result.avg_travel_time_s = None
    result.avg_waiting_time_s = None
    result.metric_sources.pop("avg_travel_time_s", None)
    result.metric_sources.pop("avg_waiting_time_s", None)


def _clear_fuel_metric(result: EvalResult) -> None:
    result.fuel_intensity_L_per_100km = None
    result.metric_sources.pop("fuel_intensity_L_per_100km", None)


def _clear_pending_tripinfo_warnings(result: EvalResult) -> None:
    result.warnings = [
        warning
        for warning in result.warnings
        if "等待TripInfo回填" not in warning.replace(" ", "")
        and "快照临时" not in warning
    ]


def _clear_pending_fuel_warnings(result: EvalResult) -> None:
    result.warnings = [
        warning
        for warning in result.warnings
        if "燃油强度" not in warning and "fuel" not in warning.lower()
    ]


def _append_warning(result: EvalResult, message: str) -> None:
    _clear_pending_tripinfo_warnings(result)
    if message and message not in result.warnings:
        result.warnings.append(message)
    logger.warning("%s", message)
