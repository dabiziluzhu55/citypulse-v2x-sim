# 事件识别与短时预测的前端接口Schema

from __future__ import annotations

from typing import Any

from pydantic import BaseModel, Field


class DetectedEventCard(BaseModel):
    event_id: str
    status: str
    traffic_state: str
    display_type: str
    display_label: str
    severity: str
    confidence: float
    intersection_id: str
    lane_ids: list[str] = Field(default_factory=list)
    edge_id: str = ""
    approach_id: str = ""
    longitude: float | None = None
    latitude: float | None = None
    start_seconds: float
    end_seconds: float | None = None
    duration_seconds: float
    evidence: list[str] = Field(default_factory=list)
    suggestion: str = ""
    cause: str = "unknown"
    cause_confidence: float = 0.0
    prediction_summary: str = ""
    event_type: str | None = None


class EventDetectionPayload(BaseModel):
    as_of_seconds: float
    cards: list[DetectedEventCard] = Field(default_factory=list)


class IntersectionPrediction(BaseModel):
    current_vehicle_count: float
    predicted_vehicle_count: float
    delta: float
    delta_ratio: float | None = None


class PredictionPayload(BaseModel):
    horizon_seconds: float
    as_of_seconds: float
    model: str
    model_version: str = ""
    ready: bool = False
    fallback: bool = False
    fallback_reason: str = ""
    inference_latency_ms: float | None = None
    intersections: dict[str, IntersectionPrediction] = Field(default_factory=dict)


class EdgeTrafficStyle(BaseModel):
    level: str
    score: float
    mean_speed: float
    occupancy_pct: float = Field(description="路段占有率百分数，口径0～100")
    occupancy: float | None = Field(
        default=None,
        description="兼容字段，数值与occupancy_pct相同",
    )
    vehicle_count: int
    halting_count: int


class TrafficStylePayload(BaseModel):
    as_of_seconds: float
    edges: dict[str, EdgeTrafficStyle] = Field(default_factory=dict)


class IntelligencePayload(BaseModel):
    event_detection: EventDetectionPayload
    prediction: PredictionPayload
    traffic_style: TrafficStylePayload
    raw: dict[str, Any] | None = None
