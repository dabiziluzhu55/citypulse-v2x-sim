"""Validated policy for balancing generated SUMO route lengths by vehicle type."""

from __future__ import annotations

import json
import math
from dataclasses import dataclass
from pathlib import Path
from typing import Mapping


class TrafficGenerationPolicyError(ValueError):
    """Raised when the route-generation policy is incomplete or invalid."""


@dataclass(frozen=True)
class ProfileRoutePolicy:
    candidate_mode: str
    maximum_pair_distance_m: float | None
    maximum_local_distance_m: float | None
    minimum_pair_distance_m: float | None
    minimum_long_share: float | None
    maximum_long_share: float | None
    fleet_share_tolerance: float


@dataclass(frozen=True)
class PeriodLoadBaseline:
    vehicle_count: int
    freeflow_vehicle_seconds: float


@dataclass(frozen=True)
class TrafficGenerationPolicy:
    short_route_max_m: float
    long_route_min_m: float
    overall_long_share_max: float
    maximum_vehicle_count_multiplier: float
    maximum_freeflow_vehicle_seconds_ratio: float
    mix_calibration_rounds: int
    selection_mode: str
    profiles: Mapping[str, ProfileRoutePolicy]
    baselines: Mapping[str, PeriodLoadBaseline]


def _finite_number(value: object, context: str, *, positive: bool = False) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise TrafficGenerationPolicyError(f"{context} must be a number.")
    result = float(value)
    if not math.isfinite(result) or (positive and result <= 0):
        qualifier = "positive and finite" if positive else "finite"
        raise TrafficGenerationPolicyError(f"{context} must be {qualifier}.")
    return result


def _optional_distance(value: object, context: str) -> float | None:
    if value is None:
        return None
    return _finite_number(value, context, positive=True) * 1000.0


def _optional_share(value: object, context: str) -> float | None:
    if value is None:
        return None
    result = _finite_number(value, context)
    if not 0 <= result <= 1:
        raise TrafficGenerationPolicyError(f"{context} must be between 0 and 1.")
    return result


def load_traffic_generation_policy(path: Path) -> TrafficGenerationPolicy:
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError as exc:
        raise TrafficGenerationPolicyError(
            f"Traffic generation policy not found: {path}"
        ) from exc
    except json.JSONDecodeError as exc:
        raise TrafficGenerationPolicyError(
            f"Invalid traffic generation policy {path}: {exc}"
        ) from exc
    if not isinstance(raw, dict):
        raise TrafficGenerationPolicyError(
            "Traffic generation policy root must be an object."
        )
    if raw.get("schema_version") != 1:
        raise TrafficGenerationPolicyError(
            "Traffic generation policy schema_version must be 1."
        )

    distance = raw.get("distance_classes", {})
    short_route_max_m = _finite_number(
        distance.get("short_max_km"), "distance_classes.short_max_km", positive=True
    ) * 1000.0
    long_route_min_m = _finite_number(
        distance.get("long_min_km"), "distance_classes.long_min_km", positive=True
    ) * 1000.0
    if short_route_max_m >= long_route_min_m:
        raise TrafficGenerationPolicyError(
            "short_max_km must be smaller than long_min_km."
        )

    raw_profiles = raw.get("profiles", {})
    if not isinstance(raw_profiles, dict) or not raw_profiles:
        raise TrafficGenerationPolicyError("profiles must be a non-empty object.")
    profiles = {}
    allowed_modes = {"local_short", "local_and_near_pair", "long_pair"}
    for profile_id, item in raw_profiles.items():
        if not isinstance(item, dict):
            raise TrafficGenerationPolicyError(
                f"profiles.{profile_id} must be an object."
            )
        mode = str(item.get("candidate_mode", ""))
        if mode not in allowed_modes:
            raise TrafficGenerationPolicyError(
                f"profiles.{profile_id}.candidate_mode must be one of "
                f"{sorted(allowed_modes)}."
            )
        tolerance = _finite_number(
            item.get("fleet_share_tolerance"),
            f"profiles.{profile_id}.fleet_share_tolerance",
        )
        if not 0 <= tolerance <= 1:
            raise TrafficGenerationPolicyError(
                f"profiles.{profile_id}.fleet_share_tolerance must be between 0 and 1."
            )
        profiles[str(profile_id)] = ProfileRoutePolicy(
            candidate_mode=mode,
            maximum_pair_distance_m=_optional_distance(
                item.get("maximum_pair_distance_km"),
                f"profiles.{profile_id}.maximum_pair_distance_km",
            ),
            maximum_local_distance_m=_optional_distance(
                item.get("maximum_local_distance_km"),
                f"profiles.{profile_id}.maximum_local_distance_km",
            ),
            minimum_pair_distance_m=_optional_distance(
                item.get("minimum_pair_distance_km"),
                f"profiles.{profile_id}.minimum_pair_distance_km",
            ),
            minimum_long_share=_optional_share(
                item.get("minimum_long_share"),
                f"profiles.{profile_id}.minimum_long_share",
            ),
            maximum_long_share=_optional_share(
                item.get("maximum_long_share"),
                f"profiles.{profile_id}.maximum_long_share",
            ),
            fleet_share_tolerance=tolerance,
        )
        parsed = profiles[str(profile_id)]
        required_distance = {
            "local_short": parsed.maximum_local_distance_m,
            "local_and_near_pair": parsed.maximum_pair_distance_m,
            "long_pair": parsed.minimum_pair_distance_m,
        }[mode]
        if required_distance is None:
            required_key = {
                "local_short": "maximum_local_distance_km",
                "local_and_near_pair": "maximum_pair_distance_km",
                "long_pair": "minimum_pair_distance_km",
            }[mode]
            raise TrafficGenerationPolicyError(
                f"profiles.{profile_id}.{required_key} is required for "
                f"candidate_mode {mode!r}."
            )
        if (
            parsed.minimum_long_share is not None
            and parsed.maximum_long_share is not None
            and parsed.minimum_long_share > parsed.maximum_long_share
        ):
            raise TrafficGenerationPolicyError(
                f"profiles.{profile_id}.minimum_long_share cannot exceed "
                "maximum_long_share."
            )

    raw_baselines = raw.get("load_baselines", {})
    if not isinstance(raw_baselines, dict) or not raw_baselines:
        raise TrafficGenerationPolicyError("load_baselines must be a non-empty object.")
    baselines = {}
    for period_id, item in raw_baselines.items():
        if not isinstance(item, dict):
            raise TrafficGenerationPolicyError(
                f"load_baselines.{period_id} must be an object."
            )
        vehicle_count = item.get("vehicle_count")
        if (
            isinstance(vehicle_count, bool)
            or not isinstance(vehicle_count, int)
            or vehicle_count <= 0
        ):
            raise TrafficGenerationPolicyError(
                f"load_baselines.{period_id}.vehicle_count must be a positive integer."
            )
        baselines[str(period_id)] = PeriodLoadBaseline(
            vehicle_count=vehicle_count,
            freeflow_vehicle_seconds=_finite_number(
                item.get("freeflow_vehicle_seconds"),
                f"load_baselines.{period_id}.freeflow_vehicle_seconds",
                positive=True,
            ),
        )

    guardrails = raw.get("load_guardrails", {})
    mix_calibration_rounds = raw.get("mix_calibration_rounds")
    if (
        isinstance(mix_calibration_rounds, bool)
        or not isinstance(mix_calibration_rounds, int)
        or not 1 <= mix_calibration_rounds <= 2
    ):
        raise TrafficGenerationPolicyError(
            "mix_calibration_rounds must be an integer between 1 and 2."
        )
    selection_mode = str(raw.get("selection_mode", ""))
    if selection_mode != "best_effort":
        raise TrafficGenerationPolicyError(
            "selection_mode must be 'best_effort'."
        )
    overall_long_share_max = _optional_share(
        raw.get("overall_long_share_max"), "overall_long_share_max"
    )
    if overall_long_share_max is None:
        raise TrafficGenerationPolicyError("overall_long_share_max is required.")
    return TrafficGenerationPolicy(
        short_route_max_m=short_route_max_m,
        long_route_min_m=long_route_min_m,
        overall_long_share_max=overall_long_share_max,
        maximum_vehicle_count_multiplier=_finite_number(
            guardrails.get("maximum_vehicle_count_multiplier"),
            "load_guardrails.maximum_vehicle_count_multiplier",
            positive=True,
        ),
        maximum_freeflow_vehicle_seconds_ratio=_finite_number(
            guardrails.get("maximum_freeflow_vehicle_seconds_ratio"),
            "load_guardrails.maximum_freeflow_vehicle_seconds_ratio",
            positive=True,
        ),
        mix_calibration_rounds=mix_calibration_rounds,
        selection_mode=selection_mode,
        profiles=profiles,
        baselines=baselines,
    )
