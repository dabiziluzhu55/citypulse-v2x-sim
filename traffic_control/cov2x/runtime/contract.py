"""Frozen CoV2X MVP contracts shared by runtime and SCREEN."""
from __future__ import annotations

from dataclasses import dataclass
from hashlib import sha256
from typing import Mapping, Sequence

import numpy as np

DESIGN_STATUS = "FROZEN"
IMPLEMENTATION_STATUS = "GO"
CANDIDATE_ID = "cov2x_movement_approach_corridor_v1"
BASELINE_ID = "strong_b:frozen_reference"
CHECKPOINT_FORMAT_VERSION = 7
VEHICLE_ACTION_SEMANTICS = "incremental_reference_speed_cap_v2"
PARENT_CANDIDATE_ID = "cov2x_vehicle_speed_advice_v1"
PARENT_CHECKPOINT_FORMAT_VERSION = 6
PARENT_CHECKPOINT_GENERATION = 5
PARENT_CHECKPOINT_SHA256 = (
    "76bdc08feef90115d12c7fd32d8fa77665e65ae6b891b226df24c4017442d22d"
)
DELTA_V_MAX_SPEED_CEILING_FRACTION = 0.10
# Compatibility name for historical code.  V7 sources the value from the
# vehicle-specific motion ceiling, never from an intersection lane snapshot.
DELTA_V_MAX_LANE_SPEED_FRACTION = DELTA_V_MAX_SPEED_CEILING_FRACTION
NATIVE_RELEASE_TOLERANCE_MPS = 0.05
CRITIC_LINEAGE = "reset_movement_context_v1"
FROZEN_ACTOR_ROLES = ("cloud", "road")
SCREEN_DELTA_R = (0.5, 1.0, 1.5)
SHAPING_COEFFICIENTS = {
    "arrival": 0.0, "braking": 0.0, "halting": 0.0,
    "waiting": 0.0, "speed": 0.0,
}


def normalized_mp_scores(
    pressures: Mapping[int | str, float],
    *,
    legal_phases: Sequence[int | str] | None = None,
    epsilon: float = 1e-8,
) -> dict[int, float]:
    """Frozen min-max MP prior over safe/legal phases."""
    legal = tuple(int(p) for p in (legal_phases if legal_phases is not None else pressures))
    values = {phase: float(pressures.get(phase, pressures.get(str(phase), 0.0))) for phase in legal}
    if not values:
        return {}
    minimum, maximum = min(values.values()), max(values.values())
    span = maximum - minimum
    if span <= float(epsilon):
        return {phase: 0.0 for phase in values}
    return {phase: (value - minimum) / (span + float(epsilon)) for phase, value in values.items()}


def q_gap(scores: Mapping[int, float]) -> float:
    ordered = sorted((float(value) for value in scores.values()), reverse=True)
    return max(0.0, ordered[0] - ordered[1]) if len(ordered) > 1 else 0.0


def override_reachable(scores: Mapping[int, float], delta_R: float) -> bool:
    """Exact P0 condition: 4*q_gap <= 2*delta_R."""
    if float(delta_R) < 0.0:
        raise ValueError("delta_R must be non-negative")
    return 4.0 * q_gap(scores) <= 2.0 * float(delta_R) + 1e-12


@dataclass(frozen=True)
class AuthorityCalibration:
    delta_R: float
    reachable_rate: float
    sample_count: int
    config_hash: str


def calibrate_delta_R(
    score_sets: Sequence[Mapping[int, float]],
    *,
    candidates: Sequence[float] = SCREEN_DELTA_R,
    lower: float = 0.25,
    upper: float = 0.50,
) -> AuthorityCalibration:
    if not score_sets:
        raise ValueError("authority calibration needs at least one score set")
    for value in candidates:
        rate = sum(override_reachable(scores, value) for scores in score_sets) / len(score_sets)
        if float(lower) <= rate <= float(upper):
            body = "|".join(f"{float(value):.8g}:{q_gap(scores):.8g}" for scores in score_sets)
            return AuthorityCalibration(float(value), float(rate), len(score_sets), sha256(body.encode()).hexdigest())
    raise ValueError("no delta_R candidate satisfies the 25%-50% reachable-rate contract")


def road_logits(
    q_scores: Mapping[int, float],
    residuals: Mapping[int, float] | None = None,
    *,
    delta_R: float,
    legal_phases: Sequence[int] | None = None,
) -> dict[int, float]:
    """Compose Road logits ell_k=4*q_k+r_k with bounded residuals."""
    legal = tuple(int(p) for p in (legal_phases if legal_phases is not None else q_scores))
    residuals = residuals or {}
    output: dict[int, float] = {}
    for phase in legal:
        residual = float(residuals.get(phase, 0.0))
        if abs(residual) > float(delta_R) + 1e-8:
            raise ValueError("Road residual exceeds delta_R")
        output[phase] = 4.0 * float(q_scores.get(phase, 0.0)) + residual
    return output


@dataclass(frozen=True)
class VehicleProfile:
    accel_mps2: float
    decel_mps2: float
    hard_braking_mps2: float


DEFAULT_VEHICLE_PROFILES = {
    "passenger": VehicleProfile(2.6, 4.5, -3.0),
    "electric_bicycle": VehicleProfile(1.5, 3.0, -2.0),
    "bus": VehicleProfile(1.2, 4.5, -2.5),
    "truck": VehicleProfile(1.3, 4.5, -2.5),
}


def project_acceleration(
    requested_mps2: float,
    *,
    profile: VehicleProfile,
    safe_lower_mps2: float,
    safe_upper_mps2: float,
    epsilon: float = 1e-6,
) -> tuple[float | None, str]:
    """Project a policy residual into A_safe intersect A_policy."""
    policy_lower = max(-float(profile.decel_mps2), float(profile.hard_braking_mps2) + epsilon)
    policy_upper = float(profile.accel_mps2)
    lower, upper = max(float(safe_lower_mps2), policy_lower), min(float(safe_upper_mps2), policy_upper)
    if lower > upper + epsilon:
        return None, "empty_intersection"
    value = min(max(float(requested_mps2), lower), upper)
    if value < float(profile.hard_braking_mps2) + epsilon:
        return None, "hard_braking"
    return value, "projected" if abs(value - float(requested_mps2)) > epsilon else "accepted"


def freeze_config_hash(config: Mapping[str, object]) -> str:
    canonical = repr(sorted((str(k), repr(v)) for k, v in config.items()))
    return sha256(canonical.encode("utf-8")).hexdigest()

# Final Vehicle-local-credit candidate. These constants do not mutate the
# historical format-v7 corridor/G30 contract above.
LOCAL_CREDIT_CANDIDATE_ID = "cov2x_vehicle_local_credit_final_v1"
LOCAL_CREDIT_CHECKPOINT_FORMAT_VERSION = 8
LOCAL_CREDIT_G30_PARENT_CANDIDATE_ID = CANDIDATE_ID
LOCAL_CREDIT_G30_PARENT_CHECKPOINT_FORMAT_VERSION = CHECKPOINT_FORMAT_VERSION
LOCAL_CREDIT_G30_PARENT_CHECKPOINT_GENERATION = 30
LOCAL_CREDIT_G30_PARENT_CHECKPOINT_SHA256 = (
    "8f6674465ce44150b83e5e4789ccecdddb7471e39e35a4b9eb40801e44dfe271"
)
LOCAL_CREDIT_G30_SEMANTIC_FINGERPRINT_SHA256 = (
    "f0b5b7faa37e9fd9dbffa4b07c9850dcd3c0f64bfa4770a1d50de028b385f817"
)
LOCAL_CREDIT_CRITIC_LINEAGE = "fresh_role_intersection_movement_context_v1"
LOCAL_CREDIT_REWARD_SEMANTICS = "movement_local_time_loss_rate_per_vehicle_v1"
LOCAL_CREDIT_INITIAL_DETERMINISTIC_MEAN = -0.25
LOCAL_CREDIT_ACTOR_UPDATE_SCHEDULE_ID = "vehicle_only_gain_1_3_2_3_1_x6_v1"

# Final temporary base-relative speed-cap candidate. Historical incremental
# candidates and their checkpoint contracts above remain immutable.
TEMPORARY_SPEED_CAP_CANDIDATE_ID = "cov2x_temporary_speed_cap_final_v1"
TEMPORARY_SPEED_CAP_CHECKPOINT_FORMAT_VERSION = 9
TEMPORARY_SPEED_CAP_ACTION_SEMANTICS = "temporary_base_relative_speed_cap_v1"
TEMPORARY_SPEED_CAP_INITIAL_DETERMINISTIC_MEAN = 0.0
TEMPORARY_SPEED_CAP_ACTOR_UPDATE_SCHEDULE_ID = (
    "temporary_base_relative_gain_1_3_2_3_1_x6_v1"
)
TEMPORARY_SPEED_CAP_EXTENDED_ACTOR_UPDATE_SCHEDULE_ID = (
    "temporary_base_relative_gain_1_3_2_3_1_x22_v1"
)
TEMPORARY_SPEED_CAP_THREE_SCOPE_ACTOR_UPDATE_SCHEDULE_ID = (
    "temporary_base_relative_three_scope_latin_gain_1_3_2_3_1_x22_v1"
)
