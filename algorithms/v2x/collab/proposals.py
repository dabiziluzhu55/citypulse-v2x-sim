# algorithms/v2x/collab/proposals.py
"""协同决策层：枚举、建议/状态模型与类型化配置（spec §1.6/§2.3/§3.4/§3.5）。"""
from __future__ import annotations

import math
from dataclasses import dataclass, field
from enum import Enum
from typing import Literal, Mapping


class DecisionMode(str, Enum):
    OFF = "off"
    SHADOW = "shadow"
    ACTIVE = "active"


class GuidanceEmissionMode(str, Enum):
    THRESHOLD = "threshold"
    FULL = "full"
    DISABLED = "disabled"


class SignalDecisionStatus(str, Enum):
    PROPOSED = "proposed"
    KEEP_CURRENT = "keep_current"
    NO_DEMAND = "no_demand"
    STALE_INPUT = "stale_input"
    MISSING_INPUT = "missing_input"
    INVALID_PROPOSAL = "invalid_proposal"
    SUPPRESSED_MIN_GREEN = "suppressed_min_green"
    SUPPRESSED_SWITCH_MARGIN = "suppressed_switch_margin"
    SUPPRESSED_TRANSITION = "suppressed_transition"


class GuidanceDecisionStatus(str, Enum):
    PROPOSED = "proposed"
    NO_ACTION_NEEDED = "no_action_needed"
    STALE_INPUT = "stale_input"
    MISSING_INPUT = "missing_input"
    INVALID_PROPOSAL = "invalid_proposal"
    SUPPRESSED_DUPLICATE = "suppressed_duplicate"
    SUPPRESSED_COOLDOWN = "suppressed_cooldown"
    SUPPRESSED_THRESHOLD = "suppressed_threshold"


class DecisionSource(str, Enum):
    BASELINE = "baseline"
    CLOUD = "cloud"
    FALLBACK = "fallback"


@dataclass(frozen=True, slots=True)
class FreshnessConfig:
    bsm_s: float = 10.0
    intent_s: float = 10.0
    spat_s: float = 10.0
    rsm_s: float = 10.0

    def __post_init__(self) -> None:
        for name in ("bsm_s", "intent_s", "spat_s", "rsm_s"):
            value = getattr(self, name)
            if not math.isfinite(value) or value <= 0.0:
                raise ValueError(f"{name} must be finite and > 0")


@dataclass(frozen=True, slots=True)
class SignalPolicyConfig:
    queue_weight: float = 1.0
    arrival_weight: float = 0.25
    forward_weight: float = 0.5
    forward_horizon_s: float = 30.0
    forward_decay_s: float = 15.0
    min_green_s: float = 5.0
    switch_score_margin: float = 1.0
    demand_epsilon: float = 1e-6
    score_epsilon: float = 1e-9
    proposal_ttl_s: float = 5.0

    def __post_init__(self) -> None:
        for name in ("queue_weight", "arrival_weight", "forward_weight"):
            value = getattr(self, name)
            if not math.isfinite(value) or value < 0.0:
                raise ValueError(f"{name} must be finite and >= 0")
        if not (self.queue_weight > 0.0 or self.arrival_weight > 0.0
                or self.forward_weight > 0.0):
            raise ValueError("at least one demand weight must be > 0")
        for name in ("forward_horizon_s", "forward_decay_s", "proposal_ttl_s"):
            value = getattr(self, name)
            if not math.isfinite(value) or value <= 0.0:
                raise ValueError(f"{name} must be finite and > 0")
        for name in ("min_green_s", "switch_score_margin"):
            value = getattr(self, name)
            if not math.isfinite(value) or value < 0.0:
                raise ValueError(f"{name} must be finite and >= 0")
        if self.demand_epsilon < 0.0 or self.score_epsilon <= 0.0:
            raise ValueError("demand_epsilon >= 0 and score_epsilon > 0 required")


@dataclass(frozen=True, slots=True)
class GuidancePolicyConfig:
    guidance_horizon_m: float = 300.0
    guidance_ttl_s: float = 10.0
    minimum_resend_interval_s: float = 5.0
    speed_trigger_delta_mps: float = 2.0
    speed_resend_delta_mps: float = 1.0
    v_min_mps: float = 0.0
    v_max_mps: float = 16.0
    speed_scale_low: float = 0.5
    speed_scale_high: float = 1.3
    lane_queue_margin: float = 2.0
    lane_change_min_distance_m: float = 30.0
    min_guidance_speed_mps: float = 0.5
    green_clearance_buffer_s: float = 1.0

    def __post_init__(self) -> None:
        if not math.isfinite(self.guidance_horizon_m) or self.guidance_horizon_m <= 0.0:
            raise ValueError("guidance_horizon_m must be finite and > 0")
        if not math.isfinite(self.guidance_ttl_s) or self.guidance_ttl_s <= 0.0:
            raise ValueError("guidance_ttl_s must be finite and > 0")
        for name in (
            "minimum_resend_interval_s", "speed_trigger_delta_mps",
            "speed_resend_delta_mps", "lane_queue_margin",
            "lane_change_min_distance_m", "min_guidance_speed_mps",
            "green_clearance_buffer_s",
        ):
            value = getattr(self, name)
            if not math.isfinite(value) or value < 0.0:
                raise ValueError(f"{name} must be finite and >= 0")
        if not (0.0 <= self.v_min_mps <= self.v_max_mps):
            raise ValueError("require 0 <= v_min_mps <= v_max_mps")
        if not (0.0 < self.speed_scale_low <= self.speed_scale_high):
            raise ValueError("require 0 < speed_scale_low <= speed_scale_high")


@dataclass(frozen=True, slots=True)
class CollabConfig:
    decision_mode: DecisionMode = DecisionMode.SHADOW
    guidance_mode: GuidanceEmissionMode = GuidanceEmissionMode.THRESHOLD
    freshness: FreshnessConfig = field(default_factory=FreshnessConfig)
    log_edge_snapshot: bool = True
    log_arbitration_mode: Literal["all", "differences"] = "all"
    signal_policy: SignalPolicyConfig = field(default_factory=SignalPolicyConfig)
    guidance_policy: GuidancePolicyConfig = field(default_factory=GuidancePolicyConfig)


@dataclass(frozen=True, slots=True)
class SignalProposal:
    intersection_id: str
    status: SignalDecisionStatus
    candidate_action: int | None
    proposed_action: int | None
    current_action: int | None
    action_scores: Mapping[int, float]
    reason: str
    confidence: float
    valid_from: float
    valid_until: float
    needs_transition: bool
    decision_frame_id: str
    source_message_ids: tuple[str, ...]
    source_frame_ids: tuple[str, ...]


@dataclass(frozen=True, slots=True)
class VehicleGuidanceProposal:
    vehicle_id: str
    status: GuidanceDecisionStatus
    speed_status: GuidanceDecisionStatus
    lane_status: GuidanceDecisionStatus
    current_speed_mps: float
    target_speed_mps: float | None
    current_lane_id: str | None
    target_lane_id: str | None
    target_lane_index: int | None
    guidance_type: str | None
    reason: str
    confidence: float | None
    valid_from: float
    valid_until: float
    source_message_ids: tuple[str, ...]
    source_frame_ids: tuple[str, ...]


@dataclass(slots=True)
class LastEmittedGuidanceState:
    """最近一次**发布**的 RSI 状态（网络可能随后丢包，不代表送达）。"""

    target_speed_mps: float | None
    target_lane_id: str | None
    target_lane_index: int | None
    emitted_at: float
    valid_until: float
    reason: str
    emitted_message_id: str
