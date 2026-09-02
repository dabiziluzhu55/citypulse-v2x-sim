"""Pure reference-speed advice contracts.

The PPO action is the normalized latent u. All speed quantities are
deterministic actuator outputs, so clipping the advice never changes the action
stored in a rollout or the log-probability recomputed by PPO.
"""
from __future__ import annotations

from dataclasses import dataclass
import math


@dataclass(frozen=True)
class SpeedAdviceDecision:
    latent_u: float
    base_speed_mps: float
    previous_advice_mps: float
    projected_advice_mps: float
    base_projection_delta_mps: float
    requested_delta_mps: float
    effective_delta_mps: float
    next_advice_mps: float | None
    target_speed_mps: float | None
    release_native: bool

    @property
    def transition_kind(self) -> str:
        return "native_release" if self.release_native else "active_cap"


def reference_base_speed(
    allowed_speed_mps: float,
    vehicle_type_max_speed_mps: float,
) -> float:
    """Return the unique legal policy ceiling from frozen payload fields."""

    allowed = float(allowed_speed_mps)
    type_max = float(vehicle_type_max_speed_mps)
    if not math.isfinite(allowed) or not math.isfinite(type_max):
        raise ValueError("base-speed inputs must be finite")
    return max(0.0, min(allowed, type_max))


def apply_incremental_speed_advice(
    *,
    previous_advice_mps: float | None,
    base_speed_mps: float,
    latent_u: float,
    delta_v_max_mps: float,
    authority_gain: float,
    native_release_tolerance_mps: float,
) -> SpeedAdviceDecision:
    """Map one PPO latent action to a reference-speed cap or native release."""

    base = float(base_speed_mps)
    latent = float(latent_u)
    delta_limit = float(delta_v_max_mps)
    gain = float(authority_gain)
    tolerance = float(native_release_tolerance_mps)
    if not all(math.isfinite(value) for value in (base, latent, delta_limit, gain, tolerance)):
        raise ValueError("speed-advice inputs must be finite")
    if base < 0.0:
        raise ValueError("base_speed_mps must be non-negative")
    if not -1.0 - 1e-7 <= latent <= 1.0 + 1e-7:
        raise ValueError("latent_u must be in [-1, 1]")
    if delta_limit < 0.0:
        raise ValueError("delta_v_max_mps must be non-negative")
    if not 0.0 < gain <= 1.0:
        raise ValueError("authority_gain must be in (0, 1]")
    if tolerance < 0.0:
        raise ValueError("native release tolerance must be non-negative")

    previous = base if previous_advice_mps is None else float(previous_advice_mps)
    if not math.isfinite(previous) or previous < 0.0:
        raise ValueError("previous advice must be finite and non-negative")
    projected = min(previous, base)
    base_projection = projected - previous
    requested = latent * delta_limit * gain
    next_cap = min(max(0.0, projected + requested), base)
    effective = next_cap - projected
    release = next_cap >= base - tolerance
    public_cap = None if release else next_cap
    return SpeedAdviceDecision(
        latent_u=latent,
        base_speed_mps=base,
        previous_advice_mps=previous,
        projected_advice_mps=projected,
        base_projection_delta_mps=base_projection,
        requested_delta_mps=requested,
        effective_delta_mps=effective,
        next_advice_mps=public_cap,
        target_speed_mps=public_cap,
        release_native=release,
    )


def apply_temporary_base_relative_speed_advice(
    *,
    previous_advice_mps: float | None,
    base_speed_mps: float,
    latent_u: float,
    delta_v_max_mps: float,
) -> SpeedAdviceDecision:
    """Map one latent action to a non-accumulating current-base speed cap.

    ``delta_v_max_mps`` is the authority-adjusted bound for this decision.
    The previous cap is audit-only: it never participates in the new cap.
    """

    base = float(base_speed_mps)
    latent = float(latent_u)
    delta_limit = float(delta_v_max_mps)
    if not all(math.isfinite(value) for value in (base, latent, delta_limit)):
        raise ValueError("temporary speed-advice inputs must be finite")
    if base < 0.0:
        raise ValueError("base_speed_mps must be non-negative")
    if not -1.0 - 1e-7 <= latent <= 1.0 + 1e-7:
        raise ValueError("latent_u must be in [-1, 1]")
    if delta_limit < 0.0:
        raise ValueError("delta_v_max_mps must be non-negative")

    previous = base if previous_advice_mps is None else float(previous_advice_mps)
    if not math.isfinite(previous) or previous < 0.0:
        raise ValueError("previous advice must be finite and non-negative")

    # The current native/base ceiling is the sole actuator reference. This is
    # deliberately not ``previous + requested``.
    projected = base
    requested = latent * delta_limit
    next_cap = min(max(0.0, base + requested), base)
    effective = next_cap - base
    release = latent >= 0.0 or next_cap >= base
    public_cap = None if release else next_cap
    return SpeedAdviceDecision(
        latent_u=latent,
        base_speed_mps=base,
        previous_advice_mps=previous,
        projected_advice_mps=projected,
        base_projection_delta_mps=projected - previous,
        requested_delta_mps=requested,
        effective_delta_mps=effective,
        next_advice_mps=public_cap,
        target_speed_mps=public_cap,
        release_native=release,
    )
