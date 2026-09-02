"""Heterogeneous feed-forward actors and centralized critic for CoV2X MVP."""
from __future__ import annotations

from dataclasses import dataclass
import hashlib
from typing import Any, Mapping, Sequence

import numpy as np
import torch
import torch.nn as nn


@dataclass(frozen=True)
class MVPPolicyConfig:
    cloud_feature_dim: int = 8
    road_feature_dim: int = 32
    phase_feature_dim: int = 8
    movement_feature_dim: int = 8
    vehicle_feature_dim: int = 40
    global_state_dim: int = 64
    hidden_dim: int = 64
    critic_hidden_dim: int = 128
    critic_context_dim: int = 16
    cloud_dim: int = 1
    delta_v_max_lane_speed_fraction: float = 0.10
    gamma: float = 0.99
    lam: float = 0.95
    clip_eps: float = 0.2
    entropy_coef: float = 0.0
    value_coef: float = 0.5
    ppo_epochs: int = 4
    max_grad_norm: float = 0.5
    actor_lr: float = 3e-4
    critic_lr: float = 1e-3
    device: str = "cpu"


def _as_float_tensor(value: Any, module: nn.Module) -> torch.Tensor:
    return torch.as_tensor(value, dtype=torch.float32, device=next(module.parameters()).device)


class DirectedGraphMessagePassing(nn.Module):
    """One edge-aware directed message-passing layer with self-loops."""

    def __init__(self, in_dim: int, hidden_dim: int) -> None:
        super().__init__()
        self.edge = nn.Linear(in_dim, hidden_dim)
        self.update = nn.Linear(in_dim + hidden_dim, hidden_dim)
        self.activation = nn.Tanh()

    def forward(self, node_features: torch.Tensor, edges: Sequence[tuple[int, int]]) -> torch.Tensor:
        if node_features.ndim != 2:
            raise ValueError("node_features must be [n_nodes, feature_dim]")
        messages = node_features.new_zeros((node_features.shape[0], self.update.out_features))
        for source, target in edges:
            source, target = int(source), int(target)
            if 0 <= source < node_features.shape[0] and 0 <= target < node_features.shape[0]:
                messages[target] = messages[target] + self.edge(node_features[source])
        return self.activation(self.update(torch.cat((node_features, messages), dim=-1)))


def directed_physical_edges(intersection_ids: Sequence[str], topology: Mapping[str, Sequence[str]] | None) -> tuple[tuple[int, int], ...]:
    ids = tuple(str(item) for item in intersection_ids)
    index = {item: pos for pos, item in enumerate(ids)}
    edges = {(pos, pos) for pos in range(len(ids))}
    for source, neighbors in (topology or {}).items():
        for target in neighbors or ():
            if str(source) in index and str(target) in index:
                edges.add((index[str(source)], index[str(target)]))
    return tuple(sorted(edges))


class CloudTanhNormalActor(nn.Module):
    """Per-intersection continuous priority c_i in [-1, 1]."""

    def __init__(self, config: MVPPolicyConfig = MVPPolicyConfig()) -> None:
        super().__init__()
        if config.cloud_dim != 1:
            raise ValueError("Cloud action_dim must remain exactly 1")
        self.config = config
        self.encoder = nn.Sequential(
            nn.Linear(config.cloud_feature_dim, config.hidden_dim), nn.Tanh(),
            nn.Linear(config.hidden_dim, config.hidden_dim), nn.Tanh(),
        )
        self.message = DirectedGraphMessagePassing(config.hidden_dim, config.hidden_dim)
        self.mean = nn.Linear(config.hidden_dim, config.cloud_dim)
        self.log_std = nn.Parameter(torch.full((config.cloud_dim,), -1.0))

    def _distribution(self, features: Any, edges: Sequence[tuple[int, int]]) -> torch.distributions.Normal:
        feature_tensor = _as_float_tensor(features, self)
        if feature_tensor.ndim != 2 or feature_tensor.shape[0] <= 0:
            raise ValueError("Cloud obs shape must be [N_cloud, obs_dim]")
        if feature_tensor.shape[1] != self.config.cloud_feature_dim:
            raise ValueError(
                "Cloud obs feature dimension mismatch: "
                f"expected {self.config.cloud_feature_dim}, got {feature_tensor.shape[1]}"
            )
        encoded = self.encoder(feature_tensor)
        hidden = self.message(encoded, edges)
        mean = self.mean(hidden)
        expected = (feature_tensor.shape[0], 1)
        if tuple(mean.shape) != expected:
            raise ValueError(
                f"Cloud mean shape must be {expected}, got {tuple(mean.shape)}"
            )
        log_std = self.log_std.clamp(-5.0, 1.0).expand_as(mean)
        if tuple(log_std.shape) != expected:
            raise ValueError(
                f"Cloud log_std shape must be {expected}, got {tuple(log_std.shape)}"
            )
        return torch.distributions.Normal(mean, log_std.exp())

    def sample(
        self,
        features: Any,
        edges: Sequence[tuple[int, int]],
        *,
        deterministic: bool = False,
        authority_gain: float = 1.0,
    ) -> tuple[torch.Tensor, torch.Tensor, torch.Tensor]:
        gain = float(authority_gain)
        if not 0.0 < gain <= 1.0:
            raise ValueError("Cloud authority_gain must be in (0, 1]")
        dist = self._distribution(features, edges)
        raw = dist.mean if deterministic else dist.rsample()
        bounded = torch.tanh(raw)
        action = gain * bounded
        element_logprob = (
            dist.log_prob(raw)
            - torch.log1p(-bounded.square()).clamp_min(-20.0)
            - np.log(gain)
        )
        expected_action = (dist.loc.shape[0], 1)
        if tuple(action.shape) != expected_action:
            raise ValueError(
                f"Cloud sampled action shape must be {expected_action}, got {tuple(action.shape)}"
            )
        if tuple(element_logprob.shape) != expected_action:
            raise ValueError("Cloud element log_prob shape mismatch")
        logprob = element_logprob.sum(dim=-1)
        entropy = dist.entropy().sum(dim=-1)
        expected_agents = (dist.loc.shape[0],)
        if tuple(logprob.shape) != expected_agents:
            raise ValueError("Cloud log_prob shape must be [N_cloud]")
        if tuple(entropy.shape) != expected_agents:
            raise ValueError("Cloud entropy shape must be [N_cloud]")
        if not bool(
            torch.isfinite(action).all()
            and torch.isfinite(logprob).all()
            and torch.isfinite(entropy).all()
        ):
            raise FloatingPointError("non-finite Cloud actor sample")
        return action, logprob, entropy

    def log_prob(
        self,
        features: Any,
        edges: Sequence[tuple[int, int]],
        action: Any,
        *,
        authority_gain: float = 1.0,
    ) -> tuple[torch.Tensor, torch.Tensor]:
        gain = float(authority_gain)
        if not 0.0 < gain <= 1.0:
            raise ValueError("Cloud authority_gain must be in (0, 1]")
        dist = self._distribution(features, edges)
        action_tensor = _as_float_tensor(action, self)
        expected_action = (dist.loc.shape[0], 1)
        if tuple(action_tensor.shape) != expected_action:
            raise ValueError(
                f"Cloud action shape must be {expected_action}, "
                f"got {tuple(action_tensor.shape)}"
            )
        if not bool(torch.isfinite(action_tensor).all()):
            raise FloatingPointError("non-finite Cloud action")
        bounded = (action_tensor / gain).clamp(-0.999999, 0.999999)
        raw = torch.atanh(bounded)
        element_logprob = (
            dist.log_prob(raw)
            - torch.log1p(-bounded.square()).clamp_min(-20.0)
            - np.log(gain)
        )
        if tuple(element_logprob.shape) != expected_action:
            raise ValueError("Cloud evaluated element log_prob shape mismatch")
        logprob = element_logprob.sum(dim=-1)
        entropy = dist.entropy().sum(dim=-1)
        expected_agents = (dist.loc.shape[0],)
        if tuple(logprob.shape) != expected_agents:
            raise ValueError("Cloud evaluated log_prob shape must be [N_cloud]")
        if tuple(entropy.shape) != expected_agents:
            raise ValueError("Cloud evaluated entropy shape must be [N_cloud]")
        if not bool(torch.isfinite(logprob).all() and torch.isfinite(entropy).all()):
            raise FloatingPointError("non-finite Cloud actor evaluation")
        return logprob, entropy


class VehicleSpeedAdviceActor(nn.Module):
    """TanhNormal actor whose PPO-semantic action is latent u in [-1, 1]."""

    def __init__(self, config: MVPPolicyConfig = MVPPolicyConfig()) -> None:
        super().__init__()
        self.config = config
        self.trunk = nn.Sequential(
            nn.Linear(config.vehicle_feature_dim, config.hidden_dim),
            nn.Tanh(),
            nn.Linear(config.hidden_dim, config.hidden_dim),
            nn.Tanh(),
        )
        self.mean = nn.Linear(config.hidden_dim, 1)
        self.log_std = nn.Parameter(torch.tensor([-1.0]))
        self.initialize_corridor_generation_zero()

    def initialize_corridor_generation_zero(self) -> None:
        """Keep a fresh trunk while freezing the exact neutral output head."""

        with torch.no_grad():
            self.mean.weight.zero_()
            self.mean.bias.zero_()
            self.log_std.fill_(-1.0)

    def initialize_local_credit_final(self, initial_mean: float = -0.25) -> None:
        """Fresh actor with a preregistered deterministic latent-action mean."""

        mean = float(initial_mean)
        if not np.isfinite(mean) or not -1.0 < mean < 1.0:
            raise ValueError("initial Vehicle mean must be finite and in (-1, 1)")
        with torch.no_grad():
            self.mean.weight.zero_()
            self.mean.bias.fill_(float(np.arctanh(mean)))
            self.log_std.fill_(-1.0)

    def _distribution(self, features: Any) -> torch.distributions.Normal:
        hidden = self.trunk(_as_float_tensor(features, self))
        return torch.distributions.Normal(
            self.mean(hidden), self.log_std.clamp(-5.0, 1.0).exp()
        )

    def sample(
        self, features: Any, *, deterministic: bool = False
    ) -> tuple[torch.Tensor, torch.Tensor, torch.Tensor]:
        dist = self._distribution(features)
        raw = dist.mean if deterministic else dist.rsample()
        latent = torch.tanh(raw)
        logprob = (
            dist.log_prob(raw)
            - torch.log1p(-latent.square()).clamp_min(-20.0)
        ).sum(-1)
        return latent.squeeze(-1), logprob, dist.entropy().sum(-1)

    def log_prob(
        self, features: Any, latent_action: Any
    ) -> tuple[torch.Tensor, torch.Tensor]:
        dist = self._distribution(features)
        latent = _as_float_tensor(latent_action, self).reshape(-1, 1).clamp(
            -0.999999, 0.999999
        )
        raw = torch.atanh(latent)
        logprob = (
            dist.log_prob(raw)
            - torch.log1p(-latent.square()).clamp_min(-20.0)
        ).sum(-1)
        return logprob, dist.entropy().sum(-1)


def _stable_identifier_pair(value: str) -> tuple[float, float]:
    if not value:
        return 0.0, 0.0
    digest = hashlib.sha256(str(value).encode("utf-8")).digest()
    first = int.from_bytes(digest[:4], "big") / float(2**32 - 1)
    second = int.from_bytes(digest[4:8], "big") / float(2**32 - 1)
    return 2.0 * first - 1.0, 2.0 * second - 1.0


def critic_context_vector(
    *,
    role: str,
    intersection_id: str = "",
    movement_id: str = "",
    movement_local_context: Sequence[float] = (),
    config: MVPPolicyConfig = MVPPolicyConfig(),
) -> np.ndarray:
    """Fixed role/intersection/movement context for the final critic."""

    roles = {"cloud": 0, "road": 1, "vehicle": 2, "value": 3}
    if role not in roles:
        raise ValueError(f"unknown critic role: {role}")
    vector = np.zeros(config.critic_context_dim, dtype=np.float32)
    if config.critic_context_dim < 8:
        raise ValueError("critic_context_dim must be at least 8")
    vector[roles[role]] = 1.0
    vector[4:6] = _stable_identifier_pair(intersection_id)
    vector[6:8] = _stable_identifier_pair(movement_id)
    local = np.asarray(tuple(movement_local_context), dtype=np.float32).reshape(-1)
    width = min(len(local), config.critic_context_dim - 8)
    if width:
        vector[8 : 8 + width] = local[:width]
    if not np.isfinite(vector).all():
        raise ValueError("critic context must be finite")
    return vector


class ConditionedCentralizedCritic(nn.Module):
    """Centralized value function conditioned on role and movement identity."""

    def __init__(self, config: MVPPolicyConfig = MVPPolicyConfig()) -> None:
        super().__init__()
        self.config = config
        self.net = nn.Sequential(
            nn.Linear(
                config.global_state_dim + config.critic_context_dim,
                config.critic_hidden_dim,
            ),
            nn.Tanh(),
            nn.Linear(config.critic_hidden_dim, config.critic_hidden_dim),
            nn.Tanh(),
            nn.Linear(config.critic_hidden_dim, 1),
        )

    def forward(self, global_state: Any, critic_context: Any) -> torch.Tensor:
        state = _as_float_tensor(global_state, self)
        context = _as_float_tensor(critic_context, self)
        if state.shape[-1] != self.config.global_state_dim:
            raise ValueError("conditioned critic global-state dimension mismatch")
        if context.shape[-1] != self.config.critic_context_dim:
            raise ValueError("conditioned critic context dimension mismatch")
        if state.shape[:-1] != context.shape[:-1]:
            raise ValueError("conditioned critic state/context batch mismatch")
        return self.net(torch.cat((state, context), dim=-1)).squeeze(-1)


class CentralizedCritic(nn.Module):
    def __init__(self, config: MVPPolicyConfig = MVPPolicyConfig()) -> None:
        super().__init__()
        self.net = nn.Sequential(
            nn.Linear(config.global_state_dim, config.critic_hidden_dim), nn.Tanh(),
            nn.Linear(config.critic_hidden_dim, config.critic_hidden_dim), nn.Tanh(),
            nn.Linear(config.critic_hidden_dim, 1),
        )

    def forward(self, global_state: Any) -> torch.Tensor:
        return self.net(_as_float_tensor(global_state, self)).squeeze(-1)


class RunningValueNormalizer:
    """Shared return normalization state for the centralized critic."""

    def __init__(self, epsilon: float = 1e-4) -> None:
        self.mean = 0.0
        self.var = 1.0
        self.count = float(epsilon)

    def update(self, values: np.ndarray) -> None:
        array = np.asarray(values, dtype=np.float64).reshape(-1)
        if not len(array):
            return
        batch_mean, batch_var, batch_count = float(array.mean()), float(array.var()), float(len(array))
        delta = batch_mean - self.mean
        total = self.count + batch_count
        m2 = self.var * self.count + batch_var * batch_count + delta * delta * self.count * batch_count / total
        self.mean += delta * batch_count / total
        self.var, self.count = m2 / total, total

    def normalize(self, values: Any) -> np.ndarray:
        return (np.asarray(values, dtype=np.float32) - self.mean) / np.sqrt(self.var + 1e-8)

    def state_dict(self) -> dict[str, float]:
        return {"mean": self.mean, "var": self.var, "count": self.count}

    def load_state_dict(self, state: Mapping[str, Any]) -> None:
        self.mean = float(state.get("mean", 0.0)); self.var = float(state.get("var", 1.0)); self.count = float(state.get("count", 1e-4))


def cloud_feature_matrix(payload: Mapping[str, Any], intersection_ids: Sequence[str]) -> np.ndarray:
    rows = []
    intersections = payload.get("intersections", {}) or {}
    for tls_id in intersection_ids:
        item = intersections.get(str(tls_id), {}) or {}
        lanes = item.get("lanes", {}) or {}
        lane_items = [lane for lane in lanes.values() if isinstance(lane, Mapping)]
        halting = sum(float(lane.get("halting_count", 0.0) or 0.0) for lane in lane_items)
        vehicles = sum(float(lane.get("vehicle_count", 0.0) or 0.0) for lane in lane_items)
        waiting = sum(float(lane.get("waiting_time", 0.0) or 0.0) for lane in lane_items)
        queue = sum(float(lane.get("queue_length_m", 0.0) or 0.0) for lane in lane_items)
        rows.append([halting / 20.0, vehicles / 20.0, waiting / 600.0, queue / 300.0, float(item.get("current_phase", 0.0) or 0.0) / 8.0, float(item.get("stage_elapsed", 0.0) or 0.0) / 60.0, float(item.get("throughput", 0.0) or 0.0) / 20.0, float(payload.get("simulation_time", 0.0) or 0.0) / 900.0])
    return np.asarray(rows, dtype=np.float32)


def road_feature_vector(payload: Mapping[str, Any], tls_id: str) -> np.ndarray:
    rows = cloud_feature_matrix(payload, (tls_id,))
    base = rows[0] if len(rows) else np.zeros(8, dtype=np.float32)
    return np.pad(base, (0, MVPPolicyConfig.road_feature_dim - len(base))).astype(np.float32)


def phase_feature_matrix(phases: Sequence[int]) -> np.ndarray:
    result = np.zeros((len(phases), MVPPolicyConfig.phase_feature_dim), dtype=np.float32)
    for index, phase in enumerate(phases):
        result[index, int(phase) % MVPPolicyConfig.phase_feature_dim] = 1.0
    return result


def movement_feature_matrix(payload: Mapping[str, Any], tls_id: str, phases: Sequence[int]) -> np.ndarray:
    item = (payload.get("intersections", {}) or {}).get(str(tls_id), {}) or {}
    mappings = item.get("phase_movements", {}) or {}
    if not mappings:
        mappings = {
            int(phase): tuple(
                ((phase_data or {}).get("connection_priorities", {}) or {}).keys()
            ) or (str((phase_data or {}).get("movement", "unknown")),)
            for phase, phase_data in (item.get("phases", {}) or {}).items()
        }
    result = np.zeros((len(phases), MVPPolicyConfig.movement_feature_dim), dtype=np.float32)
    for index, phase in enumerate(phases):
        movements = tuple(mappings.get(str(phase), mappings.get(phase, ())) or ())
        result[index, 0] = min(len(movements), 8) / 8.0
        for movement in movements:
            digest = hashlib.sha256(str(movement).encode("utf-8")).digest()
            result[index, 1 + (int.from_bytes(digest[:8], "big") % 7)] = 1.0
    return result


def vehicle_feature_vector(
    *,
    speed_mps: float,
    accel_mps2: float,
    allowed_speed_mps: float,
    base_speed_mps: float,
    advice_speed_mps: float,
    advice_active: bool,
    distance_m: float,
    green: bool,
    signal_remaining_s: float,
    cloud_priority: float,
    leader_gap_m: float | None = None,
    relative_speed_mps: float | None = None,
    previous_delta_v_mps: float = 0.0,
    previous_realized_speed_mps: float | None = None,
    reference_tracking_error_mps: float = 0.0,
    native_limited: bool = False,
    assignment_age_s: float = 0.0,
    lease_age_s: float = 0.0,
    message_age_s: float = 0.0,
    movement_code: float = 0.0,
    pooled_context: Sequence[float] = (),
) -> np.ndarray:
    safe_base = max(float(base_speed_mps), 1e-6)
    base = [
        float(speed_mps) / 30.0,
        float(accel_mps2) / 5.0,
        float(allowed_speed_mps) / 30.0,
        float(base_speed_mps) / 30.0,
        float(advice_speed_mps) / 30.0,
        float(bool(advice_active)),
        float(advice_speed_mps) / safe_base,
        float(previous_delta_v_mps) / 3.0,
        float(speed_mps if previous_realized_speed_mps is None else previous_realized_speed_mps) / 30.0,
        float(reference_tracking_error_mps) / 10.0,
        float(leader_gap_m or 0.0) / 100.0,
        float(leader_gap_m is not None),
        float(relative_speed_mps or 0.0) / 15.0,
        float(relative_speed_mps is not None),
        float(distance_m) / 150.0,
        float(bool(green)),
        float(signal_remaining_s) / 60.0,
        float(cloud_priority),
        float(assignment_age_s) / 60.0,
        float(lease_age_s) / 60.0,
        float(message_age_s) / 15.0,
        float(bool(native_limited)),
        float(movement_code),
    ]
    vector = np.asarray(base + [float(v) for v in pooled_context], dtype=np.float32)
    return np.pad(vector, (0, max(0, MVPPolicyConfig.vehicle_feature_dim - len(vector))))[:MVPPolicyConfig.vehicle_feature_dim]


def global_state_vector(payload: Mapping[str, Any], intersection_ids: Sequence[str], cloud_priority: Mapping[str, float], movement_context: Mapping[str, Sequence[float]] | None = None) -> np.ndarray:
    cloud = cloud_feature_matrix(payload, intersection_ids)
    parts: list[np.ndarray] = []
    if len(cloud):
        parts.extend((cloud.mean(0), cloud.max(0), cloud.std(0)))
    else:
        parts.append(np.zeros(24, dtype=np.float32))
    priorities = np.asarray([cloud_priority.get(str(item), 0.0) for item in intersection_ids], dtype=np.float32)
    parts.append(np.asarray([priorities.mean() if len(priorities) else 0.0, priorities.max() if len(priorities) else 0.0, priorities.min() if len(priorities) else 0.0, len(intersection_ids) / 20.0], dtype=np.float32))
    pooled = [np.asarray(value, dtype=np.float32) for value in (movement_context or {}).values()]
    if pooled:
        width = max(len(item) for item in pooled)
        matrix = np.stack([np.pad(item, (0, width - len(item))) for item in pooled])
        parts.extend((matrix.mean(0), matrix.max(0)))
    vector = np.concatenate(parts).astype(np.float32)
    return np.pad(vector, (0, max(0, MVPPolicyConfig.global_state_dim - len(vector))))[:MVPPolicyConfig.global_state_dim]
