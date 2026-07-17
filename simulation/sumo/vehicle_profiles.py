"""Validated vehicle profiles used by generated traffic and runtime telemetry."""

from __future__ import annotations

import json
import math
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Mapping


class VehicleProfileError(ValueError):
    """Raised when a configured vehicle profile is unusable."""


@dataclass(frozen=True)
class VehicleProfile:
    profile_id: str
    v_class: str
    powertrain: str
    emission_class: str
    accel_mps2: float
    decel_mps2: float
    length_m: float
    width_m: float
    min_gap_m: float
    max_speed_mps: float
    sigma: float
    fuel_density_mg_per_ml: float
    hard_braking_threshold_mps2: float

    def sumo_attributes(self, type_id: str) -> Mapping[str, str]:
        return {
            "id": type_id,
            "vClass": self.v_class,
            "emissionClass": self.emission_class,
            "accel": f"{self.accel_mps2:g}",
            "decel": f"{self.decel_mps2:g}",
            "sigma": f"{self.sigma:g}",
            "length": f"{self.length_m:g}",
            "width": f"{self.width_m:g}",
            "minGap": f"{self.min_gap_m:g}",
            "maxSpeed": f"{self.max_speed_mps:g}",
        }


def _positive(raw: Mapping[str, Any], key: str, context: str) -> float:
    value = raw.get(key)
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise VehicleProfileError(f"{context}/{key} must be a number.")
    parsed = float(value)
    if not math.isfinite(parsed) or parsed <= 0:
        raise VehicleProfileError(f"{context}/{key} must be finite and positive.")
    return parsed


def parse_vehicle_profiles(raw: Mapping[str, Any]) -> Mapping[str, VehicleProfile]:
    if int(raw.get("schema_version", 0)) != 1:
        raise VehicleProfileError("vehicle_profiles.json must use schema_version 1.")
    result = {}
    for profile_id, item in raw.get("profiles", {}).items():
        context = str(profile_id)
        if not isinstance(item, Mapping):
            raise VehicleProfileError(f"{context} must be an object.")
        v_class = str(item.get("v_class", ""))
        powertrain = str(item.get("powertrain", ""))
        emission_class = str(item.get("emission_class", ""))
        if not v_class or powertrain not in {"gasoline", "diesel", "hybrid", "electric"}:
            raise VehicleProfileError(f"{context}: invalid vehicle class or powertrain.")
        if not emission_class:
            raise VehicleProfileError(f"{context}: emission_class is required.")
        sigma = float(item.get("sigma", -1))
        braking = float(item.get("hard_braking_threshold_mps2", 0))
        if not math.isfinite(sigma) or not 0 <= sigma <= 1:
            raise VehicleProfileError(f"{context}/sigma must be between 0 and 1.")
        if not math.isfinite(braking) or braking >= 0:
            raise VehicleProfileError(
                f"{context}/hard_braking_threshold_mps2 must be negative."
            )
        result[context] = VehicleProfile(
            profile_id=context,
            v_class=v_class,
            powertrain=powertrain,
            emission_class=emission_class,
            accel_mps2=_positive(item, "accel_mps2", context),
            decel_mps2=_positive(item, "decel_mps2", context),
            length_m=_positive(item, "length_m", context),
            width_m=_positive(item, "width_m", context),
            min_gap_m=_positive(item, "min_gap_m", context),
            max_speed_mps=_positive(item, "max_speed_mps", context),
            sigma=sigma,
            fuel_density_mg_per_ml=_positive(
                item, "fuel_density_mg_per_ml", context
            ),
            hard_braking_threshold_mps2=braking,
        )
    if not result:
        raise VehicleProfileError("No vehicle profiles are configured.")
    return result


def load_vehicle_profiles(path: Path) -> Mapping[str, VehicleProfile]:
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError as exc:
        raise VehicleProfileError(f"Vehicle profile file not found: {path}") from exc
    except json.JSONDecodeError as exc:
        raise VehicleProfileError(f"Invalid JSON in {path}: {exc}") from exc
    if not isinstance(raw, Mapping):
        raise VehicleProfileError(f"Vehicle profile root must be an object: {path}")
    return parse_vehicle_profiles(raw)
