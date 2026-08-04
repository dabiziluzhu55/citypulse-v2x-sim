"""Canonical TripInfo parsing shared by every algorithm evaluator.

统一口径（所有算法的共用准入基础，详见 docs/algorithm.md 与各算法说明文档）：

- ``completed_trips``：TripInfo 记录中 ``arrival >= 0`` 且未 vaporize 的车辆（成功到达终点）。
- ``unfinished_trips``（TripInfo 残余车辆）：其余所有记录（``arrival < 0``，含窗口截断时
  SUMO 以 ``vaporized="end"`` 写出的未完成车辆）。这是"残余车辆"的权威计数，并与端末
  快照 ``remaining_vehicles``（``getMinExpectedNumber``）交叉校验（``residual_mismatch``）。
- ``end_waiting_total_s``（与 ``all_waiting_total_s`` 同值）：全部已出发车辆（完成 + 未完成）
  的累计 ``waitingTime``。这是统一的 end waiting 统计口径（累计型，不受端末瞬时波动影响，
  也不会因为只看 completed-trip 而产生幸存者偏差）。
- ``end_waiting_mean_s``：``end_waiting_total_s / trip_records``，按已出发车辆平均，
  使不同出发量的运行之间可比。
- 端末快照瞬时 ``waiting``（``sum(lane.getWaitingTime)``）仅作诊断，不作为 end waiting 主口径。
- 空数据时均值/完成率取 ``0.0``（与历史 coslight/ippo 行为一致）。
"""

from __future__ import annotations

import xml.etree.ElementTree as ET
from pathlib import Path
from typing import Any, Dict, Sequence

import numpy as np


def _mean(values: Sequence[float]) -> float:
    return float(np.mean(values)) if values else 0.0


def parse_tripinfo_diagnostics(path: str | Path) -> Dict[str, Any]:
    """解析 tripinfo.xml 为标准化的端末诊断字典（见模块 docstring）。"""
    completed: list[dict[str, float]] = []
    unfinished: list[dict[str, float]] = []
    for _, element in ET.iterparse(str(path), events=("end",)):
        if element.tag != "tripinfo":
            continue
        arrival = float(element.get("arrival", "-1"))
        vaporized = str(element.get("vaporized", "false")).lower() == "true"
        if arrival >= 0.0 and not vaporized:
            target = completed
        else:
            target = unfinished
        target.append(
            {
                "duration": float(element.get("duration", "0")),
                "waiting": float(element.get("waitingTime", "0")),
                "time_loss": float(element.get("timeLoss", "0")),
            }
        )
        element.clear()

    completed_durations = [item["duration"] for item in completed]
    completed_waiting = [item["waiting"] for item in completed]
    completed_loss = [item["time_loss"] for item in completed]
    all_records = completed + unfinished
    total = len(all_records)
    end_waiting_total = float(sum(item["waiting"] for item in all_records))
    return {
        "trip_records": total,
        "completed_trips": len(completed),
        "unfinished_trips": len(unfinished),
        "completion_rate": len(completed) / total if total else 0.0,
        "completed_duration_mean_s": _mean(completed_durations),
        "completed_duration_median_s": (
            float(np.median(completed_durations)) if completed_durations else 0.0
        ),
        "completed_duration_p95_s": (
            float(np.percentile(completed_durations, 95))
            if completed_durations
            else 0.0
        ),
        "completed_waiting_mean_s": _mean(completed_waiting),
        "completed_time_loss_mean_s": _mean(completed_loss),
        "unfinished_waiting_total_s": float(
            sum(item["waiting"] for item in unfinished)
        ),
        "unfinished_time_loss_total_s": float(
            sum(item["time_loss"] for item in unfinished)
        ),
        "all_waiting_total_s": end_waiting_total,
        "end_waiting_total_s": end_waiting_total,
        "end_waiting_mean_s": end_waiting_total / total if total else 0.0,
        "all_time_loss_total_s": float(
            sum(item["time_loss"] for item in all_records)
        ),
    }


def residual_mismatch(unfinished_trips: int, snapshot_remaining: int) -> int:
    """TripInfo 残余车辆与端末快照 remaining 的差（0 = 一致）。

    非零时说明 TripInfo 与 SUMO 端末快照对"残余车辆"的计数不一致（例如需求 horizon
    超出评估窗口、或存在非 end 原因 vaporize 的车辆），调用方应在报告中告警。
    """
    return int(unfinished_trips) - int(snapshot_remaining)
