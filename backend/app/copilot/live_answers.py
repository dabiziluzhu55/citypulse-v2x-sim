"""Compact model-facing tool views, deterministic answers, and repetition guards."""

from __future__ import annotations

import json
import math
import re
from typing import Any, Mapping, Sequence

from .query_intent import (
    AI_STATUS,
    LANE_COUNT,
    NETWORK_RISK,
    PREDICTION,
    QueryIntent,
    prefers_connection_detail,
    prefers_detailed_lanes,
)


_SENTENCE_SPLIT = re.compile(r"(?<=[。！？;；])|\n+")
_JSON_LEAK_PATTERN = re.compile(
    r'[{[]|"ok"\s*:|"source"\s*:|"model_summary"|"tool_call"'
)
_REPEATED_NGRAM = re.compile(r"(.{8,40}?)(?:\s*\1){2,}")
_DEFAULT_MAX_SENTENCES = 8
_LANE_FIELDS = (
    "lane_id",
    "edge_id",
    "approach_id",
    "vehicle_count",
    "halting_count",
    "mean_speed_kmh",
    "waiting_time_seconds",
    "occupancy_pct",
    "congestion_level",
)


def compact_tool_result_for_model(
    tool_name: str,
    result: Mapping[str, Any] | None,
    *,
    question: str = "",
    arguments: Mapping[str, Any] | str | None = None,
) -> Mapping[str, Any] | None:
    if not isinstance(result, Mapping):
        return result
    data = result.get("data")
    if not isinstance(data, Mapping):
        return result
    args = _mapping_arguments(arguments)
    envelope = {
        key: result[key]
        for key in ("source", "scope", "timestamp")
        if key in result
    }

    if tool_name == "get_current_traffic":
        envelope["data"] = _compact_current_traffic(
            data,
            question=question,
            arguments=args,
        )
        return envelope
    if tool_name == "get_network_summary":
        envelope["data"] = _compact_network_summary(data)
        return envelope
    if tool_name == "get_prediction":
        envelope["data"] = _compact_prediction(data, arguments=args)
        return envelope
    if tool_name == "get_road_context":
        envelope["data"] = _compact_road_context(data, question=question)
        return envelope
    if tool_name == "get_ai_takeover_status":
        envelope["data"] = _compact_ai_status(data)
        return envelope
    return result


def format_deterministic_answer(
    intent: QueryIntent,
    records: Sequence[Any],
) -> str | None:
    record = _latest_tool_record(records, _tool_for_intent(intent.name))
    if record is None or getattr(record, "error", None) or not isinstance(
        getattr(record, "result", None), Mapping
    ):
        return None
    data = record.result.get("data")
    if not isinstance(data, Mapping):
        return None
    if intent.name == LANE_COUNT:
        return _format_lane_count(data, intent)
    if intent.name == PREDICTION:
        return _format_prediction(data, intent)
    if intent.name == AI_STATUS:
        return _format_ai_status(data)
    if intent.name == NETWORK_RISK:
        return _format_network_risk(data)
    return None


def guard_answer(
    answer: str,
    *,
    intent: QueryIntent | None = None,
    records: Sequence[Any] = (),
    question: str = "",
) -> str:
    text = str(answer or "").strip()
    if not text:
        return text
    if _JSON_LEAK_PATTERN.search(text) and not text.startswith("当前"):
        formatted = format_deterministic_answer(intent, records) if intent else None
        if formatted:
            return formatted
    cleaned = _dedupe_repeated_sentences(text)
    if _is_repetitive(cleaned) or _phrase_overused(cleaned, "进口平均速度较低"):
        formatted = format_deterministic_answer(intent, records) if intent else None
        if formatted:
            return formatted
        cleaned = _dedupe_repeated_sentences(cleaned)
    if intent is None or not intent.wants_detailed_answer:
        cleaned = _limit_sentences(cleaned, _DEFAULT_MAX_SENTENCES)
    if "fallback" in cleaned.casefold() and "narrow-tdp" in cleaned.casefold():
        formatted = format_deterministic_answer(intent, records) if intent else None
        if formatted:
            return formatted
    return cleaned.strip()


def _tool_for_intent(intent_name: str) -> str | None:
    return {
        LANE_COUNT: "get_road_context",
        PREDICTION: "get_prediction",
        AI_STATUS: "get_ai_takeover_status",
        NETWORK_RISK: "get_network_summary",
    }.get(intent_name)


def _latest_tool_record(records: Sequence[Any], name: str | None) -> Any | None:
    if not name:
        return None
    for record in reversed(tuple(records)):
        if getattr(record, "name", None) == name:
            return record
    return None


def _compact_current_traffic(
    data: Mapping[str, Any],
    *,
    question: str,
    arguments: Mapping[str, Any],
) -> dict[str, Any]:
    summary = data.get("model_summary")
    intersections = []
    if isinstance(summary, Mapping):
        intersections = list(summary.get("intersections") or [])
    if not intersections:
        intersections = list(data.get("intersections") or [])
    detailed = prefers_detailed_lanes(
        question, lane_id=_optional_text(arguments.get("lane_id"))
    )
    requested_lane = _optional_text(arguments.get("lane_id"))
    compact_intersections = []
    for item in intersections:
        if not isinstance(item, Mapping):
            continue
        lanes = [
            _lane_view(lane)
            for lane in item.get("lanes", ())
            if isinstance(lane, Mapping)
        ]
        row: dict[str, Any] = {
            "intersection_id": item.get("intersection_id"),
            "current_phase": item.get("current_phase"),
            "totals": item.get("totals", {}),
            "lane_count": len(lanes),
            "top_slow_lanes": _top_lanes(lanes, key="mean_speed_kmh", reverse=False),
            "top_queued_lanes": _top_lanes(lanes, key="halting_count", reverse=True),
        }
        if requested_lane:
            row["requested_lane"] = next(
                (lane for lane in lanes if lane.get("lane_id") == requested_lane),
                {"lane_id": requested_lane, "found": False},
            )
        if detailed:
            row["lanes"] = lanes
        compact_intersections.append(row)
    payload: dict[str, Any] = {
        "as_of_seconds": data.get("as_of_seconds"),
        "model_view": "compact_current_traffic",
    }
    if len(compact_intersections) == 1:
        payload.update(compact_intersections[0])
    else:
        payload["intersections"] = compact_intersections
    return payload


def _compact_network_summary(data: Mapping[str, Any]) -> dict[str, Any]:
    hotspots = [
        item
        for item in data.get("hotspot_intersections", ())
        if isinstance(item, Mapping)
    ][:3]
    return {
        "as_of_seconds": data.get("as_of_seconds"),
        "model_view": "compact_network_summary",
        "hotspot_intersections": [
            {
                "intersection_id": item.get("intersection_id"),
                "risk": item.get("risk"),
                "hotspot_score": item.get("hotspot_score"),
                "congestion_levels": item.get("congestion_levels", []),
                "active_event_count": item.get("active_event_count"),
                "totals": item.get("totals", {}),
            }
            for item in hotspots
        ],
        "high_risk_events": list(data.get("high_risk_events", ()))[:3],
        "medium_risk_events": list(data.get("medium_risk_events", ()))[:3],
        "traffic_levels": data.get("traffic_levels", {}),
        "network_trend": data.get("network_trend", {}),
        "current_totals": data.get("current_totals", {}),
        "active_event_count": data.get("active_event_count"),
    }


def _compact_prediction(
    data: Mapping[str, Any],
    *,
    arguments: Mapping[str, Any],
) -> dict[str, Any]:
    requested = _requested_prediction_ids(arguments)
    rows = [
        item
        for item in data.get("intersections", ())
        if isinstance(item, Mapping)
    ]
    compact: dict[str, Any] = {
        "available": data.get("available", False),
        "as_of_seconds": data.get("as_of_seconds"),
        "supported_horizon_seconds": data.get("supported_horizon_seconds"),
        "horizon_seconds": data.get("horizon_seconds"),
        "model": data.get("model"),
        "model_version": data.get("model_version"),
        "ready": data.get("ready"),
        "fallback": data.get("fallback"),
        "fallback_reason": data.get("fallback_reason"),
        "model_view": "compact_prediction",
    }
    if requested:
        compact["intersections"] = [
            row
            for row in rows
            if str(row.get("intersection_id")) in requested
        ]
        compact["not_found"] = [
            item
            for item in data.get("not_found", ())
            if str(item) in requested
        ]
        return compact
    compact["top_increases"] = list(data.get("top_increases", ()))[:5]
    compact["network_summary"] = {
        "ready": data.get("ready"),
        "fallback": data.get("fallback"),
        "horizon_seconds": data.get("horizon_seconds"),
        "increase_count": len(list(data.get("top_increases", ()))),
    }
    return compact


def _compact_road_context(
    data: Mapping[str, Any],
    *,
    question: str,
) -> dict[str, Any]:
    target = data.get("target") if isinstance(data.get("target"), Mapping) else {}
    lanes = [
        item.get("lane_id")
        for item in data.get("lanes", ())
        if isinstance(item, Mapping) and item.get("lane_id")
    ]
    if not lanes:
        lanes = list(data.get("lane_ids", ()) or [])
    upstream = list(data.get("upstream_intersections", ()) or [])
    downstream = list(data.get("downstream_intersections", ()) or [])
    connected = sorted(
        {
            str(value)
            for value in [*upstream, *downstream]
            if str(value).strip()
        }
    )
    payload: dict[str, Any] = {
        "intersection_id": data.get("intersection_id") or target.get("intersection_id"),
        "lane_count": data.get("lane_count", len(lanes)),
        "lane_ids": data.get("lane_ids", lanes),
        "directly_connected_intersections": connected,
        "topology_available": data.get("topology_available", False),
        "model_view": "compact_road_context",
    }
    incoming = data.get("incoming_lane_count")
    outgoing = data.get("outgoing_lane_count")
    if incoming is not None:
        payload["incoming_lane_count"] = incoming
    if outgoing is not None:
        payload["outgoing_lane_count"] = outgoing
    if prefers_connection_detail(question):
        payload["upstream_intersections"] = upstream
        payload["downstream_intersections"] = downstream
        payload["connections"] = list(data.get("connections", ()) or [])
        payload["connection_note"] = (
            "只回答当前 TLS manifest 能证明的直接相连路口；同一路口同时出现在上游和下游时，"
            "表示双向直接连接，不要重复计数，也不要扩展到其他走廊或路径邻居。"
        )
    return payload


def _compact_ai_status(data: Mapping[str, Any]) -> dict[str, Any]:
    execution_state, execution_note = _ai_execution_view(data)
    return {
        "available": data.get("available", False),
        "as_of_seconds": data.get("as_of_seconds"),
        "simulation_state": data.get("simulation_state"),
        "takeover_state": data.get("takeover_state"),
        "execution_state": execution_state,
        "is_currently_executing": execution_state == "EXECUTING",
        "execution_note": execution_note,
        "ai_enabled": data.get("ai_enabled", False),
        "active_event_id": data.get("active_event_id"),
        "allowed_scope": data.get("allowed_scope", []),
        "controlled_intersections": data.get("controlled_intersections", []),
        "plan_sequence": data.get("plan_sequence", 0),
        "installed_plan_active": data.get("installed_plan_active", False),
        "installed_plan": data.get("installed_plan"),
        "last_objective": data.get("last_objective"),
        "last_reason": data.get("last_reason"),
        "last_error": data.get("last_error"),
        "fallback_reason": data.get("fallback_reason"),
        "rag_status": data.get("rag_status"),
        "plan_valid_until": data.get("plan_valid_until"),
    }


def _ai_execution_view(data: Mapping[str, Any]) -> tuple[str, str]:
    simulation_state = str(data.get("simulation_state", "")).upper()
    takeover_state = str(data.get("takeover_state", "")).upper()
    if simulation_state in {"STOPPED", "COMPLETED", "FAILED"}:
        return "FINISHED", "仿真已经结束，AI 信号控制当前没有执行。"
    if bool(data.get("control_active", False)):
        return "EXECUTING", "仿真正在运行，AI 信号控制计划当前正在执行。"
    if simulation_state == "PAUSED" and takeover_state == "ACTIVE":
        return "PLANNING_PAUSED", "仿真当前暂停，AI 正在规划或安装计划，信号控制动作尚未执行。"
    if takeover_state in {"RECOVERY", "FALLBACK"}:
        return "RECOVERY", "AI 接管正在恢复或回退到基线，当前不能视为正常 AI 计划执行。"
    return "BASELINE", "当前没有正在执行的 AI 信号控制计划，仿真使用基线控制。"


def _format_lane_count(data: Mapping[str, Any], intent: QueryIntent) -> str:
    target = data.get("target") if isinstance(data.get("target"), Mapping) else {}
    intersection_id = str(
        data.get("intersection_id")
        or target.get("intersection_id")
        or intent.intersection_id
        or ""
    )
    lane_ids = [
        str(item)
        for item in (data.get("lane_ids") or [])
        if str(item).strip()
    ]
    if not lane_ids:
        lane_ids = [
            str(item.get("lane_id"))
            for item in data.get("lanes", ())
            if isinstance(item, Mapping) and item.get("lane_id")
        ]
    count = int(data.get("lane_count") or len(lane_ids) or 0)
    label = _intersection_label(intersection_id)
    extras = []
    incoming = data.get("incoming_lane_count")
    outgoing = data.get("outgoing_lane_count")
    if incoming is not None:
        extras.append(f"进口车道{int(incoming)}条")
    if outgoing is not None:
        extras.append(f"出口车道{int(outgoing)}条")
    if extras:
        return f"{label}当前路网定义中共有{count}条相关车道，其中{'，'.join(extras)}。"
    return f"{label}当前路网定义中共有{count}条相关车道。"


def _format_prediction(data: Mapping[str, Any], intent: QueryIntent) -> str:
    if not bool(data.get("available", True)):
        return "当前没有可用的短时预测结果。"
    if data.get("ready") is False:
        return "当前短时预测尚未就绪。"
    horizon = _number(data.get("horizon_seconds")) or _number(
        data.get("supported_horizon_seconds")
    ) or 60.0
    row = _selected_prediction_row(data, intent.intersection_id)
    if row is None:
        increases = [
            item
            for item in data.get("top_increases", ())
            if isinstance(item, Mapping)
        ]
        if not increases:
            return f"当前没有未来{int(horizon)}秒的明显车流上升预测。"
        row = increases[0]
    current = _number(row.get("current_vehicle_count")) or 0.0
    predicted = _number(row.get("predicted_vehicle_count")) or 0.0
    delta = _number(row.get("delta"))
    if delta is None:
        delta = predicted - current
    ratio = _number(row.get("delta_ratio"))
    if ratio is None and abs(current) > 1e-9:
        ratio = delta / current
    trend = str(row.get("trend") or _trend_from_delta(delta))
    label = _intersection_label(str(row.get("intersection_id") or intent.intersection_id or ""))
    change = "增至" if delta >= 0 else "降至"
    percent = f"，变化约{abs(ratio) * 100:.1f}%" if ratio is not None else ""
    trend_text = {
        "increasing": "上升",
        "decreasing": "下降",
        "stable": "平稳",
    }.get(trend, trend or "平稳")
    body = (
        f"{label}未来{int(horizon)}秒车辆数由{int(round(current))}辆"
        f"{change}{int(round(predicted))}辆{percent}，整体呈{trend_text}趋势。"
    )
    if bool(data.get("fallback")):
        reason = str(data.get("fallback_reason") or "").strip()
        prefix = f"当前使用降级预测（{reason}），" if reason else "当前使用降级预测，"
        return prefix + body + "该结果不是 Narrow-TDP 正式预测。"
    return "Narrow-TDP预计" + body


def _format_ai_status(data: Mapping[str, Any]) -> str:
    if not bool(data.get("available", False)) or not bool(data.get("ai_enabled", False)):
        return "当前AI管控未开启。"
    state = str(data.get("takeover_state") or "").upper()
    execution = str(data.get("execution_state") or "")
    controlled = [
        _intersection_label(str(item))
        for item in data.get("controlled_intersections", ())
        if str(item).strip()
    ]
    objective = str(data.get("last_objective") or "").strip()
    if state == "FALLBACK":
        reason = str(data.get("fallback_reason") or "").strip()
        return "当前AI已回退到基线控制" + (f"：{reason}。" if reason else "。")
    if execution == "PLANNING_PAUSED" or state == "ARMED":
        return "当前AI正在规划管控策略，信号控制动作尚未执行。"
    if execution == "EXECUTING" or state == "ACTIVE":
        parts = ["当前AI接管正在执行"]
        if controlled:
            parts.append(f"受控路口为{'、'.join(controlled)}")
        if objective:
            parts.append(f"目标是{objective}")
        return "，".join(parts) + "。"
    return "当前没有正在执行的AI信号控制计划。"


def _format_network_risk(data: Mapping[str, Any]) -> str:
    hotspots = [
        item
        for item in data.get("hotspot_intersections", ())
        if isinstance(item, Mapping)
    ][:3]
    high_events = [
        item
        for item in data.get("high_risk_events", ())
        if isinstance(item, Mapping)
    ]
    medium_events = [
        item
        for item in data.get("medium_risk_events", ())
        if isinstance(item, Mapping)
    ]
    if not hotspots and not high_events and not medium_events:
        return "当前没有明显高风险热点。"
    focus = hotspots[0] if hotspots else {}
    label = _intersection_label(str(focus.get("intersection_id") or ""))
    points = _risk_points(focus, data.get("network_trend") if isinstance(data.get("network_trend"), Mapping) else {})
    if high_events:
        event = high_events[0]
        event_label = _intersection_label(str(event.get("intersection_id") or ""))
        traffic_state = str(event.get("traffic_state") or event.get("event_type") or "高风险事件")
        points.append(f"{event_label}存在{traffic_state}")
    unique_points = list(dict.fromkeys(point for point in points if point))[:3]
    if not unique_points:
        unique_points = ["局部交通运行异常，建议关注热点路口排队和速度"]
    numbered = "\n".join(
        f"{index}. {point}；" if index < len(unique_points) else f"{index}. {point}。"
        for index, point in enumerate(unique_points, start=1)
    )
    prefix = f"当前主要风险集中在{label}：\n" if label else "当前主要风险如下：\n"
    return prefix + numbered


def _risk_points(hotspot: Mapping[str, Any], trend: Mapping[str, Any]) -> list[str]:
    points: list[str] = []
    totals = hotspot.get("totals") if isinstance(hotspot.get("totals"), Mapping) else {}
    mean_speed = _number(totals.get("mean_speed_kmh"))
    halting = _number(totals.get("halting_count"))
    if mean_speed is not None and mean_speed < 20:
        points.append("部分进口平均速度较低")
    if halting is not None and halting >= 3:
        points.append("排队车辆较多")
    levels = [str(item) for item in hotspot.get("congestion_levels", ()) if str(item)]
    if "severe" in levels or "congested" in levels:
        points.append("局部路段处于拥堵状态")
    direction = str(trend.get("direction") or "")
    fallback = bool(trend.get("fallback"))
    if direction == "increasing":
        if fallback:
            points.append("降级预测显示未来60秒车流仍可能上升")
        else:
            points.append("Narrow-TDP显示未来60秒仍有上升趋势")
    return points


def _selected_prediction_row(
    data: Mapping[str, Any],
    intersection_id: str | None,
) -> Mapping[str, Any] | None:
    rows = [
        item
        for item in data.get("intersections", ())
        if isinstance(item, Mapping)
    ]
    if intersection_id:
        for row in rows:
            if str(row.get("intersection_id")) == intersection_id:
                return row
    if len(rows) == 1:
        return rows[0]
    return None


def _lane_view(lane: Mapping[str, Any]) -> dict[str, Any]:
    return {field: lane[field] for field in _LANE_FIELDS if field in lane}


def _top_lanes(
    lanes: Sequence[Mapping[str, Any]],
    *,
    key: str,
    reverse: bool,
    limit: int = 3,
) -> list[dict[str, Any]]:
    ranked = [
        dict(lane)
        for lane in lanes
        if _number(lane.get(key)) is not None
    ]
    ranked.sort(key=lambda item: (_number(item.get(key)) or 0.0, str(item.get("lane_id") or "")), reverse=reverse)
    return ranked[:limit]


def _requested_prediction_ids(arguments: Mapping[str, Any]) -> set[str]:
    values: list[str] = []
    singular = _optional_text(arguments.get("intersection_id"))
    if singular:
        values.append(singular)
    raw_ids = arguments.get("intersection_ids")
    if isinstance(raw_ids, Sequence) and not isinstance(raw_ids, (str, bytes)):
        values.extend(str(item) for item in raw_ids if str(item).strip())
    return set(values)


def _mapping_arguments(arguments: Mapping[str, Any] | str | None) -> dict[str, Any]:
    if isinstance(arguments, Mapping):
        return dict(arguments)
    if isinstance(arguments, str) and arguments.strip():
        try:
            parsed = json.loads(arguments)
        except json.JSONDecodeError:
            return {}
        return dict(parsed) if isinstance(parsed, Mapping) else {}
    return {}


def _dedupe_repeated_sentences(text: str) -> str:
    sentences = [item.strip() for item in _SENTENCE_SPLIT.split(text) if item.strip()]
    if not sentences:
        return text.strip()
    result: list[str] = []
    previous = ""
    repeat = 0
    for sentence in sentences:
        normalized = re.sub(r"\s+", "", sentence)
        if normalized == previous:
            repeat += 1
            if repeat >= 2:
                continue
        else:
            previous = normalized
            repeat = 0
        if result and re.sub(r"\s+", "", result[-1]) == normalized:
            continue
        if sum(1 for item in result if re.sub(r"\s+", "", item) == normalized) >= 2:
            continue
        result.append(sentence)
    joined = "".join(
        item if item.endswith(("。", "！", "？", "；", ";")) else f"{item}。"
        for item in result
    )
    return joined


def _is_repetitive(text: str) -> bool:
    if _REPEATED_NGRAM.search(re.sub(r"\s+", "", text)):
        return True
    sentences = [re.sub(r"\s+", "", item) for item in _SENTENCE_SPLIT.split(text) if item.strip()]
    if len(sentences) >= 3 and len(set(sentences)) == 1:
        return True
    return False


def _phrase_overused(text: str, phrase: str, limit: int = 2) -> bool:
    if not phrase:
        return False
    return text.count(phrase) > limit


def _limit_sentences(text: str, limit: int) -> str:
    sentences = [item.strip() for item in _SENTENCE_SPLIT.split(text) if item.strip()]
    if len(sentences) <= limit:
        return text.strip()
    clipped = sentences[:limit]
    return "".join(
        item if item.endswith(("。", "！", "？")) else f"{item}。"
        for item in clipped
    )


def _intersection_label(intersection_id: str) -> str:
    match = re.fullmatch(r"demo_(\d+)", str(intersection_id or "").strip())
    if match:
        return f"路口{int(match.group(1))}"
    return str(intersection_id or "").strip()


def _optional_text(value: Any) -> str | None:
    text = str(value or "").strip()
    return text or None


def _number(value: Any) -> float | None:
    if value is None or isinstance(value, bool):
        return None
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    return number if math.isfinite(number) else None


def _trend_from_delta(delta: float) -> str:
    if delta > 1e-9:
        return "increasing"
    if delta < -1e-9:
        return "decreasing"
    return "stable"
