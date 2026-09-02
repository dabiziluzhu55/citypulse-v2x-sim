"""TripInfo解析与已出发车辆行程/等待/燃油强度/路径类标准指标回填"""

from __future__ import annotations

import logging
import math
import time
import xml.etree.ElementTree as ET
from pathlib import Path
from typing import Mapping, Optional, Sequence

from .models import EvalResult
from .powertrain import VehicleTypeFuelMeta
from .tpi import TPI_SOURCE, tpi_from_optional_dtp

logger = logging.getLogger(__name__)

TRIPINFO_READY_RETRIES = 8
TRIPINFO_READY_DELAY_S = 0.25
FUEL_POWERTRAINS = frozenset({"gasoline", "diesel", "hybrid"})
FUEL_SOURCE = "tripinfo_completed_fuel_vehicles"
TRAVEL_WAIT_SOURCE = "tripinfo_departed"
PATH_AVG_SPEED_SOURCE = "tripinfo_routeLength_over_duration"
TTI_SOURCE = "tripinfo_duration_over_equivalent_free_flow_time"
DTP_SOURCE = "tripinfo_timeLoss_over_duration"
STOPS_SOURCE = "tripinfo_waitingCount"


def _parse_tripinfo_root(
    tripinfo_path: str | Path,
) -> tuple[Optional[ET.Element], Optional[str]]:
    path = Path(tripinfo_path)
    try:
        return ET.parse(path).getroot(), None
    except (OSError, ET.ParseError) as exc:
        return None, f"TripInfo 解析失败: {exc}"


def _completed_from_root(root: ET.Element) -> list[ET.Element]:
    completed: list[ET.Element] = []
    for trip in root.findall("tripinfo"):
        try:
            arrival = float(trip.get("arrival", -1))
        except (TypeError, ValueError):
            continue
        if arrival < 0 or str(trip.get("vaporized", "false")).lower() == "true":
            continue
        completed.append(trip)
    return completed


def _departed_from_root(
    root: ET.Element,
    *,
    include_vtypes: Optional[Sequence[str]] = None,
) -> list[ET.Element]:
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
    return departed


def parse_completed_tripinfo(
    tripinfo_path: str | Path,
) -> tuple[list[ET.Element], Optional[str]]:
    """返回已完成且未vaporize的tripinfo节点；失败时返回 ([], warning)"""

    root, warning = _parse_tripinfo_root(tripinfo_path)
    if root is None:
        return [], warning
    return _completed_from_root(root), None


def parse_departed_tripinfo(
    tripinfo_path: str | Path,
    *,
    include_vtypes: Optional[Sequence[str]] = None,
) -> tuple[list[ET.Element], Optional[str]]:
    """返回已出发车辆的tripinfo节点（含未完成 / vaporize）

    与仿真终态 departed 对齐：若提供 include_vtypes，仅保留这些 vType；
    否则排除 citypulse_* 扰动车（与 load_tripinfo_totals 跳过未知类型的意图一致）。
    """

    root, warning = _parse_tripinfo_root(tripinfo_path)
    if root is None:
        return [], warning
    return _departed_from_root(root, include_vtypes=include_vtypes), None


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


def _trip_id(trip: ET.Element) -> str:
    return str(trip.get("id") or "")


def _parse_required_number(
    trip: ET.Element,
    attr: str,
    *,
    minimum: float | None = None,
    exclusive_min: float | None = None,
) -> tuple[Optional[float], Optional[str]]:
    """显式校验 TripInfo 数值；缺失/NaN/Inf/越界返回 (None, warning)"""

    raw = trip.get(attr)
    vid = _trip_id(trip) or "?"
    if raw is None or str(raw) == "":
        return None, f"TripInfo 车辆 {vid!r} 缺少 {attr}"
    try:
        value = float(raw)
    except (TypeError, ValueError):
        return None, f"TripInfo 车辆 {vid!r} 的 {attr} 非法"
    if not math.isfinite(value):
        return None, f"TripInfo 车辆 {vid!r} 的 {attr} 非有限值"
    if minimum is not None and value < minimum:
        return None, f"TripInfo 车辆 {vid!r} 的 {attr} 越界"
    if exclusive_min is not None and value <= exclusive_min:
        return None, f"TripInfo 车辆 {vid!r} 的 {attr} 越界"
    return value, None


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
    """用TripInfo覆盖平均行程时间与平均等待时间"""

    del expected_arrived  

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

    _fill_travel_wait_from_departed(
        result,
        departed_trips,
        departed_expected=departed_expected,
    )
    return result


def apply_tripinfo_path_metrics(
    result: EvalResult,
    completed_trips: Sequence[ET.Element],
) -> EvalResult:
    """完整路径类标准指标：仅completed、non-vaporized车辆

    仿真环境中采用SUMO timeLoss对应的无延误等效行程时间作为自由流参考时间，
    构造仿真等效TTI
    """

    _clear_path_metrics(result)
    if not completed_trips:
        _append_warning(result, "TripInfo 中没有已完成车辆，路径类标准指标不可计算")
        return result

    speed_distance = 0.0
    speed_duration = 0.0
    speed_n = 0
    delay_loss = 0.0
    delay_duration = 0.0
    delay_n = 0
    tti_duration = 0.0
    tti_free = 0.0
    tti_n = 0
    stops_total = 0.0
    stops_n = 0

    for trip in completed_trips:
        duration, duration_warning = _parse_required_number(
            trip, "duration", exclusive_min=0.0
        )
        route_length, route_warning = _parse_required_number(
            trip, "routeLength", minimum=0.0
        )
        time_loss, time_loss_warning = _parse_required_number(
            trip, "timeLoss", minimum=0.0
        )
        waiting_count, waiting_count_warning = _parse_required_number(
            trip, "waitingCount", minimum=0.0
        )

        if duration is None:
            _append_warning(result, duration_warning or "duration 非法")
        elif route_length is None:
            _append_warning(result, route_warning or "routeLength 非法")
        else:
            speed_distance += route_length
            speed_duration += duration
            speed_n += 1

        if duration is None:
            pass
        elif time_loss is None:
            _append_warning(result, time_loss_warning or "timeLoss 非法")
        elif time_loss > duration:
            _append_warning(
                result,
                f"TripInfo 车辆 {_trip_id(trip) or '?'!r} 的 timeLoss 大于 duration，"
                "已跳过 TTI/DTP 样本",
            )
        else:
            free_flow = duration - time_loss
            delay_loss += time_loss
            delay_duration += duration
            delay_n += 1
            if free_flow <= 0.0:
                _append_warning(
                    result,
                    f"TripInfo 车辆 {_trip_id(trip) or '?'!r} 的等效自由流时间"
                    "（duration-timeLoss）非正，已跳过 TTI 样本",
                )
            else:
                tti_duration += duration
                tti_free += free_flow
                tti_n += 1

        if waiting_count is None:
            _append_warning(
                result, waiting_count_warning or "waitingCount 非法"
            )
        else:
            stops_total += waiting_count
            stops_n += 1

    if speed_n <= 0 or speed_duration <= 0:
        _append_warning(result, "路径平均速度有效样本不足，指标不可计算")
    else:
        result.path_avg_speed_kmh = (speed_distance / speed_duration) * 3.6
        result.metric_sources["path_avg_speed_kmh"] = PATH_AVG_SPEED_SOURCE

    if delay_n <= 0 or delay_duration <= 0:
        _append_warning(result, "延误时间比有效样本不足，指标不可计算")
    else:
        result.delay_time_proportion = delay_loss / delay_duration
        result.metric_sources["delay_time_proportion"] = DTP_SOURCE

    if tti_n <= 0 or tti_free <= 0:
        _append_warning(result, "行程时间比有效样本不足，指标不可计算")
    else:
        result.travel_time_index = tti_duration / tti_free
        result.metric_sources["travel_time_index"] = TTI_SOURCE

    if stops_n <= 0:
        _append_warning(result, "路径平均停车次数有效样本不足，指标不可计算")
    else:
        result.avg_stops_per_vehicle = stops_total / float(stops_n)
        result.metric_sources["avg_stops_per_vehicle"] = STOPS_SOURCE

    tpi, state, method = tpi_from_optional_dtp(result.delay_time_proportion)
    result.traffic_performance_index = tpi
    result.traffic_state = state
    result.tpi_method = method
    if tpi is not None:
        result.metric_sources["traffic_performance_index"] = TPI_SOURCE
    return result


def apply_tripinfo_official_metrics(
    result: EvalResult,
    tripinfo_path: str | Path,
    fuel_meta_by_type: Mapping[str, VehicleTypeFuelMeta],
    *,
    expected_departed: Optional[int] = None,
    include_vtypes: Optional[Sequence[str]] = None,
    retries: int = TRIPINFO_READY_RETRIES,
    delay_s: float = TRIPINFO_READY_DELAY_S,
) -> EvalResult:
    """一次解析 TripInfo，同时回填行程/等待、路径类标准指标和燃油强度"""

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
        _clear_path_metrics(result)
        _clear_fuel_metric(result)
        _append_warning(result, wait_warning or "TripInfo 不可用")
        return result

    root, parse_warning = _parse_tripinfo_root(path)
    if root is None:
        _clear_travel_wait_metric(result)
        _clear_path_metrics(result)
        _clear_fuel_metric(result)
        _append_warning(result, parse_warning or "TripInfo 解析失败")
        return result

    departed_trips = _departed_from_root(root, include_vtypes=include_vtypes)
    completed_trips = _completed_from_root(root)
    _fill_travel_wait_from_departed(
        result,
        departed_trips,
        departed_expected=departed_expected,
    )
    apply_tripinfo_path_metrics(result, completed_trips)
    _fill_fuel_from_completed(result, completed_trips, fuel_meta_by_type)
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

    _fill_fuel_from_completed(result, completed, fuel_meta_by_type)
    return result


def _fill_travel_wait_from_departed(
    result: EvalResult,
    departed_trips: Sequence[ET.Element],
    *,
    departed_expected: int,
) -> None:
    if departed_expected <= 0:
        _clear_travel_wait_metric(result)
        _append_warning(result, "出发车辆数为0，平均行程时间和等待时间不可用")
        return

    if len(departed_trips) != departed_expected:
        _clear_travel_wait_metric(result)
        _append_warning(
            result,
            "TripInfo 已出发车辆数与 finish departed_vehicles 不一致；"
            "平均行程时间和等待时间不可用",
        )
        return

    if not departed_trips:
        _clear_travel_wait_metric(result)
        _append_warning(result, "TripInfo中没有已出发车辆")
        return

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


def _fill_fuel_from_completed(
    result: EvalResult,
    completed: Sequence[ET.Element],
    fuel_meta_by_type: Mapping[str, VehicleTypeFuelMeta],
) -> None:
    _clear_fuel_metric(result)

    if not fuel_meta_by_type:
        _append_warning(result, "缺少车辆powertrain/fuel_density元数据，燃油强度不可计算")
        return

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
            return
        meta = fuel_meta_by_type.get(type_id)
        if meta is None:
            # 扰动/活动车使用 citypulse_* 前缀，不计入正式燃油强度
            if type_id.startswith("citypulse_"):
                continue
            _append_warning(
                result,
                f"TripInfo 车辆类型 {type_id!r} 未知，燃油强度不可计算",
            )
            return
        if meta.powertrain not in FUEL_POWERTRAINS:
            continue

        emissions = trip.find("emissions")
        if emissions is None:
            _append_warning(
                result,
                f"TripInfo 车辆 {trip.get('id')!r} 缺少emissions，燃油强度不可计算",
            )
            return
        try:
            fuel_abs_mg = float(emissions.get("fuel_abs", "nan"))
        except (TypeError, ValueError):
            _append_warning(
                result,
                f"TripInfo 车辆 {trip.get('id')!r} 的fuel_abs非法，燃油强度不可计算",
            )
            return
        if fuel_abs_mg < 0 or fuel_abs_mg != fuel_abs_mg:  # NaN check
            _append_warning(
                result,
                f"TripInfo 车辆 {trip.get('id')!r} 的fuel_abs非法，燃油强度不可计算",
            )
            return

        density = float(meta.fuel_density_mg_per_ml)
        if density <= 0 or density != density:
            _append_warning(
                result,
                f"车辆类型 {type_id!r} 的fuel_density_mg_per_ml非法，燃油强度不可计算",
            )
            return

        try:
            distance_m = float(trip.get("routeLength", "nan"))
        except (TypeError, ValueError):
            _append_warning(
                result,
                f"TripInfo 车辆 {trip.get('id')!r} 的routeLength非法，燃油强度不可计算",
            )
            return
        if distance_m < 0 or distance_m != distance_m:
            _append_warning(
                result,
                f"TripInfo 车辆 {trip.get('id')!r} 的routeLength非法，燃油强度不可计算",
            )
            return

        total_fuel_ml += fuel_abs_mg / density
        total_distance_m += distance_m
        fuel_vehicle_count += 1

    if fuel_vehicle_count == 0:
        _append_warning(result, "TripInfo中没有可用的燃油车辆，燃油强度不可计算")
        return
    if total_distance_m <= 0:
        _append_warning(result, "燃油车辆总里程为0，燃油强度不可计算")
        return

    result.fuel_intensity_L_per_100km = (total_fuel_ml / 1000.0) / (
        total_distance_m / 100000.0
    )
    result.metric_sources["fuel_intensity_L_per_100km"] = FUEL_SOURCE
    _clear_pending_fuel_warnings(result)


def _clear_travel_wait_metric(result: EvalResult) -> None:
    result.avg_travel_time_s = None
    result.avg_waiting_time_s = None
    result.metric_sources.pop("avg_travel_time_s", None)
    result.metric_sources.pop("avg_waiting_time_s", None)


def _clear_path_metrics(result: EvalResult) -> None:
    result.path_avg_speed_kmh = None
    result.travel_time_index = None
    result.delay_time_proportion = None
    result.traffic_performance_index = None
    result.traffic_state = None
    result.tpi_method = None
    result.avg_stops_per_vehicle = None
    for key in (
        "path_avg_speed_kmh",
        "travel_time_index",
        "delay_time_proportion",
        "traffic_performance_index",
        "avg_stops_per_vehicle",
    ):
        result.metric_sources.pop(key, None)


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
