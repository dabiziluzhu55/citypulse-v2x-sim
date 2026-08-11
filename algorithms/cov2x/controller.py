"""CoV2X Protocol 2.0 controller: frozen signal + trainable vehicle policy.

This is the algorithm-side Stage-2 closed loop:

- ``COV2X_SIGNAL_MODE=max_pressure`` (default) freezes signals to a
  deterministic MaxPressure mirror; ``fixed`` holds the current phase.
- ``COV2X_VEHICLE_MODE=learned`` (default) drives the shared
  approach-advisor policy; ``rule`` uses the conservative rule baseline;
  ``off`` sends no vehicle commands (untreated-traffic baseline).
- ``COV2X_MODE=train`` collects one episode of transitions, exposes them via
  :func:`take_collected_rollout`, and applies a PPO update via
  :func:`train_on_rollout`.  ``eval`` uses deterministic argmax actions.

The module intentionally imports PyTorch lazily so that rule-mode and
signal-side code remain testable without a torch runtime.
"""

from __future__ import annotations

import json
import os
from dataclasses import asdict
from pathlib import Path
from typing import Any, Mapping

import numpy as np

from algorithms.cov2x.vehicle.agent import (
    LANE_ACTION_KEEP,
    LANE_ACTION_LEFT,
    LANE_ACTION_RIGHT,
    SPEED_FRACTIONS,
    VehicleAction,
    VehicleActionError,
    validate_vehicle_action,
)
from algorithms.cov2x.cloud.observations import (
    build_cloud_observation,
    build_global_state,
    neutral_priority,
    priority_for_action,
)
from algorithms.cov2x.collab.joint_rewards import (
    TEAM_WEIGHT_DEFAULT,
    TrafficSnapshot,
    cloud_local_reward,
    signal_local_reward,
    team_reward,
)
from algorithms.cov2x.collab.joint_rollout import (
    CloudRolloutStep,
    JointRollout,
    SignalRolloutStep,
    compute_gae as compute_joint_gae,
)
from algorithms.cov2x.vehicle.observations import build_vehicle_observations
from algorithms.cov2x.vehicle.rewards import vehicle_reward, vehicle_reward_components
from algorithms.cov2x.vehicle.rollout import (
    Rollout,
    RolloutStep,
    compute_gae,
    episode_summary,
    to_training_arrays,
)
from algorithms.cov2x.road.signal import fixed_actions, max_pressure_actions

CHECKPOINT_FORMAT_VERSION = 1
JOINT_CHECKPOINT_FORMAT_VERSION = 2


_mode = "eval"
_signal_mode = "max_pressure"
_cloud_mode = "off"
_vehicle_mode = "learned"
_team_weight = TEAM_WEIGHT_DEFAULT
_model_path: str | None = None
_resume_loaded_path: str | None = None
_checkpoint_dir: str | None = None
_decision_interval_s = 5.0
_signal_decision_interval_s = 15.0
_cloud_decision_interval_s = 30.0

_lane_state: Any = None
_policy: Any = None
_joint_policy: Any = None
_signal_controller: Any = None
_phase_orders: dict[str, tuple[int, ...]] = {}
_episode_id = ""
_period = ""
_seed = 0
_duration_s = 0.0

_episode: Rollout | None = None
_joint_episode: JointRollout | None = None
_pending: list[RolloutStep] = []
_signal_pending: list[SignalRolloutStep] = []
_cloud_pending: list[CloudRolloutStep] = []
_cloud_priorities: dict[str, Any] = {}
_last_signal_actions: dict[str, dict[str, int]] = {}
_collected: Rollout | None = None
_episode_stats: dict[str, int] = {}
_initialized = False


def _lazy_lane_state() -> Any:
    global _lane_state
    if _lane_state is None:
        from algorithms.cov2x.road import lane_state

        _lane_state = lane_state
    return _lane_state


def _lazy_policy() -> Any:
    global _policy
    if _policy is None:
        from algorithms.cov2x.vehicle.policy import VehiclePPOAgent, VehiclePolicyConfig

        _policy = VehiclePPOAgent(VehiclePolicyConfig())
    return _policy


def _lazy_joint_policy() -> Any:
    global _joint_policy
    if _joint_policy is None:
        from algorithms.cov2x.collab.joint_policy import (
            JointPPOAgent,
            JointPolicyConfig,
        )

        _joint_policy = JointPPOAgent(
            JointPolicyConfig(
                signal_decision_interval_s=_signal_decision_interval_s,
                cloud_decision_interval_s=_cloud_decision_interval_s,
            )
        )
    return _joint_policy


def _read_env() -> None:
    global _mode, _signal_mode, _cloud_mode, _vehicle_mode, _model_path
    global _checkpoint_dir, _signal_decision_interval_s
    global _cloud_decision_interval_s, _team_weight
    _mode = os.environ.get("COV2X_MODE", "eval").strip().lower()
    if _mode not in {"train", "eval", "rule"}:
        raise ValueError(
            "COV2X_MODE must be one of 'train', 'eval', 'rule', "
            f"got {_mode!r}"
        )
    _signal_mode = os.environ.get(
        "COV2X_SIGNAL_MODE", "max_pressure"
    ).strip().lower()
    if _signal_mode not in {"max_pressure", "fixed", "learned"}:
        raise ValueError(
            "COV2X_SIGNAL_MODE must be 'max_pressure', 'fixed' or 'learned', "
            f"got {_signal_mode!r}"
        )
    _cloud_mode = os.environ.get("COV2X_CLOUD_MODE", "").strip().lower()
    if not _cloud_mode:
        _cloud_mode = "learned" if _signal_mode == "learned" else "off"
    if _cloud_mode not in {"learned", "off"}:
        raise ValueError(
            "COV2X_CLOUD_MODE must be 'learned' or 'off', "
            f"got {_cloud_mode!r}"
        )
    _vehicle_mode = os.environ.get(
        "COV2X_VEHICLE_MODE", "rule" if _mode == "rule" else "learned"
    ).strip().lower()
    if _vehicle_mode not in {"learned", "rule", "off"}:
        raise ValueError(
            "COV2X_VEHICLE_MODE must be 'learned', 'rule', or 'off', "
            f"got {_vehicle_mode!r}"
        )
    if _mode == "rule" and _vehicle_mode != "rule":
        _vehicle_mode = "rule"
    if _mode == "rule" and _signal_mode == "learned":
        _signal_mode = "max_pressure"
        _cloud_mode = "off"
    _model_path = os.environ.get("COV2X_MODEL_PATH") or None
    _checkpoint_dir = os.environ.get("COV2X_CHECKPOINT_DIR") or None
    _signal_decision_interval_s = float(
        os.environ.get("COV2X_SIGNAL_DECISION_INTERVAL", "15.0") or 15.0
    )
    _cloud_decision_interval_s = float(
        os.environ.get("COV2X_CLOUD_DECISION_INTERVAL", "30.0") or 30.0
    )
    _team_weight = float(
        os.environ.get("COV2X_TEAM_WEIGHT", str(TEAM_WEIGHT_DEFAULT))
        or TEAM_WEIGHT_DEFAULT
    )


def initialize(payload: Mapping[str, Any]) -> dict[str, Any]:
    global _initialized, _episode_id, _period, _seed, _duration_s
    global _phase_orders, _decision_interval_s, _episode, _pending, _collected
    global _episode_stats, _policy, _joint_policy, _signal_controller
    global _joint_episode, _signal_pending, _cloud_pending
    global _cloud_priorities, _last_signal_actions, _resume_loaded_path

    _read_env()
    _signal_controller = None
    _joint_episode = None
    _signal_pending = []
    _cloud_pending = []
    _cloud_priorities = {}
    _last_signal_actions = {}
    lane_state = _lazy_lane_state()
    edge_lanes = payload.get("edge_lanes", {}) or {}
    lane_state.build_static_indices(dict(payload), edge_lanes)

    _phase_orders = {}
    for tls_id, meta in (payload.get("intersections", {}) or {}).items():
        order = tuple(
            int(phase_id)
            for phase_id in (meta.get("phase_order") or [])
        )
        _phase_orders[str(tls_id)] = order

    _episode_id = str(payload.get("episode_id", ""))
    _period = str(payload.get("period", ""))
    _seed = int(payload.get("seed", 0))
    _duration_s = float(payload.get("duration_seconds", 0.0) or 0.0)
    _decision_interval_s = float(
        payload.get("decision_interval", 5.0) or 5.0
    )

    if _signal_mode == "max_pressure":
        try:
            from traffic_control.max_pressure import MaxPressureController

            _signal_controller = MaxPressureController(dict(payload))
        except Exception:
            # Minimal synthetic payloads may lack connection_priorities;
            # fall back to the simplified signal mirror in that case.
            _signal_controller = None

    if _signal_mode == "learned":
        if _cloud_mode != "learned":
            raise ValueError(
                "COV2X_SIGNAL_MODE=learned requires COV2X_CLOUD_MODE=learned "
                "(joint vehicle-road-cloud training)"
            )
        if _mode == "train" and _vehicle_mode != "learned":
            raise ValueError(
                "joint training requires COV2X_VEHICLE_MODE=learned; "
                "eval may disable vehicle guidance for ablation"
            )
        _policy = None
        created_joint = _joint_policy is None
        if created_joint:
            _joint_policy = _lazy_joint_policy()
        if _model_path and (
            created_joint or _resume_loaded_path != _model_path
        ):
            load_checkpoint(Path(_model_path))
            _resume_loaded_path = _model_path
    elif _vehicle_mode == "learned":
        _joint_policy = None
        created_vehicle = _policy is None
        if created_vehicle:
            _policy = _lazy_policy()
        if _model_path and (
            created_vehicle or _resume_loaded_path != _model_path
        ):
            load_checkpoint(Path(_model_path))
            _resume_loaded_path = _model_path
    else:
        _policy = None
        _joint_policy = None

    if _joint_policy is not None:
        _joint_policy.reset(_episode_id)
        _joint_episode = JointRollout(
            episode_id=_episode_id,
            period=_period,
            seed=_seed,
            duration_s=_duration_s,
            generation=int(_joint_policy.policy_generation),
            signal_mode=_signal_mode,
        )
    elif _policy is not None:
        _policy.reset(_episode_id)

    _episode = Rollout(
        episode_id=_episode_id,
        period=_period,
        seed=_seed,
        duration_s=_duration_s,
        generation=int(
            _joint_policy.policy_generation
            if _joint_policy is not None
            else (_policy.policy_generation if _policy is not None else 0)
        ),
        signal_mode=_signal_mode,
    )
    _pending = []
    _collected = None
    _episode_stats = {
        "decision_count": 0,
        "command_count": 0,
        "lane_change_requested": 0,
        "lane_change_completed": 0,
        "lane_change_not_completed": 0,
    }
    _initialized = True
    return {
        "protocol_version": "2.0",
        "episode_id": _episode_id,
        "ready": True,
    }


def _build_signal_actions(payload: Mapping[str, Any]) -> dict[str, dict[str, int]]:
    lane_state = _lazy_lane_state()
    if _signal_mode == "max_pressure":
        if _signal_controller is not None:
            try:
                raw = _signal_controller.compute_actions(
                    dict(payload), tie_keep_current=True
                )
            except Exception:
                raw = None
            if raw is not None:
                intersections = payload.get("intersections", {}) or {}
                actions: dict[str, dict[str, int]] = {}
                for tls_id, phase in raw.items():
                    if phase is None:
                        order = _phase_orders.get(str(tls_id), ())
                        obs = intersections.get(str(tls_id)) or {}
                        phase = int(
                            obs.get("current_phase", order[0] if order else 0)
                        )
                    actions[str(tls_id)] = {"target_phase": int(phase)}
                return actions
        return max_pressure_actions(payload, _phase_orders, lane_state)
    if _signal_mode == "fixed":
        return fixed_actions(payload, _phase_orders)
    raise RuntimeError(f"unsupported signal mode {_signal_mode!r}")


def _signal_ratio() -> int:
    return max(1, round(_signal_decision_interval_s / _decision_interval_s))


def _cloud_ratio() -> int:
    return max(1, round(_cloud_decision_interval_s / _decision_interval_s))


def _signal_due(step_id: int) -> bool:
    return int(step_id) % _signal_ratio() == 0


def _cloud_due(step_id: int) -> bool:
    return int(step_id) % _cloud_ratio() == 0


def _sample_cloud(
    payload: Mapping[str, Any],
    snapshot: TrafficSnapshot,
    global_state: Any,
    value_now: float,
    *,
    deterministic: bool,
) -> None:
    """Sample one cloud priority action and update ``_cloud_priorities``."""
    global _cloud_priorities
    from algorithms.cov2x.cloud.observations import build_cloud_observation

    cloud_obs = build_cloud_observation(payload, phase_orders=_phase_orders)
    batch = _joint_policy.act_cloud(cloud_obs, deterministic=deterministic)
    priorities: dict[str, Any] = {}
    for index, tls_id in enumerate(cloud_obs.intersection_ids):
        priorities[str(tls_id)] = priority_for_action(int(batch.action[index]))
    _cloud_priorities = priorities
    if _mode == "train" and _joint_episode is not None:
        _cloud_pending.append(
            CloudRolloutStep(
                state=np.asarray(cloud_obs.state, dtype=np.float32),
                action=np.asarray(batch.action, dtype=np.int64),
                logprob=float(batch.logprob),
                value=float(value_now),
                sim_time=float(payload.get("simulation_time", 0.0)),
                step_id=int(payload.get("step_id", 0)),
                intersection_ids=cloud_obs.intersection_ids,
                traffic_before=snapshot,
                global_state=np.asarray(global_state, dtype=np.float32),
            )
        )


def _joint_signal_actions(
    payload: Mapping[str, Any],
    snapshot: TrafficSnapshot,
    global_state: Any,
    value_now: float,
    *,
    deterministic: bool,
) -> dict[str, dict[str, int]]:
    """Learned signal actions; holds the last decision on intermediate steps."""
    global _last_signal_actions
    if not _signal_due(int(payload.get("step_id", 0))) and _last_signal_actions:
        return dict(_last_signal_actions)
    from algorithms.cov2x.road.signal_observations import (
        SIGNAL_ACTION_ADVANCE,
        build_signal_observations,
    )

    observations = build_signal_observations(
        payload,
        phase_orders=_phase_orders,
        cloud_priorities=_cloud_priorities,
        min_green_s=float(payload.get("minimum_green", 5.0) or 5.0),
    )
    actions: dict[str, dict[str, int]] = {}
    if not observations:
        _last_signal_actions = {}
        return actions
    batch = _joint_policy.act_signal_batch(
        observations, deterministic=deterministic
    )
    for index, obs in enumerate(observations):
        order = obs.phase_order
        try:
            current_index = int(order.index(obs.current_phase))
        except ValueError:
            current_index = -1
        if (
            int(batch.action[index]) == SIGNAL_ACTION_ADVANCE
            and current_index >= 0
            and len(order) > 1
        ):
            phase_index = (current_index + 1) % len(order)
        else:
            phase_index = max(current_index, 0)
        target_phase = int(order[phase_index])
        actions[str(obs.tls_id)] = {"target_phase": target_phase}
        if _mode == "train" and _joint_episode is not None:
            _signal_pending.append(
                SignalRolloutStep(
                    tls_id=str(obs.tls_id),
                    state=np.asarray(obs.state, dtype=np.float32),
                    mask=np.asarray(obs.action_mask, dtype=bool),
                    action=int(batch.action[index]),
                    logprob=float(batch.logprob[index]),
                    value=float(value_now),
                    sim_time=float(payload.get("simulation_time", 0.0)),
                    step_id=int(payload.get("step_id", 0)),
                    source_phase=obs.current_phase,
                    requested_phase=target_phase,
                    phase_order=order,
                    traffic_before=snapshot,
                    global_state=np.asarray(global_state, dtype=np.float32),
                )
            )
    _last_signal_actions = dict(actions)
    return actions


def _settle_signal_pending(
    payload: Mapping[str, Any],
    snapshot: TrafficSnapshot,
    global_state: Any,
    value_now: float,
) -> None:
    global _signal_pending
    if not _signal_pending or _joint_episode is None:
        return
    intersections = payload.get("intersections", {}) or {}
    pending, _signal_pending = _signal_pending, []
    for step in pending:
        obs = intersections.get(step.tls_id) or {}
        current_phase = int(obs.get("current_phase", step.source_phase))
        executed_switch = (
            current_phase == int(step.requested_phase)
            and int(step.requested_phase) != int(step.source_phase)
        )
        local, components = signal_local_reward(
            obs,
            source_phase=step.source_phase,
            requested_phase=step.requested_phase,
            executed_switch=executed_switch,
        )
        team = team_reward(step.traffic_before, snapshot)
        total = float(local) + _team_weight * float(team)
        step.reward = total
        step.reward_components = {
            **components,
            "team": float(team),
            "total": total,
        }
        step.reward_basis = "fresh"
        step.next_value = float(value_now)
        step.executed_phase = current_phase
        step.executed_switch = executed_switch
        _joint_episode.signal_steps.append(step)


def _settle_cloud_pending(
    snapshot: TrafficSnapshot,
    global_state: Any,
    value_now: float,
) -> None:
    global _cloud_pending
    if not _cloud_pending or _joint_episode is None:
        return
    pending, _cloud_pending = _cloud_pending, []
    for step in pending:
        total, components = cloud_local_reward(
            step.traffic_before, snapshot
        )
        step.reward = float(total)
        step.reward_components = dict(components)
        step.reward_basis = "fresh"
        step.next_value = float(value_now)
        _joint_episode.cloud_steps.append(step)


def _requested_command(
    obs: Any,
    lane_action: int,
    speed_bin: int,
) -> dict[str, float | int]:
    target_speed = float(
        SPEED_FRACTIONS[int(speed_bin)] * obs.allowed_speed_mps
    )
    target_lane = None
    if (
        int(lane_action) != LANE_ACTION_KEEP
        and obs.lane_index is not None
        and obs.road_lane_indices
    ):
        delta = (
            1 if int(lane_action) == LANE_ACTION_LEFT else -1
        )
        target_lane = int(obs.lane_index) + delta
        if target_speed <= 1e-3:
            # SUMO rejects lane changes while commanded speed is ~0.
            target_speed = 0.25 * float(obs.allowed_speed_mps)
    requested: dict[str, float | int] = {}
    if target_speed is not None:
        requested["target_speed_mps"] = float(
            min(max(target_speed, 0.0), obs.allowed_speed_mps)
        )
    if target_lane is not None:
        requested["target_lane_index"] = int(target_lane)
    return requested


def _build_vehicle_commands(
    observations: list[Any],
    batch: Any,
) -> dict[str, dict[str, float | int]]:
    commands: dict[str, dict[str, float | int]] = {}
    for index, obs in enumerate(observations):
        requested = _requested_command(
            obs, int(batch.lane_action[index]), int(batch.speed_bin[index])
        )
        action = VehicleAction(
            target_speed_mps=requested.get("target_speed_mps"),
            target_lane_index=requested.get("target_lane_index"),
            source="learned",
        )
        try:
            commands[obs.vehicle_id] = validate_vehicle_action(
                action,
                allowed_speed_mps=obs.allowed_speed_mps,
                road_lane_indices=obs.road_lane_indices,
            )
        except VehicleActionError:
            # The mask should prevent this; degrade to a safe speed-only
            # command rather than terminating the episode.
            commands[obs.vehicle_id] = validate_vehicle_action(
                VehicleAction(
                    target_speed_mps=obs.allowed_speed_mps,
                    source="safety",
                ),
                allowed_speed_mps=obs.allowed_speed_mps,
                road_lane_indices=obs.road_lane_indices,
            )
    return commands


def _execution_result(
    payload: Mapping[str, Any],
    vehicle_id: str,
) -> dict[str, Any]:
    previous = (
        (payload.get("previous_action_results") or {})
        .get("vehicles", {})
        .get(vehicle_id)
    )
    if not isinstance(previous, Mapping):
        return {}
    return {
        key: previous.get(key)
        for key in (
            "speed_status",
            "lane_change_status",
            "actual_speed_mps",
            "actual_lane_index",
        )
        if key in previous
    }


def _settle_pending(
    payload: Mapping[str, Any],
    observations: list[Any],
    batch: Any,
    *,
    global_state: Any = None,
    value_now: float | None = None,
    snapshot: TrafficSnapshot | None = None,
) -> None:
    global _pending, _episode
    if _episode is None and _joint_episode is None:
        return
    rollout = _joint_episode if _joint_policy is not None else _episode
    current_by_slot = {
        obs.slot_index: obs for obs in observations
    }
    value_by_slot = {
        obs.slot_index: float(batch.value[index])
        for index, obs in enumerate(observations)
    }
    policy_config = _policy.config if _policy is not None else None
    pending, _pending = _pending, []
    for step in pending:
        obs_now = current_by_slot.get(step.slot_index)
        weights = (
            policy_config.reward_weights
            if policy_config is not None
            else None
        )
        if obs_now is not None:
            reward_inputs = obs_now.to_reward_inputs(
                max_accel_mps2=(
                    policy_config.max_accel_mps2
                    if policy_config is not None
                    else 5.0
                ),
                dist_to_stopline_max_m=(
                    policy_config.guide_zone_max_m
                    if policy_config is not None
                    else 150.0
                ),
                weights=weights,
            )
            components = vehicle_reward_components(reward_inputs)
            step.reward = float(components["total"])
            step.reward_components = components
            step.reward_basis = "fresh"
            step.next_value = (
                float(value_now)
                if value_now is not None
                else value_by_slot.get(step.slot_index)
            )
        else:
            reward_inputs = step.obs.to_reward_inputs(
                max_accel_mps2=(
                    policy_config.max_accel_mps2
                    if policy_config is not None
                    else 5.0
                ),
                dist_to_stopline_max_m=(
                    policy_config.guide_zone_max_m
                    if policy_config is not None
                    else 150.0
                ),
                weights=weights,
            )
            components = vehicle_reward_components(reward_inputs)
            step.reward = float(components["total"])
            step.reward_components = components
            step.reward_basis = "stale"
            step.next_value = None
        if snapshot is not None and step.traffic_before is not None:
            team = team_reward(step.traffic_before, snapshot)
            step.reward = float(step.reward) + _team_weight * float(team)
            step.reward_components = dict(step.reward_components or {})
            step.reward_components["team"] = float(team)
            step.reward_components["total"] = float(step.reward)
        if global_state is not None:
            step.global_state = np.asarray(global_state, dtype=np.float32)
        step.executed = _execution_result(payload, step.vehicle_id)
        if isinstance(rollout, JointRollout):
            rollout.vehicle_steps.append(step)
        else:
            rollout.steps.append(step)


def _record_pending(
    payload: Mapping[str, Any],
    observations: list[Any],
    batch: Any,
    *,
    global_state: Any = None,
    value_now: float | None = None,
    snapshot: TrafficSnapshot | None = None,
) -> None:
    global _pending
    simulation_time = float(payload.get("simulation_time", 0.0))
    step_id = int(payload.get("step_id", 0))
    for index, obs in enumerate(observations):
        lane_action = int(batch.lane_action[index])
        speed_bin = int(batch.speed_bin[index])
        _pending.append(
            RolloutStep(
                slot_index=obs.slot_index,
                vehicle_id=obs.vehicle_id,
                edge_id=obs.edge_id,
                tls_id=obs.tls_id,
                obs=obs,
                lane_action=lane_action,
                speed_bin=speed_bin,
                logprob=float(batch.logprob[index]),
                value=(
                    float(value_now)
                    if value_now is not None
                    else float(batch.value[index])
                ),
                sim_time=simulation_time,
                step_id=step_id,
                requested=_requested_command(obs, lane_action, speed_bin),
            )
        )
    if snapshot is not None and observations:
        for step in _pending[-len(observations) :]:
            step.traffic_before = snapshot


def _update_episode_stats(payload: Mapping[str, Any]) -> None:
    previous = (
        (payload.get("previous_action_results") or {}).get("vehicles", {}) or {}
    )
    for result in previous.values():
        if not isinstance(result, Mapping):
            continue
        status = result.get("lane_change_status")
        if status == "completed":
            _episode_stats["lane_change_completed"] += 1
        elif status == "not_completed":
            _episode_stats["lane_change_not_completed"] += 1


def step(payload: Mapping[str, Any]) -> dict[str, Any]:
    if not _initialized:
        raise RuntimeError("CoV2X controller is not initialized")

    joint = _joint_policy is not None
    global_state = None
    value_now = None
    snapshot = None
    if joint:
        global_state_pre = build_global_state(
            payload,
            phase_orders=_phase_orders,
            cloud_priorities=_cloud_priorities,
        )
        value_pre = float(_joint_policy.critic_value(global_state_pre))
        snapshot = TrafficSnapshot.from_payload(payload)
        step_id = int(payload.get("step_id", 0))
        if _signal_due(step_id):
            _settle_signal_pending(
                payload, snapshot, global_state_pre, value_pre
            )
        if _cloud_due(step_id):
            _settle_cloud_pending(snapshot, global_state_pre, value_pre)
        if _cloud_due(step_id):
            _sample_cloud(
                payload,
                snapshot,
                global_state_pre,
                value_pre,
                deterministic=(_mode != "train"),
            )
        # After a cloud decision, the state consumed by road/vehicle actors
        # (and the critic) includes the new cloud priorities.
        global_state = build_global_state(
            payload,
            phase_orders=_phase_orders,
            cloud_priorities=_cloud_priorities,
        )
        value_now = float(_joint_policy.critic_value(global_state))
        signal_actions = _joint_signal_actions(
            payload,
            snapshot,
            global_state,
            value_now,
            deterministic=(_mode != "train"),
        )
    else:
        signal_actions = _build_signal_actions(payload)
    phase_intents = {
        str(tls_id): int(action["target_phase"])
        for tls_id, action in signal_actions.items()
    }
    observations = build_vehicle_observations(
        payload,
        phase_intents,
        cloud_priorities=_cloud_priorities if joint else None,
        lane_state_module=_lazy_lane_state(),
    )
    _episode_stats["decision_count"] += 1

    if _mode == "train":
        if _policy is None and _joint_policy is None:
            raise RuntimeError(
                "COV2X_MODE=train requires COV2X_VEHICLE_MODE=learned"
            )
        batch = (
            _joint_policy.act_vehicle_batch(observations, deterministic=False)
            if joint
            else _policy.act_batch(observations, deterministic=False)
        )
        _settle_pending(
            payload,
            observations,
            batch,
            global_state=global_state,
            value_now=value_now,
            snapshot=snapshot,
        )
        _record_pending(
            payload,
            observations,
            batch,
            global_state=global_state,
            value_now=value_now,
            snapshot=snapshot,
        )
        vehicle_actions = _build_vehicle_commands(observations, batch)
    elif _mode == "eval":
        if _vehicle_mode == "off":
            vehicle_actions = {}
        elif _vehicle_mode == "rule":
            from algorithms.cov2x.vehicle.rule import rule_vehicle_actions

            vehicle_actions = rule_vehicle_actions(
                payload,
                signal_actions,
                lane_state_module=_lazy_lane_state(),
                vehicle_ids={obs.vehicle_id for obs in observations},
            )
        else:
            if _policy is None and _joint_policy is None:
                raise RuntimeError(
                    "COV2X_MODE=eval with learned vehicle policy requires "
                    "COV2X_MODEL_PATH"
                )
            batch = (
                _joint_policy.act_vehicle_batch(
                    observations, deterministic=True
                )
                if joint
                else _policy.act_batch(observations, deterministic=True)
            )
            vehicle_actions = _build_vehicle_commands(observations, batch)
    else:  # rule
        from algorithms.cov2x.vehicle.rule import rule_vehicle_actions

        vehicle_actions = rule_vehicle_actions(
            payload,
            signal_actions,
            lane_state_module=_lazy_lane_state(),
            vehicle_ids={obs.vehicle_id for obs in observations},
        )

    _episode_stats["command_count"] += len(vehicle_actions)
    _episode_stats["lane_change_requested"] += sum(
        1
        for action in vehicle_actions.values()
        if isinstance(action, Mapping) and "target_lane_index" in action
    )
    _update_episode_stats(payload)

    return {
        "protocol_version": "2.0",
        "episode_id": _episode_id,
        "step_id": int(payload.get("step_id", 0)),
        "actions": {
            "signals": signal_actions,
            "vehicles": vehicle_actions,
        },
    }


def finish(payload: Mapping[str, Any]) -> None:
    global _episode, _joint_episode, _pending, _signal_pending, _cloud_pending
    global _collected, _initialized, _episode_stats, _signal_controller
    global _cloud_priorities, _last_signal_actions
    intersections = payload.get("intersections", {}) or {}
    if _mode == "train" and _joint_episode is not None:
        for step in _pending:
            step.reward = None
            step.next_value = None
            step.reward_basis = "terminal"
            step.executed = {}
            _joint_episode.vehicle_steps.append(step)
        for step in _signal_pending:
            obs = intersections.get(step.tls_id) or {}
            step.reward = None
            step.next_value = None
            step.reward_basis = "terminal"
            step.executed_phase = int(obs.get("current_phase", step.source_phase))
            _joint_episode.signal_steps.append(step)
        for step in _cloud_pending:
            step.reward = None
            step.next_value = None
            step.reward_basis = "terminal"
            _joint_episode.cloud_steps.append(step)
        config = _joint_policy.config
        compute_gae(
            _joint_episode.vehicle_steps,
            gamma=config.gamma,
            lam=config.lam,
        )
        compute_joint_gae(
            _joint_episode.signal_steps,
            lambda step: step.tls_id,
            gamma=config.signal_gamma,
            lam=config.lam,
        )
        compute_joint_gae(
            _joint_episode.cloud_steps,
            lambda step: "cloud",
            gamma=config.cloud_gamma,
            lam=config.lam,
        )
        _joint_episode.metrics.update(_episode_stats)
        _joint_episode.metrics["episode_summary"] = _joint_episode_summary(
            _joint_episode
        )
        _collected = _joint_episode
    if _mode == "train" and _episode is not None and _joint_policy is None:
        for step in _pending:
            step.reward = None
            step.next_value = None
            step.reward_basis = "terminal"
            step.executed = {}
            _episode.steps.append(step)
        compute_gae(_episode.steps, gamma=0.99, lam=0.95)
        _episode.metrics.update(_episode_stats)
        _episode.metrics["episode_summary"] = episode_summary(_episode)
        _collected = _episode
    _episode = None
    _joint_episode = None
    _pending = []
    _signal_pending = []
    _cloud_pending = []
    _cloud_priorities = {}
    _last_signal_actions = {}
    _episode_stats = {}
    _signal_controller = None
    _lazy_lane_state().reset_lane_state()
    _initialized = False


def take_collected_rollout() -> Rollout | None:
    global _collected
    rollout = _collected
    _collected = None
    return rollout


def _joint_episode_summary(rollout: JointRollout) -> dict[str, Any]:
    """Auditable per-family summary of a joint training episode."""
    vehicle = episode_summary(
        Rollout(
            episode_id=rollout.episode_id,
            period=rollout.period,
            seed=rollout.seed,
            duration_s=rollout.duration_s,
            generation=rollout.generation,
            signal_mode=rollout.signal_mode,
            steps=rollout.vehicle_steps,
        )
    )
    signal_rewards = [
        float(step.reward)
        for step in rollout.signal_steps
        if step.reward is not None
    ]
    cloud_rewards = [
        float(step.reward)
        for step in rollout.cloud_steps
        if step.reward is not None
    ]
    requested_switches = [
        step for step in rollout.signal_steps
        if int(step.requested_phase) != int(step.source_phase)
    ]
    executed_switches = [
        step for step in requested_switches if step.executed_switch
    ]
    cloud_action_histogram = [0, 0, 0]
    for step in rollout.cloud_steps:
        for action in np.asarray(step.action, dtype=np.int64).tolist():
            if 0 <= int(action) < 3:
                cloud_action_histogram[int(action)] += 1
    return {
        "vehicle": vehicle,
        "signal": {
            "decision_count": len(rollout.signal_steps),
            "reward_mean": (
                float(np.mean(signal_rewards)) if signal_rewards else None
            ),
            "switch_requested": len(requested_switches),
            "switch_executed": len(executed_switches),
            "switch_execution_rate": (
                round(len(executed_switches) / len(requested_switches), 4)
                if requested_switches
                else None
            ),
        },
        "cloud": {
            "decision_count": len(rollout.cloud_steps),
            "reward_mean": (
                float(np.mean(cloud_rewards)) if cloud_rewards else None
            ),
            "priority_histogram": cloud_action_histogram,
        },
    }


def train_on_rollout(rollout: Rollout | None) -> dict[str, Any] | None:
    """Apply one PPO update over the episode and return diagnostics."""
    if rollout is None:
        return None
    if isinstance(rollout, JointRollout):
        if _joint_policy is None:
            raise RuntimeError(
                "train_on_rollout requires a joint policy in joint mode"
            )
        diagnostics = _joint_policy.update(rollout)
        diagnostics["episode_summary"] = rollout.metrics.get(
            "episode_summary", {}
        )
        if _checkpoint_dir:
            path = (
                Path(_checkpoint_dir)
                / f"cov2x_joint_ep{_joint_policy.episode_count:04d}.pt"
            )
            path.parent.mkdir(parents=True, exist_ok=True)
            save_checkpoint(path)
        return diagnostics
    if _policy is None:
        raise RuntimeError("train_on_rollout requires a learned policy")
    arrays = to_training_arrays(rollout)
    if not arrays or arrays["states"].shape[0] == 0:
        return {
            "steps": 0,
            "policy_generation": _policy.policy_generation,
        }
    diagnostics = _policy.update(
        states=arrays["states"],
        masks=arrays["masks"],
        lane_actions=arrays["lane_actions"],
        speed_bins=arrays["speed_bins"],
        old_logprobs=arrays["old_logprobs"],
        advantages=arrays["advantages"],
        returns=arrays["returns"],
    )
    _policy.episode_count += 1
    diagnostics["steps"] = int(arrays["states"].shape[0])
    diagnostics["policy_generation"] = int(_policy.policy_generation)
    diagnostics["episode_count"] = int(_policy.episode_count)
    diagnostics["episode_summary"] = rollout.metrics.get("episode_summary", {})
    if _checkpoint_dir:
        path = (
            Path(_checkpoint_dir)
            / f"cov2x_vehicle_ep{_policy.episode_count:04d}.pt"
        )
        path.parent.mkdir(parents=True, exist_ok=True)
        save_checkpoint(path)
    return diagnostics


def save_checkpoint(path: str | Path) -> Path:
    if _policy is None and _joint_policy is None:
        raise RuntimeError("no learned policy to save")
    import torch

    target = Path(path)
    target.parent.mkdir(parents=True, exist_ok=True)
    if _joint_policy is not None:
        payload = {
            "format_version": JOINT_CHECKPOINT_FORMAT_VERSION,
            "joint_policy": _joint_policy.state_dict(),
            "config": asdict(_joint_policy.config),
            "phase_orders": {
                key: list(value) for key, value in _phase_orders.items()
            },
            "episode_count": int(_joint_policy.episode_count),
            "policy_generation": int(_joint_policy.policy_generation),
        }
    else:
        payload = {
            "format_version": CHECKPOINT_FORMAT_VERSION,
            "policy": _policy.state_dict(),
            "config": asdict(_policy.config),
            "phase_orders": {
                key: list(value) for key, value in _phase_orders.items()
            },
            "episode_count": int(_policy.episode_count),
            "policy_generation": int(_policy.policy_generation),
        }
    tmp = target.with_suffix(target.suffix + ".tmp")
    torch.save(payload, tmp)
    tmp.replace(target)
    return target


def load_checkpoint(path: str | Path) -> None:
    import torch

    source = Path(path)
    if not source.is_file():
        raise FileNotFoundError(f"CoV2X checkpoint not found: {source}")
    payload = torch.load(source, map_location="cpu")
    if not isinstance(payload, Mapping):
        raise ValueError(f"CoV2X checkpoint must be a mapping: {source}")
    version = int(payload.get("format_version", 0))
    if version not in {
        CHECKPOINT_FORMAT_VERSION,
        JOINT_CHECKPOINT_FORMAT_VERSION,
    }:
        raise ValueError(
            "CoV2X checkpoint format_version mismatch: "
            f"expected {CHECKPOINT_FORMAT_VERSION} or "
            f"{JOINT_CHECKPOINT_FORMAT_VERSION}, "
            f"got {payload.get('format_version')!r}"
        )
    if version == JOINT_CHECKPOINT_FORMAT_VERSION:
        if _joint_policy is None:
            raise ValueError(
                "joint checkpoint requires COV2X_SIGNAL_MODE=learned"
            )
        policy = _lazy_joint_policy()
        policy.load_state_dict(payload["joint_policy"])
    else:
        if _joint_policy is not None:
            raise ValueError(
                "Stage-2 (vehicle-only) checkpoint cannot be loaded in "
                "joint mode; train a joint checkpoint with "
                "COV2X_SIGNAL_MODE=learned"
            )
        policy = _lazy_policy()
        policy.load_state_dict(payload["policy"])
    # 执行期相序以 payload.intersections[].phase_order 为权威：
    # demo_4 等路口在不同时段的信号程序相位数不同（off=3 / morning=4 /
    # evening=4），checkpoint 内 phase_orders 仅作训练记录，不能覆盖时段相序，
    # 否则会输出 SUMO 程序不存在的相位。


def diagnostics() -> dict[str, Any]:
    return {
        "mode": _mode,
        "signal_mode": _signal_mode,
        "cloud_mode": _cloud_mode,
        "vehicle_mode": _vehicle_mode,
        "model_path": _model_path,
        "checkpoint_dir": _checkpoint_dir,
        "episode_id": _episode_id,
        "initialized": _initialized,
        "episode_stats": dict(_episode_stats),
        "policy_generation": (
            int(_joint_policy.policy_generation)
            if _joint_policy is not None
            else (int(_policy.policy_generation) if _policy is not None else 0)
        ),
        "episode_count": (
            int(_joint_policy.episode_count)
            if _joint_policy is not None
            else (int(_policy.episode_count) if _policy is not None else 0)
        ),
        "signal_decision_interval_s": _signal_decision_interval_s,
        "cloud_decision_interval_s": _cloud_decision_interval_s,
    }
