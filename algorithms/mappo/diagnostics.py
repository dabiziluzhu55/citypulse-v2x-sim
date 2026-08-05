from __future__ import annotations

import copy
import math
from typing import Any, Iterable, Mapping, Sequence

import numpy as np
import torch

from algorithms.mappo.config import MAPPOConfig
from algorithms.mappo.models import MAPPOPolicy
from algorithms.mappo.reward import V5ARewardResult
from algorithms.mappo.rollout import Transition, compute_gae


_REWARD_COMPONENTS = ("D", "L", "S", "Qmax", "F_safe", "B", "H")
_REWARD_WEIGHTS = {
    "congestion": ("D", -0.60),
    "safe_flow": ("F_safe", 0.20),
    "spillback": ("B", -0.15),
    "waiting_gain": ("H", 0.05),
}


def _finite_float(value: object, *, name: str) -> float:
    number = float(value)
    if not math.isfinite(number):
        raise ValueError(f"{name} must be finite")
    return number


class ActionServiceDiagnostics:
    """Track candidate availability, selection, and observed green service."""

    def __init__(self, phase_orders: Mapping[str, Sequence[int]]) -> None:
        self._phase_orders = {
            str(intersection_id): tuple(int(phase) for phase in phases)
            for intersection_id, phases in phase_orders.items()
        }
        if not self._phase_orders or any(
            not phases for phases in self._phase_orders.values()
        ):
            raise ValueError("action diagnostics require non-empty phase orders")
        if any(
            len(phases) != len(set(phases))
            for phases in self._phase_orders.values()
        ):
            raise ValueError("candidate phases must be unique per intersection")
        self._decision_counts = {
            intersection_id: 0 for intersection_id in self._phase_orders
        }
        self._last_stages: dict[str, str | None] = {
            intersection_id: None for intersection_id in self._phase_orders
        }
        self._last_green_phases: dict[str, int | None] = {
            intersection_id: None for intersection_id in self._phase_orders
        }
        self._candidates: dict[str, list[dict[str, int | float]]] = {}
        for intersection_id, phases in self._phase_orders.items():
            self._candidates[intersection_id] = [
                {
                    "available_count": 0,
                    "selected_count": 0,
                    "current_available_unselected": 0,
                    "max_available_unselected": 0,
                    "green_observation_count": 0,
                    "green_entry_count": 0,
                    "last_service_time_s": 0.0,
                    "max_service_gap_s": 0.0,
                }
                for _ in phases
            ]

    def _intersection_index(self, intersection_id: str) -> str:
        normalized = str(intersection_id)
        if normalized not in self._phase_orders:
            raise KeyError(f"unknown intersection: {normalized}")
        return normalized

    def observe_state(
        self,
        intersection_id: str,
        *,
        simulation_time_s: float,
        stage: str,
        current_phase: int,
    ) -> None:
        normalized = self._intersection_index(intersection_id)
        simulation_time = _finite_float(
            simulation_time_s, name="simulation time"
        )
        if simulation_time < 0.0:
            raise ValueError("simulation time must be non-negative")
        stage_name = str(stage).strip().upper()
        phase = int(current_phase)
        phases = self._phase_orders[normalized]
        candidates = self._candidates[normalized]
        current_index = phases.index(phase) if phase in phases else None
        if stage_name == "GREEN" and current_index is not None:
            candidate = candidates[current_index]
            candidate["green_observation_count"] = int(
                candidate["green_observation_count"]
            ) + 1
            if (
                self._last_stages[normalized] != "GREEN"
                or self._last_green_phases[normalized] != phase
            ):
                candidate["green_entry_count"] = int(
                    candidate["green_entry_count"]
                ) + 1
            candidate["last_service_time_s"] = simulation_time

        for candidate in candidates:
            service_gap = max(
                simulation_time - float(candidate["last_service_time_s"]),
                0.0,
            )
            candidate["max_service_gap_s"] = max(
                float(candidate["max_service_gap_s"]), service_gap
            )

        self._last_stages[normalized] = stage_name
        self._last_green_phases[normalized] = (
            phase if stage_name == "GREEN" else None
        )

    def observe_decision(
        self,
        intersection_id: str,
        *,
        action_mask: np.ndarray | Sequence[bool],
        selected_action: int,
    ) -> None:
        normalized = self._intersection_index(intersection_id)
        mask = np.asarray(action_mask, dtype=np.bool_)
        candidate_count = len(self._phase_orders[normalized])
        if mask.shape != (candidate_count,):
            raise ValueError("action mask does not match candidate phase order")
        selected = int(selected_action)
        if not 0 <= selected < candidate_count or not bool(mask[selected]):
            raise ValueError("selected action must be an available candidate")
        self._decision_counts[normalized] += 1
        for index, (available, candidate) in enumerate(
            zip(mask, self._candidates[normalized], strict=True)
        ):
            if not bool(available):
                continue
            candidate["available_count"] = int(candidate["available_count"]) + 1
            if index == selected:
                candidate["selected_count"] = int(candidate["selected_count"]) + 1
                candidate["current_available_unselected"] = 0
            else:
                missed = int(candidate["current_available_unselected"]) + 1
                candidate["current_available_unselected"] = missed
                candidate["max_available_unselected"] = max(
                    int(candidate["max_available_unselected"]), missed
                )

    def snapshot(self) -> dict[str, object]:
        intersections: dict[str, object] = {}
        for intersection_id, phases in self._phase_orders.items():
            candidates = []
            for candidate_index, (phase, state) in enumerate(
                zip(phases, self._candidates[intersection_id], strict=True)
            ):
                available = int(state["available_count"])
                selected = int(state["selected_count"])
                candidates.append(
                    {
                        "candidate_index": candidate_index,
                        "phase": phase,
                        "available_count": available,
                        "selected_count": selected,
                        "selection_rate_when_available": (
                            selected / available if available else None
                        ),
                        "never_selected_while_available": bool(
                            available and not selected
                        ),
                        "max_available_opportunities_without_selection": int(
                            state["max_available_unselected"]
                        ),
                        "green_observation_count": int(
                            state["green_observation_count"]
                        ),
                        "green_entry_count": int(state["green_entry_count"]),
                        "max_observed_service_gap_s": float(
                            state["max_service_gap_s"]
                        ),
                    }
                )
            intersections[intersection_id] = {
                "decision_count": self._decision_counts[intersection_id],
                "candidates": candidates,
            }
        return {
            "schema": "mappo_action_service_v1",
            "intersections": intersections,
        }


def merge_action_diagnostics(
    snapshots: Iterable[Mapping[str, object]],
) -> dict[str, object]:
    values = tuple(snapshots)
    merged: dict[str, dict[str, Any]] = {}
    for snapshot in values:
        intersections = snapshot.get("intersections")
        if not isinstance(intersections, Mapping):
            raise ValueError("action diagnostics have no intersection mapping")
        for intersection_id, raw_tls in intersections.items():
            if not isinstance(raw_tls, Mapping):
                raise ValueError("intersection diagnostics must be a mapping")
            raw_candidates = raw_tls.get("candidates")
            if not isinstance(raw_candidates, Sequence):
                raise ValueError("candidate diagnostics must be a sequence")
            tls = merged.setdefault(
                str(intersection_id),
                {
                    "decision_count": 0,
                    "episodes_available": 0,
                    "candidates": {},
                },
            )
            tls["decision_count"] += int(raw_tls.get("decision_count", 0))
            tls["episodes_available"] += 1
            for raw_candidate in raw_candidates:
                if not isinstance(raw_candidate, Mapping):
                    raise ValueError("candidate diagnostics must be mappings")
                index = int(raw_candidate["candidate_index"])
                phase = int(raw_candidate["phase"])
                candidates = tls["candidates"]
                candidate = candidates.setdefault(
                    index,
                    {
                        "candidate_index": index,
                        "phase": phase,
                        "available_count": 0,
                        "selected_count": 0,
                        "episodes_available": 0,
                        "episodes_selected": 0,
                        "green_observation_count": 0,
                        "green_entry_count": 0,
                        "max_available_opportunities_without_selection": 0,
                        "max_observed_service_gap_s": 0.0,
                    },
                )
                if candidate["phase"] != phase:
                    raise ValueError("candidate phase semantics changed across runs")
                available = int(raw_candidate.get("available_count", 0))
                selected = int(raw_candidate.get("selected_count", 0))
                candidate["available_count"] += available
                candidate["selected_count"] += selected
                candidate["episodes_available"] += int(available > 0)
                candidate["episodes_selected"] += int(selected > 0)
                for count_name in (
                    "green_observation_count",
                    "green_entry_count",
                ):
                    candidate[count_name] += int(raw_candidate.get(count_name, 0))
                for maximum_name in (
                    "max_available_opportunities_without_selection",
                    "max_observed_service_gap_s",
                ):
                    candidate[maximum_name] = max(
                        candidate[maximum_name],
                        raw_candidate.get(maximum_name, 0),
                    )

    intersections: dict[str, object] = {}
    for intersection_id, tls in merged.items():
        candidates = []
        for index in sorted(tls["candidates"]):
            candidate = dict(tls["candidates"][index])
            available = int(candidate["available_count"])
            selected = int(candidate["selected_count"])
            candidate["selection_rate_when_available"] = (
                selected / available if available else None
            )
            candidate["never_selected_while_available"] = bool(
                available and not selected
            )
            candidates.append(candidate)
        intersections[intersection_id] = {
            "decision_count": int(tls["decision_count"]),
            "episodes_available": int(tls["episodes_available"]),
            "candidates": candidates,
        }
    return {
        "schema": "mappo_action_service_aggregate_v1",
        "episodes_available": len(values),
        "intersections": intersections,
    }


def _describe(values: Sequence[float]) -> dict[str, float | None]:
    if not values:
        return {"sum": None, "mean": None, "min": None, "max": None}
    array = np.asarray(values, dtype=np.float64)
    if not np.isfinite(array).all():
        raise ValueError("diagnostic values must be finite")
    return {
        "sum": float(array.sum()),
        "mean": float(array.mean()),
        "min": float(array.min()),
        "max": float(array.max()),
    }


def _summarize_reward_subset(
    results: Sequence[V5ARewardResult],
) -> dict[str, object]:
    rewards = [float(result.reward) for result in results]
    raw_rewards = [float(result.raw_reward) for result in results]
    components = {
        name: _describe([float(result.components[name]) for result in results])
        for name in _REWARD_COMPONENTS
    }
    weighted_components = {
        label: _describe(
            [float(result.components[name]) * weight for result in results]
        )
        for label, (name, weight) in _REWARD_WEIGHTS.items()
    }
    clipped_count = sum(
        not math.isclose(reward, raw, rel_tol=0.0, abs_tol=1e-9)
        for reward, raw in zip(rewards, raw_rewards, strict=True)
    )
    return {
        "transition_count": len(results),
        "observation_count": sum(int(result.observations) for result in results),
        "observed_seconds": float(
            sum(float(result.observed_seconds) for result in results)
        ),
        "reward": _describe(rewards),
        "raw_reward": _describe(raw_rewards),
        "clipped_count": clipped_count,
        "clipped_fraction": (
            clipped_count / len(results) if results else None
        ),
        "components": components,
        "weighted_components": weighted_components,
    }


def summarize_reward_results(
    results_by_intersection: Mapping[str, Sequence[V5ARewardResult]],
) -> dict[str, object]:
    normalized = {
        str(intersection_id): tuple(results)
        for intersection_id, results in results_by_intersection.items()
    }
    all_results = tuple(
        result
        for intersection_results in normalized.values()
        for result in intersection_results
    )
    summary = _summarize_reward_subset(all_results)
    summary["schema"] = "mappo_reward_accounting_v1"
    summary["intersections"] = {
        intersection_id: _summarize_reward_subset(results)
        for intersection_id, results in normalized.items()
    }
    return summary


def _merge_reward_subsets(
    summaries: Sequence[Mapping[str, object]],
) -> dict[str, object]:
    transition_count = sum(
        int(summary.get("transition_count", 0)) for summary in summaries
    )

    def merge_description(
        descriptions: Sequence[Mapping[str, object]],
    ) -> dict[str, float | None]:
        available = [
            description
            for description in descriptions
            if description.get("sum") is not None
        ]
        if not available or transition_count == 0:
            return {"sum": None, "mean": None, "min": None, "max": None}
        total = sum(float(description["sum"]) for description in available)
        return {
            "sum": total,
            "mean": total / transition_count,
            "min": min(float(description["min"]) for description in available),
            "max": max(float(description["max"]) for description in available),
        }

    def merge_group(group_name: str) -> dict[str, object]:
        names = sorted(
            {
                str(name)
                for summary in summaries
                if isinstance(summary.get(group_name), Mapping)
                for name in summary[group_name]
            }
        )
        return {
            name: merge_description(
                [
                    group[name]
                    for summary in summaries
                    if isinstance((group := summary.get(group_name)), Mapping)
                    and isinstance(group.get(name), Mapping)
                ]
            )
            for name in names
        }

    clipped_count = sum(int(summary.get("clipped_count", 0)) for summary in summaries)
    return {
        "transition_count": transition_count,
        "observation_count": sum(
            int(summary.get("observation_count", 0)) for summary in summaries
        ),
        "observed_seconds": float(
            sum(float(summary.get("observed_seconds", 0.0)) for summary in summaries)
        ),
        "reward": merge_description(
            [
                description
                for summary in summaries
                if isinstance((description := summary.get("reward")), Mapping)
            ]
        ),
        "raw_reward": merge_description(
            [
                description
                for summary in summaries
                if isinstance(
                    (description := summary.get("raw_reward")), Mapping
                )
            ]
        ),
        "clipped_count": clipped_count,
        "clipped_fraction": (
            clipped_count / transition_count if transition_count else None
        ),
        "components": merge_group("components"),
        "weighted_components": merge_group("weighted_components"),
    }


def merge_reward_diagnostics(
    summaries: Iterable[Mapping[str, object]],
) -> dict[str, object]:
    values = tuple(summaries)
    merged = _merge_reward_subsets(values)
    intersection_ids = sorted(
        {
            str(intersection_id)
            for summary in values
            if isinstance(summary.get("intersections"), Mapping)
            for intersection_id in summary["intersections"]
        }
    )
    merged["schema"] = "mappo_reward_accounting_aggregate_v1"
    merged["episodes_available"] = len(values)
    merged["intersections"] = {
        intersection_id: _merge_reward_subsets(
            [
                tls_summary
                for summary in values
                if isinstance(
                    (intersections := summary.get("intersections")), Mapping
                )
                and isinstance(
                    (tls_summary := intersections.get(intersection_id)), Mapping
                )
            ]
        )
        for intersection_id in intersection_ids
    }
    return merged


def _pearson(first: Sequence[float], second: Sequence[float]) -> float | None:
    if len(first) < 2 or len(first) != len(second):
        return None
    left = np.asarray(first, dtype=np.float64)
    right = np.asarray(second, dtype=np.float64)
    if (
        not np.isfinite(left).all()
        or not np.isfinite(right).all()
        or float(left.std()) <= 1e-12
        or float(right.std()) <= 1e-12
    ):
        return None
    return float(np.corrcoef(left, right)[0, 1])


def _reward_features(summary: Mapping[str, object]) -> dict[str, float]:
    features: dict[str, float] = {}
    for name in ("reward", "raw_reward"):
        description = summary.get(name)
        if isinstance(description, Mapping) and description.get("mean") is not None:
            features[f"{name}_mean"] = _finite_float(
                description["mean"], name=f"{name} mean"
            )
    for group_name, prefix in (
        ("components", "component"),
        ("weighted_components", "weighted"),
    ):
        group = summary.get(group_name)
        if not isinstance(group, Mapping):
            continue
        for name, raw_description in group.items():
            if not isinstance(raw_description, Mapping):
                continue
            mean = raw_description.get("mean")
            if mean is not None:
                features[f"{prefix}_{name}_mean"] = _finite_float(
                    mean, name=f"{prefix} {name} mean"
                )
    return features


def reward_metric_alignment(
    records: Iterable[Mapping[str, object]],
) -> dict[str, object]:
    normalized: list[tuple[dict[str, float], dict[str, float]]] = []
    for record in records:
        reward_summary = record.get("reward_diagnostics")
        metrics = record.get("metrics")
        if not isinstance(reward_summary, Mapping) or not isinstance(
            metrics, Mapping
        ):
            continue
        metric_values = {
            str(name): _finite_float(value, name=f"metric {name}")
            for name, value in metrics.items()
            if isinstance(value, (int, float)) and not isinstance(value, bool)
        }
        normalized.append((_reward_features(reward_summary), metric_values))

    feature_names = sorted(
        {name for features, _ in normalized for name in features}
    )
    metric_names = sorted(
        {name for _, metrics in normalized for name in metrics}
    )
    correlations: dict[str, dict[str, float | None]] = {}
    pair_counts: dict[str, dict[str, int]] = {}
    for feature_name in feature_names:
        correlations[feature_name] = {}
        pair_counts[feature_name] = {}
        for metric_name in metric_names:
            pairs = [
                (features[feature_name], metrics[metric_name])
                for features, metrics in normalized
                if feature_name in features and metric_name in metrics
            ]
            correlations[feature_name][metric_name] = _pearson(
                [pair[0] for pair in pairs], [pair[1] for pair in pairs]
            )
            pair_counts[feature_name][metric_name] = len(pairs)
    return {
        "schema": "mappo_reward_metric_alignment_v1",
        "workers_available": len(normalized),
        "correlations": correlations,
        "pair_counts": pair_counts,
    }


def _critic_values(
    policy: MAPPOPolicy,
    transitions: Sequence[Transition],
    *,
    successor: bool,
) -> np.ndarray:
    states = [
        transition.next_global_state if successor else transition.global_state
        for transition in transitions
    ]
    device = next(policy.critic_parameters()).device
    observations = torch.from_numpy(
        np.stack([state.observations for state in states]).astype(
            np.float32, copy=True
        )
    ).to(device)
    masks = torch.from_numpy(
        np.stack([state.agent_mask for state in states]).astype(
            np.bool_, copy=True
        )
    ).to(device)
    owners = torch.tensor(
        [transition.agent_index for transition in transitions],
        dtype=torch.long,
        device=device,
    )
    with torch.no_grad():
        return (
            policy.value(observations, masks, owners)
            .squeeze(-1)
            .detach()
            .cpu()
            .numpy()
            .astype(np.float32, copy=False)
        )


def _advantages_for_values(
    workers: Sequence[Any],
    transitions: Sequence[Transition],
    values: np.ndarray,
    next_values: np.ndarray,
    *,
    config: MAPPOConfig,
) -> np.ndarray:
    advantages = np.empty(len(transitions), dtype=np.float32)
    offset = 0
    for worker in workers:
        worker_transitions = tuple(worker.transitions)
        by_agent: dict[int, list[int]] = {}
        for position, transition in enumerate(worker_transitions):
            by_agent.setdefault(int(transition.agent_index), []).append(position)
        for positions in by_agent.values():
            positions.sort(
                key=lambda position: (
                    worker_transitions[position].decision_time_s,
                    position,
                )
            )
            absolute = np.asarray(
                [offset + position for position in positions], dtype=np.int64
            )
            trajectory = [worker_transitions[position] for position in positions]
            computed, _ = compute_gae(
                rewards=np.asarray(
                    [transition.reward for transition in trajectory],
                    dtype=np.float32,
                ),
                values=values[absolute],
                next_values=next_values[absolute],
                terminated=np.asarray(
                    [transition.terminated for transition in trajectory],
                    dtype=np.bool_,
                ),
                truncated=np.asarray(
                    [transition.truncated for transition in trajectory],
                    dtype=np.bool_,
                ),
                gamma=config.gamma,
                gae_lambda=config.gae_lambda,
            )
            advantages[absolute] = computed
        offset += len(worker_transitions)
    return advantages


def _advantage_summary(values: np.ndarray) -> dict[str, float]:
    return {
        "mean": float(values.mean()),
        "std": float(values.std()),
        "abs_mean": float(np.abs(values).mean()),
        "positive_fraction": float((values > 0.0).mean()),
        "negative_fraction": float((values < 0.0).mean()),
    }


def _normalize_advantages(
    values: np.ndarray,
    agent_indices: np.ndarray,
    *,
    mode: str,
) -> np.ndarray:
    normalized_mode = str(mode).strip().lower()
    if normalized_mode == "none":
        return values.astype(np.float32, copy=True)
    if normalized_mode == "global":
        standard_deviation = float(values.std())
        return (
            (values - float(values.mean()))
            / max(standard_deviation, 1e-8)
        ).astype(np.float32, copy=False)
    if normalized_mode != "per_agent":
        raise ValueError(f"unsupported advantage normalization: {mode}")
    result = np.empty_like(values, dtype=np.float32)
    for agent_index in sorted(set(agent_indices.tolist())):
        mask = agent_indices == agent_index
        subset = values[mask]
        standard_deviation = float(subset.std())
        result[mask] = (
            (subset - float(subset.mean()))
            / max(standard_deviation, 1e-8)
        ).astype(np.float32, copy=False)
    return result


def _flatten_gradients(
    parameters: Sequence[torch.Tensor],
    gradients: Sequence[torch.Tensor | None],
) -> torch.Tensor:
    return torch.cat(
        [
            torch.zeros_like(parameter).reshape(-1)
            if gradient is None
            else gradient.detach().reshape(-1)
            for parameter, gradient in zip(parameters, gradients, strict=True)
        ]
    )


def _actor_gradient_decomposition(
    policy: MAPPOPolicy,
    transitions: Sequence[Transition],
    weights: np.ndarray,
    agent_indices: np.ndarray,
) -> tuple[torch.Tensor, dict[int, torch.Tensor], float, float]:
    parameters = tuple(policy.actor_parameters())
    device = next(iter(parameters)).device
    local_obs = torch.from_numpy(
        np.stack([transition.local_obs for transition in transitions]).astype(
            np.float32, copy=True
        )
    ).to(device)
    phase_features = torch.from_numpy(
        np.stack([transition.phase_features for transition in transitions]).astype(
            np.float32, copy=True
        )
    ).to(device)
    action_mask = torch.from_numpy(
        np.stack([transition.action_mask for transition in transitions]).astype(
            np.bool_, copy=True
        )
    ).to(device)
    actions = torch.tensor(
        [transition.action for transition in transitions],
        dtype=torch.long,
        device=device,
    )
    old_log_probs = torch.tensor(
        [transition.log_prob for transition in transitions],
        dtype=torch.float32,
        device=device,
    )
    distribution = policy.actor(local_obs, phase_features, action_mask)
    log_probs = distribution.log_prob(actions)
    tensor_weights = torch.from_numpy(
        weights.astype(np.float32, copy=False)
    ).to(device)
    direct_loss = -(log_probs * tensor_weights).mean()
    direct_gradient = _flatten_gradients(
        parameters,
        torch.autograd.grad(
            direct_loss,
            parameters,
            allow_unused=True,
            retain_graph=True,
        ),
    )
    agent_values = sorted(set(agent_indices.tolist()))
    contributions: dict[int, torch.Tensor] = {}
    for position, agent_index in enumerate(agent_values):
        mask = torch.from_numpy(agent_indices == agent_index).to(device)
        contribution_loss = -(
            log_probs[mask] * tensor_weights[mask]
        ).sum() / len(transitions)
        contributions[int(agent_index)] = _flatten_gradients(
            parameters,
            torch.autograd.grad(
                contribution_loss,
                parameters,
                allow_unused=True,
                retain_graph=position < len(agent_values) - 1,
            ),
        ).cpu()
    direct_gradient = direct_gradient.cpu()
    reconstructed = torch.stack(tuple(contributions.values())).sum(dim=0)
    reconstruction_error = float((direct_gradient - reconstructed).abs().max())
    replay_error = float((log_probs.detach() - old_log_probs).abs().max())
    return direct_gradient, contributions, replay_error, reconstruction_error


def _cosine(first: torch.Tensor, second: torch.Tensor) -> float | None:
    denominator = float(first.norm() * second.norm())
    if denominator <= 1e-12:
        return None
    return float(torch.dot(first, second) / denominator)


def _gradient_coherence(
    aggregate: torch.Tensor,
    contributions: Mapping[int, torch.Tensor],
) -> float | None:
    denominator = sum(float(value.norm()) for value in contributions.values())
    if denominator <= 1e-12:
        return None
    return min(max(float(aggregate.norm()) / denominator, 0.0), 1.0)


def _mean_pairwise_cosine(
    contributions: Mapping[int, torch.Tensor],
) -> float | None:
    values = tuple(contributions.values())
    cosines = [
        cosine
        for left_index, left in enumerate(values)
        for right in values[left_index + 1 :]
        if (cosine := _cosine(left, right)) is not None
    ]
    return float(np.mean(cosines)) if cosines else None


def _normalization_diagnostic(
    *,
    mode: str,
    actor_policy: MAPPOPolicy,
    transitions: Sequence[Transition],
    agent_indices: np.ndarray,
    intersection_ids: Sequence[str],
    local_advantages: np.ndarray,
    global_advantages: np.ndarray,
) -> tuple[dict[str, object], torch.Tensor, torch.Tensor]:
    local_weights = _normalize_advantages(
        local_advantages, agent_indices, mode=mode
    )
    global_weights = _normalize_advantages(
        global_advantages, agent_indices, mode=mode
    )
    (
        local_gradient,
        local_contributions,
        local_replay_error,
        local_reconstruction_error,
    ) = _actor_gradient_decomposition(
        actor_policy, transitions, local_weights, agent_indices
    )
    (
        global_gradient,
        global_contributions,
        global_replay_error,
        global_reconstruction_error,
    ) = _actor_gradient_decomposition(
        actor_policy, transitions, global_weights, agent_indices
    )
    per_agent = {}
    for agent_index in sorted(local_contributions):
        mask = agent_indices == agent_index
        local_contribution = local_contributions[agent_index]
        global_contribution = global_contributions[agent_index]
        per_agent[str(agent_index)] = {
            "intersection_id": str(intersection_ids[agent_index]),
            "sample_count": int(mask.sum()),
            "local_weight": _advantage_summary(local_weights[mask]),
            "global_weight": _advantage_summary(global_weights[mask]),
            "local_gradient_contribution_norm": float(
                local_contribution.norm()
            ),
            "global_gradient_contribution_norm": float(
                global_contribution.norm()
            ),
            "local_global_gradient_cosine": _cosine(
                local_contribution, global_contribution
            ),
            "local_contribution_to_aggregate_cosine": _cosine(
                local_contribution, local_gradient
            ),
            "global_contribution_to_aggregate_cosine": _cosine(
                global_contribution, global_gradient
            ),
        }
    payload = {
        "sample_count": len(transitions),
        "normalization": mode,
        "local_gradient_norm": float(local_gradient.norm()),
        "global_gradient_norm": float(global_gradient.norm()),
        "local_global_gradient_cosine": _cosine(
            local_gradient, global_gradient
        ),
        "local_global_gradient_delta_norm": float(
            (global_gradient - local_gradient).norm()
        ),
        "local_gradient_coherence_ratio": _gradient_coherence(
            local_gradient, local_contributions
        ),
        "global_gradient_coherence_ratio": _gradient_coherence(
            global_gradient, global_contributions
        ),
        "local_agent_pairwise_cosine_mean": _mean_pairwise_cosine(
            local_contributions
        ),
        "global_agent_pairwise_cosine_mean": _mean_pairwise_cosine(
            global_contributions
        ),
        "aggregate_reconstruction_max_abs_error": max(
            local_reconstruction_error, global_reconstruction_error
        ),
        "actor_rollout_log_prob_max_abs_error": max(
            local_replay_error, global_replay_error
        ),
        "per_agent": per_agent,
    }
    return payload, local_gradient, global_gradient


def _candidate_phase_lookup(
    workers: Sequence[Any],
) -> dict[tuple[str, int], int]:
    phases: dict[tuple[str, int], int] = {}
    for worker in workers:
        diagnostics = getattr(worker, "action_diagnostics", None)
        if not isinstance(diagnostics, Mapping):
            continue
        intersections = diagnostics.get("intersections")
        if not isinstance(intersections, Mapping):
            continue
        for intersection_id, raw_intersection in intersections.items():
            if not isinstance(raw_intersection, Mapping):
                continue
            candidates = raw_intersection.get("candidates")
            if not isinstance(candidates, Sequence):
                continue
            for raw_candidate in candidates:
                if not isinstance(raw_candidate, Mapping):
                    continue
                key = (
                    str(intersection_id),
                    int(raw_candidate["candidate_index"]),
                )
                phase = int(raw_candidate["phase"])
                if key in phases and phases[key] != phase:
                    raise ValueError(
                        "candidate phase semantics changed across probe workers"
                    )
                phases[key] = phase
    return phases


def _pairwise_contrast_sign_disagreement(
    local_means: Mapping[int, float],
    global_means: Mapping[int, float],
) -> tuple[int, float | None]:
    action_indices = sorted(set(local_means) & set(global_means))
    comparisons = [
        np.sign(local_means[left] - local_means[right])
        != np.sign(global_means[left] - global_means[right])
        for position, left in enumerate(action_indices)
        for right in action_indices[position + 1 :]
    ]
    if not comparisons:
        return 0, None
    return len(comparisons), float(np.mean(comparisons))


def _actor_after_plain_gradient_step(
    actor: torch.nn.Module,
    gradient: torch.Tensor,
    *,
    step_size: float,
) -> torch.nn.Module:
    stepped = copy.deepcopy(actor)
    flat_gradient = gradient.detach().to(dtype=torch.float64)
    offset = 0
    with torch.no_grad():
        for parameter in stepped.parameters():
            count = parameter.numel()
            parameter.add_(
                flat_gradient[offset : offset + count]
                .reshape_as(parameter)
                .to(device=parameter.device),
                alpha=-step_size,
            )
            offset += count
    if offset != flat_gradient.numel():
        raise ValueError("Actor gradient size does not match Actor parameters")
    stepped.eval()
    return stepped


def _categorical_kl(
    reference_probabilities: torch.Tensor,
    target_probabilities: torch.Tensor,
) -> torch.Tensor:
    reference = reference_probabilities.to(dtype=torch.float64)
    target = target_probabilities.to(dtype=torch.float64)
    positive = reference > 0.0
    terms = torch.where(
        positive,
        reference
        * (
            reference.clamp_min(1e-300).log()
            - target.clamp_min(1e-300).log()
        ),
        torch.zeros_like(reference),
    )
    return terms.sum(dim=-1).clamp_min(0.0)


def _parameter_step_integrity(
    baseline: torch.nn.Module,
    stepped: torch.nn.Module,
    gradient: torch.Tensor,
    *,
    step_size: float,
) -> tuple[float, float]:
    flat_gradient = gradient.detach().to(dtype=torch.float64)
    squared_norm = 0.0
    max_error = 0.0
    offset = 0
    with torch.no_grad():
        for baseline_parameter, stepped_parameter in zip(
            baseline.parameters(), stepped.parameters(), strict=True
        ):
            count = baseline_parameter.numel()
            delta = (stepped_parameter - baseline_parameter).reshape(-1)
            expected = -step_size * flat_gradient[offset : offset + count].to(
                device=delta.device
            )
            squared_norm += float(torch.dot(delta, delta))
            max_error = max(max_error, float((delta - expected).abs().max()))
            offset += count
    if offset != flat_gradient.numel():
        raise ValueError("Actor gradient size does not match Actor parameters")
    return math.sqrt(squared_norm), max_error


def _one_step_policy_response_diagnostic(
    *,
    workers: Sequence[Any],
    actor_policy: MAPPOPolicy,
    transitions: Sequence[Transition],
    agent_indices: np.ndarray,
    intersection_ids: Sequence[str],
    local_gradient: torch.Tensor,
    global_gradient: torch.Tensor,
    step_size: float,
) -> dict[str, object]:
    """Measure the probability geometry of one plain loss-gradient step."""

    if not math.isfinite(step_size) or step_size <= 0.0:
        raise ValueError("policy response step size must be positive and finite")
    device = next(actor_policy.actor_parameters()).device
    baseline_actor = copy.deepcopy(actor_policy.actor).to(
        device=device, dtype=torch.float64
    )
    baseline_actor.eval()
    local_actor = _actor_after_plain_gradient_step(
        baseline_actor, local_gradient, step_size=step_size
    )
    global_actor = _actor_after_plain_gradient_step(
        baseline_actor, global_gradient, step_size=step_size
    )
    local_step_norm, local_step_error = _parameter_step_integrity(
        baseline_actor,
        local_actor,
        local_gradient,
        step_size=step_size,
    )
    global_step_norm, global_step_error = _parameter_step_integrity(
        baseline_actor,
        global_actor,
        global_gradient,
        step_size=step_size,
    )
    local_obs = torch.from_numpy(
        np.stack([transition.local_obs for transition in transitions]).astype(
            np.float64, copy=True
        )
    ).to(device)
    phase_features = torch.from_numpy(
        np.stack([transition.phase_features for transition in transitions]).astype(
            np.float64, copy=True
        )
    ).to(device)
    action_mask = torch.from_numpy(
        np.stack([transition.action_mask for transition in transitions]).astype(
            np.bool_, copy=True
        )
    ).to(device)
    actions = torch.tensor(
        [transition.action for transition in transitions],
        dtype=torch.long,
        device=device,
    )
    with torch.no_grad():
        baseline_probabilities = baseline_actor(
            local_obs, phase_features, action_mask
        ).probs
        local_probabilities = local_actor(
            local_obs, phase_features, action_mask
        ).probs
        global_probabilities = global_actor(
            local_obs, phase_features, action_mask
        ).probs

    local_response = local_probabilities - baseline_probabilities
    global_response = global_probabilities - baseline_probabilities
    row_indices = torch.arange(len(transitions), device=device)
    local_selected_response = local_response[row_indices, actions]
    global_selected_response = global_response[row_indices, actions]
    baseline_to_local_kl = _categorical_kl(
        baseline_probabilities, local_probabilities
    )
    baseline_to_global_kl = _categorical_kl(
        baseline_probabilities, global_probabilities
    )
    local_to_global_kl = _categorical_kl(
        local_probabilities, global_probabilities
    )
    global_to_local_kl = _categorical_kl(
        global_probabilities, local_probabilities
    )
    phase_lookup = _candidate_phase_lookup(workers)
    per_agent: dict[str, object] = {}
    best_match_count = 0
    multi_candidate_count = 0
    for agent_index in sorted(set(agent_indices.tolist())):
        numpy_owner_mask = agent_indices == agent_index
        owner_mask = torch.from_numpy(numpy_owner_mask).to(device)
        owner_action_mask = action_mask[owner_mask]
        candidate_rows: dict[str, object] = {}
        local_candidate_means: dict[int, float] = {}
        global_candidate_means: dict[int, float] = {}
        intersection_id = str(intersection_ids[agent_index])
        for candidate_index in range(action_mask.shape[1]):
            available = owner_action_mask[:, candidate_index]
            if not bool(available.any()):
                continue
            local_mean = float(
                local_response[owner_mask, candidate_index][available].mean()
            )
            global_mean = float(
                global_response[owner_mask, candidate_index][available].mean()
            )
            local_candidate_means[candidate_index] = local_mean
            global_candidate_means[candidate_index] = global_mean
            candidate_rows[str(candidate_index)] = {
                "candidate_index": candidate_index,
                "phase": phase_lookup.get(
                    (intersection_id, candidate_index)
                ),
                "available_sample_count": int(available.sum()),
                "local_mean_probability_response": local_mean,
                "global_mean_probability_response": global_mean,
            }
        local_best = max(
            local_candidate_means,
            key=lambda candidate: (
                local_candidate_means[candidate],
                -candidate,
            ),
        )
        global_best = max(
            global_candidate_means,
            key=lambda candidate: (
                global_candidate_means[candidate],
                -candidate,
            ),
        )
        if len(local_candidate_means) > 1:
            multi_candidate_count += 1
            best_match_count += int(local_best == global_best)
        owner_local_response = local_response[owner_mask]
        owner_global_response = global_response[owner_mask]
        owner_valid = owner_action_mask
        owner_local_selected = local_selected_response[owner_mask]
        owner_global_selected = global_selected_response[owner_mask]
        per_agent[str(agent_index)] = {
            "intersection_id": intersection_id,
            "sample_count": int(owner_mask.sum()),
            "candidate_count": len(local_candidate_means),
            "local_global_valid_probability_response_cosine": _cosine(
                owner_local_response[owner_valid].cpu(),
                owner_global_response[owner_valid].cpu(),
            ),
            "local_global_selected_action_response_cosine": _cosine(
                owner_local_selected.cpu(), owner_global_selected.cpu()
            ),
            "selected_action_response_sign_disagreement_fraction": float(
                (
                    torch.sign(owner_local_selected)
                    != torch.sign(owner_global_selected)
                )
                .to(dtype=torch.float64)
                .mean()
            ),
            "local_probability_response_abs_mean": float(
                owner_local_response[owner_valid].abs().mean()
            ),
            "global_probability_response_abs_mean": float(
                owner_global_response[owner_valid].abs().mean()
            ),
            "baseline_to_local_kl_mean": float(
                baseline_to_local_kl[owner_mask].mean()
            ),
            "baseline_to_global_kl_mean": float(
                baseline_to_global_kl[owner_mask].mean()
            ),
            "local_to_global_kl_mean": float(
                local_to_global_kl[owner_mask].mean()
            ),
            "global_to_local_kl_mean": float(
                global_to_local_kl[owner_mask].mean()
            ),
            "local_best_response_candidate_index": int(local_best),
            "global_best_response_candidate_index": int(global_best),
            "best_response_candidate_matches": local_best == global_best,
            "actions": candidate_rows,
        }

    valid = action_mask
    probability_mass_error = max(
        float(local_response.sum(dim=-1).abs().max()),
        float(global_response.sum(dim=-1).abs().max()),
    )
    return {
        "schema": "mappo_one_step_policy_response_v1",
        "step_definition": (
            "plain_sgd_full_batch_loss_gradient_step_without_adam_entropy_"
            "ppo_ratio_clipping_gradient_clipping_or_minibatches"
        ),
        "step_size": float(step_size),
        "sample_count": len(transitions),
        "local_parameter_step_norm": local_step_norm,
        "global_parameter_step_norm": global_step_norm,
        "parameter_step_reconstruction_max_abs_error": max(
            local_step_error, global_step_error
        ),
        "probability_mass_conservation_max_abs_error": probability_mass_error,
        "local_global_valid_probability_response_cosine": _cosine(
            local_response[valid].cpu(), global_response[valid].cpu()
        ),
        "local_global_selected_action_response_cosine": _cosine(
            local_selected_response.cpu(), global_selected_response.cpu()
        ),
        "selected_action_response_sign_disagreement_fraction": float(
            (
                torch.sign(local_selected_response)
                != torch.sign(global_selected_response)
            )
            .to(dtype=torch.float64)
            .mean()
        ),
        "local_probability_response_abs_mean": float(
            local_response[valid].abs().mean()
        ),
        "global_probability_response_abs_mean": float(
            global_response[valid].abs().mean()
        ),
        "baseline_to_local_kl_mean": float(baseline_to_local_kl.mean()),
        "baseline_to_global_kl_mean": float(baseline_to_global_kl.mean()),
        "local_to_global_kl_mean": float(local_to_global_kl.mean()),
        "global_to_local_kl_mean": float(global_to_local_kl.mean()),
        "multi_candidate_agent_count": multi_candidate_count,
        "best_response_candidate_match_count": best_match_count,
        "best_response_candidate_mismatch_count": (
            multi_candidate_count - best_match_count
        ),
        "per_agent": per_agent,
    }


def _action_score_covariance_diagnostic(
    *,
    workers: Sequence[Any],
    actor_policy: MAPPOPolicy,
    transitions: Sequence[Transition],
    agent_indices: np.ndarray,
    intersection_ids: Sequence[str],
    local_advantages: np.ndarray,
    global_advantages: np.ndarray,
    expected_local_gradient: torch.Tensor,
    expected_global_gradient: torch.Tensor,
) -> dict[str, object]:
    """Exactly decompose the current Actor loss gradient by selected action."""

    local_weights = _normalize_advantages(
        local_advantages, agent_indices, mode="global"
    )
    global_weights = _normalize_advantages(
        global_advantages, agent_indices, mode="global"
    )
    parameters = tuple(actor_policy.actor_parameters())
    device = next(iter(parameters)).device
    local_obs = torch.from_numpy(
        np.stack([transition.local_obs for transition in transitions]).astype(
            np.float32, copy=True
        )
    ).to(device)
    phase_features = torch.from_numpy(
        np.stack([transition.phase_features for transition in transitions]).astype(
            np.float32, copy=True
        )
    ).to(device)
    action_mask = torch.from_numpy(
        np.stack([transition.action_mask for transition in transitions]).astype(
            np.bool_, copy=True
        )
    ).to(device)
    actions = np.asarray(
        [transition.action for transition in transitions], dtype=np.int64
    )
    action_tensor = torch.from_numpy(actions).to(device)
    distribution = actor_policy.actor(local_obs, phase_features, action_mask)
    log_probs = distribution.log_prob(action_tensor)
    local_weight_tensor = torch.from_numpy(local_weights).to(device)
    global_weight_tensor = torch.from_numpy(global_weights).to(device)
    sample_count = len(transitions)
    groups = sorted(
        {
            (int(agent_index), int(action))
            for agent_index, action in zip(
                agent_indices.tolist(), actions.tolist(), strict=True
            )
        }
    )

    losses: list[tuple[tuple[int, int, str], torch.Tensor]] = []
    group_masks: dict[tuple[int, int], np.ndarray] = {}
    for agent_index, action in groups:
        numpy_mask = (agent_indices == agent_index) & (actions == action)
        group_masks[(agent_index, action)] = numpy_mask
        mask = torch.from_numpy(numpy_mask).to(device)
        losses.extend(
            (
                (
                    (agent_index, action, "score"),
                    -log_probs[mask].sum() / sample_count,
                ),
                (
                    (agent_index, action, "local"),
                    -(
                        log_probs[mask] * local_weight_tensor[mask]
                    ).sum()
                    / sample_count,
                ),
                (
                    (agent_index, action, "global"),
                    -(
                        log_probs[mask] * global_weight_tensor[mask]
                    ).sum()
                    / sample_count,
                ),
            )
        )

    gradients: dict[tuple[int, int, str], torch.Tensor] = {}
    for position, (key, loss) in enumerate(losses):
        gradients[key] = _flatten_gradients(
            parameters,
            torch.autograd.grad(
                loss,
                parameters,
                allow_unused=True,
                retain_graph=position < len(losses) - 1,
            ),
        ).cpu()

    phase_lookup = _candidate_phase_lookup(workers)
    weighted_local: dict[tuple[int, int], torch.Tensor] = {}
    weighted_global: dict[tuple[int, int], torch.Tensor] = {}
    mean_local: dict[tuple[int, int], torch.Tensor] = {}
    mean_global: dict[tuple[int, int], torch.Tensor] = {}
    covariance_local: dict[tuple[int, int], torch.Tensor] = {}
    covariance_global: dict[tuple[int, int], torch.Tensor] = {}
    action_rows: dict[tuple[int, int], dict[str, object]] = {}
    for key, numpy_mask in group_masks.items():
        agent_index, action = key
        score_gradient = gradients[(agent_index, action, "score")]
        local_gradient = gradients[(agent_index, action, "local")]
        global_gradient = gradients[(agent_index, action, "global")]
        local_mean_weight = float(local_weights[numpy_mask].mean())
        global_mean_weight = float(global_weights[numpy_mask].mean())
        local_mean_gradient = local_mean_weight * score_gradient
        global_mean_gradient = global_mean_weight * score_gradient
        local_covariance_gradient = local_gradient - local_mean_gradient
        global_covariance_gradient = global_gradient - global_mean_gradient
        weighted_local[key] = local_gradient
        weighted_global[key] = global_gradient
        mean_local[key] = local_mean_gradient
        mean_global[key] = global_mean_gradient
        covariance_local[key] = local_covariance_gradient
        covariance_global[key] = global_covariance_gradient
        intersection_id = str(intersection_ids[agent_index])
        action_rows[key] = {
            "candidate_index": action,
            "phase": phase_lookup.get((intersection_id, action)),
            "sample_count": int(numpy_mask.sum()),
            "selection_fraction_within_agent": float(
                numpy_mask.sum() / (agent_indices == agent_index).sum()
            ),
            "local_weight": _advantage_summary(local_weights[numpy_mask]),
            "global_weight": _advantage_summary(global_weights[numpy_mask]),
            "score_loss_gradient_norm": float(score_gradient.norm()),
            "local_weighted_loss_gradient_norm": float(local_gradient.norm()),
            "global_weighted_loss_gradient_norm": float(global_gradient.norm()),
            "local_global_weighted_loss_gradient_cosine": _cosine(
                local_gradient, global_gradient
            ),
            "local_mean_component_norm": float(local_mean_gradient.norm()),
            "global_mean_component_norm": float(global_mean_gradient.norm()),
            "local_within_action_covariance_component_norm": float(
                local_covariance_gradient.norm()
            ),
            "global_within_action_covariance_component_norm": float(
                global_covariance_gradient.norm()
            ),
            "local_global_within_action_covariance_component_cosine": _cosine(
                local_covariance_gradient, global_covariance_gradient
            ),
        }

    def sum_gradients(
        values: Mapping[tuple[int, int], torch.Tensor],
        keys: Sequence[tuple[int, int]],
    ) -> torch.Tensor:
        return torch.stack([values[key] for key in keys]).sum(dim=0)

    per_agent: dict[str, object] = {}
    for agent_index in sorted(set(agent_indices.tolist())):
        agent_keys = [key for key in groups if key[0] == agent_index]
        local_action_means = {
            action: float(local_weights[group_masks[(agent_index, action)]].mean())
            for _, action in agent_keys
        }
        global_action_means = {
            action: float(global_weights[group_masks[(agent_index, action)]].mean())
            for _, action in agent_keys
        }
        contrast_count, contrast_disagreement = (
            _pairwise_contrast_sign_disagreement(
                local_action_means, global_action_means
            )
        )
        local_best = max(
            local_action_means,
            key=lambda action: (local_action_means[action], -action),
        )
        global_best = max(
            global_action_means,
            key=lambda action: (global_action_means[action], -action),
        )
        agent_local_weighted = sum_gradients(weighted_local, agent_keys)
        agent_global_weighted = sum_gradients(weighted_global, agent_keys)
        agent_local_covariance = sum_gradients(covariance_local, agent_keys)
        agent_global_covariance = sum_gradients(covariance_global, agent_keys)
        per_agent[str(agent_index)] = {
            "intersection_id": str(intersection_ids[agent_index]),
            "sample_count": int((agent_indices == agent_index).sum()),
            "observed_candidate_count": len(agent_keys),
            "local_action_weighted_gradient_norm": float(
                agent_local_weighted.norm()
            ),
            "global_action_weighted_gradient_norm": float(
                agent_global_weighted.norm()
            ),
            "local_global_action_weighted_gradient_cosine": _cosine(
                agent_local_weighted, agent_global_weighted
            ),
            "local_within_action_covariance_gradient_norm": float(
                agent_local_covariance.norm()
            ),
            "global_within_action_covariance_gradient_norm": float(
                agent_global_covariance.norm()
            ),
            "local_global_within_action_covariance_gradient_cosine": _cosine(
                agent_local_covariance, agent_global_covariance
            ),
            "local_global_action_conditional_mean_weight_correlation": _pearson(
                [local_action_means[action] for action in sorted(local_action_means)],
                [global_action_means[action] for action in sorted(global_action_means)],
            ),
            "local_best_candidate_index": int(local_best),
            "global_best_candidate_index": int(global_best),
            "best_candidate_matches": local_best == global_best,
            "pairwise_action_contrast_count": contrast_count,
            "pairwise_action_contrast_sign_disagreement_fraction": (
                contrast_disagreement
            ),
            "actions": {
                str(action): action_rows[(agent_index, action)]
                for _, action in agent_keys
            },
        }

    aggregate_local_weighted = sum_gradients(weighted_local, groups)
    aggregate_global_weighted = sum_gradients(weighted_global, groups)
    aggregate_local_mean = sum_gradients(mean_local, groups)
    aggregate_global_mean = sum_gradients(mean_global, groups)
    aggregate_local_covariance = sum_gradients(covariance_local, groups)
    aggregate_global_covariance = sum_gradients(covariance_global, groups)
    reconstruction_error = max(
        float((aggregate_local_weighted - expected_local_gradient).abs().max()),
        float((aggregate_global_weighted - expected_global_gradient).abs().max()),
    )
    decomposition_error = max(
        float(
            (
                aggregate_local_weighted
                - aggregate_local_mean
                - aggregate_local_covariance
            )
            .abs()
            .max()
        ),
        float(
            (
                aggregate_global_weighted
                - aggregate_global_mean
                - aggregate_global_covariance
            )
            .abs()
            .max()
        ),
    )
    return {
        "schema": "mappo_action_score_covariance_v1",
        "gradient_definition": (
            "first_step_actor_loss_gradient_by_agent_and_selected_action_"
            "with_global_advantage_normalization_without_entropy_or_ppo_clipping"
        ),
        "normalization": "global",
        "sample_count": sample_count,
        "observed_action_group_count": len(groups),
        "aggregate_weighted_gradient_reconstruction_max_abs_error": (
            reconstruction_error
        ),
        "aggregate_mean_plus_covariance_reconstruction_max_abs_error": (
            decomposition_error
        ),
        "local_weighted_gradient_norm": float(aggregate_local_weighted.norm()),
        "global_weighted_gradient_norm": float(aggregate_global_weighted.norm()),
        "local_global_weighted_gradient_cosine": _cosine(
            aggregate_local_weighted, aggregate_global_weighted
        ),
        "local_mean_component_norm": float(aggregate_local_mean.norm()),
        "global_mean_component_norm": float(aggregate_global_mean.norm()),
        "local_global_mean_component_cosine": _cosine(
            aggregate_local_mean, aggregate_global_mean
        ),
        "local_within_action_covariance_component_norm": float(
            aggregate_local_covariance.norm()
        ),
        "global_within_action_covariance_component_norm": float(
            aggregate_global_covariance.norm()
        ),
        "local_global_within_action_covariance_component_cosine": _cosine(
            aggregate_local_covariance, aggregate_global_covariance
        ),
        "per_agent": per_agent,
    }


def probe_critic_signal(
    workers: Iterable[Any],
    *,
    config: MAPPOConfig,
    actor_policy: MAPPOPolicy,
    local_policy: MAPPOPolicy,
    global_policy: MAPPOPolicy,
) -> dict[str, object]:
    """Compare Local/Global Critic advantages on one frozen-Actor rollout."""

    worker_values = tuple(workers)
    transitions = tuple(
        transition
        for worker in worker_values
        for transition in tuple(worker.transitions)
    )
    if not transitions:
        raise ValueError("critic signal probe requires transitions")
    if any(not isinstance(transition, Transition) for transition in transitions):
        raise TypeError("critic signal probe requires Transition values")
    if local_policy.critic_scope != "local":
        raise ValueError("local policy must use a local critic")
    if global_policy.critic_scope != "global":
        raise ValueError("global policy must use a global critic")

    local_values = _critic_values(local_policy, transitions, successor=False)
    local_next_values = _critic_values(local_policy, transitions, successor=True)
    global_values = _critic_values(global_policy, transitions, successor=False)
    global_next_values = _critic_values(global_policy, transitions, successor=True)
    local_advantages = _advantages_for_values(
        worker_values,
        transitions,
        local_values,
        local_next_values,
        config=config,
    )
    global_advantages = _advantages_for_values(
        worker_values,
        transitions,
        global_values,
        global_next_values,
        config=config,
    )
    agent_indices = np.asarray(
        [transition.agent_index for transition in transitions], dtype=np.int64
    )
    normalization_ablations: dict[str, object] = {}
    normalization_gradients: dict[
        str, tuple[torch.Tensor, torch.Tensor]
    ] = {}
    for normalization in ("global", "per_agent", "none"):
        diagnostic, local_gradient, global_gradient = (
            _normalization_diagnostic(
                mode=normalization,
                actor_policy=actor_policy,
                transitions=transitions,
                agent_indices=agent_indices,
                intersection_ids=config.intersection_ids,
                local_advantages=local_advantages,
                global_advantages=global_advantages,
            )
        )
        normalization_ablations[normalization] = diagnostic
        normalization_gradients[normalization] = (
            local_gradient,
            global_gradient,
        )
    global_normalized = normalization_ablations["global"]
    global_local_gradient, global_global_gradient = normalization_gradients[
        "global"
    ]
    per_agent_local_gradient, per_agent_global_gradient = (
        normalization_gradients["per_agent"]
    )
    global_normalized_cosine = global_normalized[
        "local_global_gradient_cosine"
    ]
    per_agent_normalized_cosine = normalization_ablations["per_agent"][
        "local_global_gradient_cosine"
    ]
    cosine_delta = (
        None
        if global_normalized_cosine is None
        or per_agent_normalized_cosine is None
        else per_agent_normalized_cosine - global_normalized_cosine
    )
    per_agent: dict[str, object] = {}
    for agent_index in sorted(set(agent_indices.tolist())):
        mask = agent_indices == agent_index
        per_agent[str(agent_index)] = {
            "intersection_id": str(config.intersection_ids[agent_index]),
            "sample_count": int(mask.sum()),
            "advantage_correlation": _pearson(
                local_advantages[mask].tolist(),
                global_advantages[mask].tolist(),
            ),
            "advantage_sign_disagreement_fraction": float(
                (
                    np.sign(local_advantages[mask])
                    != np.sign(global_advantages[mask])
                ).mean()
            ),
            "local_advantage": _advantage_summary(local_advantages[mask]),
            "global_advantage": _advantage_summary(global_advantages[mask]),
        }
    action_score_covariance = _action_score_covariance_diagnostic(
        workers=worker_values,
        actor_policy=actor_policy,
        transitions=transitions,
        agent_indices=agent_indices,
        intersection_ids=config.intersection_ids,
        local_advantages=local_advantages,
        global_advantages=global_advantages,
        expected_local_gradient=global_local_gradient,
        expected_global_gradient=global_global_gradient,
    )
    one_step_policy_response = _one_step_policy_response_diagnostic(
        workers=worker_values,
        actor_policy=actor_policy,
        transitions=transitions,
        agent_indices=agent_indices,
        intersection_ids=config.intersection_ids,
        local_gradient=global_local_gradient,
        global_gradient=global_global_gradient,
        step_size=config.actor_lr,
    )
    return {
        "schema": "mappo_critic_signal_probe_v4",
        "sample_count": len(transitions),
        "worker_count": len(worker_values),
        "actor_gradient_definition": (
            "first_step_score_function_surrogate_without_entropy_or_ppo_clipping"
        ),
        "actor_rollout_log_prob_max_abs_error": global_normalized[
            "actor_rollout_log_prob_max_abs_error"
        ],
        "advantage_correlation": _pearson(
            local_advantages.tolist(), global_advantages.tolist()
        ),
        "advantage_sign_disagreement_fraction": float(
            (np.sign(local_advantages) != np.sign(global_advantages)).mean()
        ),
        "local_advantage": _advantage_summary(local_advantages),
        "global_advantage": _advantage_summary(global_advantages),
        "local_actor_gradient_norm": global_normalized[
            "local_gradient_norm"
        ],
        "global_actor_gradient_norm": global_normalized[
            "global_gradient_norm"
        ],
        "actor_gradient_cosine": global_normalized[
            "local_global_gradient_cosine"
        ],
        "per_agent": per_agent,
        "normalization_ablations": normalization_ablations,
        "action_score_covariance": action_score_covariance,
        "one_step_policy_response": one_step_policy_response,
        "normalization_comparison": {
            "local_global_cosine_delta_per_agent_minus_global": cosine_delta,
            "local_gradient_cosine_per_agent_vs_global": _cosine(
                per_agent_local_gradient, global_local_gradient
            ),
            "global_gradient_cosine_per_agent_vs_global": _cosine(
                per_agent_global_gradient, global_global_gradient
            ),
        },
    }


def advantage_quantiles(advantages: torch.Tensor) -> dict[str, float]:
    values = advantages.detach().float().cpu().numpy()
    return {
        "mean": float(values.mean()),
        "std": float(values.std()),
        "p10": float(np.percentile(values, 10)),
        "p50": float(np.percentile(values, 50)),
        "p90": float(np.percentile(values, 90)),
        "positive_fraction": float((values > 0).mean()),
    }


def td_target_duplicate_stats(
    returns: torch.Tensor, joint_step_index: torch.Tensor
) -> dict[str, float]:
    """统计同一 joint step 内完全相同的 target 行占比。"""
    joint_ids = joint_step_index.detach().cpu().numpy()
    values = returns.detach().float().cpu().numpy()
    duplicate_count = 0
    total = 0
    for jid in np.unique(joint_ids):
        rows = values[joint_ids == jid]
        if rows.size > 1:
            dup = int(np.sum(rows[1:] == rows[0]))
            duplicate_count += dup
            total += rows.size - 1
    return {
        "duplicate_rate": float(duplicate_count / total) if total else 0.0,
        "variance": float(values.var()),
    }


def actor_grad_cosine(
    actor: torch.nn.Module,
    local_obs: torch.Tensor,
    phase_features: torch.Tensor,
    action_mask: torch.Tensor,
    actions: torch.Tensor,
) -> dict[str, float]:
    """按样本行计算 Actor policy loss 梯度，输出行间 off-diagonal cosine 分布。

    每行独立 forward+backward（同批内逐行，梯度互不污染）；
    vanilla 诊断用途：共享 team advantage 下各行梯度同向，cosine 应接近 1.0。
    """
    import torch.nn.functional as F

    per_row: list[torch.Tensor] = []
    for row in range(local_obs.shape[0]):
        actor.zero_grad(set_to_none=True)
        dist = actor(
            local_obs[row : row + 1],
            phase_features[row : row + 1],
            action_mask[row : row + 1],
        )
        loss = -dist.log_prob(actions[row : row + 1]).mean()
        loss.backward()
        grads = [
            p.grad.flatten() for p in actor.parameters() if p.grad is not None
        ]
        if not grads:
            raise RuntimeError("actor produced no gradients")
        per_row.append(torch.cat(grads))
    matrix = torch.stack(per_row)
    normed = F.normalize(matrix, dim=1)
    cos = normed @ normed.T
    off_diag = cos[~torch.eye(cos.shape[0], dtype=torch.bool)].cpu().numpy()
    return {
        "mean": float(off_diag.mean()),
        "p10": float(np.percentile(off_diag, 10)),
        "p50": float(np.percentile(off_diag, 50)),
        "p90": float(np.percentile(off_diag, 90)),
    }
