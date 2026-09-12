#!/usr/bin/env python3
"""离线回填第四章已有实验结果的 TripInfo 正式指标。

只读取 JSON/XML 并调用 traffic_eval 现有实现。
不 import SimulationManager，不启动 SUMO / libsumo，不重跑任何实验。
"""

from __future__ import annotations

import argparse
import csv
import json
import math
import xml.etree.ElementTree as ET
from copy import deepcopy
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

_PROJECT_ROOT = Path(__file__).resolve().parents[2]

from traffic_eval.models import EvalResult
from traffic_eval.powertrain import load_fuel_meta_by_type
from traffic_eval.tripinfo import apply_tripinfo_official_metrics

DEFAULT_SESSION_ROOT = _PROJECT_ROOT / "outputs" / "sessions"
DEFAULT_TRAFFIC_MANIFEST = (
    _PROJECT_ROOT / "data" / "maps" / "sumo" / "generated" / "manifests" / "traffic_manifest.json"
)

GROUP_LABELS = {
    "B1": "校园周边早高峰无扰动",
    "B2": "校园周边早高峰大型活动",
    "C1": "窄路密网早高峰无扰动",
    "C2": "窄路密网早高峰车道关闭",
    "C3": "窄路密网早高峰临时限速",
    "C4": "窄路密网早高峰交通事故",
}
GROUP_ORDER = ("B1", "B2", "C1", "C2", "C3", "C4")
ALGORITHM_ORDER = (
    "fixed",
    "max_pressure",
    "sotl",
    "ippo",
    "mappo",
    "cov2x",
)

SESSION_FILES = (
    "session_manifest.json",
    "session.rou.xml",
    "session.sumocfg",
    "session.add.xml",
    "tripinfo.xml",
)

SCENE_AFFECTED_UNRESTORABLE_REASON = (
    "session 目录只持久化了 evaluation_scope / 路网文件 / tripinfo.xml，"
    "没有保存 ever_entered vehicle_ids、snapshot 历史或车辆 membership。"
    "不能用 route 起终点或路段前缀猜测局部车辆集合，"
    "因此 scene_affected_trip_metrics 无法离线精确恢复。"
)

MISSING = "—"


def atomic_write_json(path: Path, payload: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    tmp.replace(path)


def write_csv(path: Path, rows: list[dict[str, Any]], fieldnames: list[str]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=fieldnames, extrasaction="ignore")
        writer.writeheader()
        for row in rows:
            writer.writerow(row)


def _as_float(value: Any) -> float | None:
    if value is None or value == "" or value == MISSING:
        return None
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    if math.isnan(number):
        return None
    return number


def _fmt_cell(value: Any) -> str:
    if value is None or value == "":
        return MISSING
    if isinstance(value, float):
        return f"{value:.4g}" if abs(value) < 1 else f"{value:.2f}"
    return str(value)


def _diagnostic_tti_from_dtp(dtp: Any) -> float | None:
    number = _as_float(dtp)
    if number is None or number >= 1.0 or number < 0.0:
        return None
    denom = 1.0 - number
    if abs(denom) < 1e-12:
        return None
    return 1.0 / denom


def locate_session_artifacts(
    session_id: str,
    *,
    experiment_session_root: Path,
    default_session_root: Path,
) -> dict[str, Any]:
    candidates = [
        ("experiment_output", experiment_session_root / session_id),
        ("default_session_root", default_session_root / session_id),
    ]
    files: dict[str, dict[str, Any]] = {}
    chosen_dir: Path | None = None
    chosen_root_kind: str | None = None
    tripinfo_path: Path | None = None
    for kind, session_dir in candidates:
        for name in SESSION_FILES:
            path = session_dir / name
            key = f"{kind}:{name}"
            files[key] = {
                "path": str(path),
                "exists": path.is_file(),
                "size_bytes": path.stat().st_size if path.is_file() else 0,
            }
        trip = session_dir / "tripinfo.xml"
        if trip.is_file() and tripinfo_path is None:
            chosen_dir = session_dir
            chosen_root_kind = kind
            tripinfo_path = trip

    parse_ok = False
    parse_error: str | None = None
    trip_count: int | None = None
    if tripinfo_path is not None:
        try:
            root = ET.parse(tripinfo_path).getroot()
            trip_count = len(root.findall("tripinfo"))
            parse_ok = True
        except (OSError, ET.ParseError) as exc:
            parse_error = str(exc)

    inspect_keys = (
        "evaluation_scope",
        "vehicle_ids",
        "ever_entered",
        "scene_affected",
        "membership",
        "snapshot",
    )
    membership_hints: list[str] = []
    if chosen_dir is not None:
        manifest_path = chosen_dir / "session_manifest.json"
        if manifest_path.is_file():
            try:
                manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
            except (OSError, json.JSONDecodeError):
                manifest = {}
            if isinstance(manifest, dict):
                if isinstance(manifest.get("evaluation_scope"), dict):
                    membership_hints.append("session_manifest.evaluation_scope")
                for key in inspect_keys:
                    if key in {"evaluation_scope", "snapshot"}:
                        continue
                    if manifest.get(key):
                        membership_hints.append(f"session_manifest.{key}")
        names = {path.name for path in chosen_dir.iterdir()} if chosen_dir.is_dir() else set()
        for extra in (
            "snapshots.jsonl",
            "metrics.json",
            "vehicle_ids.json",
            "ever_entered.json",
            "scene_affected_vehicle_ids.json",
        ):
            if extra in names:
                membership_hints.append(extra)

    return {
        "session_id": session_id,
        "session_dir": str(chosen_dir) if chosen_dir else None,
        "session_root_kind": chosen_root_kind,
        "tripinfo_path": str(tripinfo_path) if tripinfo_path else None,
        "tripinfo_size_bytes": tripinfo_path.stat().st_size if tripinfo_path else 0,
        "xml_parse_ok": parse_ok,
        "xml_parse_error": parse_error,
        "tripinfo_trip_count": trip_count,
        "files": files,
        "membership_hints": membership_hints,
        "can_restore_scene_vehicle_ids": False,
    }


def _source_map(payload: dict[str, Any] | None) -> dict[str, Any]:
    if not isinstance(payload, dict):
        return {}
    sources = payload.get("metric_sources")
    return dict(sources) if isinstance(sources, dict) else {}


def _network_departed(metrics: dict[str, Any]) -> int | None:
    sample = metrics.get("sample_sizes") or {}
    if isinstance(sample, dict) and sample.get("network_departed") is not None:
        try:
            return int(sample["network_departed"])
        except (TypeError, ValueError):
            return None
    network = metrics.get("network_metrics") or {}
    if isinstance(network, dict) and network.get("departed") is not None:
        try:
            return int(network["departed"])
        except (TypeError, ValueError):
            return None
    return None


def apply_network_tripinfo(
    *,
    tripinfo_path: Path,
    session_dir: Path,
    traffic_manifest_path: Path,
    algorithm: str,
    expected_departed: int | None,
) -> dict[str, Any]:
    fuel_meta, fuel_warnings = load_fuel_meta_by_type(
        session_dir=session_dir,
        traffic_manifest_path=traffic_manifest_path,
    )
    result = EvalResult(algorithm=algorithm)
    if expected_departed is not None:
        result.departed = int(expected_departed)
    apply_tripinfo_official_metrics(
        result,
        tripinfo_path,
        fuel_meta,
        expected_departed=expected_departed,
    )
    payload = result.to_frontend_metrics()
    payload["warnings"] = list(result.warnings) + list(fuel_warnings)
    payload["unrounded"] = {
        "path_avg_speed_kmh": result.path_avg_speed_kmh,
        "travel_time_index": result.travel_time_index,
        "delay_time_proportion": result.delay_time_proportion,
        "traffic_performance_index": result.traffic_performance_index,
        "avg_stops_per_vehicle": result.avg_stops_per_vehicle,
        "avg_travel_time": result.avg_travel_time_s,
        "avg_waiting_time": result.avg_waiting_time_s,
        "fuel_intensity_L_per_100km": result.fuel_intensity_L_per_100km,
    }
    return payload


def _overlay_network_metrics(
    original_network: dict[str, Any] | None,
    official: dict[str, Any],
) -> dict[str, Any]:
    merged = dict(original_network or {})
    for key in (
        "path_avg_speed_kmh",
        "travel_time_index",
        "delay_time_proportion",
        "traffic_performance_index",
        "traffic_state",
        "tpi_method",
        "avg_stops_per_vehicle",
        "avg_travel_time",
        "avg_waiting_time",
        "fuel_consumption",
        "fuel_intensity_L_per_100km",
    ):
        if key in official:
            merged[key] = official.get(key)
    sources = dict(merged.get("metric_sources") or {})
    sources.update(official.get("metric_sources") or {})
    merged["metric_sources"] = sources
    warnings = [
        item
        for item in (merged.get("warnings") or [])
        if "TripInfo 文件不存在" not in str(item)
    ]
    for item in official.get("warnings") or []:
        if item and item not in warnings:
            warnings.append(item)
    merged["warnings"] = warnings
    return merged


def postprocess_one(
    item: dict[str, Any],
    *,
    experiment_session_root: Path,
    default_session_root: Path,
    traffic_manifest_path: Path,
) -> tuple[dict[str, Any], dict[str, Any]]:
    session_id = str(item.get("session_id") or "")
    metrics = dict(item.get("metrics") or {})
    locate = locate_session_artifacts(
        session_id,
        experiment_session_root=experiment_session_root,
        default_session_root=default_session_root,
    )
    scene_metrics = dict(metrics.get("scene_metrics") or {})
    network_metrics = dict(metrics.get("network_metrics") or {})
    official: dict[str, Any] | None = None
    recovery_warnings: list[str] = []

    if item.get("status") != "SUCCESS":
        recovery_warnings.append("run 不是 SUCCESS，跳过 TripInfo 回填")
    elif not locate["tripinfo_path"]:
        recovery_warnings.append("两个候选目录都找不到 tripinfo.xml")
    elif not locate["xml_parse_ok"]:
        recovery_warnings.append(
            f"tripinfo.xml 无法解析: {locate.get('xml_parse_error')}"
        )
    else:
        official = apply_network_tripinfo(
            tripinfo_path=Path(locate["tripinfo_path"]),
            session_dir=Path(locate["session_dir"]),
            traffic_manifest_path=traffic_manifest_path,
            algorithm=str(item.get("algorithm") or metrics.get("algorithm") or ""),
            expected_departed=_network_departed(metrics),
        )
        network_metrics = _overlay_network_metrics(network_metrics, official)
        recovery_warnings.extend(official.get("warnings") or [])

    recovery_warnings.append(SCENE_AFFECTED_UNRESTORABLE_REASON)

    scene_affected = {
        "metric_kind": "scene_affected_trip_metrics",
        "restored": False,
        "reason": SCENE_AFFECTED_UNRESTORABLE_REASON,
        "sample_vehicle_count": (metrics.get("sample_sizes") or {}).get(
            "scene_affected_trip_vehicles"
        ),
        "path_avg_speed_kmh": None,
        "travel_time_index": None,
        "delay_time_proportion": None,
        "traffic_performance_index": None,
        "traffic_state": None,
        "avg_stops_per_vehicle": None,
        "avg_travel_time": None,
        "avg_waiting_time": None,
        "fuel_intensity_L_per_100km": None,
        "metric_sources": {},
    }

    official_sources = (official or {}).get("metric_sources") or {}
    scene_sources = _source_map(scene_metrics) or _source_map(metrics)
    updated_metrics = dict(metrics)
    updated_metrics["scene_metrics"] = scene_metrics
    updated_metrics["network_metrics"] = network_metrics
    updated_metrics["scene_affected_trip_metrics"] = scene_affected
    updated_metrics["postprocess"] = {
        "tripinfo_path": locate["tripinfo_path"],
        "session_root_kind": locate["session_root_kind"],
        "network_tripinfo_applied": official is not None
        and official.get("travel_time_index") is not None,
        "scene_affected_restored": False,
        "tti_source": official_sources.get("travel_time_index"),
        "dtp_source": official_sources.get("delay_time_proportion"),
        "tpi_source": official_sources.get("traffic_performance_index"),
        "fuel_source": official_sources.get("fuel_intensity_L_per_100km"),
        "diagnostic_tti_from_scene_dtp": _diagnostic_tti_from_dtp(
            scene_metrics.get("delay_time_proportion", metrics.get("delay_time_proportion"))
        ),
        "note": (
            "diagnostic_tti_from_scene_dtp 仅供核对，不是正式 travel_time_index；"
            "正式 TTI 只来自 traffic_eval.tripinfo 的 completed TripInfo。"
        ),
    }

    updated = dict(item)
    updated["metrics"] = updated_metrics
    existing_warnings = list(item.get("warnings") or [])
    for warning in recovery_warnings:
        if warning and warning not in existing_warnings:
            existing_warnings.append(warning)
    updated["warnings"] = existing_warnings

    recovery = {
        **locate,
        "experiment_id": item.get("experiment_id"),
        "algorithm": item.get("algorithm"),
        "run_key": item.get("run_key"),
        "status": item.get("status"),
        "network_tripinfo_applied": official is not None,
        "tti_recovered": official is not None and official.get("travel_time_index") is not None,
        "dtp_recovered": official is not None
        and official.get("delay_time_proportion") is not None,
        "tpi_recovered": official is not None
        and official.get("traffic_performance_index") is not None,
        "fuel_recovered": official is not None
        and official.get("fuel_intensity_L_per_100km") is not None,
        "travel_wait_recovered": official is not None
        and official.get("avg_travel_time") is not None,
        "scene_affected_restored": False,
        "scene_vehicle_ids_restored": False,
        "official_sources": official_sources,
        "scene_sources": scene_sources,
        "warnings": recovery_warnings,
    }
    return updated, recovery


def build_report_rows(results: list[dict[str, Any]]) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for item in results:
        metrics = item.get("metrics") or {}
        scene = metrics.get("scene_metrics") or metrics
        network = metrics.get("network_metrics") or {}
        latency = item.get("decision_latency") or {}
        latency_value = (
            MISSING
            if not latency.get("applicable", True)
            else metrics.get("avg_decision_latency_ms", latency.get("mean"))
        )
        scene_sources = _source_map(scene)
        network_sources = _source_map(network)
        common = {
            "experiment_id": item.get("experiment_id"),
            "algorithm": item.get("algorithm"),
            "session_id": item.get("session_id"),
            "status": item.get("status"),
        }
        rows.append(
            {
                **common,
                "scope": "scene_snapshot",
                "path_avg_speed_kmh": scene.get("path_avg_speed_kmh"),
                "avg_stops_per_vehicle": scene.get("avg_stops_per_vehicle"),
                "regional_max_queue_length_m": scene.get("regional_max_queue_length_m"),
                "avg_travel_time": scene.get("avg_travel_time"),
                "avg_waiting_time": scene.get("avg_waiting_time"),
                "throughput": scene.get("throughput"),
                "travel_time_index": MISSING,
                "delay_time_proportion": scene.get("delay_time_proportion"),
                "traffic_performance_index": scene.get("traffic_performance_index"),
                "traffic_state": scene.get("traffic_state"),
                "spillback_rate": scene.get("spillback_rate"),
                "hard_braking_rate": scene.get("hard_braking_rate"),
                "fuel_intensity_L_per_100km": scene.get("fuel_intensity_L_per_100km"),
                "avg_decision_latency_ms": latency_value,
                "tti_source": MISSING,
                "dtp_source": scene_sources.get(
                    "delay_time_proportion", "snapshot_provisional_timeLoss_over_duration"
                ),
                "tpi_source": scene_sources.get(
                    "traffic_performance_index",
                    "GB/T33171-2016_Annex_C_DTP_piecewise_linear_snapshot_provisional",
                ),
                "fuel_source": scene_sources.get(
                    "fuel_intensity_L_per_100km", "scene_in_scope_snapshot"
                ),
            }
        )
        rows.append(
            {
                **common,
                "scope": "network_tripinfo",
                "path_avg_speed_kmh": network.get("path_avg_speed_kmh"),
                "avg_stops_per_vehicle": network.get("avg_stops_per_vehicle"),
                "regional_max_queue_length_m": network.get("regional_max_queue_length_m"),
                "avg_travel_time": network.get("avg_travel_time"),
                "avg_waiting_time": network.get("avg_waiting_time"),
                "throughput": network.get("throughput"),
                "travel_time_index": network.get("travel_time_index"),
                "delay_time_proportion": network.get("delay_time_proportion"),
                "traffic_performance_index": network.get("traffic_performance_index"),
                "traffic_state": network.get("traffic_state"),
                "spillback_rate": network.get("spillback_rate"),
                "hard_braking_rate": network.get("hard_braking_rate"),
                "fuel_intensity_L_per_100km": network.get("fuel_intensity_L_per_100km"),
                "avg_decision_latency_ms": latency_value,
                "tti_source": network_sources.get("travel_time_index") or MISSING,
                "dtp_source": network_sources.get("delay_time_proportion") or MISSING,
                "tpi_source": network_sources.get("traffic_performance_index") or MISSING,
                "fuel_source": network_sources.get("fuel_intensity_L_per_100km") or MISSING,
            }
        )
        rows.append(
            {
                **common,
                "scope": "scene_affected_tripinfo",
                "path_avg_speed_kmh": MISSING,
                "avg_stops_per_vehicle": MISSING,
                "regional_max_queue_length_m": MISSING,
                "avg_travel_time": MISSING,
                "avg_waiting_time": MISSING,
                "throughput": MISSING,
                "travel_time_index": MISSING,
                "delay_time_proportion": MISSING,
                "traffic_performance_index": MISSING,
                "traffic_state": MISSING,
                "spillback_rate": MISSING,
                "hard_braking_rate": MISSING,
                "fuel_intensity_L_per_100km": MISSING,
                "avg_decision_latency_ms": MISSING,
                "tti_source": MISSING,
                "dtp_source": MISSING,
                "tpi_source": MISSING,
                "fuel_source": MISSING,
            }
        )
    return rows


def build_summary_rows(results: list[dict[str, Any]]) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for item in results:
        metrics = item.get("metrics") or {}
        scene = metrics.get("scene_metrics") or {}
        network = metrics.get("network_metrics") or {}
        latency = item.get("decision_latency") or {}
        sources = _source_map(network)
        rows.append(
            {
                "experiment_id": item.get("experiment_id"),
                "algorithm": item.get("algorithm"),
                "seed": item.get("seed"),
                "status": item.get("status"),
                "session_id": item.get("session_id"),
                "scene_regional_max_queue_length_m": scene.get("regional_max_queue_length_m"),
                "scene_avg_travel_time": scene.get("avg_travel_time"),
                "scene_avg_waiting_time": scene.get("avg_waiting_time"),
                "scene_throughput": scene.get("throughput"),
                "scene_delay_time_proportion": scene.get("delay_time_proportion"),
                "scene_traffic_performance_index": scene.get("traffic_performance_index"),
                "scene_fuel_intensity_L_per_100km": scene.get("fuel_intensity_L_per_100km"),
                "avg_decision_latency_ms": (
                    MISSING
                    if not latency.get("applicable", True)
                    else latency.get("mean")
                ),
                "network_path_avg_speed_kmh": network.get("path_avg_speed_kmh"),
                "network_avg_stops_per_vehicle": network.get("avg_stops_per_vehicle"),
                "network_avg_travel_time": network.get("avg_travel_time"),
                "network_avg_waiting_time": network.get("avg_waiting_time"),
                "network_travel_time_index": network.get("travel_time_index"),
                "network_delay_time_proportion": network.get("delay_time_proportion"),
                "network_traffic_performance_index": network.get("traffic_performance_index"),
                "network_traffic_state": network.get("traffic_state"),
                "network_fuel_intensity_L_per_100km": network.get(
                    "fuel_intensity_L_per_100km"
                ),
                "tti_source": sources.get("travel_time_index") or MISSING,
                "dtp_source": sources.get("delay_time_proportion") or MISSING,
                "tpi_source": sources.get("traffic_performance_index") or MISSING,
                "fuel_source": sources.get("fuel_intensity_L_per_100km") or MISSING,
                "scene_affected_trip_metrics_restored": False,
            }
        )
    return rows


def write_summary_md(
    path: Path,
    *,
    results: list[dict[str, Any]],
    recovery_summary: dict[str, Any],
) -> None:
    by_group: dict[str, list[dict[str, Any]]] = {}
    for item in results:
        by_group.setdefault(str(item.get("experiment_id")), []).append(item)

    lines = [
        "# 第四章附加实验后处理结果",
        "",
        "本文件由 `experiments/chapter4/postprocess_existing_results.py` 离线生成，**没有重新启动 SUMO**。",
        "",
        "口径说明：",
        "",
        "- **最大排队长度 / 吞吐流率 / 平均决策时延**：沿用原 `scene_metrics` 局部场景快照。",
        "- **平均行程时间 / 平均等待时间 / TTI / DTP / TPI / 燃油强度**：全路网 TripInfo 正式回填。",
        "- **局部 scene_affected TripInfo 无法离线恢复**，因此下表没有把全路网 TripInfo 写成局部场景指标；"
        "请同时阅读列含义，不要把 TTI/DTP/TPI/油耗理解成 east_dense / west_dense 进口范围。",
        "- 正式 TTI 只来自 `traffic_eval.tripinfo` 的 `duration / equivalent_free_flow_time`，"
        "没有用 `1/(1-DTP)` 填表。",
        "",
        f"- 找到 TripInfo：{recovery_summary.get('tripinfo_found')}/"
        f"{recovery_summary.get('session_count')}",
        f"- TTI 正式回填：{recovery_summary.get('tti_recovered')}",
        f"- DTP/TPI 正式回填：{recovery_summary.get('dtp_recovered')}/"
        f"{recovery_summary.get('tpi_recovered')}",
        f"- 百公里油耗正式回填：{recovery_summary.get('fuel_recovered')}",
        "",
    ]

    headers = [
        "算法",
        "最大排队长度",
        "平均行程时间",
        "平均等待时间",
        "吞吐流率",
        "TTI",
        "DTP",
        "TPI",
        "燃油强度",
        "平均决策时延",
    ]
    for experiment_id in GROUP_ORDER:
        rows = by_group.get(experiment_id)
        if not rows:
            continue
        rows = sorted(
            rows,
            key=lambda item: ALGORITHM_ORDER.index(item["algorithm"])
            if item.get("algorithm") in ALGORITHM_ORDER
            else 99,
        )
        label = GROUP_LABELS.get(experiment_id, experiment_id)
        lines.append(f"## {experiment_id} {label}")
        lines.append("")
        lines.append(
            "最大排队 / 吞吐 / 决策时延 = 局部场景快照；"
            "行程 / 等待 / TTI / DTP / TPI / 燃油 = 全路网 TripInfo 正式指标。"
        )
        lines.append("")
        lines.append("| " + " | ".join(headers) + " |")
        lines.append("| " + " | ".join("---" for _ in headers) + " |")
        for item in rows:
            metrics = item.get("metrics") or {}
            scene = metrics.get("scene_metrics") or {}
            network = metrics.get("network_metrics") or {}
            latency = item.get("decision_latency") or {}
            latency_value = (
                MISSING
                if not latency.get("applicable", True)
                else latency.get("mean")
            )
            cells = [
                item.get("algorithm"),
                _fmt_cell(scene.get("regional_max_queue_length_m")),
                _fmt_cell(network.get("avg_travel_time")),
                _fmt_cell(network.get("avg_waiting_time")),
                _fmt_cell(scene.get("throughput")),
                _fmt_cell(network.get("travel_time_index")),
                _fmt_cell(network.get("delay_time_proportion")),
                _fmt_cell(network.get("traffic_performance_index")),
                _fmt_cell(network.get("fuel_intensity_L_per_100km")),
                _fmt_cell(latency_value),
            ]
            lines.append("| " + " | ".join(str(cell) for cell in cells) + " |")
        lines.append("")

    lines.extend(
        [
            "## 无法离线恢复的指标",
            "",
            "- `scene_affected_trip_metrics`：缺少曾进入 evaluation_scope 的 `vehicle_ids`。",
            "- 局部场景正式 TTI / TripInfo DTP / TripInfo TPI / TripInfo 燃油强度："
            "没有局部车辆集合，不能从全路网 TripInfo 冒充。",
            "- 由局部 snapshot DTP 反推的 `1/(1-DTP)` 只写在 "
            "`results_raw_postprocessed.json` 的 diagnostic 字段中，未进入本表。",
            "",
        ]
    )
    path.write_text("\n".join(lines), encoding="utf-8")


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="离线回填第四章已有实验结果的 TripInfo 正式指标（不启动 SUMO）",
    )
    parser.add_argument(
        "--output-dir",
        required=True,
        help="已完成实验输出目录，例如 outputs/chapter4_experiments/20260912_115540",
    )
    parser.add_argument(
        "--default-session-root",
        default=str(DEFAULT_SESSION_ROOT),
        help="SimulationManager 默认 session 根目录",
    )
    parser.add_argument(
        "--traffic-manifest",
        default=str(DEFAULT_TRAFFIC_MANIFEST),
        help="traffic_manifest.json 路径，供燃油元数据回退",
    )
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    output_dir = Path(args.output_dir)
    if not output_dir.is_absolute():
        output_dir = _PROJECT_ROOT / output_dir
    raw_path = output_dir / "results_raw.json"
    if not raw_path.is_file():
        raise SystemExit(f"找不到 {raw_path}")

    raw = json.loads(raw_path.read_text(encoding="utf-8"))
    original_results = list(raw.get("results") or [])
    experiment_session_root = output_dir / "sessions"
    default_session_root = Path(args.default_session_root)
    if not default_session_root.is_absolute():
        default_session_root = _PROJECT_ROOT / default_session_root
    traffic_manifest_path = Path(args.traffic_manifest)
    if not traffic_manifest_path.is_absolute():
        traffic_manifest_path = _PROJECT_ROOT / traffic_manifest_path

    updated_results: list[dict[str, Any]] = []
    recoveries: list[dict[str, Any]] = []
    for item in original_results:
        updated, recovery = postprocess_one(
            deepcopy(item),
            experiment_session_root=experiment_session_root,
            default_session_root=default_session_root,
            traffic_manifest_path=traffic_manifest_path,
        )
        updated_results.append(updated)
        recoveries.append(recovery)

    recovery_summary = {
        "created_at": datetime.now(timezone.utc).isoformat(),
        "sumo_started": False,
        "simulation_manager_imported": False,
        "output_dir": str(output_dir),
        "experiment_session_root": str(experiment_session_root),
        "default_session_root": str(default_session_root),
        "session_count": len(recoveries),
        "tripinfo_found": sum(1 for item in recoveries if item.get("tripinfo_path")),
        "tripinfo_in_experiment_output": sum(
            1 for item in recoveries if item.get("session_root_kind") == "experiment_output"
        ),
        "tripinfo_in_default_session_root": sum(
            1 for item in recoveries if item.get("session_root_kind") == "default_session_root"
        ),
        "xml_parse_ok": sum(1 for item in recoveries if item.get("xml_parse_ok")),
        "tti_recovered": sum(1 for item in recoveries if item.get("tti_recovered")),
        "dtp_recovered": sum(1 for item in recoveries if item.get("dtp_recovered")),
        "tpi_recovered": sum(1 for item in recoveries if item.get("tpi_recovered")),
        "fuel_recovered": sum(1 for item in recoveries if item.get("fuel_recovered")),
        "travel_wait_recovered": sum(
            1 for item in recoveries if item.get("travel_wait_recovered")
        ),
        "scene_affected_restored": 0,
        "unrestorable": [
            {
                "metric": "scene_affected_trip_metrics",
                "reason": SCENE_AFFECTED_UNRESTORABLE_REASON,
            },
            {
                "metric": "local_scene_official_TTI_DTP_TPI_fuel",
                "reason": "没有 ever_entered vehicle_ids，不能对局部场景调用 apply_tripinfo_official_metrics(vehicle_ids=...)",
            },
        ],
        "sessions": recoveries,
    }

    generated_files = [
        "results_raw_postprocessed.json",
        "results_summary_postprocessed.csv",
        "report_metrics_postprocessed.csv",
        "tripinfo_recovery_report.json",
        "summary_postprocessed.md",
    ]
    atomic_write_json(
        output_dir / "results_raw_postprocessed.json",
        {
            "results": updated_results,
            "postprocess": {
                "sumo_started": False,
                "source_results_raw": str(raw_path),
                "recovery_summary": {
                    key: recovery_summary[key]
                    for key in (
                        "session_count",
                        "tripinfo_found",
                        "tti_recovered",
                        "dtp_recovered",
                        "tpi_recovered",
                        "fuel_recovered",
                        "scene_affected_restored",
                    )
                },
            },
        },
    )
    summary_rows = build_summary_rows(updated_results)
    write_csv(
        output_dir / "results_summary_postprocessed.csv",
        summary_rows,
        list(summary_rows[0].keys()) if summary_rows else [],
    )
    report_rows = build_report_rows(updated_results)
    report_fields = [
        "experiment_id",
        "algorithm",
        "scope",
        "path_avg_speed_kmh",
        "avg_stops_per_vehicle",
        "regional_max_queue_length_m",
        "avg_travel_time",
        "avg_waiting_time",
        "throughput",
        "travel_time_index",
        "delay_time_proportion",
        "traffic_performance_index",
        "traffic_state",
        "spillback_rate",
        "hard_braking_rate",
        "fuel_intensity_L_per_100km",
        "avg_decision_latency_ms",
        "tti_source",
        "dtp_source",
        "tpi_source",
        "fuel_source",
        "session_id",
        "status",
    ]
    write_csv(output_dir / "report_metrics_postprocessed.csv", report_rows, report_fields)
    atomic_write_json(output_dir / "tripinfo_recovery_report.json", recovery_summary)
    write_summary_md(
        output_dir / "summary_postprocessed.md",
        results=updated_results,
        recovery_summary=recovery_summary,
    )

    print("======== Chapter 4 offline TripInfo postprocess ========")
    print(f"sumo_started: False")
    print(f"output: {output_dir}")
    print(
        f"tripinfo found: {recovery_summary['tripinfo_found']}/"
        f"{recovery_summary['session_count']}"
    )
    print(
        f"actual root: default_session_root={recovery_summary['tripinfo_in_default_session_root']} "
        f"experiment_output={recovery_summary['tripinfo_in_experiment_output']}"
    )
    print(f"TTI recovered: {recovery_summary['tti_recovered']}")
    print(
        f"DTP/TPI recovered: {recovery_summary['dtp_recovered']}/"
        f"{recovery_summary['tpi_recovered']}"
    )
    print(f"fuel recovered: {recovery_summary['fuel_recovered']}")
    print("scene_affected_trip_metrics restored: 0")
    print("generated:")
    for name in generated_files:
        print(f"  {output_dir / name}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
