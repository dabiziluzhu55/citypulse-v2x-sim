from __future__ import annotations

from copy import deepcopy
import math
import time
from typing import Any, Mapping

import numpy as np
import torch

from algorithms.mappo.checkpoint import policy_digest
from algorithms.mappo.config import (
        COOPERATIVE_MODEL_VERSION,
    REWARD_SCOPE_SHARED_TEAM,
    MAPPOConfig,
    configuration_signature,
)
from algorithms.mappo.diagnostics import (
    ActionServiceDiagnostics,
    summarize_reward_results,
)
from algorithms.mappo.features import (
    CentralizedState,
    CentralizedStateBuilder,
    IPPOV8FeatureBuilder,
    IPPO_V8_LOCAL_OBSERVATION_SCHEMA,
)
from algorithms.mappo.models import MAPPOPolicy
from algorithms.mappo.parallel_train import WorkerRollout
from traffic_control.ippo.identity import IDENTITY_SLOT_IDS, identity_slots_for
from algorithms.mappo.joint_rollout import (
    JointExecutionAlignedRollout,
    JointPendingTransition,
    JointTransition,
)
from algorithms.mappo.reward import (
    TeamRewardResult,
    V5ARewardAccumulator,
    V5ARewardResult,
    aggregate_team_reward,
)
from algorithms.mappo.rollout import (
    ActionAlignmentError,
    ExecutionAlignedRollout,
    PendingTransition,
    Transition,
)


_SUPPORTED_MODES = {"random", "fixed", "model", "train", "collect"}


def _next_intersection(vehicle: Mapping[str, Any]) -> str | None:
    next_signal = vehicle.get("next_signal")
    if not isinstance(next_signal, Mapping):
        return None
    intersection_id = next_signal.get("intersection_id")
    return str(intersection_id) if intersection_id is not None else None


class MAPPOController:
    """One worker's Protocol-v2 controller with execution-aligned CTDE data."""

    def __init__(
        self,
        *,
        metadata: Mapping[str, Any],
        config: MAPPOConfig,
        policy: MAPPOPolicy,
        mode: str,
        policy_generation: int,
        expected_duration_s: float,
        record_evaluation: bool,
        rollout_seed: int = 0,
    ) -> None:
        normalized_mode = str(mode).strip().lower()
        if normalized_mode not in _SUPPORTED_MODES:
            raise ValueError(f"unsupported MAPPO mode: {mode!r}")
        if int(policy_generation) < 0:
            raise ValueError("policy generation must be non-negative")
        if float(expected_duration_s) <= 0.0:
            raise ValueError("expected duration must be positive")

        self.metadata = deepcopy(dict(metadata))
        self.config = config
        self.policy = policy
        self._shared_team = (
            config.reward_scope == REWARD_SCOPE_SHARED_TEAM
        )
        self.mode = normalized_mode
        self._inference_only = normalized_mode == "model"
        self.policy_generation = int(policy_generation)
        self.expected_duration_s = float(expected_duration_s)
        self.record_evaluation = bool(record_evaluation)
        self.rollout_seed = int(rollout_seed)
        self.episode_id = str(metadata.get("episode_id", ""))
        self._feature_builder = IPPOV8FeatureBuilder(metadata)
        if self._feature_builder.intersection_ids != config.intersection_ids:
            raise ValueError(
                "metadata intersection order does not match frozen configuration"
            )
        if self._feature_builder.max_state_dim != config.obs_dim:
            raise ValueError("metadata observation dimension does not match config")
        if self._feature_builder.max_phases <= 0:
            raise ValueError("MAPPO requires at least one candidate phase")
        if self._feature_builder.max_phases > config.max_action_dim:
            raise ValueError("metadata action dimension exceeds frozen config")
        if policy.actor.obs_dim != config.obs_dim:
            raise ValueError("policy observation dimension does not match config")
        if policy.model_version != config.model_version:
            raise ValueError("policy model version does not match config")
        if policy.actor_variant != config.actor_variant:
            raise ValueError("policy actor variant does not match config")
        if policy.actor.phase_feature_dim != config.phase_feature_dim:
            raise ValueError(
                "policy phase feature dimension does not match config"
            )
        if policy.actor.hidden_dim != config.hidden_dim:
            raise ValueError("policy hidden dimension does not match config")
        if policy.critic_scope != config.critic_scope:
            raise ValueError("policy critic scope does not match config")
        if policy.critic.obs_dim != config.obs_dim:
            raise ValueError("critic observation dimension does not match config")
        if policy.critic.hidden_dim != config.hidden_dim:
            raise ValueError("critic hidden dimension does not match config")
        if policy.critic.num_agents != len(IDENTITY_SLOT_IDS):
            raise ValueError(
                "critic agent count does not match the fixed 20-slot identity"
            )

        self.intersection_ids = config.intersection_ids
        self._agent_indices = {
            intersection_id: index
            for index, intersection_id in enumerate(self.intersection_ids)
        }
        self._slot_indices = {
            intersection_id: slot
            for intersection_id, slot in zip(
                self.intersection_ids,
                identity_slots_for(self.intersection_ids),
            )
        }
        self._phase_orders = {
            intersection_id: tuple(
                self._feature_builder.get_phase_order(intersection_id)
            )
            for intersection_id in self.intersection_ids
        }
        if any(not phases for phases in self._phase_orders.values()):
            raise ValueError("every controlled intersection must have phases")
        self._act_dim = config.max_action_dim
        self._central_builder = CentralizedStateBuilder(
            self.intersection_ids, config.obs_dim
        )
        self._decision_interval = float(metadata.get("decision_interval", 5.0))
        self._minimum_green = float(metadata.get("minimum_green", 5.0))
        if self._decision_interval <= 0.0 or self._minimum_green < 0.0:
            raise ValueError("invalid Protocol decision timing")
        self._action_interval = max(
            config.action_interval_s,
            self._decision_interval,
            self._minimum_green,
        )

        self._pressure_shapers: dict[str, "PressureShaper"] = {}
        if config.pressure_shaping_enabled:
            from algorithms.common.pressure_shaping import PressureShaper
            for intersection_id in self.intersection_ids:
                phase_conns = self._feature_builder.get_phase_connections(
                    intersection_id
                )
                self._pressure_shapers[intersection_id] = PressureShaper(
                    phase_conns,
                    epsilon=config.pressure_shaping_epsilon,
                )

        self._rollouts = {
            intersection_id: ExecutionAlignedRollout()
            for intersection_id in self.intersection_ids
        }
        self._trajectories: dict[str, list[Transition]] = {
            intersection_id: [] for intersection_id in self.intersection_ids
        }
        self._joint_rollout = JointExecutionAlignedRollout(
            len(self.intersection_ids),
            require_shared_values=config.requires_shared_values,
            team_value_mode="scalar",
            expected_state_schema=config.centralized_state_schema,
        )
        self._joint_transitions: list[JointTransition] = []
        self._next_joint_step_id = 0
        self._rewards: dict[str, V5ARewardAccumulator] = {}
        self._reward_results: dict[str, list[V5ARewardResult]] = {
            intersection_id: [] for intersection_id in self.intersection_ids
        }
        self._team_reward_results: list[TeamRewardResult] = []
        self._application_confirmed = {
            intersection_id: False for intersection_id in self.intersection_ids
        }
        self._last_decision_times = {
            intersection_id: -math.inf
            for intersection_id in self.intersection_ids
        }
        self._last_phase_service_times = {
            intersection_id: {
                phase: 0.0 for phase in self._phase_orders[intersection_id]
            }
            for intersection_id in self.intersection_ids
        }
        self._action_diagnostics = ActionServiceDiagnostics(self._phase_orders)
        self._latest_local: dict[str, np.ndarray] = {}
        self._latest_global: CentralizedState | None = None
        self._vehicle_reward_state: dict[str, tuple[float, str | None]] = {}
        self._last_reward_time: float | None = None
        self._invalid_reason: str | None = None
        self._dropped_pending = 0

        if self.record_evaluation:
            from algorithms.evaluation import runtime as evaluation_runtime

            evaluation_runtime.start("MAPPO", dict(metadata))

    @property
    def invalid(self) -> bool:
        return self._invalid_reason is not None

    @property
    def invalid_reason(self) -> str | None:
        return self._invalid_reason

    @property
    def trajectories(self) -> dict[str, tuple[Transition, ...]]:
        return {
            intersection_id: tuple(self._trajectories[intersection_id])
            for intersection_id in self.intersection_ids
        }

    @property
    def reward_results(self) -> dict[str, tuple[V5ARewardResult, ...]]:
        return {
            intersection_id: tuple(self._reward_results[intersection_id])
            for intersection_id in self.intersection_ids
        }

    @property
    def action_diagnostics(self) -> dict[str, object]:
        return self._action_diagnostics.snapshot()

    def _shared_reward_diagnostics(self) -> dict[str, object]:
        team_results = tuple(
            V5ARewardResult(
                reward=result.reward,
                raw_reward=result.raw_reward,
                components=dict(result.components),
                observations=result.observations,
                observed_seconds=result.observed_seconds,
            )
            for result in self._team_reward_results
        )
        summary = summarize_reward_results({"team": team_results})
        local_summary = summarize_reward_results(self.reward_results)
        summary["schema"] = "mappo_team_reward_accounting_v1"
        summary["team_reward_schema"] = self.config.team_reward_schema
        summary["intersections"] = local_summary["intersections"]
        return summary

    def _fail(self, error: Exception) -> None:
        self._invalid_reason = str(error)

    def _validate_intersections(
        self, payload: Mapping[str, Any]
    ) -> Mapping[str, Any]:
        intersections = payload.get("intersections", {})
        if not isinstance(intersections, Mapping):
            error = ValueError("intersections must be a mapping")
            self._fail(error)
            raise error
        supplied = {str(value) for value in intersections}
        expected = set(self.intersection_ids)
        missing = [
            value for value in self.intersection_ids if value not in supplied
        ]
        extra = sorted(supplied - expected)
        if missing:
            error = ValueError(
                "missing controlled intersections: " + ", ".join(missing)
            )
            self._fail(error)
            raise error
        if extra:
            error = ValueError("unexpected intersections: " + ", ".join(extra))
            self._fail(error)
            raise error
        return intersections

    def _vehicle_interval_statistics(
        self, payload: Mapping[str, Any]
    ) -> tuple[dict[str, float], dict[str, int]]:
        delays = {intersection_id: 0.0 for intersection_id in self.intersection_ids}
        crossings = {intersection_id: 0 for intersection_id in self.intersection_ids}
        current: dict[str, tuple[float, str | None]] = {}
        controlled = set(self.intersection_ids)
        vehicles = payload.get("vehicles", {})
        if not isinstance(vehicles, Mapping):
            vehicles = {}
        for vehicle_id, raw_vehicle in vehicles.items():
            if not isinstance(raw_vehicle, Mapping):
                continue
            traffic = raw_vehicle.get("traffic", {})
            time_loss = (
                float(traffic.get("time_loss_s", 0.0))
                if isinstance(traffic, Mapping)
                else 0.0
            )
            next_intersection = _next_intersection(raw_vehicle)
            previous = self._vehicle_reward_state.get(str(vehicle_id))
            if previous is not None:
                previous_time_loss, previous_intersection = previous
                target = (
                    previous_intersection
                    if previous_intersection in controlled
                    else next_intersection
                )
                if target in controlled:
                    delays[target] += max(time_loss - previous_time_loss, 0.0)
                if (
                    previous_intersection in controlled
                    and next_intersection != previous_intersection
                ):
                    crossings[previous_intersection] += 1
            current[str(vehicle_id)] = (time_loss, next_intersection)
        for vehicle_id, (_, previous_intersection) in self._vehicle_reward_state.items():
            if vehicle_id not in current and previous_intersection in controlled:
                crossings[previous_intersection] += 1
        self._vehicle_reward_state = current
        return delays, crossings

    def _incoming_waiting(
        self, intersection_id: str, intersection: Mapping[str, Any]
    ) -> float:
        lanes = intersection.get("lanes", {})
        if not isinstance(lanes, Mapping):
            lanes = {}
        return sum(
            float(
                lanes.get(lane_id, {}).get("waiting_time", 0.0)
                if isinstance(lanes.get(lane_id, {}), Mapping)
                else 0.0
            )
            for lane_id in self._feature_builder.get_incoming_lanes(
                intersection_id
            )
        )

    def _pre_action_density(
        self, intersection_id: str, intersection: Mapping[str, Any]
    ) -> float:
        lanes = intersection.get("lanes", {})
        if not isinstance(lanes, Mapping):
            return 0.0
        incoming = self._feature_builder.get_incoming_lanes(intersection_id)
        if not incoming:
            return 0.0
        total, count = 0.0, 0
        for lane_id in incoming:
            lane = lanes.get(lane_id, {})
            if isinstance(lane, Mapping):
                total += float(lane.get("occupancy", 0.0))
                count += 1
        return total / count if count else 0.0

    def _new_reward(
        self, intersection_id: str, intersection: Mapping[str, Any]
    ) -> V5ARewardAccumulator:
        lanes = (
            self._feature_builder.get_incoming_lanes(intersection_id)
            + self._feature_builder.get_outgoing_lanes(intersection_id)
        )
        return V5ARewardAccumulator(
            incoming_lanes=self._feature_builder.get_incoming_lanes(
                intersection_id
            ),
            outgoing_lanes=self._feature_builder.get_outgoing_lanes(
                intersection_id
            ),
            lane_capacities={
                lane_id: self._feature_builder.get_lane_capacity(
                    intersection_id, lane_id
                )
                for lane_id in lanes
            },
            incoming_capacity=self._feature_builder.get_incoming_capacity(
                intersection_id
            ),
            flow_reference_rate=self._feature_builder.get_flow_reference_rate(
                intersection_id
            ),
            waiting_start=self._incoming_waiting(intersection_id, intersection),
        )

    def _padded_global(
        self, global_state: CentralizedState
    ) -> tuple[np.ndarray, np.ndarray]:
        """Pad the active-subset global state to canonical identity slots.

        The joint rollout keeps active-subset states (observations and mask
        sized by the controlled intersections), while every critic is a
        20-slot model from the fixed identity contract.  Inactive slots are
        zero-filled and masked out before the critic call.
        """
        observations = np.zeros(
            (len(IDENTITY_SLOT_IDS), self.config.obs_dim),
            dtype=np.float32,
        )
        agent_mask = np.zeros(len(IDENTITY_SLOT_IDS), dtype=np.bool_)
        for intersection_id in self.intersection_ids:
            slot = self._slot_indices[intersection_id]
            row = int(self._agent_indices[intersection_id])
            observations[slot] = np.asarray(
                global_state.observations[row], dtype=np.float32
            )
            agent_mask[slot] = True
        return observations, agent_mask

    def _value(
        self, global_state: CentralizedState, agent_index: int
    ) -> float:
        with torch.no_grad():
            observations_20, mask_20 = self._padded_global(global_state)
            observations = torch.from_numpy(observations_20).unsqueeze(0)
            mask = torch.from_numpy(mask_20).unsqueeze(0)
            owner = torch.tensor([agent_index], dtype=torch.long)
            return float(
                self.policy.value(observations, mask, owner).squeeze().item()
            )

    def _choose_action(
        self,
        local_obs: np.ndarray,
        phase_features: np.ndarray,
        action_mask: np.ndarray,
    ) -> tuple[int, float]:
        valid_actions = np.flatnonzero(action_mask)
        if valid_actions.size == 0:
            raise RuntimeError("MAPPO action mask contains no valid phase")
        if self.mode == "fixed":
            return int(valid_actions[0]), 0.0
        if self.mode == "random":
            return int(np.random.choice(valid_actions)), 0.0
        with torch.no_grad():
            observation_tensor = torch.from_numpy(local_obs).unsqueeze(0)
            phase_tensor = torch.from_numpy(phase_features).unsqueeze(0)
            mask_tensor = torch.from_numpy(action_mask).unsqueeze(0)
            distribution = self.policy.actor(
                observation_tensor, phase_tensor, mask_tensor
            )
            action = (
                torch.argmax(distribution.probs, dim=-1)
                if self.mode == "model"
                else distribution.sample()
            )
            return int(action.item()), float(distribution.log_prob(action).item())

    def _eligible(
        self,
        intersection: Mapping[str, Any],
        simulation_time: float,
        last_decision: float,
    ) -> bool:
        return (
            str(intersection.get("stage", "")).upper() == "GREEN"
            and intersection.get("pending_phase") is None
            and float(intersection.get("stage_elapsed", 0.0)) + 1e-9
            >= self._minimum_green
            and simulation_time + 1e-9
            >= last_decision + self._action_interval
        )

    def _complete_pending(
        self,
        intersection_id: str,
        local_obs: np.ndarray,
        global_state: CentralizedState,
        *,
        terminated: bool,
        truncated: bool,
    ) -> None:
        rollout = self._rollouts[intersection_id]
        if rollout.pending is None:
            return
        if not self._application_confirmed[intersection_id]:
            raise RuntimeError("requested phase application has not been confirmed")
        accumulator = self._rewards.pop(intersection_id)
        result = accumulator.finalize()
        rollout.add_reward(result.reward)
        next_value = (
            0.0
            if terminated
            else self._value(
                global_state, self._slot_indices[intersection_id]
            )
        )
        transition = rollout.complete(
            local_obs,
            global_state,
            next_value,
            terminated=terminated,
            truncated=truncated,
        )
        self._trajectories[intersection_id].append(transition)
        self._reward_results[intersection_id].append(result)
        self._application_confirmed[intersection_id] = False

    def _joint_values(
        self, global_state: CentralizedState
    ) -> tuple[float, ...]:
        active_slots = tuple(
            self._slot_indices[intersection_id]
            for intersection_id in self.intersection_ids
        )
        if self.config.requires_shared_values:
            team_value = self._value(global_state, active_slots[0])
            return (team_value,) * len(self.intersection_ids)
        if self.config.model_version == COOPERATIVE_MODEL_VERSION:
            return tuple(
                self._value(global_state, slot) for slot in active_slots
            )
        num_slots = len(IDENTITY_SLOT_IDS)
        with torch.no_grad():
            observations_20, mask_20 = self._padded_global(global_state)
            observations = torch.from_numpy(observations_20).unsqueeze(
                0
            ).expand(num_slots, -1, -1)
            mask = torch.from_numpy(mask_20).unsqueeze(0).expand(
                num_slots, -1
            )
            owners = torch.arange(num_slots, dtype=torch.long)
            values = self.policy.value(
                observations, mask, owners
            ).squeeze(-1)
        return tuple(
            float(values[slot]) for slot in active_slots
        )

    def _complete_joint_pending(
        self,
        local_states: Mapping[str, np.ndarray],
        global_state: CentralizedState,
        simulation_time: float,
        next_values: tuple[float, ...],
        *,
        terminated: bool,
        truncated: bool,
    ) -> None:
        pending = self._joint_rollout.pending
        if pending is None:
            return
        if not all(
            self._application_confirmed[intersection_id]
            for intersection_id in self.intersection_ids
        ):
            raise RuntimeError(
                "not every requested joint phase application has been confirmed"
            )
        if set(self._rewards) != set(self.intersection_ids):
            raise RuntimeError("joint reward accumulator set is incomplete")

        local_results = {
            intersection_id: self._rewards[intersection_id].finalize()
            for intersection_id in self.intersection_ids
        }
        window_start_s = float(
            pending.agent_pendings[0].decision_time_s
        )
        team_result = aggregate_team_reward(
            local_results,
            self.intersection_ids,
            window_start_s,
            simulation_time,
        )
        joint = self._joint_rollout.complete(
            next_local_observations=tuple(
                local_states[intersection_id]
                for intersection_id in self.intersection_ids
            ),
            next_global_state=global_state,
            next_values=next_values,
            team_reward=team_result.reward,
            team_raw_reward=team_result.raw_reward,
            window_end_s=simulation_time,
            raw_local_rewards=team_result.per_intersection_raw_rewards,
            terminated=terminated,
            truncated=truncated,
        )
        self._joint_transitions.append(joint)
        self._team_reward_results.append(team_result)
        for intersection_id in self.intersection_ids:
            self._reward_results[intersection_id].append(
                local_results[intersection_id]
            )
            self._rewards.pop(intersection_id)
            self._application_confirmed[intersection_id] = False

    def _shared_signal_actions(
        self,
        payload: Mapping[str, Any],
        intersections: Mapping[str, Any],
        simulation_time: float,
        local_states: Mapping[str, np.ndarray],
        global_state: CentralizedState | None,
    ) -> dict[str, dict[str, int]]:
        eligible_ids = tuple(
            intersection_id
            for intersection_id in self.intersection_ids
            if self._eligible(
                intersections[intersection_id],
                simulation_time,
                self._last_decision_times[intersection_id],
            )
        )
        if eligible_ids and len(eligible_ids) != len(self.intersection_ids):
            raise RuntimeError(
                "partial joint eligibility: eligible "
                f"{eligible_ids}, expected {self.intersection_ids}"
            )
        if not eligible_ids:
            return {}

        boundary_values: tuple[float, ...] | None = None
        if self._joint_rollout.pending is not None:
            if global_state is None:
                raise RuntimeError(
                    "inference-only controller created a pending joint rollout"
                )
            for intersection_id in self.intersection_ids:
                if self._application_confirmed[intersection_id]:
                    continue
                index = self._agent_indices[intersection_id]
                current_phase = int(
                    intersections[intersection_id].get("current_phase", -1)
                )
                self._joint_rollout.confirm_applied(
                    index, current_phase, simulation_time
                )
                self._application_confirmed[intersection_id] = True
            boundary_values = self._joint_values(global_state)
            self._complete_joint_pending(
                local_states,
                global_state,
                simulation_time,
                boundary_values,
                terminated=False,
                truncated=False,
            )

        staged_actions: list[
            tuple[str, np.ndarray, np.ndarray, int, float, int]
        ] = []
        _sp_contexts: dict[str, tuple[float, float]] = {}
        for intersection_id in self.intersection_ids:
            intersection = intersections[intersection_id]
            local_features = self._feature_builder.build_phase_features(
                intersection_id,
                intersection,
                simulation_time=simulation_time,
                last_service_times=self._last_phase_service_times[
                    intersection_id
                ],
                vehicles=(
                    payload.get("vehicles", {})
                    if self.config.effective_demand_enabled
                    else {}
                ),
                demand_horizon_seconds=self._action_interval,
            )
            local_mask, _ = self._feature_builder.build_action_mask(
                intersection_id,
                intersection,
                max_green_factor=self.config.max_green_factor,
            )
            phase_features = np.zeros(
                (self._act_dim, self.config.phase_feature_dim),
                dtype=np.float32,
            )
            phase_features[: len(local_features)] = local_features
            action_mask = np.zeros(self._act_dim, dtype=np.bool_)
            action_mask[: len(local_mask)] = local_mask
            action_index, log_probability = self._choose_action(
                local_states[intersection_id],
                phase_features,
                action_mask,
            )
            target_phase = self._phase_orders[intersection_id][action_index]
            _sp_regret: float = 0.0
            _sp_alpha: float = 0.0
            if (
                self.config.pressure_shaping_enabled
                and intersection_id in self._pressure_shapers
            ):
                shaper = self._pressure_shapers[intersection_id]
                legal_phase_ids = [
                    self._phase_orders[intersection_id][i]
                    for i, m in enumerate(local_mask) if m
                ]
                result = shaper.compute_pressure_regret(
                    intersections[intersection_id].get("lanes", {}),
                    legal_phases=legal_phase_ids,
                    selected_phase=target_phase,
                )
                _sp_regret = result.regret
                occupancy_pct = self._pre_action_density(
                    intersection_id, intersections[intersection_id]
                )
                from algorithms.common.pressure_shaping import density_gate
                _density, _sp_alpha = density_gate(
                    occupancy_pct,
                    threshold=self.config.pressure_shaping_density_threshold,
                    alpha_base=self.config.pressure_shaping_alpha_base,
                    density_decay=self.config.pressure_shaping_density_decay,
                )
            staged_actions.append(
                (
                    intersection_id,
                    phase_features,
                    action_mask,
                    action_index,
                    log_probability,
                    target_phase,
                )
            )
            if self.config.pressure_shaping_enabled:
                _sp_contexts[intersection_id] = (_sp_regret, _sp_alpha)

        if not self._inference_only:
            if global_state is None:
                raise RuntimeError("training controller has no centralized state")
            values = (
                boundary_values
                if boundary_values is not None
                else self._joint_values(global_state)
            )
            staged_rewards = {
                intersection_id: self._new_reward(
                    intersection_id, intersections[intersection_id]
                )
                for intersection_id in self.intersection_ids
            }
            if self.config.pressure_shaping_enabled:
                for intersection_id, (regret, alpha) in _sp_contexts.items():
                    if intersection_id in staged_rewards:
                        staged_rewards[intersection_id].set_pressure_context(
                            regret=regret, alpha=alpha,
                        )
            pendings = tuple(
                PendingTransition(
                    local_obs=local_states[intersection_id],
                    phase_features=phase_features,
                    action_mask=action_mask,
                    global_state=global_state,
                    agent_index=self._agent_indices[intersection_id],
                    action=action_index,
                    requested_phase=target_phase,
                    log_prob=log_probability,
                    value=values[self._agent_indices[intersection_id]],
                    decision_time_s=simulation_time,
                    policy_generation=self.policy_generation,
                )
                for (
                    intersection_id,
                    phase_features,
                    action_mask,
                    action_index,
                    log_probability,
                    target_phase,
                ) in staged_actions
            )
            self._joint_rollout.begin(
                JointPendingTransition(
                    joint_step_id=self._next_joint_step_id,
                    agent_pendings=pendings,
                )
            )
            self._next_joint_step_id += 1
            self._rewards.update(staged_rewards)
            for (
                intersection_id,
                _phase_features,
                _action_mask,
                _action_index,
                _log_probability,
                target_phase,
            ) in staged_actions:
                intersection = intersections[intersection_id]
                current_phase = int(
                    intersection.get("current_phase", target_phase)
                )
                if (
                    str(intersection.get("stage", "")).upper() == "GREEN"
                    and current_phase == target_phase
                ):
                    index = self._agent_indices[intersection_id]
                    self._joint_rollout.confirm_applied(
                        index, current_phase, simulation_time
                    )
                    self._application_confirmed[intersection_id] = True

        signal_actions: dict[str, dict[str, int]] = {}
        for (
            intersection_id,
            _phase_features,
            action_mask,
            action_index,
            _log_probability,
            target_phase,
        ) in staged_actions:
            self._action_diagnostics.observe_decision(
                intersection_id,
                action_mask=action_mask[
                    : len(self._phase_orders[intersection_id])
                ],
                selected_action=action_index,
            )
            signal_actions[intersection_id] = {
                "target_phase": int(target_phase)
            }
            self._last_decision_times[intersection_id] = simulation_time
        return signal_actions

    def _protocol_response(
        self,
        payload: Mapping[str, Any],
        signal_actions: Mapping[str, Mapping[str, int]],
        decision_started: float,
    ) -> dict[str, Any]:
        response = {
            "protocol_version": "2.0",
            "episode_id": payload["episode_id"],
            "step_id": payload["step_id"],
            "actions": {
                "signals": dict(signal_actions),
                "vehicles": {},
            },
        }
        if self.record_evaluation:
            from algorithms.evaluation import runtime as evaluation_runtime

            evaluation_runtime.record_latency(
                (time.perf_counter() - decision_started) * 1000.0,
                episode_id=str(payload["episode_id"]),
            )
            evaluation_runtime.observe_decision(dict(payload))
        return response

    def step(self, payload: Mapping[str, Any]) -> dict[str, Any]:
        if self.invalid:
            raise RuntimeError(f"MAPPO worker is invalid: {self._invalid_reason}")
        decision_started = time.perf_counter()
        if self.episode_id and str(payload.get("episode_id", "")) != self.episode_id:
            error = ValueError("step episode_id does not match initialized episode")
            self._fail(error)
            raise error
        intersections = self._validate_intersections(payload)
        simulation_time = float(
            payload.get(
                "simulation_time",
                float(payload.get("step_id", 0)) * self._decision_interval,
            )
        )
        if not math.isfinite(simulation_time):
            error = ValueError("simulation time must be finite")
            self._fail(error)
            raise error

        local_states = self._feature_builder.get_all_states(payload)
        global_state = (
            None
            if self._inference_only
            else self._central_builder.build(local_states)
        )
        self._latest_local = local_states
        self._latest_global = global_state

        for intersection_id in self.intersection_ids:
            intersection = intersections[intersection_id]
            if not isinstance(intersection, Mapping):
                error = ValueError(
                    f"intersection {intersection_id} must be a mapping"
                )
                self._fail(error)
                raise error
            self._action_diagnostics.observe_state(
                intersection_id,
                simulation_time_s=simulation_time,
                stage=str(intersection.get("stage", "")),
                current_phase=int(intersection.get("current_phase", -1)),
            )
            if str(intersection.get("stage", "")).upper() == "GREEN":
                current_phase = int(intersection.get("current_phase", -1))
                if current_phase in self._last_phase_service_times[intersection_id]:
                    self._last_phase_service_times[intersection_id][
                        current_phase
                    ] = simulation_time
                if self._shared_team:
                    joint_pending = self._joint_rollout.pending
                    if joint_pending is not None:
                        index = self._agent_indices[intersection_id]
                        requested_phase = int(
                            joint_pending.agent_pendings[
                                index
                            ].requested_phase
                        )
                        if (
                            current_phase == requested_phase
                            and not self._application_confirmed[
                                intersection_id
                            ]
                        ):
                            self._joint_rollout.confirm_applied(
                                index, current_phase, simulation_time
                            )
                            self._application_confirmed[
                                intersection_id
                            ] = True
                else:
                    pending = self._rollouts[intersection_id].pending
                    if (
                        pending is not None
                        and current_phase == pending.requested_phase
                        and not self._application_confirmed[intersection_id]
                    ):
                        self._rollouts[intersection_id].confirm_applied(
                            current_phase, simulation_time
                        )
                        self._application_confirmed[intersection_id] = True

        if not self._inference_only:
            delays, crossings = self._vehicle_interval_statistics(payload)
            elapsed_seconds = (
                0.0
                if self._last_reward_time is None
                else max(simulation_time - self._last_reward_time, 0.0)
            )
            self._last_reward_time = simulation_time
            for intersection_id, accumulator in tuple(
                self._rewards.items()
            ):
                accumulator.observe(
                    intersections[intersection_id],
                    elapsed_seconds=elapsed_seconds,
                    delay_increment=delays.get(intersection_id, 0.0),
                    crossings=crossings.get(intersection_id, 0),
                )

        if self._shared_team:
            try:
                signal_actions = self._shared_signal_actions(
                    payload,
                    intersections,
                    simulation_time,
                    local_states,
                    global_state,
                )
            except (ActionAlignmentError, RuntimeError, ValueError) as error:
                self._fail(error)
                raise
            return self._protocol_response(
                payload, signal_actions, decision_started
            )

        signal_actions: dict[str, dict[str, int]] = {}
        try:
            for intersection_id in self.intersection_ids:
                intersection = intersections[intersection_id]
                if not self._eligible(
                    intersection,
                    simulation_time,
                    self._last_decision_times[intersection_id],
                ):
                    continue
                rollout = self._rollouts[intersection_id]
                if rollout.pending is not None:
                    if global_state is None:
                        raise RuntimeError(
                            "inference-only controller created a pending rollout"
                        )
                    if not self._application_confirmed[intersection_id]:
                        rollout.confirm_applied(
                            int(intersection.get("current_phase", -1)),
                            simulation_time,
                        )
                        self._application_confirmed[intersection_id] = True
                    self._complete_pending(
                        intersection_id,
                        local_states[intersection_id],
                        global_state,
                        terminated=False,
                        truncated=False,
                    )

                local_features = self._feature_builder.build_phase_features(
                    intersection_id,
                    intersection,
                    simulation_time=simulation_time,
                    last_service_times=self._last_phase_service_times[
                        intersection_id
                    ],
                    vehicles=(
                        payload.get("vehicles", {})
                        if self.config.effective_demand_enabled
                        else {}
                    ),
                    demand_horizon_seconds=self._action_interval,
                )
                local_mask, _ = self._feature_builder.build_action_mask(
                    intersection_id,
                    intersection,
                    max_green_factor=self.config.max_green_factor,
                )
                phase_features = np.zeros(
                    (self._act_dim, self.config.phase_feature_dim),
                    dtype=np.float32,
                )
                phase_features[: len(local_features)] = local_features
                action_mask = np.zeros(self._act_dim, dtype=np.bool_)
                action_mask[: len(local_mask)] = local_mask
                action_index, log_probability = self._choose_action(
                    local_states[intersection_id],
                    phase_features,
                    action_mask,
                )
                self._action_diagnostics.observe_decision(
                    intersection_id,
                    action_mask=action_mask[
                        : len(self._phase_orders[intersection_id])
                    ],
                    selected_action=action_index,
                )
                target_phase = self._phase_orders[intersection_id][action_index]
                _pressure_regret: float = 0.0
                _pressure_alpha: float = 0.0
                if (
                    self.config.pressure_shaping_enabled
                    and intersection_id in self._pressure_shapers
                    and not self._inference_only
                ):
                    shaper = self._pressure_shapers[intersection_id]
                    legal_phase_ids = [
                        self._phase_orders[intersection_id][i]
                        for i, m in enumerate(local_mask) if m
                    ]
                    result = shaper.compute_pressure_regret(
                        intersection.get("lanes", {}),
                        legal_phases=legal_phase_ids,
                        selected_phase=target_phase,
                    )
                    _pressure_regret = result.regret
                    occupancy_pct = self._pre_action_density(
                        intersection_id, intersection
                    )
                    from algorithms.common.pressure_shaping import density_gate
                    _density, _pressure_alpha = density_gate(
                        occupancy_pct,
                        threshold=self.config.pressure_shaping_density_threshold,
                        alpha_base=self.config.pressure_shaping_alpha_base,
                        density_decay=self.config.pressure_shaping_density_decay,
                    )
                if not self._inference_only:
                    if global_state is None:
                        raise RuntimeError(
                            "training controller has no centralized state"
                        )
                    value = self._value(
                        global_state, self._slot_indices[intersection_id]
                    )
                    rollout.begin(
                        PendingTransition(
                            local_obs=local_states[intersection_id],
                            phase_features=phase_features,
                            action_mask=action_mask,
                            global_state=global_state,
                            agent_index=self._agent_indices[intersection_id],
                            action=action_index,
                            requested_phase=target_phase,
                            log_prob=log_probability,
                            value=value,
                            decision_time_s=simulation_time,
                            policy_generation=self.policy_generation,
                        )
                    )
                    self._rewards[intersection_id] = self._new_reward(
                        intersection_id, intersection
                    )
                    if self.config.pressure_shaping_enabled:
                        self._rewards[intersection_id].set_pressure_context(
                            regret=_pressure_regret, alpha=_pressure_alpha,
                        )
                    current_phase = int(
                        intersection.get("current_phase", target_phase)
                    )
                    if (
                        str(intersection.get("stage", "")).upper() == "GREEN"
                        and current_phase == target_phase
                    ):
                        rollout.confirm_applied(
                            current_phase, simulation_time
                        )
                        self._application_confirmed[intersection_id] = True
                signal_actions[intersection_id] = {
                    "target_phase": int(target_phase)
                }
                self._last_decision_times[intersection_id] = simulation_time
        except (ActionAlignmentError, RuntimeError, ValueError) as error:
            self._fail(error)
            raise

        return self._protocol_response(
            payload, signal_actions, decision_started
        )

    def _clear_rollout(self) -> None:
        self._rollouts = {
            intersection_id: ExecutionAlignedRollout()
            for intersection_id in self.intersection_ids
        }
        self._trajectories = {
            intersection_id: [] for intersection_id in self.intersection_ids
        }
        self._joint_rollout = JointExecutionAlignedRollout(
            len(self.intersection_ids),
            require_shared_values=self.config.requires_shared_values,
            team_value_mode="scalar",
            expected_state_schema=self.config.centralized_state_schema,
        )
        self._joint_transitions = []
        self._next_joint_step_id = 0
        self._team_reward_results = []
        self._rewards.clear()
        self._application_confirmed = {
            intersection_id: False for intersection_id in self.intersection_ids
        }
        self._latest_local.clear()
        self._latest_global = None
        self._dropped_pending = 0

    def _finish_shared(
        self, final_time: float, *, time_limit: bool
    ) -> WorkerRollout | None:
        pending = self._joint_rollout.pending
        if pending is not None:
            complete_application = all(
                self._application_confirmed[intersection_id]
                for intersection_id in self.intersection_ids
            )
            complete_rewards = (
                set(self._rewards) == set(self.intersection_ids)
                and all(
                    self._rewards[intersection_id].observations > 0
                    for intersection_id in self.intersection_ids
                )
            )
            observed_through_finish = (
                self._last_reward_time is not None
                and abs(self._last_reward_time - final_time) <= 1e-9
            )
            if not (
                complete_application
                and complete_rewards
                and observed_through_finish
            ):
                error = RuntimeError(
                    "incomplete final joint transition invalidates worker batch"
                )
                self._fail(error)
                self._clear_rollout()
                return None
            try:
                assert self._latest_global is not None
                next_values = (
                    self._joint_values(self._latest_global)
                    if time_limit
                    else (0.0,) * len(self.intersection_ids)
                )
                self._complete_joint_pending(
                    self._latest_local,
                    self._latest_global,
                    final_time,
                    next_values,
                    terminated=not time_limit,
                    truncated=time_limit,
                )
            except (ActionAlignmentError, RuntimeError, ValueError) as error:
                self._fail(error)
                self._clear_rollout()
                return None
        elif self._rewards:
            error = RuntimeError(
                "joint rewards exist without a pending joint transition"
            )
            self._fail(error)
            self._clear_rollout()
            return None

        transitions = tuple(self._joint_transitions)
        if not transitions:
            self._clear_rollout()
            return None
        return WorkerRollout(
            seed=self.rollout_seed,
            status="ok",
            policy_generation=self.policy_generation,
            policy_digest=policy_digest(self.policy),
            config_signature=configuration_signature(self.config),
            local_observation_schema=IPPO_V8_LOCAL_OBSERVATION_SCHEMA,
            centralized_state_schema=self.config.centralized_state_schema,
            transitions=transitions,
            pending_count=0,
            invalid_reason=None,
            error=None,
            dropped_pending=0,
            action_diagnostics=self.action_diagnostics,
            reward_diagnostics=self._shared_reward_diagnostics(),
            reward_scope=self.config.reward_scope,
            team_reward_schema=self.config.team_reward_schema,
            joint_step_schema=self.config.joint_step_schema,
            period=str(self.metadata.get("period", "")),
            metadata=deepcopy(self.metadata),
        )

    def finish(self, payload: Mapping[str, Any]) -> WorkerRollout | None:
        if self.record_evaluation:
            from algorithms.evaluation import runtime as evaluation_runtime

            evaluation_payload = dict(payload)
            evaluation_payload.setdefault("episode_id", self.episode_id)
            evaluation_runtime.finish(evaluation_payload)
        if self._inference_only:
            self._clear_rollout()
            return None
        reason = str(payload.get("reason", "error")).lower()
        if reason != "completed" or self.invalid:
            self._clear_rollout()
            return None
        if self._latest_global is None:
            self._clear_rollout()
            return None
        final_time = float(payload.get("simulation_time", math.nan))
        if not math.isfinite(final_time):
            error = ValueError(
                "completed finish payload requires finite simulation_time"
            )
            self._fail(error)
            self._clear_rollout()
            return None
        time_limit = final_time + 1e-9 >= self.expected_duration_s

        if self._shared_team:
            return self._finish_shared(final_time, time_limit=time_limit)

        try:
            for intersection_id in self.intersection_ids:
                rollout = self._rollouts[intersection_id]
                if rollout.pending is None:
                    continue
                accumulator = self._rewards.get(intersection_id)
                if (
                    not self._application_confirmed[intersection_id]
                    or accumulator is None
                    or accumulator.observations == 0
                ):
                    if rollout.discard_pending():
                        self._dropped_pending += 1
                    self._rewards.pop(intersection_id, None)
                    self._application_confirmed[intersection_id] = False
                    continue
                self._complete_pending(
                    intersection_id,
                    self._latest_local[intersection_id],
                    self._latest_global,
                    terminated=not time_limit,
                    truncated=time_limit,
                )
        except (RuntimeError, ValueError) as error:
            self._fail(error)
            self._clear_rollout()
            return None

        transitions = tuple(
            transition
            for intersection_id in self.intersection_ids
            for transition in self._trajectories[intersection_id]
        )
        if not transitions:
            self._clear_rollout()
            return None
        return WorkerRollout(
            seed=self.rollout_seed,
            status="ok",
            policy_generation=self.policy_generation,
            policy_digest=policy_digest(self.policy),
            config_signature=configuration_signature(self.config),
            local_observation_schema=IPPO_V8_LOCAL_OBSERVATION_SCHEMA,
            centralized_state_schema=self.config.centralized_state_schema,
            transitions=transitions,
            pending_count=0,
            invalid_reason=None,
            error=None,
            dropped_pending=self._dropped_pending,
            action_diagnostics=self.action_diagnostics,
            reward_diagnostics=summarize_reward_results(self.reward_results),
            reward_scope=self.config.reward_scope,
            team_reward_schema=self.config.team_reward_schema,
            joint_step_schema=self.config.joint_step_schema,
            period=str(self.metadata.get("period", "")),
            metadata=deepcopy(self.metadata),
        )
