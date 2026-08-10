# algorithms/v2x/entities.py
"""车/路实体与通信能力判定（显式字段优先，vehicle_class 仅兼容默认）。"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Mapping, Optional

from .config import V2XConfig
from .messages import stable_hash01

DEFAULT_V2X_CAPABILITY = {
    "passenger": True,
    "bus": True,
    "truck": False,
    "bicycle": False,
    "pedestrian": False,
}


@dataclass(frozen=True, slots=True)
class VehicleCapability:
    vehicle_class: str
    v2x_enabled: bool
    obu_type: Optional[str] = None


@dataclass(frozen=True, slots=True)
class Vehicle:
    vehicle_id: str
    type_id: str
    vehicle_class: str
    v2x_enabled: bool
    obu_type: Optional[str] = None


@dataclass(frozen=True, slots=True)
class RSU:
    rsu_id: str
    covered_lane_ids: frozenset
    position: Optional[tuple[float, float]] = None


def resolve_v2x_enabled(
    *,
    vehicle_id: str,
    vehicle_class: str,
    explicit: Optional[bool],
    type_v2x: Optional[bool],
    config: V2XConfig,
) -> bool:
    if explicit is not None:
        return bool(explicit)
    if type_v2x is not None:
        return bool(type_v2x)
    if vehicle_class in config.connected_classes:
        score = stable_hash01(f"{config.capability_seed}|{vehicle_id}")
        return score < config.penetration_rate
    return bool(DEFAULT_V2X_CAPABILITY.get(vehicle_class, False))


def build_rsu_covered_lanes(
    protocol_lanes: Mapping[str, frozenset],
    extra_lanes: Mapping[str, frozenset],
) -> dict[str, frozenset]:
    rsu_ids = set(protocol_lanes) | set(extra_lanes)
    return {
        rid: frozenset(protocol_lanes.get(rid, ())) | frozenset(extra_lanes.get(rid, ()))
        for rid in rsu_ids
    }
