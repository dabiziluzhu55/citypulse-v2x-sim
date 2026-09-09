"""Shared, versioned interfaces. Owned by the coordinator, not by workers."""
from __future__ import annotations

from dataclasses import dataclass
import json
import math
from typing import Any, Mapping

import numpy as np

SCHEMA_VERSION = "cv_joint_v1.0"
HORIZON = 900.0
CLOUD_INTERVAL = 15.0
CALLBACK_INTERVAL = 5.0
CONTEXT_DIM = 3300
BASE_VEHICLE_DIM = 42
VEHICLE_DIM = 45
MOVEMENT_DIM = 65
LANE_SLOTS = 4
PERIODS = ("morning_peak", "off_peak", "evening_peak")
PERMISSION_INDEX = 42
MESSAGE_VALID_INDEX = 43
MESSAGE_AGE_INDEX = 44

BASE_FEATURE_NAMES = (
    "eta_over_20", "actuation_group_over_10", "movement_demand_over_20",
    "movement_halted_over_20", "movement_moving_over_20", "halted_tail_over_250",
    "approaching_gap_over_100", "speed_over_native_ceiling",
    "left", "right", "through", "phase_age_over_20", "ippo_margin",
    "ippo_cooldown_over_15", "signal_G", "signal_g", "signal_y", "signal_r",
    "signal_unknown", "remaining_fraction", "eta_valid", "native_ceiling_valid",
) + tuple(f"tls_demo_{i}" for i in range(1, 21))


def float_vector(value: Any, size: int, label: str) -> np.ndarray:
    result = np.array(value, dtype=np.float32, copy=True)
    if result.shape != (size,) or not np.isfinite(result).all():
        raise ValueError(f"{label} must be a finite vector of length {size}")
    result.setflags(write=False)
    return result


@dataclass(frozen=True, order=True)
class MovementKey:
    tls_id: str
    from_edge: str
    to_edge: str

    def __post_init__(self):
        if any(not isinstance(x, str) or not x.strip()
               for x in (self.tls_id, self.from_edge, self.to_edge)):
            raise ValueError("movement requires non-empty TLS, from-edge and to-edge IDs")

    @property
    def token(self) -> str:
        return json.dumps([self.tls_id, self.from_edge, self.to_edge], separators=(",", ":"))

    @classmethod
    def from_token(cls, value: str) -> "MovementKey":
        parts = json.loads(value)
        if not isinstance(parts, list) or len(parts) != 3:
            raise ValueError("invalid movement token")
        return cls(*parts)


@dataclass(frozen=True)
class Leader:
    vehicle_id: str
    movement: MovementKey
    movement_index: int
    lane_id: str
    base_obs: np.ndarray
    lane_mask: np.ndarray
    lane_targets: tuple[int | None, ...]
    lane_target_ids: tuple[str | None, ...]
    native_ceiling: float | None
    speed_eligible: bool
    members: tuple[str, ...]

    def __post_init__(self):
        object.__setattr__(self, "base_obs", float_vector(self.base_obs, BASE_VEHICLE_DIM, "base_obs"))
        mask = np.asarray(self.lane_mask)
        if mask.dtype != np.bool_ or mask.shape != (LANE_SLOTS,) or not mask[0]:
            raise ValueError("lane mask must be boolean, length four, with KEEP legal")
        mask = mask.copy(); mask.setflags(write=False)
        object.__setattr__(self, "lane_mask", mask)
        if len(self.lane_targets) != LANE_SLOTS or len(self.lane_target_ids) != LANE_SLOTS:
            raise ValueError("lane targets must have four slots")
        if self.lane_targets[0] is not None or self.lane_target_ids[0] is not None:
            raise ValueError("KEEP cannot emit a lane command")
        active_ids = []
        for slot in range(1, LANE_SLOTS):
            if mask[slot]:
                target = self.lane_targets[slot]
                if not isinstance(target, int) or target < 0 or not self.lane_target_ids[slot]:
                    raise ValueError("legal lane slot requires an absolute index and lane ID")
                active_ids.append(self.lane_target_ids[slot])
        if len(active_ids) != len(set(active_ids)):
            raise ValueError("duplicate active lane targets")
        if self.movement_index < 0:
            raise ValueError("movement index must be non-negative")
        if self.speed_eligible:
            if self.native_ceiling is None or not math.isfinite(float(self.native_ceiling)) or float(self.native_ceiling) <= 0:
                raise ValueError("speed-eligible leader needs a positive finite native ceiling")
            if self.base_obs[21] != 1:
                raise ValueError("speed eligibility disagrees with native-ceiling validity")


@dataclass(frozen=True)
class FeatureSnapshot:
    episode_id: str
    step_id: int
    simulation_time: float
    context: np.ndarray
    leaders: Mapping[str, Leader]
    movements: Mapping[MovementKey, np.ndarray]
    road_states: Mapping[str, Mapping[str, Any]]
    vehicle_states: Mapping[str, Mapping[str, Any]]

    def __post_init__(self):
        if not math.isfinite(float(self.simulation_time)) or self.simulation_time < 0:
            raise ValueError("invalid snapshot time")
        object.__setattr__(self, "context", float_vector(self.context, CONTEXT_DIM, "context"))
        object.__setattr__(self, "movements", {
            key: float_vector(value, MOVEMENT_DIM, "movement_obs")
            for key, value in self.movements.items()
        })


def vehicle_observation(leader: Leader, permit: bool, message_valid: bool,
                        message_age_seconds: float | None) -> np.ndarray:
    if message_valid:
        age = float(message_age_seconds)
        if not math.isfinite(age) or not 0 <= age < CLOUD_INTERVAL:
            raise ValueError("valid message age must be in [0, 15)")
        tail = [float(bool(permit)), 1., age / CLOUD_INTERVAL]
    else:
        tail = [0., 0., 1.]
    return np.concatenate([leader.base_obs, np.asarray(tail, np.float32)])
