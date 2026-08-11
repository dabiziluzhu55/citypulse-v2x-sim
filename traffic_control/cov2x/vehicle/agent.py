"""Vehicle/approach agent contract for the CoV2X framework.

Identity: approach advisor. There is one fixed slot per incoming edge of a
controlled intersection; a slot selects one candidate vehicle (the semantics
of ``algorithms/cov2x/road/lane_state.py``). Parameters are shared within the
homogeneous approach-agent population.

Execution semantics (Protocol 2.0):
- decision interval: 5 s (configurable);
- action lease: one decision interval; omitting speed resumes SUMO
  autonomous speed, lane change is not renewed;
- safety mask and command validation live at the execution boundary;
  this module validates protocol-facing fields but is not a safety layer.

Status: contract + reward only. No policy in this package is trained yet;
do not claim vehicle-side learning until a policy and training loop exist.
"""

from __future__ import annotations

import abc
from dataclasses import dataclass
from typing import Optional

import numpy as np

from traffic_control.cov2x.vehicle.rewards import (
    GUIDE_ZONE_MAX_M,
    MAX_ACCEL_MPS2_DEFAULT,
    VehicleRewardInputs,
    VehicleRewardWeights,
)

LANE_ACTION_KEEP = 0
LANE_ACTION_LEFT = 1
LANE_ACTION_RIGHT = 2
SPEED_FRACTIONS = (0.0, 0.25, 0.5, 0.75, 1.0)


@dataclass(frozen=True)
class VehicleAgentConfig:
    """Shared configuration for the approach-advisor agent family."""

    decision_interval_s: float = 5.0
    max_accel_mps2: float = MAX_ACCEL_MPS2_DEFAULT
    guide_zone_max_m: float = GUIDE_ZONE_MAX_M
    reward_weights: VehicleRewardWeights = VehicleRewardWeights()


@dataclass(frozen=True)
class VehicleAction:
    """One protocol-facing vehicle command for one decision interval."""

    target_speed_mps: Optional[float] = None
    target_lane_index: Optional[int] = None
    source: str = "learned"  # learned | rule | safety

    def to_protocol_dict(self) -> dict[str, float | int]:
        """Serialize to the ``actions.vehicles[vehicle_id]`` payload."""
        out: dict[str, float | int] = {}
        if self.target_speed_mps is not None:
            out["target_speed_mps"] = float(self.target_speed_mps)
        if self.target_lane_index is not None:
            out["target_lane_index"] = int(self.target_lane_index)
        return out


class VehicleActionError(ValueError):
    """Raised when a vehicle action violates Protocol 2.0 field rules."""


def validate_vehicle_action(
    action: VehicleAction,
    *,
    allowed_speed_mps: float,
    road_lane_indices: tuple[int, ...],
) -> dict[str, float | int]:
    """Validate and serialize an action against Protocol 2.0 rules.

    Rules enforced here:
    - at least one of speed/lane must be set (interface doc §3.4);
    - ``target_speed_mps`` in ``[0, allowed_speed_mps]`` (interface doc §3.5);
    - ``target_lane_index`` must be on the current road (interface doc §3.6).
    """
    if action.target_speed_mps is None and action.target_lane_index is None:
        raise VehicleActionError("vehicle action must set speed and/or lane")

    if action.target_speed_mps is not None:
        speed = float(action.target_speed_mps)
        allowed = float(allowed_speed_mps)
        if not 0.0 <= speed <= allowed + 1e-9:
            raise VehicleActionError(
                f"target_speed_mps {speed} outside [0, {allowed}]"
            )

    if action.target_lane_index is not None:
        lane = int(action.target_lane_index)
        allowed_lanes = {int(index) for index in road_lane_indices}
        if lane not in allowed_lanes:
            raise VehicleActionError(
                f"target_lane_index {lane} not on current road lanes {sorted(allowed_lanes)}"
            )

    return action.to_protocol_dict()


@dataclass(frozen=True)
class VehicleObservation:
    """Per-slot observation for one approach advisor decision."""

    vehicle_id: str
    tls_id: str
    speed_mps: float
    accel_mps2: float
    allowed_speed_mps: float
    dist_to_stopline_m: float
    phase_is_green: bool
    signal_remaining_s: float
    time_to_next_green_s: Optional[float] = None
    lane_index: Optional[int] = None
    road_lane_indices: tuple[int, ...] = ()
    previous_lane_change_success: Optional[bool] = None
    state_41: Optional[np.ndarray] = None
    action_mask: Optional[np.ndarray] = None
    cloud_context: Optional[np.ndarray] = None
    edge_id: str = ""
    slot_index: int = -1
    waiting_time_s: float = 0.0

    def to_reward_inputs(
        self,
        *,
        max_accel_mps2: float = MAX_ACCEL_MPS2_DEFAULT,
        dist_to_stopline_max_m: float = GUIDE_ZONE_MAX_M,
        weights: Optional[VehicleRewardWeights] = None,
    ) -> VehicleRewardInputs:
        return VehicleRewardInputs(
            speed_mps=self.speed_mps,
            accel_mps2=self.accel_mps2,
            dist_to_stopline_m=self.dist_to_stopline_m,
            phase_is_green=self.phase_is_green,
            signal_remaining_s=self.signal_remaining_s,
            max_speed_mps=self.allowed_speed_mps,
            max_accel_mps2=max_accel_mps2,
            dist_to_stopline_max_m=dist_to_stopline_max_m,
            waiting_time_s=self.waiting_time_s,
            weights=weights or VehicleRewardWeights(),
        )


class VehicleAgent(abc.ABC):
    """Abstract shared approach-advisor policy across the vehicle population.

    Concrete implementations must return actions that pass
    :func:`validate_vehicle_action`; safety override remains outside the
    policy at the execution boundary.
    """

    def __init__(self, config: VehicleAgentConfig) -> None:
        self.config = config

    @property
    def decision_interval_s(self) -> float:
        return self.config.decision_interval_s

    @abc.abstractmethod
    def decide(self, obs: VehicleObservation) -> VehicleAction:
        """Return one validated action for one approach slot."""

    @abc.abstractmethod
    def reset(self, episode_id: str) -> None:
        """Reset policy-private state at episode start."""
