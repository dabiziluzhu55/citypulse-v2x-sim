"""Deterministic Copilot query intent routing.

This router is keyword- and context-based. It does not train a classifier.
Knowledge questions must win over live-tool intents so RAG answers are not
replaced by current-traffic snapshots.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Sequence


CURRENT_TRAFFIC = "CURRENT_TRAFFIC"
NETWORK_RISK = "NETWORK_RISK"
PREDICTION = "PREDICTION"
ROAD_CONTEXT = "ROAD_CONTEXT"
LANE_COUNT = "LANE_COUNT"
AI_STATUS = "AI_STATUS"
KNOWLEDGE = "KNOWLEDGE"
GENERAL = "GENERAL"

LIVE_INTENTS = frozenset(
    {
        CURRENT_TRAFFIC,
        NETWORK_RISK,
        PREDICTION,
        ROAD_CONTEXT,
        LANE_COUNT,
        AI_STATUS,
    }
)
DETERMINISTIC_INTENTS = frozenset({LANE_COUNT, PREDICTION, AI_STATUS, NETWORK_RISK})

_INTERSECTION_PATTERN = re.compile(
    r"(?:路口|交叉口|junction|demo[_-]?)\s*(\d+)",
    re.IGNORECASE,
)
_LANE_ID_PATTERN = re.compile(r"\blane[_-]?([A-Za-z0-9._-]+)\b", re.IGNORECASE)
_SCOPE_INTERSECTION_PREFIX = "intersection:"

_LANE_COUNT_MARKERS = (
    "几个车道",
    "多少车道",
    "车道数量",
    "有几条lane",
    "有几条 lane",
    "几条车道",
    "有几个车道",
    "车道数",
    "多少条车道",
    "几条lane",
)
_PREDICTION_MARKERS = (
    "预测",
    "未来",
    "60秒",
    "六十秒",
    "60s",
    "一分钟",
    "接下来车流",
    "未来车流",
    "怎么变",
    "将会怎样",
    "会怎么变",
)
_RISK_MARKERS = (
    "主要风险",
    "主要问题",
    "风险最大",
    "哪些路口风险",
    "风险有哪些",
    "热点路口",
    "高风险",
    "当前交通仿真的主要问题",
    "出现的主要风险",
)
_AI_STATUS_MARKERS = (
    "ai接管",
    "ai 接管",
    "ai管控",
    "ai 管控",
    "是否在管控",
    "管控是否",
    "当前策略",
    "接管状态",
    "ai状态",
    "ai 状态",
)
_ROAD_CONTEXT_MARKERS = (
    "相连路口",
    "连接关系",
    "相邻路口",
    "上游路口",
    "下游路口",
    "直接相连",
    "拓扑",
)
_CURRENT_TRAFFIC_MARKERS = (
    "现在拥堵",
    "当前拥堵",
    "交通状况",
    "交通状态",
    "现在怎么样",
    "当前怎么样",
    "车流怎么样",
    "拥堵吗",
    "多少辆",
    "多少车",
    "实时车流",
    "实时交通",
    "当前车流",
    "现在有多少",
)
_KNOWLEDGE_DEFINITION_MARKERS = (
    "是什么",
    "什么是",
    "介绍一下",
    "介绍下",
    "原理",
    "如何工作",
    "怎么实现",
)
_NAMED_KNOWLEDGE_MARKERS = (
    "max pressure",
    "max-pressure",
    "maxpressure",
    "最大压力",
    "backpressure",
    "sotl",
    "自组织交通灯",
    "ippo",
    "mappo",
    "固定配时",
    "fixed time",
    "cov2x",
    "co-v2x",
    "citypulse-qwen",
    "citypulse qwen",
    "narrow-tdp",
    "narrow tdp",
    "narrownet-tdp",
    "narrownet tdp",
)
_DETAIL_LANE_MARKERS = (
    "逐车道",
    "所有车道",
    "每条车道",
    "各车道",
    "车道详情",
)
_CONNECTION_DETAIL_MARKERS = (
    "连接关系",
    "相连",
    "相邻",
    "上游",
    "下游",
    "拓扑",
)
_DETAIL_ANSWER_MARKERS = (
    "详细解释",
    "详细说明",
    "请详细",
    "展开说明",
)


@dataclass(frozen=True)
class QueryIntent:
    name: str
    intersection_id: str | None = None
    lane_id: str | None = None
    wants_lane_detail: bool = False
    wants_connection_detail: bool = False
    wants_detailed_answer: bool = False


def route_query_intent(
    question: str,
    *,
    active_scope: str | None = None,
) -> QueryIntent:
    normalized = str(question or "").strip()
    lowered = normalized.casefold()
    intersection_id = extract_intersection_id(normalized, active_scope=active_scope)
    lane_id = extract_lane_id(normalized)
    wants_lane_detail = _contains_any(lowered, _DETAIL_LANE_MARKERS) or bool(lane_id)
    wants_connection_detail = _contains_any(lowered, _CONNECTION_DETAIL_MARKERS)
    wants_detailed_answer = _contains_any(lowered, _DETAIL_ANSWER_MARKERS)

    def intent(name: str) -> QueryIntent:
        return QueryIntent(
            name=name,
            intersection_id=intersection_id,
            lane_id=lane_id,
            wants_lane_detail=wants_lane_detail,
            wants_connection_detail=wants_connection_detail,
            wants_detailed_answer=wants_detailed_answer,
        )

    if _is_knowledge_query(lowered):
        return intent(KNOWLEDGE)
    if _contains_any(lowered, _LANE_COUNT_MARKERS):
        return intent(LANE_COUNT)
    if _contains_any(lowered, _PREDICTION_MARKERS):
        return intent(PREDICTION)
    if _contains_any(lowered, _RISK_MARKERS):
        return intent(NETWORK_RISK)
    if _contains_any(lowered, _AI_STATUS_MARKERS):
        return intent(AI_STATUS)
    if _contains_any(lowered, _ROAD_CONTEXT_MARKERS):
        return intent(ROAD_CONTEXT)
    if _contains_any(lowered, _CURRENT_TRAFFIC_MARKERS):
        return intent(CURRENT_TRAFFIC)
    return intent(GENERAL)


def extract_intersection_id(
    question: str,
    *,
    active_scope: str | None = None,
) -> str | None:
    match = _INTERSECTION_PATTERN.search(str(question or ""))
    if match:
        return f"demo_{int(match.group(1))}"
    scoped = _intersection_from_scope(active_scope)
    if scoped:
        return scoped
    return None


def extract_lane_id(question: str) -> str | None:
    match = _LANE_ID_PATTERN.search(str(question or ""))
    if not match:
        return None
    value = str(match.group(1) or "").strip()
    return value or None


def prefers_detailed_lanes(question: str, *, lane_id: str | None = None) -> bool:
    lowered = str(question or "").casefold()
    return _contains_any(lowered, _DETAIL_LANE_MARKERS) or bool(lane_id)


def prefers_connection_detail(question: str) -> bool:
    return _contains_any(str(question or "").casefold(), _CONNECTION_DETAIL_MARKERS)


def _is_knowledge_query(lowered: str) -> bool:
    named = _contains_any(lowered, _NAMED_KNOWLEDGE_MARKERS)
    definitional = _contains_any(lowered, _KNOWLEDGE_DEFINITION_MARKERS)
    live_operational = (
        _contains_any(lowered, _LANE_COUNT_MARKERS)
        or _contains_any(lowered, _PREDICTION_MARKERS)
        or _contains_any(lowered, _RISK_MARKERS)
        or _contains_any(lowered, _CURRENT_TRAFFIC_MARKERS)
        or _contains_any(lowered, _AI_STATUS_MARKERS)
    )
    if named and definitional:
        return True
    if named and not live_operational:
        return True
    if definitional and not live_operational:
        return True
    return False


def _intersection_from_scope(active_scope: str | None) -> str | None:
    normalized = str(active_scope or "").strip()
    if not normalized.startswith(_SCOPE_INTERSECTION_PREFIX):
        return None
    intersection_id = normalized[len(_SCOPE_INTERSECTION_PREFIX) :].strip()
    return intersection_id or None


def _contains_any(text: str, markers: Sequence[str]) -> bool:
    return any(marker in text for marker in markers)
