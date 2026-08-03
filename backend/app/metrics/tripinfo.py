"""TripInfo 解析与已完成车辆行程/等待指标回填。"""

from __future__ import annotations

import logging
import time
import xml.etree.ElementTree as ET
from pathlib import Path
from typing import Optional

from .models import EvalResult

logger = logging.getLogger(__name__)

TRIPINFO_READY_RETRIES = 8
TRIPINFO_READY_DELAY_S = 0.25


def parse_completed_tripinfo(
    tripinfo_path: str | Path,
) -> tuple[list[ET.Element], Optional[str]]:
    """返回已完成且未 vaporize 的 tripinfo 节点；失败时返回 ([], warning)。"""

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


def wait_for_readable_tripinfo(
    tripinfo_path: str | Path,
    *,
    retries: int = TRIPINFO_READY_RETRIES,
    delay_s: float = TRIPINFO_READY_DELAY_S,
) -> tuple[Optional[Path], Optional[str]]:
    """终态后等待 TripInfo 写完并可解析，避免读到未关闭文件。"""

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
    expected_arrived: Optional[int] = None,
    retries: int = TRIPINFO_READY_RETRIES,
    delay_s: float = TRIPINFO_READY_DELAY_S,
) -> EvalResult:
    """用 TripInfo 覆盖平均行程时间与平均等待时间。

    文件缺失、解析失败或已完成车辆数与 arrived 不一致时，对应指标置为 None
    并记录 warning，不抛出异常。
    """

    arrived_expected = (
        result.arrived if expected_arrived is None else int(expected_arrived)
    )
    path, wait_warning = wait_for_readable_tripinfo(
        tripinfo_path,
        retries=retries,
        delay_s=delay_s,
    )
    if path is None:
        result.avg_travel_time_s = None
        result.avg_waiting_time_s = None
        result.metric_sources.pop("avg_travel_time_s", None)
        result.metric_sources.pop("avg_waiting_time_s", None)
        _append_warning(result, wait_warning or "TripInfo 不可用。")
        return result

    completed, parse_warning = parse_completed_tripinfo(path)
    if parse_warning is not None:
        result.avg_travel_time_s = None
        result.avg_waiting_time_s = None
        result.metric_sources.pop("avg_travel_time_s", None)
        result.metric_sources.pop("avg_waiting_time_s", None)
        _append_warning(result, parse_warning)
        return result

    if len(completed) != arrived_expected:
        result.avg_travel_time_s = None
        result.avg_waiting_time_s = None
        result.metric_sources.pop("avg_travel_time_s", None)
        result.metric_sources.pop("avg_waiting_time_s", None)
        _append_warning(
            result,
            "TripInfo 已完成车辆数与 finish arrived_vehicles 不一致；"
            "平均行程时间和等待时间不可用。",
        )
        return result

    if not completed:
        result.avg_travel_time_s = None
        result.avg_waiting_time_s = None
        result.metric_sources.pop("avg_travel_time_s", None)
        result.metric_sources.pop("avg_waiting_time_s", None)
        _append_warning(result, "TripInfo 中没有已完成且未 vaporize 的车辆。")
        return result

    result.avg_travel_time_s = sum(
        float(trip.get("duration", 0.0)) for trip in completed
    ) / len(completed)
    result.avg_waiting_time_s = sum(
        float(trip.get("waitingTime", 0.0)) for trip in completed
    ) / len(completed)
    result.metric_sources["avg_travel_time_s"] = "tripinfo_completed"
    result.metric_sources["avg_waiting_time_s"] = "tripinfo_completed"
    _clear_pending_tripinfo_warnings(result)
    return result


def _clear_pending_tripinfo_warnings(result: EvalResult) -> None:
    result.warnings = [
        warning
        for warning in result.warnings
        if "等待 TripInfo 回填" not in warning and "快照临时" not in warning
    ]


def _append_warning(result: EvalResult, message: str) -> None:
    _clear_pending_tripinfo_warnings(result)
    if message and message not in result.warnings:
        result.warnings.append(message)
    logger.warning("%s", message)
