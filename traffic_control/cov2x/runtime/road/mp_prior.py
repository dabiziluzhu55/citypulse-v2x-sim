"""Frozen MP-prior normalization and Cloud-conditioned Road residual actor."""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Mapping, Sequence

import numpy as np
import torch
import torch.nn as nn

from traffic_control.cov2x.runtime.contract import normalized_mp_scores, override_reachable, q_gap, road_logits
from traffic_control.max_pressure import (
    DEFAULT_PERMISSIVE_WEIGHT,
    DEFAULT_PROTECTED_WEIGHT,
    _build_intersection_index,
    _compute_phase_pressure,
    _estimate_movement_queues,
    _intersection_vehicles,
    _match_vehicle_movement,
    _vehicle_lane_speed,
)


class StrongMPPressureOracle:
    """Read-only adapter over the frozen Strong-MP pressure kernel.

    Strong-MP owns the movement queue estimator, downstream back-pressure,
    and protected/permissive service weights. The MVP imports those helpers
    instead of maintaining a second pressure formula that could drift.
    """

    def __init__(self, metadata: Mapping[str, Any]) -> None:
        protected = float(
            metadata.get(
                "max_pressure_protected_weight", DEFAULT_PROTECTED_WEIGHT
            )
        )
        permissive = float(
            metadata.get(
                "max_pressure_permissive_weight", DEFAULT_PERMISSIVE_WEIGHT
            )
        )
        self._indices = {}
        for tls_id, item in (metadata.get("intersections", {}) or {}).items():
            intersection = dict(item)
            intersection.setdefault("intersection_id", str(tls_id))
            self._indices[str(tls_id)] = _build_intersection_index(
                intersection,
                protected_weight=protected,
                permissive_weight=permissive,
            )

    def phase_pressures(
        self,
        payload: Mapping[str, Any],
        tls_id: str,
        phase_order: Sequence[int],
    ) -> dict[int, float]:
        ix = self._indices.get(str(tls_id))
        if ix is None:
            raise KeyError(f"Strong-MP metadata missing intersection {tls_id}")
        intersection = (
            (payload.get("intersections", {}) or {}).get(str(tls_id), {}) or {}
        )
        lanes = intersection.get("lanes", {}) or {}
        if not isinstance(lanes, Mapping):
            raise TypeError(f"intersection {tls_id} lanes must be a mapping")
        vehicles = _intersection_vehicles(
            ix, dict(payload.get("vehicles", {}) or {})
        )
        movement_queues = _estimate_movement_queues(
            ix, dict(lanes), vehicles
        )
        return {
            int(phase): _compute_phase_pressure(
                ix,
                ix.phase_movements.get(int(phase), []),
                movement_queues,
                dict(lanes),
            )
            for phase in phase_order
        }

    def vehicle_movement(
        self, tls_id: str, vehicle: Mapping[str, Any]
    ) -> str | None:
        """Resolve a vehicle to the same movement index used by Strong-MP."""
        ix = self._indices.get(str(tls_id))
        if ix is None:
            return None
        lane_id, _ = _vehicle_lane_speed(dict(vehicle))
        if lane_id is None:
            return None
        connection_id = _match_vehicle_movement(dict(vehicle), lane_id, ix)
        if connection_id is None:
            return None
        movement = ix.movements.get(connection_id)
        if movement is None:
            return None
        return str(movement.movement or movement.connection_id)


def phase_pressures_from_payload(
    payload: Mapping[str, Any],
    tls_id: str,
    phase_order: Sequence[int],
    *,
    oracle: StrongMPPressureOracle,
) -> dict[int, float]:
    """Return exact frozen Strong-MP pressures for runtime and SCREEN."""
    return oracle.phase_pressures(payload, tls_id, phase_order)


def normalized_phase_prior(
    payload: Mapping[str, Any],
    tls_id: str,
    phase_order: Sequence[int],
    *,
    oracle: StrongMPPressureOracle,
    legal_phases: Sequence[int] | None = None,
) -> dict[int, float]:
    return normalized_mp_scores(
        phase_pressures_from_payload(
            payload, tls_id, phase_order, oracle=oracle
        ),
        legal_phases=legal_phases or phase_order,
    )


@dataclass(frozen=True)
class RoadResidualConfig:
    delta_R: float = 0.5
    hidden_dim: int = 64
    road_obs_dim: int = 32
    cloud_dim: int = 1
    phase_dim: int = 8
    movement_dim: int = 8


class RoadResidualNetwork(nn.Module):
    """Produces per-phase residuals; c_i is an input, never a common logit shift."""

    def __init__(self, config: RoadResidualConfig = RoadResidualConfig()) -> None:
        super().__init__()
        self.config = config
        self.net = nn.Sequential(
            nn.Linear(config.road_obs_dim + config.cloud_dim + config.phase_dim + config.movement_dim, config.hidden_dim), nn.Tanh(),
            nn.Linear(config.hidden_dim, config.hidden_dim), nn.Tanh(),
            nn.Linear(config.hidden_dim, 1),
        )

    def residuals(
        self,
        road_obs: Any,
        cloud_priority: Any,
        phase_features: Any,
        movement_features: Any,
        *,
        authority_gain: float = 1.0,
    ) -> torch.Tensor:
        gain = float(authority_gain)
        if not 0.0 <= gain <= 1.0:
            raise ValueError("Road authority_gain must be in [0, 1]")
        device = next(self.parameters()).device
        road = torch.as_tensor(road_obs, dtype=torch.float32, device=device)
        cloud = torch.as_tensor(cloud_priority, dtype=torch.float32, device=device)
        phase = torch.as_tensor(phase_features, dtype=torch.float32, device=device)
        movement = torch.as_tensor(movement_features, dtype=torch.float32, device=device)
        if road.ndim == 1:
            road = road.unsqueeze(0).expand(phase.shape[0], -1)
        if cloud.ndim == 0:
            cloud = cloud.reshape(1)
        if cloud.ndim == 1:
            cloud = cloud.reshape(1, -1).expand(phase.shape[0], -1)
        features = torch.cat((road, cloud, phase, movement), dim=-1)
        return (
            gain
            * float(self.config.delta_R)
            * torch.tanh(self.net(features)).squeeze(-1)
        )

    def logits(
        self,
        q_values: Any,
        road_obs: Any,
        cloud_priority: Any,
        phase_features: Any,
        movement_features: Any,
        legal_mask: Any | None = None,
        *,
        authority_gain: float = 1.0,
    ) -> torch.Tensor:
        device = next(self.parameters()).device
        q_tensor = torch.as_tensor(q_values, dtype=torch.float32, device=device)
        logits = 4.0 * q_tensor + self.residuals(
            road_obs,
            cloud_priority,
            phase_features,
            movement_features,
            authority_gain=authority_gain,
        )
        if legal_mask is not None:
            mask = torch.as_tensor(legal_mask, dtype=torch.bool, device=device)
            if not bool(mask.any()):
                raise ValueError("Road action mask has no legal phase")
            logits = logits.masked_fill(~mask, -torch.inf)
        return logits

    def sample(
        self,
        q_values: Any,
        road_obs: Any,
        cloud_priority: Any,
        phase_features: Any,
        movement_features: Any,
        legal_mask: Any,
        *,
        deterministic: bool = False,
        authority_gain: float = 1.0,
    ) -> tuple[torch.Tensor, torch.Tensor, torch.Tensor]:
        logits = self.logits(
            q_values,
            road_obs,
            cloud_priority,
            phase_features,
            movement_features,
            legal_mask,
            authority_gain=authority_gain,
        )
        dist = torch.distributions.Categorical(logits=logits)
        action = dist.probs.argmax(-1) if deterministic else dist.sample()
        return action, dist.log_prob(action), dist.entropy()

    def log_prob(
        self,
        q_values: Any,
        road_obs: Any,
        cloud_priority: Any,
        phase_features: Any,
        movement_features: Any,
        legal_mask: Any,
        action: Any,
        *,
        authority_gain: float = 1.0,
    ) -> tuple[torch.Tensor, torch.Tensor]:
        logits = self.logits(
            q_values,
            road_obs,
            cloud_priority,
            phase_features,
            movement_features,
            legal_mask,
            authority_gain=authority_gain,
        )
        dist = torch.distributions.Categorical(logits=logits)
        action_t = torch.as_tensor(action, dtype=torch.int64, device=logits.device)
        return dist.log_prob(action_t), dist.entropy()

    forward = residuals


def compose_runtime_logits(q_scores: Mapping[int, float], *, cloud_priority: Sequence[float], road_observation: Sequence[float], phase_features: Mapping[int, Sequence[float]] | None = None, movement_features: Mapping[int, Sequence[float]] | None = None, network: RoadResidualNetwork | None = None, delta_R: float = 0.5) -> dict[int, float]:
    phases = tuple(int(p) for p in q_scores)
    residuals: dict[int, float] = {}
    phase_features, movement_features = phase_features or {}, movement_features or {}
    if network is not None:
        residual_tensor = network.residuals(
            road_observation, cloud_priority,
            np.asarray([phase_features.get(p, [0.0] * network.config.phase_dim) for p in phases], dtype=np.float32),
            np.asarray([movement_features.get(p, [0.0] * network.config.movement_dim) for p in phases], dtype=np.float32),
        )
        residuals = {phase: float(value) for phase, value in zip(phases, residual_tensor.detach().cpu().numpy())}
    return road_logits(q_scores, residuals, delta_R=delta_R, legal_phases=phases)


def authority_audit(score_sets: Sequence[Mapping[int, float]], delta_R: float) -> dict[str, float | bool]:
    rates = [override_reachable(scores, delta_R) for scores in score_sets]
    return {"delta_R": float(delta_R), "reachable_rate": float(np.mean(rates)) if rates else 0.0, "q_gap_mean": float(np.mean([q_gap(scores) for scores in score_sets])) if score_sets else 0.0, "in_band": bool(rates and 0.25 <= float(np.mean(rates)) <= 0.50)}
