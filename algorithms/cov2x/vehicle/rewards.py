"""Vehicle-side normalized reward, adapted from CCTV-MAC Eq. (9)-(10).

Reference:
    Zhang et al., "Cooperative Control of Traffic Signals and Vehicle
    Trajectories Using Multi-Agent Actor-Critic Approach With
    Vehicle-Road-Cloud Integration", IEEE TITS, vol. 27, no. 8, Aug. 2026,
    DOI 10.1109/TITS.2026.3681395.

This module is protocol-agnostic: callers supply measured/derived quantities
(speed, acceleration, distance to stop line, signal phase and remaining
time). Raw per-component values are kept available for logging so the reward
chain stays auditable (see develop-coslight-marl: log raw and normalized
reward components).
"""

from __future__ import annotations

from dataclasses import dataclass

MAX_ACCEL_MPS2_DEFAULT = 5.0
GUIDE_ZONE_MAX_M = 150.0
BRAKING_PENALTY_SCALE = 6.0


@dataclass(frozen=True)
class VehicleRewardWeights:
    """Weights of the four normalized objective terms (paper: ω1..ω4)."""

    speed: float = 1.0
    accel: float = 1.0
    proximity: float = 1.0
    braking: float = 1.0
    waiting: float = 0.5


@dataclass(frozen=True)
class VehicleRewardInputs:
    """Measured/derived quantities needed by the vehicle reward."""

    speed_mps: float
    accel_mps2: float
    dist_to_stopline_m: float
    phase_is_green: bool
    signal_remaining_s: float
    max_speed_mps: float
    max_accel_mps2: float = MAX_ACCEL_MPS2_DEFAULT
    dist_to_stopline_max_m: float = GUIDE_ZONE_MAX_M
    waiting_time_s: float = 0.0
    waiting_time_max_s: float = 300.0
    weights: VehicleRewardWeights = VehicleRewardWeights()


def _normalized(value: float, bound: float) -> float:
    """Normalize ``value`` into [0, 1]; zero for non-positive bounds."""
    if bound <= 0.0:
        return 0.0
    return min(max(value / bound, 0.0), 1.0)


def braking_penalty(
    speed_mps: float,
    accel_mps2: float,
    *,
    phase_is_green: bool,
    signal_remaining_s: float,
    dist_to_stopline_m: float,
    max_accel_mps2: float = MAX_ACCEL_MPS2_DEFAULT,
    scale: float = BRAKING_PENALTY_SCALE,
) -> float:
    """Paper Eq. (10): penalize braking when stopped or when green and crossable.

    Returns a non-positive value; zero means no penalty.
    """
    if accel_mps2 >= 0.0 or max_accel_mps2 <= 0.0:
        return 0.0

    stopped = speed_mps <= 0.0
    green_but_could_cross = (
        phase_is_green
        and speed_mps > 0.0
        and signal_remaining_s > 0.0
        and dist_to_stopline_m / speed_mps < signal_remaining_s
    )
    if not (stopped or green_but_could_cross):
        return 0.0
    return scale * accel_mps2 / max_accel_mps2


def vehicle_reward_components(inputs: VehicleRewardInputs) -> dict[str, float]:
    """Return every normalized component plus the total vehicle reward."""
    weights = inputs.weights
    speed_term = weights.speed * _normalized(inputs.speed_mps, inputs.max_speed_mps)
    accel_term = -weights.accel * _normalized(abs(inputs.accel_mps2), inputs.max_accel_mps2)
    proximity_term = -weights.proximity * _normalized(
        inputs.dist_to_stopline_m, inputs.dist_to_stopline_max_m
    )
    braking_term = weights.braking * braking_penalty(
        inputs.speed_mps,
        inputs.accel_mps2,
        phase_is_green=inputs.phase_is_green,
        signal_remaining_s=inputs.signal_remaining_s,
        dist_to_stopline_m=inputs.dist_to_stopline_m,
        max_accel_mps2=inputs.max_accel_mps2,
    )
    waiting_term = -weights.waiting * _normalized(
        inputs.waiting_time_s, inputs.waiting_time_max_s
    )
    total = (
        speed_term
        + accel_term
        + proximity_term
        + braking_term
        + waiting_term
    )
    return {
        "speed": speed_term,
        "accel": accel_term,
        "proximity": proximity_term,
        "braking": braking_term,
        "waiting": waiting_term,
        "total": total,
    }


def vehicle_reward(inputs: VehicleRewardInputs) -> float:
    """Scalar vehicle reward, i.e. the ``total`` component."""
    return vehicle_reward_components(inputs)["total"]
