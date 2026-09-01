"""Deployable MAPPO cooperative Protocol 2.0 controller (inference only).

MAPPO uses centralized training with decentralized execution: the actor is a
per-intersection local policy conditioned on the fixed 20-slot identity, so
online inference is actor-only and every eligible intersection is decided
independently (the cooperative critic is a training-time artifact and is never
used here).  The checkpoint contract is the fixed 20-slot IPPO-v8 identity
schema; scenario presets (east_dense / west_dense / xiongan_20) are controlled
subsets of the checkpoint's 20 training intersections.
"""

from __future__ import annotations

import logging
import math
import os
from pathlib import Path
from typing import Any, Dict, List, Mapping, Optional, Tuple

import numpy as np
import torch

from traffic_control.mappo.model import MAPPOPolicy
from traffic_control.mappo.features import IPPOV8FeatureBuilder
from traffic_control.mappo.contract import (
    EXPECTED_OBS_DIM,
    load_checkpoint_metadata,
    load_contract,
    validate_contract,
)
from traffic_control.ippo.identity import IDENTITY_SLOT_IDS
from traffic_control.protocol import finish_response, initialize_response, step_response

logger = logging.getLogger(__name__)

MODEL_VERSION = "mappo_v1"
DEFAULT_ACTION_INTERVAL = 15.0
DEFAULT_MODEL_FILENAME = "mappo_cooperative_20tls_ep160.pt"
DEFAULT_MODEL_PATH = Path(__file__).resolve().parent / "models" / DEFAULT_MODEL_FILENAME

MAX_WAITING = 200.0
MAX_STAGE_ELAPSED = 120.0
MAX_PHASES = 8
MAX_LANES = 20
MAX_OCCUPANCY = 100.0
VEHICLE_LENGTH_WITH_GAP = 7.5

SATURATION_FLOW_PER_LANE = 0.5
DEFAULT_GREEN_DURATION = 30.0
DEFAULT_MAX_GREEN_FACTOR = 2.0
MIN_MAX_GREEN_SECONDS = 60.0
MAX_SERVICE_AGE = 120.0

PHASE_FEATURE_SCHEMA = "connection_pressure_service_age_eta_demand_v2"

_policy: Optional[MAPPOPolicy] = None
_state_builder: Optional[IPPOV8FeatureBuilder] = None
_phase_orders: Dict[str, List[int]] = {}
_intersection_ids: Tuple[str, ...] = ()
_inference_mode = "model"
_model_path: Optional[str] = None
_loaded_model_path: Optional[str] = None
_last_decision_times: Dict[str, float] = {}
_last_phase_service_times: Dict[str, Dict[int, float]] = {}
_signal_execution_stats: Dict[str, dict] = {}
_pending_signal_commands: Dict[str, dict] = {}
_decision_interval = 5.0
_minimum_green = 5.0
_action_interval = DEFAULT_ACTION_INTERVAL
_max_green_factor = DEFAULT_MAX_GREEN_FACTOR
_effective_demand_enabled = True
_obs_dim = 0
_act_dim = 0


def _effective_demand_from_environment() -> bool:
    value = os.environ.get("MAPPO_EFFECTIVE_DEMAND", "on").strip().lower()
    if value in {"1", "true", "on", "yes"}:
        return True
    if value in {"0", "false", "off", "no"}:
        return False
    raise ValueError("MAPPO_EFFECTIVE_DEMAND must be 'on' or 'off'.")


def default_model_path() -> Path:
    alias = os.environ.get("MAPPO_MODEL_ALIAS", "").strip()
    if alias:
        from .aliases import resolve_model_path

        return resolve_model_path(alias)
    override = os.environ.get("MAPPO_MODEL_PATH", "").strip()
    if override:
        return Path(override)
    return DEFAULT_MODEL_PATH


def _eligible_for_decision(
    intersection: Mapping[str, Any], simulation_time: float, last_decision: float
) -> bool:
    return (
        str(intersection.get("stage", "")).upper() == "GREEN"
        and intersection.get("pending_phase") is None
        and float(intersection.get("stage_elapsed", 0.0)) + 1e-9 >= _minimum_green
        and simulation_time + 1e-9 >= last_decision + _action_interval
    )


def _observe_signal_execution(
    intersections: Mapping[str, Any], simulation_time: float
) -> None:
    """Infer whether a previously requested phase became effective."""

    for intersection_id in _intersection_ids:
        intersection = intersections.get(intersection_id, {})
        if str(intersection.get("stage", "")).upper() != "GREEN":
            continue
        current_phase = int(intersection.get("current_phase", -1))
        if current_phase in _last_phase_service_times[intersection_id]:
            _last_phase_service_times[intersection_id][current_phase] = simulation_time
        stats = _signal_execution_stats[intersection_id]
        stats["max_observed_green_s"] = max(
            float(stats["max_observed_green_s"]),
            float(intersection.get("stage_elapsed", 0.0)),
        )

    for intersection_id, pending in list(_pending_signal_commands.items()):
        intersection = intersections.get(intersection_id, {})
        if (
            str(intersection.get("stage", "")).upper() == "GREEN"
            and int(intersection.get("current_phase", -1))
            == int(pending["target_phase"])
        ):
            delay = max(simulation_time - float(pending["requested_at"]), 0.0)
            stats = _signal_execution_stats[intersection_id]
            stats["observed_changes"] += 1.0
            stats["change_delay_total_s"] += delay
            stats["change_delay_max_s"] = max(
                float(stats["change_delay_max_s"]), delay
            )
            _pending_signal_commands.pop(intersection_id, None)


def _record_signal_command(
    intersection_id: str,
    intersection: Mapping[str, Any],
    target_phase: int,
    simulation_time: float,
    *,
    max_green_forced: bool,
) -> None:
    stats = _signal_execution_stats[intersection_id]
    stats["commands"] += 1.0
    if max_green_forced:
        stats["max_green_forced_commands"] += 1.0
    phase_key = str(int(target_phase))
    phase_counts = stats["phase_commands"]
    phase_counts[phase_key] = int(phase_counts.get(phase_key, 0)) + 1
    current_phase = int(intersection.get("current_phase", target_phase))
    if int(target_phase) == current_phase:
        return
    stats["change_requests"] += 1.0
    if intersection_id in _pending_signal_commands:
        stats["unresolved_changes"] += 1.0
    _pending_signal_commands[intersection_id] = {
        "target_phase": int(target_phase),
        "requested_at": float(simulation_time),
    }


def _choose_action(
    state: np.ndarray, phase_features: np.ndarray, action_mask: np.ndarray
) -> int:
    valid_actions = np.flatnonzero(action_mask)
    if valid_actions.size == 0:
        raise RuntimeError("MAPPO action mask contains no valid phase.")
    if _inference_mode == "fixed":
        return int(valid_actions[0])
    if _policy is None:
        raise RuntimeError("MAPPO model is not initialized.")
    with torch.no_grad():
        tensor = torch.from_numpy(state).unsqueeze(0).float()
        phase_tensor = torch.from_numpy(phase_features).unsqueeze(0).float()
        mask_tensor = torch.from_numpy(action_mask).unsqueeze(0).bool()
        logits = _policy.actor.masked_logits(tensor, phase_tensor, mask_tensor)
        # Deployed MAPPO always selects the mode of the masked categorical.
        action = torch.argmax(logits, dim=-1)
        return int(action.item())


def _build_policy(metadata: Mapping[str, Any]) -> MAPPOPolicy:
    policy = MAPPOPolicy(
        obs_dim=EXPECTED_OBS_DIM,
        num_agents=len(IDENTITY_SLOT_IDS),
        critic_scope=str(metadata.get("critic_scope", "global")),
        actor_init_seed=int(metadata.get("actor_init_seed", 42)),
        critic_init_seed=int(metadata.get("critic_init_seed", 43)),
        hidden_dim=int(metadata.get("hidden_dim", 128)),
        phase_feature_dim=int(metadata.get("phase_feature_dim", 11)),
        model_version=str(metadata.get("model_version", "cooperative_joint_v1")),
        actor_variant=str(metadata.get("actor_variant", "shared")),
        residual_hidden_dim=int(metadata.get("residual_hidden_dim", 32)),
        identity_offset=int(metadata.get("identity_offset", 9)),
        residual_init_seed=(
            44
            if metadata.get("residual_init_seed") is None
            else int(metadata["residual_init_seed"])
        ),
    )
    return policy


def initialize(payload: dict) -> dict:
    global _policy, _state_builder, _phase_orders, _intersection_ids
    global _inference_mode, _model_path, _loaded_model_path
    global _last_decision_times, _last_phase_service_times
    global _signal_execution_stats, _pending_signal_commands
    global _decision_interval, _minimum_green, _action_interval, _max_green_factor
    global _effective_demand_enabled
    global _obs_dim, _act_dim

    mode = os.environ.get("MAPPO_MODE", "model").strip().lower()
    if mode not in {"model", "fixed"}:
        raise ValueError(
            f"Unsupported MAPPO_MODE: {mode!r}. Deployable MAPPO supports 'model' or 'fixed'."
        )
    _inference_mode = mode
    _model_path = str(default_model_path())
    _effective_demand_enabled = _effective_demand_from_environment()

    _state_builder = IPPOV8FeatureBuilder(payload)
    _intersection_ids = _state_builder.intersection_ids
    _phase_orders = {
        intersection_id: list(_state_builder.get_phase_order(intersection_id))
        for intersection_id in _intersection_ids
    }
    _obs_dim = _state_builder.max_state_dim
    _act_dim = _state_builder.max_phases
    if not _intersection_ids or _obs_dim <= 0 or _act_dim <= 0:
        raise ValueError("MAPPO requires at least one intersection with at least one phase.")
    if _act_dim > MAX_PHASES:
        raise ValueError(f"MAPPO supports at most {MAX_PHASES} phases per intersection.")
    if any(not phases for phases in _phase_orders.values()):
        raise ValueError("Every controlled intersection must have at least one phase.")

    _decision_interval = float(payload.get("decision_interval", 5.0))
    _minimum_green = float(payload.get("minimum_green", 5.0))
    requested_interval = float(
        os.environ.get("MAPPO_ACTION_INTERVAL", str(DEFAULT_ACTION_INTERVAL))
    )
    if _decision_interval <= 0 or _minimum_green < 0 or requested_interval <= 0:
        raise ValueError("Decision interval and action interval must be positive.")
    _action_interval = max(requested_interval, _decision_interval, _minimum_green)
    _max_green_factor = float(
        os.environ.get("MAPPO_MAX_GREEN_FACTOR", str(DEFAULT_MAX_GREEN_FACTOR))
    )
    if not math.isfinite(_max_green_factor) or _max_green_factor < 0.0:
        raise ValueError("MAPPO_MAX_GREEN_FACTOR must be finite and non-negative.")

    if mode == "model":
        model_path = Path(_model_path)
        if not model_path.is_file():
            raise FileNotFoundError(f"MAPPO checkpoint does not exist: {model_path}")
        checkpoint = torch.load(model_path, map_location="cpu", weights_only=False)
        _contract_version, contract_view = load_contract(model_path, checkpoint)
        metadata = validate_contract(
            contract_view,
            intersection_ids=_intersection_ids,
            obs_dim=_obs_dim,
            action_interval=_action_interval,
            max_green_factor=_max_green_factor,
            effective_demand_enabled=_effective_demand_enabled,
        )
        _act_dim = int(metadata.get("max_action_dim", _act_dim))
        if _act_dim > MAX_PHASES:
            raise ValueError(f"MAPPO supports at most {MAX_PHASES} phases per intersection.")
        if _state_builder.max_phases > _act_dim:
            raise ValueError(
                "Active subset requires more phase slots than the checkpoint action "
                f"dimension ({_state_builder.max_phases} > {_act_dim}); the checkpoint "
                "cannot represent this subset."
            )
        _policy = _build_policy(metadata)
        _policy.load_state_dict(checkpoint["policy_state_dict"], strict=True)
        _policy.eval()
        _loaded_model_path = str(model_path.resolve())
        logger.info("MAPPO %s 推理: %s", MODEL_VERSION, _loaded_model_path)
    else:
        _policy = None
        _loaded_model_path = None
        logger.info("MAPPO %s fixed 占位模式（无模型）", MODEL_VERSION)

    _last_decision_times = {
        intersection_id: -math.inf for intersection_id in _intersection_ids
    }
    _last_phase_service_times = {
        intersection_id: {phase: 0.0 for phase in _phase_orders[intersection_id]}
        for intersection_id in _intersection_ids
    }
    _signal_execution_stats = {
        intersection_id: {
            "commands": 0.0,
            "change_requests": 0.0,
            "observed_changes": 0.0,
            "change_delay_total_s": 0.0,
            "change_delay_max_s": 0.0,
            "unresolved_changes": 0.0,
            "max_green_forced_commands": 0.0,
            "max_observed_green_s": 0.0,
            "valid_phase_count": len(_phase_orders[intersection_id]),
            "phase_commands": {},
        }
        for intersection_id in _intersection_ids
    }
    _pending_signal_commands = {}
    return initialize_response(episode_id=str(payload["episode_id"]))


def step(payload: dict) -> dict:
    if _state_builder is None:
        raise RuntimeError("MAPPO is not initialized.")
    simulation_time = float(
        payload.get(
            "simulation_time", float(payload.get("step_id", 0)) * _decision_interval
        )
    )
    observations = payload.get("intersections", {})
    _observe_signal_execution(observations, simulation_time)
    states = _state_builder.get_all_states(payload)
    signal_actions: Dict[str, dict] = {}

    for intersection_id in _intersection_ids:
        intersection = observations.get(intersection_id)
        if not intersection:
            continue
        state = states[intersection_id]
        if not _eligible_for_decision(
            intersection, simulation_time, _last_decision_times[intersection_id]
        ):
            continue

        phase_order = _phase_orders[intersection_id]
        local_features = _state_builder.build_phase_features(
            intersection_id,
            intersection,
            simulation_time=simulation_time,
            last_service_times=_last_phase_service_times[intersection_id],
            vehicles=(payload.get("vehicles", {}) if _effective_demand_enabled else {}),
            demand_horizon_seconds=_action_interval,
        )
        local_mask, max_green_forced = _state_builder.build_action_mask(
            intersection_id,
            intersection,
            max_green_factor=_max_green_factor,
        )
        phase_features = np.zeros((_act_dim, 11), dtype=np.float32)
        phase_features[: len(phase_order)] = local_features
        action_mask = np.zeros(_act_dim, dtype=np.bool_)
        action_mask[: len(local_mask)] = local_mask
        action_index = _choose_action(state, phase_features, action_mask)
        target_phase = phase_order[action_index]
        signal_actions[intersection_id] = {"target_phase": target_phase}
        _record_signal_command(
            intersection_id,
            intersection,
            target_phase,
            simulation_time,
            max_green_forced=max_green_forced,
        )
        _last_decision_times[intersection_id] = simulation_time

    return step_response(
        episode_id=str(payload["episode_id"]),
        step_id=payload["step_id"],
        signals=signal_actions,
    )


def finish(payload: dict) -> dict:
    global _policy, _state_builder, _phase_orders, _intersection_ids
    global _loaded_model_path, _model_path
    global _last_decision_times, _last_phase_service_times
    global _signal_execution_stats, _pending_signal_commands
    already = _state_builder is None
    _state_builder = None
    _phase_orders = {}
    _intersection_ids = ()
    _last_decision_times = {}
    _last_phase_service_times = {}
    _signal_execution_stats = {}
    _pending_signal_commands = {}
    # Keep loaded model weights for warm reuse within the same worker process.
    _ = payload
    return finish_response(already_finished=already)
