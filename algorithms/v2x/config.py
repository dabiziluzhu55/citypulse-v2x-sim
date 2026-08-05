# algorithms/v2x/config.py
"""V2X 框架配置：通信周期、能力、网络随机与 RSU 覆盖。"""
from __future__ import annotations

import math
from dataclasses import dataclass, field
from typing import Mapping, Optional

UPSTREAM_TYPES = frozenset({"BSM", "INTENT", "SPaT", "MAP", "RSM"})
DOWNSTREAM_TYPES = frozenset({"RSI", "SIGNAL_CONTROL"})


class V2XConfigError(ValueError):
    pass


@dataclass(frozen=True, slots=True)
class V2XConfig:
    schema_version: str = "1.0"

    bsm_interval_s: float = 5.0
    intent_interval_s: float = 5.0
    spat_interval_s: float = 5.0
    rsm_interval_s: float = 5.0
    scheduling_epsilon_s: float = 1e-9

    connected_classes: frozenset = frozenset({"passenger", "bus"})
    penetration_rate: float = 1.0
    capability_seed: int = 0

    default_latency_ms: float = 20.0
    uplink_latency_ms: Optional[float] = None
    downlink_latency_ms: Optional[float] = None
    latency_jitter_ms: float = 0.0
    drop_rate: float = 0.0
    network_seed: int = 0

    detection_radius_m: Optional[float] = None

    def __post_init__(self) -> None:
        if not isinstance(self.capability_seed, int) or isinstance(self.capability_seed, bool):
            raise V2XConfigError("capability_seed must be int")
        if not isinstance(self.network_seed, int) or isinstance(self.network_seed, bool):
            raise V2XConfigError("network_seed must be int")
        if not isinstance(self.connected_classes, frozenset):
            raise V2XConfigError("connected_classes must be frozenset")
        if not (0.0 <= self.penetration_rate <= 1.0):
            raise V2XConfigError("penetration_rate must be in [0, 1]")
        if not (0.0 <= self.drop_rate <= 1.0):
            raise V2XConfigError("drop_rate must be in [0, 1]")
        for name in (
            "bsm_interval_s", "intent_interval_s", "spat_interval_s",
            "rsm_interval_s", "scheduling_epsilon_s", "default_latency_ms",
            "latency_jitter_ms", "uplink_latency_ms", "downlink_latency_ms",
        ):
            value = getattr(self, name)
            if value is not None and (not math.isfinite(float(value)) or math.isnan(float(value))):
                raise V2XConfigError(f"{name} must be finite")
        for name in ("default_latency_ms", "latency_jitter_ms",
                     "uplink_latency_ms", "downlink_latency_ms"):
            value = getattr(self, name)
            if value is not None and float(value) < 0.0:
                raise V2XConfigError(f"{name} must be >= 0")
        if self.detection_radius_m is not None and self.detection_radius_m <= 0.0:
            raise V2XConfigError("detection_radius_m must be > 0 when set")

    def to_dict(self) -> dict:
        return {
            "schema_version": self.schema_version,
            "bsm_interval_s": self.bsm_interval_s,
            "intent_interval_s": self.intent_interval_s,
            "spat_interval_s": self.spat_interval_s,
            "rsm_interval_s": self.rsm_interval_s,
            "connected_classes": sorted(self.connected_classes),
            "penetration_rate": self.penetration_rate,
            "capability_seed": self.capability_seed,
            "default_latency_ms": self.default_latency_ms,
            "uplink_latency_ms": self.uplink_latency_ms,
            "downlink_latency_ms": self.downlink_latency_ms,
            "latency_jitter_ms": self.latency_jitter_ms,
            "drop_rate": self.drop_rate,
            "network_seed": self.network_seed,
            "detection_radius_m": self.detection_radius_m,
        }

    def interval_for(self, message_type: str) -> float:
        return {
            "BSM": self.bsm_interval_s,
            "INTENT": self.intent_interval_s,
            "SPaT": self.spat_interval_s,
            "RSM": self.rsm_interval_s,
        }.get(message_type, 0.0)

    def latency_ms_for(self, message_type: str) -> float:
        if message_type in DOWNSTREAM_TYPES:
            return self.downlink_latency_ms if self.downlink_latency_ms is not None else self.default_latency_ms
        return self.uplink_latency_ms if self.uplink_latency_ms is not None else self.default_latency_ms


@dataclass(frozen=True, slots=True)
class RSUCoverageConfig:
    positions: Mapping[str, tuple[float, float]] = field(default_factory=dict)
    extra_covered_lane_ids: Mapping[str, frozenset] = field(default_factory=dict)
