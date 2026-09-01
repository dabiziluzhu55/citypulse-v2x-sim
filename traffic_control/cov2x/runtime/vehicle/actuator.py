"""Pure Vehicle actuator contracts for the ACTUATOR_BLOCKER_v1 screen.

The policy action remains an acceleration command held for five seconds.  The
adapter is the only variable: ``one_shot`` freezes the five-second target,
whereas ``micro_step`` rebuilds the next-step speed reference from the latest
realized speed.  This module has no PPO, Cloud, Road, reward, or TraCI imports.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
import math
from typing import Any, Mapping


REALIZATION_ABS_TOLERANCE_MPS2 = 0.25
SIGNAL_REACTION_SECONDS = 1.0
LEADER_HEADWAY_SECONDS = 1.5
MIN_CONSTRAINT_DISTANCE_M = 12.0


class AdapterMode(str, Enum):
    ONE_SHOT = "one_shot"
    MICRO_STEP = "micro_step"


class ConstraintState(str, Enum):
    FREE_FLOW = "free_flow"
    LEADER_LIMITED = "leader_limited"
    SIGNAL_LIMITED = "signal_limited"
    UNCLASSIFIED = "unclassified"


@dataclass(frozen=True)
class VehicleLimits:
    accel_mps2: float
    decel_mps2: float
    min_gap_m: float
    max_speed_mps: float


@dataclass(frozen=True)
class StateClassification:
    state: ConstraintState
    evidence: str


@dataclass(frozen=True)
class SpeedReference:
    target_speed_mps: float
    unclipped_target_speed_mps: float
    clipped: bool


def vehicle_limits(
    vehicle: Mapping[str, Any], vehicle_types: Mapping[str, Any]
) -> VehicleLimits:
    type_id = str(vehicle.get("type_id", ""))
    raw = vehicle_types.get(type_id) or {}
    return VehicleLimits(
        accel_mps2=max(1e-6, float(raw.get("accel_mps2", 2.0))),
        decel_mps2=max(1e-6, float(raw.get("decel_mps2", 4.5))),
        min_gap_m=max(0.0, float(raw.get("min_gap_m", 2.5))),
        max_speed_mps=max(0.0, float(raw.get("max_speed_mps", 55.0))),
    )


def classify_constraint(
    vehicle: Mapping[str, Any], limits: VehicleLimits
) -> StateClassification:
    """Classify observable constraints without claiming SUMO internals.

    Signal evidence takes precedence because a queue leader near a restrictive
    signal is ultimately signal-limited.  The result is an observable proxy;
    exact car-following clamp internals are intentionally not fabricated.
    """

    motion = vehicle.get("motion") or {}
    speed = max(0.0, float(motion.get("speed_mps", 0.0)))
    location = vehicle.get("location") or {}
    road_id = str(location.get("road_id", ""))
    traffic = vehicle.get("traffic") or {}
    waiting_time = max(0.0, float(traffic.get("waiting_time_s", 0.0)))
    if road_id.startswith(":"):
        return StateClassification(
            ConstraintState.UNCLASSIFIED, "internal_edge_right_of_way_context"
        )
    if waiting_time > 0.1:
        return StateClassification(
            ConstraintState.UNCLASSIFIED,
            f"native_waiting_context={waiting_time:.3f}",
        )
    if speed < 0.4:
        return StateClassification(
            ConstraintState.UNCLASSIFIED,
            f"near_stationary_without_observable_clearance:speed={speed:.3f}",
        )
    next_signal = vehicle.get("next_signal")
    if isinstance(next_signal, Mapping):
        signal_state = str(next_signal.get("state", ""))
        signal_distance = max(0.0, float(next_signal.get("distance_m", 0.0)))
        restrictive = signal_state not in {"G", "g"}
        stopping_distance = (
            speed * SIGNAL_REACTION_SECONDS
            + speed * speed / (2.0 * limits.decel_mps2)
        )
        signal_horizon = max(MIN_CONSTRAINT_DISTANCE_M, stopping_distance)
        if restrictive and signal_distance <= signal_horizon:
            return StateClassification(
                ConstraintState.SIGNAL_LIMITED,
                f"restrictive_signal:{signal_state}:distance={signal_distance:.3f}",
            )

    leader_gap = vehicle.get("leader_gap_m")
    if leader_gap is not None:
        gap = max(0.0, float(leader_gap))
        following_horizon = limits.min_gap_m + speed * LEADER_HEADWAY_SECONDS
        if gap <= max(MIN_CONSTRAINT_DISTANCE_M, following_horizon):
            return StateClassification(
                ConstraintState.LEADER_LIMITED,
                f"leader_gap={gap:.3f}:horizon={following_horizon:.3f}",
            )

    allowed = max(0.0, float(motion.get("allowed_speed_mps", limits.max_speed_mps)))
    if math.isfinite(speed) and math.isfinite(allowed) and allowed > 0.0:
        return StateClassification(
            ConstraintState.FREE_FLOW,
            f"no_near_observable_constraint:allowed={allowed:.3f}",
        )
    return StateClassification(ConstraintState.UNCLASSIFIED, "invalid_speed_envelope")


def scripted_acceleration(state: ConstraintState, policy_generation: int) -> float:
    """Deterministic, bounded commands shared by both paired adapter arms."""

    if state == ConstraintState.FREE_FLOW:
        cycle = (1.0, -1.0, 0.5, -0.5)
        return cycle[int(policy_generation) % len(cycle)]
    if state in {ConstraintState.LEADER_LIMITED, ConstraintState.SIGNAL_LIMITED}:
        return 1.0
    return 0.0


def eligible_for_command(
    vehicle: Mapping[str, Any],
    limits: VehicleLimits,
    state: ConstraintState,
    acceleration_mps2: float,
    policy_cadence_s: float,
) -> bool:
    motion = vehicle.get("motion") or {}
    speed = max(0.0, float(motion.get("speed_mps", 0.0)))
    allowed = min(
        limits.max_speed_mps,
        max(0.0, float(motion.get("allowed_speed_mps", limits.max_speed_mps))),
    )
    if state == ConstraintState.FREE_FLOW:
        semantic_delta = acceleration_mps2 * policy_cadence_s
        if semantic_delta > 0.0 and allowed - speed < semantic_delta + 0.5:
            return False
        if semantic_delta < 0.0 and speed < abs(semantic_delta) + 0.5:
            return False
    return 0.0 < speed or acceleration_mps2 > 0.0


def speed_reference(
    *,
    mode: AdapterMode,
    realized_speed_mps: float,
    policy_start_speed_mps: float,
    acceleration_mps2: float,
    step_length_s: float,
    policy_cadence_s: float,
    max_speed_mps: float,
) -> SpeedReference:
    horizon = policy_cadence_s if mode == AdapterMode.ONE_SHOT else step_length_s
    base = policy_start_speed_mps if mode == AdapterMode.ONE_SHOT else realized_speed_mps
    raw = base + acceleration_mps2 * horizon
    target = min(max(0.0, raw), max(0.0, max_speed_mps))
    return SpeedReference(target, raw, abs(target - raw) > 1e-9)


def realized_acceleration(
    previous_speed_mps: float, current_speed_mps: float, delta_seconds: float
) -> float:
    if delta_seconds <= 0.0:
        raise ValueError("delta_seconds must be positive")
    return (float(current_speed_mps) - float(previous_speed_mps)) / float(delta_seconds)


def transfer_gain(realized_mps2: float, requested_mps2: float) -> float | None:
    if abs(float(requested_mps2)) <= 1e-9:
        return None
    return float(realized_mps2) / float(requested_mps2)


def is_realized(realized_mps2: float, requested_mps2: float) -> bool:
    tolerance = max(
        REALIZATION_ABS_TOLERANCE_MPS2,
        0.20 * abs(float(requested_mps2)),
    )
    return abs(float(realized_mps2) - float(requested_mps2)) <= tolerance


def outcome_reason(
    *,
    accepted: bool,
    status: str | None,
    realized: bool,
    state: ConstraintState,
    reference_clipped: bool,
    requested_mps2: float,
    realized_mps2: float | None,
    limits: VehicleLimits,
) -> tuple[str | None, str]:
    """Return ``(native_safety_clamp_reason, outcome_reason)``.

    Clamp reasons are evidence-backed categories, not direct reads of SUMO's
    private car-following decision.  ``outcome_reason`` retains adapter errors
    separately so misses are never collapsed into one generic bucket.
    """

    if not accepted:
        reason = "vehicle_arrived" if status == "vehicle_arrived" else "command_rejected"
        return None, reason
    if realized:
        return None, "realized_within_tolerance"
    if reference_clipped:
        return "speed_envelope", "native_speed_envelope_clamp"
    if state == ConstraintState.SIGNAL_LIMITED:
        return "signal_safety", "native_signal_safety_clamp"
    if state == ConstraintState.LEADER_LIMITED:
        return "leader_safety", "native_car_following_clamp"
    if realized_mps2 is not None:
        if requested_mps2 > 0.0 and realized_mps2 >= 0.90 * limits.accel_mps2:
            return "accel_limit", "native_acceleration_limit"
        if requested_mps2 < 0.0 and realized_mps2 <= -0.90 * limits.decel_mps2:
            return "decel_limit", "native_deceleration_limit"
    return None, "unconstrained_tracking_error"
