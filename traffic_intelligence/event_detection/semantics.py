"""Compatibility mappings for layered event detection semantics."""

from __future__ import annotations


EVENT_NORMAL = "normal"
EVENT_LANE_BLOCKED = "lane_blocked"
EVENT_SPILLBACK = "spillback"
EVENT_SPEED_RESTRICTION = "speed_restriction"
EVENT_ACCIDENT = "accident"

TRAFFIC_NORMAL = "normal"
TRAFFIC_LOCALIZED_BLOCKAGE = "localized_blockage"
TRAFFIC_SPILLBACK = "spillback"
TRAFFIC_CAPACITY_DROP = "capacity_drop"
TRAFFIC_UNKNOWN_ABNORMAL = "unknown_abnormal"

CAUSE_UNKNOWN = "unknown"
CAUSE_DOWNSTREAM_CONGESTION = "downstream_congestion"
CAUSE_STOPPED_OR_CRASHED_VEHICLE = "stopped_or_crashed_vehicle"
CAUSE_CONSTRUCTION_OR_LANE_CLOSURE = "construction_or_lane_closure"
CAUSE_CAPACITY_RESTRICTION = "capacity_restriction"
CAUSE_NO_VISIBLE_OBSTACLE = "no_visible_obstacle"


def normalize_event_type(event_type: str | None) -> str:
    normalized = (event_type or EVENT_NORMAL).strip()
    if normalized in {"", EVENT_NORMAL}:
        return EVENT_NORMAL
    if normalized in {
        EVENT_LANE_BLOCKED,
        "lane_closure",
        "stopped_vehicle",
        "collision_blockage",
        "suspected_blockage",
    }:
        return EVENT_LANE_BLOCKED
    if normalized == EVENT_ACCIDENT:
        return EVENT_LANE_BLOCKED
    if normalized in {EVENT_SPILLBACK, "queue_spillback", "queue_blockage"}:
        return EVENT_SPILLBACK
    if normalized in {EVENT_SPEED_RESTRICTION, "speed_limit"}:
        return EVENT_SPEED_RESTRICTION
    return normalized


def traffic_state_for_event_type(event_type: str | None) -> str:
    normalized = normalize_event_type(event_type)
    if normalized == EVENT_NORMAL:
        return TRAFFIC_NORMAL
    if normalized == EVENT_LANE_BLOCKED:
        return TRAFFIC_LOCALIZED_BLOCKAGE
    if normalized == EVENT_SPILLBACK:
        return TRAFFIC_SPILLBACK
    if normalized == EVENT_SPEED_RESTRICTION:
        return TRAFFIC_CAPACITY_DROP
    return TRAFFIC_UNKNOWN_ABNORMAL


def cause_for_event_type(event_type: str | None) -> str:
    if (event_type or "").strip() == EVENT_ACCIDENT:
        return CAUSE_STOPPED_OR_CRASHED_VEHICLE
    normalized = normalize_event_type(event_type)
    if normalized == EVENT_SPILLBACK:
        return CAUSE_DOWNSTREAM_CONGESTION
    if normalized == EVENT_SPEED_RESTRICTION:
        return CAUSE_CAPACITY_RESTRICTION
    return CAUSE_UNKNOWN


def is_abnormal_event_type(event_type: str | None) -> bool:
    return normalize_event_type(event_type) != EVENT_NORMAL
