"""Protocol adapter and heterogeneous SMDP-MAPPO runtime for frozen CoV2X MVP."""
from __future__ import annotations

from collections import Counter
from copy import deepcopy
from dataclasses import dataclass, field
from hashlib import sha256
from pathlib import Path
from threading import RLock
from typing import Any, Mapping, Sequence
import os
import random
import tempfile

import numpy as np
import torch

from traffic_control.cov2x.runtime.collab.mvp_policy import (
    CentralizedCritic,
    ConditionedCentralizedCritic,
    CloudTanhNormalActor,
    MVPPolicyConfig,
    RunningValueNormalizer,
    VehicleSpeedAdviceActor,
    cloud_feature_matrix,
    critic_context_vector,
    directed_physical_edges,
    global_state_vector,
    movement_feature_matrix,
    phase_feature_matrix,
    road_feature_vector,
    vehicle_feature_vector,
)
from traffic_control.cov2x.runtime.contract import (
    CANDIDATE_ID,
    CHECKPOINT_FORMAT_VERSION,
    CRITIC_LINEAGE,
    DELTA_V_MAX_SPEED_CEILING_FRACTION,
    FROZEN_ACTOR_ROLES,
    LOCAL_CREDIT_ACTOR_UPDATE_SCHEDULE_ID,
    LOCAL_CREDIT_CANDIDATE_ID,
    LOCAL_CREDIT_CHECKPOINT_FORMAT_VERSION,
    LOCAL_CREDIT_CRITIC_LINEAGE,
    LOCAL_CREDIT_G30_PARENT_CANDIDATE_ID,
    LOCAL_CREDIT_G30_PARENT_CHECKPOINT_FORMAT_VERSION,
    LOCAL_CREDIT_G30_PARENT_CHECKPOINT_GENERATION,
    LOCAL_CREDIT_G30_PARENT_CHECKPOINT_SHA256,
    LOCAL_CREDIT_INITIAL_DETERMINISTIC_MEAN,
    LOCAL_CREDIT_REWARD_SEMANTICS,
    NATIVE_RELEASE_TOLERANCE_MPS,
    PARENT_CANDIDATE_ID,
    PARENT_CHECKPOINT_FORMAT_VERSION,
    PARENT_CHECKPOINT_GENERATION,
    PARENT_CHECKPOINT_SHA256,
    SHAPING_COEFFICIENTS,
    TEMPORARY_SPEED_CAP_ACTION_SEMANTICS,
    TEMPORARY_SPEED_CAP_ACTOR_UPDATE_SCHEDULE_ID,
    TEMPORARY_SPEED_CAP_EXTENDED_ACTOR_UPDATE_SCHEDULE_ID,
    TEMPORARY_SPEED_CAP_THREE_SCOPE_ACTOR_UPDATE_SCHEDULE_ID,
    TEMPORARY_SPEED_CAP_CANDIDATE_ID,
    TEMPORARY_SPEED_CAP_CHECKPOINT_FORMAT_VERSION,
    TEMPORARY_SPEED_CAP_INITIAL_DETERMINISTIC_MEAN,
    VEHICLE_ACTION_SEMANTICS,
    normalized_mp_scores,
)
from traffic_control.cov2x.runtime.reward_ledger import VehicleLifecycleLedger, network_time_loss_reward
from traffic_control.cov2x.runtime.road.mp_prior import (
    RoadResidualConfig,
    RoadResidualNetwork,
    StrongMPPressureOracle,
    phase_pressures_from_payload,
)
from traffic_control.cov2x.runtime.smdp import SMDPTransition, assert_closed, role_entity_gae
from traffic_control.cov2x.communication.transport import IdealPhasedTransport, TypedEnvelope
from traffic_control.cov2x.runtime.vehicle.actuator import (
    ConstraintState,
    classify_constraint,
    vehicle_limits,
)
from traffic_control.cov2x.runtime.vehicle.movement_corridor import MovementApproachCorridor
from traffic_control.cov2x.runtime.vehicle.movement_local_credit import MovementLocalCreditLedger
from traffic_control.cov2x.runtime.vehicle.pooling import masked_movement_pool
from traffic_control.cov2x.runtime.vehicle.speed_advice import (
    apply_incremental_speed_advice,
    apply_temporary_base_relative_speed_advice,
    reference_base_speed,
)
from traffic_control.cov2x.runtime.vehicle.sticky_leader import StickyLeadCAV
from traffic_control.max_pressure import MaxPressureController


@dataclass
class MVPRollout:
    episode_id: str
    period: str
    seed: int
    duration_s: float
    policy_generation: int
    authority_gains: dict[str, float] = field(default_factory=dict)
    transitions: list[SMDPTransition] = field(default_factory=list)
    metrics: dict[str, Any] = field(default_factory=dict)


_config = MVPPolicyConfig()
_initialized = False
_episode_id = ""
_period = ""
_seed = 0
_duration_s = 0.0
_decision_interval = 5.0
_minimum_green = 5.0
_phase_orders: dict[str, tuple[int, ...]] = {}
_intersection_metadata: dict[str, Mapping[str, Any]] = {}
_vehicle_min_gaps: dict[str, float] = {}
_vehicle_types: dict[str, Mapping[str, Any]] = {}
_cloud_priority: dict[str, float] = {}
_policy_generation = 0
_mode = "eval"
_delta_R = 0.5
_authority_gains = {"road": 1.0, "cloud": 1.0, "vehicle": 1.0}
_cloud_actor: CloudTanhNormalActor | None = None
_road_actor: RoadResidualNetwork | None = None
_vehicle_actor: VehicleSpeedAdviceActor | None = None
_critic: CentralizedCritic | None = None
_optimizers: dict[str, torch.optim.Optimizer] = {}
_value_normalizer = RunningValueNormalizer()
_loaded_checkpoint: str | None = None
_parent_provenance_verified = False
_initialization_seed = 0
_initialization_seed_role: str | None = None
_critic_updates = 0
_last_trainable_actor_roles: tuple[str, ...] | None = None
_ledger = VehicleLifecycleLedger()
_transport = IdealPhasedTransport()
_v2x_event_sink: Any | None = None
_leaders = StickyLeadCAV()
_movement_corridor: MovementApproachCorridor | None = None
_movement_local_credit = MovementLocalCreditLedger()
_rollout: MVPRollout | None = None
_pending: dict[str, SMDPTransition] = {}
_collected_rollouts: list[MVPRollout] = []
_last_total = 0.0
_commanded: dict[str, dict[str, Any]] = {}
_speed_advice: dict[tuple[str, str, str, int], dict[str, float]] = {}
_blocked_vehicle_ids: set[str] = set()
_authority = {
    "opportunities": 0,
    "eligible_action_opportunities": 0,
    "stochastic_active_cap": 0,
    "advice_transitions": 0,
    "active_cap": 0,
    "native_release": 0,
    "authority_zero_release": 0,
    "fail_closed": 0,
    "commands_submitted": 0,
    "commands_audited": 0,
    "commands_accepted": 0,
    "commands_rejected": 0,
    "tracking_miss": 0,
    "observable_native_limit_proxy": 0,
    "vehicle_arrived": 0,
    "terminal_unaudited": 0,
}
_corridor_stats: Counter[str] = Counter()
_fail_closed_reasons: Counter[str] = Counter()
_pressure_oracle: StrongMPPressureOracle | None = None
_strong_mp_controller: MaxPressureController | None = None
_vehicle_execution_audit: list[dict[str, Any]] = []
_safety = {"red_crossing_proxy": 0, "dangerous_gap": 0}
_base_policy = {
    "road_decisions": 0,
    "road_mismatches": 0,
    "cloud_abs_max": 0.0,
    "vehicle_commands": 0,
}
_phase_history_audit_lock = RLock()
_phase_history_audit: dict[str, list[dict[str, Any]]] = {
    "policy_snapshots": [],
    "proxy_events": [],
}
_phase_a_joint_action_override: dict[str, dict[str, Any]] | None = None


def _temporary_speed_cap_enabled() -> bool:
    return os.environ.get("COV2X_TEMPORARY_SPEED_CAP_V1", "0") == "1"


def _local_credit_enabled() -> bool:
    return (
        os.environ.get("COV2X_LOCAL_CREDIT_V1", "0") == "1"
        or _temporary_speed_cap_enabled()
    )


def _runtime_candidate_id() -> str:
    if _temporary_speed_cap_enabled():
        return TEMPORARY_SPEED_CAP_CANDIDATE_ID
    return LOCAL_CREDIT_CANDIDATE_ID if _local_credit_enabled() else CANDIDATE_ID


def _runtime_action_semantics() -> str:
    return (
        TEMPORARY_SPEED_CAP_ACTION_SEMANTICS
        if _temporary_speed_cap_enabled()
        else VEHICLE_ACTION_SEMANTICS
    )


def _runtime_checkpoint_format_version() -> int:
    return (
        TEMPORARY_SPEED_CAP_CHECKPOINT_FORMAT_VERSION
        if _temporary_speed_cap_enabled()
        else LOCAL_CREDIT_CHECKPOINT_FORMAT_VERSION
    )


def _runtime_initial_vehicle_mean() -> float:
    return (
        TEMPORARY_SPEED_CAP_INITIAL_DETERMINISTIC_MEAN
        if _temporary_speed_cap_enabled()
        else LOCAL_CREDIT_INITIAL_DETERMINISTIC_MEAN
    )


def _runtime_actor_update_schedule_id() -> str:
    if not _temporary_speed_cap_enabled():
        return LOCAL_CREDIT_ACTOR_UPDATE_SCHEDULE_ID
    schedule_id = os.environ.get(
        "COV2X_ACTOR_UPDATE_SCHEDULE_ID",
        TEMPORARY_SPEED_CAP_ACTOR_UPDATE_SCHEDULE_ID,
    )
    allowed = {
        TEMPORARY_SPEED_CAP_ACTOR_UPDATE_SCHEDULE_ID,
        TEMPORARY_SPEED_CAP_EXTENDED_ACTOR_UPDATE_SCHEDULE_ID,
        TEMPORARY_SPEED_CAP_THREE_SCOPE_ACTOR_UPDATE_SCHEDULE_ID,
    }
    if schedule_id not in allowed:
        raise ValueError("temporary speed-cap actor update schedule mismatch")
    return schedule_id


def _vehicle_local_policy_generation_limit(
    *, temporary: bool, schedule_id: str
) -> int:
    if temporary and schedule_id in {
        TEMPORARY_SPEED_CAP_EXTENDED_ACTOR_UPDATE_SCHEDULE_ID,
        TEMPORARY_SPEED_CAP_THREE_SCOPE_ACTOR_UPDATE_SCHEDULE_ID,
    }:
        return 24
    return 8


def _read_authority_gains() -> dict[str, float]:
    result = {}
    for role in ("road", "cloud", "vehicle"):
        value = float(os.environ.get(f"COV2X_{role.upper()}_GAIN", "1.0"))
        if not 0.0 <= value <= 1.0:
            raise ValueError(f"{role} authority gain must be in [0, 1]")
        result[role] = value
    return result


def set_v2x_event_sink(sink: Any | None) -> None:
    """Attach an optional backend sink without coupling it to control."""
    global _v2x_event_sink
    _v2x_event_sink = sink
    _transport.set_event_sink(sink)


def drain_v2x_events() -> dict[str, Any]:
    """Destructively drain events not returned by the previous drain call."""
    return _transport.drain()


def reset_untrained_state() -> None:
    """Reset process-local model state before creating a generation-0 file."""
    global _cloud_actor, _road_actor, _vehicle_actor, _critic, _optimizers
    global _value_normalizer, _loaded_checkpoint, _policy_generation
    global _parent_provenance_verified
    global _critic_updates, _initialization_seed, _initialization_seed_role
    global _last_trainable_actor_roles
    global _initialized, _rollout, _pending, _cloud_priority, _commanded
    global _speed_advice, _blocked_vehicle_ids, _movement_corridor
    global _movement_local_credit
    global _corridor_stats, _fail_closed_reasons
    global _phase_history_audit, _phase_a_joint_action_override
    _cloud_actor = _road_actor = _vehicle_actor = _critic = None
    _optimizers = {}
    _value_normalizer = RunningValueNormalizer()
    _loaded_checkpoint = None
    _parent_provenance_verified = False
    _policy_generation = 0
    _critic_updates = 0
    _last_trainable_actor_roles = None
    _initialization_seed = 0
    _initialization_seed_role = None
    _initialized = False
    _rollout = None
    _pending = {}
    _cloud_priority = {}
    _commanded = {}
    _speed_advice = {}
    _blocked_vehicle_ids = set()
    _movement_corridor = None
    _movement_local_credit = MovementLocalCreditLedger()
    _corridor_stats = Counter()
    _fail_closed_reasons = Counter()
    with _phase_history_audit_lock:
        _phase_history_audit = {"policy_snapshots": [], "proxy_events": []}
    _phase_a_joint_action_override = None
    _collected_rollouts.clear()


def _is_base_policy() -> bool:
    return all(value == 0.0 for value in _authority_gains.values())


def _initialize_models() -> None:
    global _cloud_actor, _road_actor, _vehicle_actor, _critic, _optimizers
    global _initialization_seed, _initialization_seed_role
    fresh = all(
        model is None
        for model in (_cloud_actor, _road_actor, _vehicle_actor, _critic)
    )
    if _cloud_actor is None:
        _cloud_actor = CloudTanhNormalActor(_config)
    if _road_actor is None:
        _road_actor = RoadResidualNetwork(RoadResidualConfig(delta_R=_delta_R))
    if _vehicle_actor is None:
        _vehicle_actor = VehicleSpeedAdviceActor(_config)
    if _critic is None:
        _critic = (
            ConditionedCentralizedCritic(_config)
            if _local_credit_enabled()
            else CentralizedCritic(_config)
        )
    if not _optimizers:
        _optimizers = {
            "cloud": torch.optim.Adam(_cloud_actor.parameters(), lr=_config.actor_lr),
            "road": torch.optim.Adam(_road_actor.parameters(), lr=_config.actor_lr),
            "vehicle": torch.optim.Adam(_vehicle_actor.parameters(), lr=_config.actor_lr),
            "critic": torch.optim.Adam(_critic.parameters(), lr=_config.critic_lr),
        }
    if fresh:
        _initialization_seed = _seed
        _initialization_seed_role = os.environ.get("COV2X_SEED_ROLE")


def _module_schema(module: torch.nn.Module) -> dict[str, tuple[int, ...]]:
    return {
        name: tuple(int(value) for value in tensor.shape)
        for name, tensor in module.state_dict().items()
    }


def _load_schema_compatible(
    module: torch.nn.Module,
    state: Mapping[str, Any],
    component: str,
) -> None:
    incoming = {
        str(name): tuple(int(value) for value in tensor.shape)
        for name, tensor in state.items()
    }
    expected = _module_schema(module)
    if incoming != expected:
        raise ValueError(f"{component} checkpoint schema is incompatible")
    module.load_state_dict(state)


def _validate_common_checkpoint(state: Mapping[str, Any]) -> None:
    if abs(float(state.get("delta_R", _delta_R)) - _delta_R) > 1e-12:
        raise ValueError("checkpoint delta_R does not match frozen authority config")
    if state.get("shaping_coefficients") != SHAPING_COEFFICIENTS:
        raise ValueError("checkpoint shaping coefficients are not the frozen zero config")
    expected_screen_hash = os.environ.get("COV2X_SCREEN_CONFIG_HASH")
    if expected_screen_hash and state.get("screen_config_hash") != expected_screen_hash:
        raise ValueError("checkpoint SCREEN config hash does not match protocol")


def _migrate_parent_checkpoint(
    state: Mapping[str, Any],
    actual_sha256: str,
    *,
    expected_sha256: str = PARENT_CHECKPOINT_SHA256,
) -> None:
    """Retain frozen Road/Cloud actors and reset changed context semantics."""

    global _policy_generation, _critic_updates, _parent_provenance_verified
    global _last_trainable_actor_roles
    global _vehicle_actor, _critic, _optimizers, _value_normalizer
    if actual_sha256 != expected_sha256:
        raise ValueError("parent checkpoint SHA-256 does not match frozen provenance")
    if state.get("candidate_id") != PARENT_CANDIDATE_ID:
        raise ValueError("parent checkpoint candidate_id is not the frozen predecessor")
    if int(state.get("format_version", -1)) != PARENT_CHECKPOINT_FORMAT_VERSION:
        raise ValueError("parent checkpoint format does not match frozen version 6")
    if int(state.get("policy_generation", -1)) != PARENT_CHECKPOINT_GENERATION:
        raise ValueError("parent checkpoint generation is not the frozen generation 5")
    _validate_common_checkpoint(state)
    assert _cloud_actor is not None and _road_actor is not None
    for name, module in (("cloud", _cloud_actor), ("road", _road_actor)):
        _load_schema_compatible(module, state[f"{name}_actor"], name)
    _vehicle_actor = VehicleSpeedAdviceActor(_config)
    _critic = CentralizedCritic(_config)
    _value_normalizer = RunningValueNormalizer()
    _optimizers = {
        "cloud": torch.optim.Adam(
            _cloud_actor.parameters(), lr=_config.actor_lr
        ),
        "road": torch.optim.Adam(
            _road_actor.parameters(), lr=_config.actor_lr
        ),
        "vehicle": torch.optim.Adam(
            _vehicle_actor.parameters(), lr=_config.actor_lr
        ),
        "critic": torch.optim.Adam(
            _critic.parameters(), lr=_config.critic_lr
        ),
    }
    _policy_generation = 0
    _critic_updates = 0
    _last_trainable_actor_roles = None
    _parent_provenance_verified = True


def _migrate_g30_local_credit(
    state: Mapping[str, Any], actual_sha256: str
) -> None:
    """Copy only frozen G30 Road/Cloud actors; reset all Vehicle/value state."""

    global _policy_generation, _critic_updates, _parent_provenance_verified
    global _last_trainable_actor_roles
    global _vehicle_actor, _critic, _optimizers, _value_normalizer
    if not _local_credit_enabled():
        raise ValueError("local-credit parent requires COV2X_LOCAL_CREDIT_V1=1")
    expected = {
        "candidate_id": LOCAL_CREDIT_G30_PARENT_CANDIDATE_ID,
        "format_version": LOCAL_CREDIT_G30_PARENT_CHECKPOINT_FORMAT_VERSION,
        "policy_generation": LOCAL_CREDIT_G30_PARENT_CHECKPOINT_GENERATION,
        "vehicle_action_semantics": VEHICLE_ACTION_SEMANTICS,
    }
    if actual_sha256 != LOCAL_CREDIT_G30_PARENT_CHECKPOINT_SHA256:
        raise ValueError("local-credit G30 parent SHA-256 mismatch")
    for field, value in expected.items():
        if state.get(field) != value:
            raise ValueError(f"local-credit G30 parent {field} mismatch")
    _validate_common_checkpoint(state)
    assert _cloud_actor is not None and _road_actor is not None
    for name, module in (("cloud", _cloud_actor), ("road", _road_actor)):
        _load_schema_compatible(module, state[f"{name}_actor"], name)
    _vehicle_actor = VehicleSpeedAdviceActor(_config)
    _vehicle_actor.initialize_local_credit_final(
        _runtime_initial_vehicle_mean()
    )
    _critic = ConditionedCentralizedCritic(_config)
    _value_normalizer = RunningValueNormalizer()
    _optimizers = {
        "cloud": torch.optim.Adam(_cloud_actor.parameters(), lr=_config.actor_lr),
        "road": torch.optim.Adam(_road_actor.parameters(), lr=_config.actor_lr),
        "vehicle": torch.optim.Adam(_vehicle_actor.parameters(), lr=_config.actor_lr),
        "critic": torch.optim.Adam(_critic.parameters(), lr=_config.critic_lr),
    }
    _policy_generation = 0
    _critic_updates = 0
    _last_trainable_actor_roles = None
    _parent_provenance_verified = True


def _load_local_credit_checkpoint(state: Mapping[str, Any]) -> None:
    global _policy_generation, _critic_updates, _initialization_seed
    global _initialization_seed_role, _last_trainable_actor_roles
    global _parent_provenance_verified
    candidate_id = state.get("candidate_id")
    temporary = candidate_id == TEMPORARY_SPEED_CAP_CANDIDATE_ID
    if candidate_id not in {
        LOCAL_CREDIT_CANDIDATE_ID, TEMPORARY_SPEED_CAP_CANDIDATE_ID
    }:
        raise ValueError("Vehicle-local checkpoint candidate_id mismatch")
    if temporary != _temporary_speed_cap_enabled():
        raise ValueError("Vehicle-local checkpoint/runtime semantic profile mismatch")
    if not _local_credit_enabled():
        raise ValueError("Vehicle-local checkpoint requires local-credit runtime")
    expected = {
        "candidate_id": (
            TEMPORARY_SPEED_CAP_CANDIDATE_ID
            if temporary else LOCAL_CREDIT_CANDIDATE_ID
        ),
        "format_version": (
            TEMPORARY_SPEED_CAP_CHECKPOINT_FORMAT_VERSION
            if temporary else LOCAL_CREDIT_CHECKPOINT_FORMAT_VERSION
        ),
        "vehicle_action_semantics": (
            TEMPORARY_SPEED_CAP_ACTION_SEMANTICS
            if temporary else VEHICLE_ACTION_SEMANTICS
        ),
        "parent_candidate_id": LOCAL_CREDIT_G30_PARENT_CANDIDATE_ID,
        "parent_checkpoint_format_version": (
            LOCAL_CREDIT_G30_PARENT_CHECKPOINT_FORMAT_VERSION
        ),
        "parent_checkpoint_generation": LOCAL_CREDIT_G30_PARENT_CHECKPOINT_GENERATION,
        "parent_checkpoint_sha256": LOCAL_CREDIT_G30_PARENT_CHECKPOINT_SHA256,
        "critic_lineage": LOCAL_CREDIT_CRITIC_LINEAGE,
        "local_credit_reward_semantics": LOCAL_CREDIT_REWARD_SEMANTICS,
        "initial_deterministic_vehicle_mean": (
            TEMPORARY_SPEED_CAP_INITIAL_DETERMINISTIC_MEAN
            if temporary else LOCAL_CREDIT_INITIAL_DETERMINISTIC_MEAN
        ),
        "actor_update_schedule_id": (
            _runtime_actor_update_schedule_id()
            if temporary else LOCAL_CREDIT_ACTOR_UPDATE_SCHEDULE_ID
        ),
        "frozen_actor_roles": list(FROZEN_ACTOR_ROLES),
    }
    for field, value in expected.items():
        if state.get(field) != value:
            raise ValueError(f"Vehicle-local checkpoint {field} mismatch")
    if temporary:
        expected_config_hash = os.environ.get(
            "COV2X_TEMPORARY_SPEED_CAP_CONFIG_HASH"
        )
        checkpoint_config_hash = state.get("temporary_speed_cap_config_hash")
    else:
        expected_config_hash = os.environ.get("COV2X_LOCAL_CREDIT_CONFIG_HASH")
        checkpoint_config_hash = state.get("local_credit_config_hash")
    if expected_config_hash and checkpoint_config_hash != expected_config_hash:
        raise ValueError("Vehicle-local checkpoint config hash mismatch")
    _validate_common_checkpoint(state)
    assert _cloud_actor is not None and _road_actor is not None
    assert _vehicle_actor is not None and _critic is not None
    if not isinstance(_critic, ConditionedCentralizedCritic):
        raise ValueError("Vehicle-local critic architecture mismatch")
    components = {
        "cloud": (_cloud_actor, state["cloud_actor"]),
        "road": (_road_actor, state["road_actor"]),
        "vehicle": (_vehicle_actor, state["vehicle_actor"]),
        "critic": (_critic, state["critic"]),
    }
    expected_schema = {
        name: _module_schema(module) for name, (module, _) in components.items()
    }
    if state.get("component_schema") != expected_schema:
        raise ValueError("Vehicle-local component schema fingerprint mismatch")
    generation = int(state.get("policy_generation", -1))
    generation_limit = _vehicle_local_policy_generation_limit(
        temporary=temporary,
        schedule_id=str(expected["actor_update_schedule_id"]),
    )
    if generation < 0 or generation > generation_limit:
        raise ValueError("Vehicle-local policy generation outside frozen schedule")
    if generation == 0:
        vehicle_state = state["vehicle_actor"]
        target_mean = float(expected["initial_deterministic_vehicle_mean"])
        target_bias = float(np.arctanh(target_mean))
        if (
            torch.count_nonzero(vehicle_state["mean.weight"]).item() != 0
            or abs(float(vehicle_state["mean.bias"].item()) - target_bias) > 1e-7
            or float(vehicle_state["log_std"].item()) != -1.0
        ):
            raise ValueError("Vehicle-local generation-0 initialization mismatch")
    for name, (module, component_state) in components.items():
        _load_schema_compatible(module, component_state, name)
    if set(state.get("optimizers", {})) != {"cloud", "road", "vehicle", "critic"}:
        raise ValueError("Vehicle-local optimizer role set mismatch")
    for name in ("cloud", "road", "vehicle", "critic"):
        _optimizers[name].load_state_dict(state["optimizers"][name])
    _value_normalizer.load_state_dict(state.get("value_normalizer", {}))
    rng_state = state.get("rng_state")
    if set(rng_state or {}) != {"python", "numpy", "torch_cpu"}:
        raise ValueError("Vehicle-local checkpoint RNG state contract mismatch")
    random.setstate(rng_state["python"])
    np.random.set_state(rng_state["numpy"])
    torch.set_rng_state(rng_state["torch_cpu"])
    _policy_generation = generation
    _critic_updates = int(state.get("critic_updates", 0))
    _initialization_seed = int(state.get("initialization_seed", 0) or 0)
    _initialization_seed_role = state.get("initialization_seed_role")
    saved_trainable = state.get("trainable_actor_roles_at_save")
    _last_trainable_actor_roles = (
        None if saved_trainable is None else tuple(str(role) for role in saved_trainable)
    )
    if set(_last_trainable_actor_roles or ()) - {"vehicle"}:
        raise ValueError("Vehicle-local checkpoint restored non-Vehicle trainable actor")
    _parent_provenance_verified = True


def _load_v7_checkpoint(state: Mapping[str, Any]) -> None:
    global _loaded_checkpoint, _policy_generation, _critic_updates
    global _initialization_seed, _initialization_seed_role
    global _last_trainable_actor_roles
    global _parent_provenance_verified
    if state.get("candidate_id") != CANDIDATE_ID:
        raise ValueError("checkpoint candidate_id does not match corridor v1")
    if int(state.get("format_version", -1)) != CHECKPOINT_FORMAT_VERSION:
        raise ValueError("checkpoint format version is not 7")
    if state.get("vehicle_action_semantics") != VEHICLE_ACTION_SEMANTICS:
        raise ValueError("checkpoint Vehicle action semantics mismatch")
    if (
        float(state.get("delta_v_max_speed_ceiling_fraction", -1.0))
        != DELTA_V_MAX_SPEED_CEILING_FRACTION
    ):
        raise ValueError("checkpoint delta_v_max_speed_ceiling_fraction mismatch")
    if (
        float(state.get("native_release_tolerance_mps", -1.0))
        != NATIVE_RELEASE_TOLERANCE_MPS
    ):
        raise ValueError("checkpoint native release tolerance mismatch")
    if state.get("parent_candidate_id") != PARENT_CANDIDATE_ID:
        raise ValueError("checkpoint parent candidate provenance mismatch")
    if (
        int(state.get("parent_checkpoint_format_version", -1))
        != PARENT_CHECKPOINT_FORMAT_VERSION
    ):
        raise ValueError("checkpoint parent format provenance mismatch")
    if (
        int(state.get("parent_checkpoint_generation", -1))
        != PARENT_CHECKPOINT_GENERATION
    ):
        raise ValueError("checkpoint parent generation provenance mismatch")
    if state.get("parent_checkpoint_sha256") != PARENT_CHECKPOINT_SHA256:
        raise ValueError("checkpoint parent SHA-256 provenance mismatch")
    if state.get("critic_lineage") != CRITIC_LINEAGE:
        raise ValueError("checkpoint critic lineage mismatch")
    if tuple(state.get("frozen_actor_roles", ())) != FROZEN_ACTOR_ROLES:
        raise ValueError("checkpoint frozen actor roles mismatch")
    expected_config_hash = os.environ.get("COV2X_CORRIDOR_CONFIG_HASH")
    if (
        expected_config_hash
        and state.get("corridor_config_hash") != expected_config_hash
    ):
        raise ValueError("checkpoint corridor config hash does not match protocol")
    _validate_common_checkpoint(state)
    assert _cloud_actor is not None and _road_actor is not None and _vehicle_actor is not None and _critic is not None
    components = {
        "cloud": (_cloud_actor, state["cloud_actor"]),
        "road": (_road_actor, state["road_actor"]),
        "vehicle": (_vehicle_actor, state["vehicle_actor"]),
        "critic": (_critic, state["critic"]),
    }
    frozen_schema = state.get("component_schema")
    expected_schema = {
        name: _module_schema(module) for name, (module, _) in components.items()
    }
    if frozen_schema != expected_schema:
        raise ValueError("checkpoint component schema fingerprint mismatch")
    if int(state.get("policy_generation", -1)) == 0:
        vehicle_state = state["vehicle_actor"]
        if (
            torch.count_nonzero(vehicle_state["mean.weight"]).item() != 0
            or torch.count_nonzero(vehicle_state["mean.bias"]).item() != 0
            or float(vehicle_state["log_std"].item()) != -1.0
        ):
            raise ValueError(
                "generation-0 Vehicle actor is not exact zero-mean"
            )
    for name, (module, component_state) in components.items():
        _load_schema_compatible(module, component_state, name)
    for name in ("cloud", "road", "vehicle", "critic"):
        optimizer = _optimizers[name]
        if name in state.get("optimizers", {}):
            optimizer.load_state_dict(state["optimizers"][name])
    _value_normalizer.load_state_dict(state.get("value_normalizer", {}))
    rng_state = state.get("rng_state")
    if rng_state is not None:
        required_rng = {"python", "numpy", "torch_cpu"}
        if set(rng_state) != required_rng:
            raise ValueError("checkpoint RNG state contract mismatch")
        random.setstate(rng_state["python"])
        np.random.set_state(rng_state["numpy"])
        torch.set_rng_state(rng_state["torch_cpu"])
    _policy_generation = int(state.get("policy_generation", 0))
    _critic_updates = int(state.get("critic_updates", 0))
    _initialization_seed = int(
        state.get("initialization_seed", _initialization_seed) or 0
    )
    _initialization_seed_role = state.get(
        "initialization_seed_role", state.get("seed_role", _initialization_seed_role)
    )
    saved_trainable = state.get("trainable_actor_roles_at_save")
    if saved_trainable is None:
        _last_trainable_actor_roles = None
    else:
        roles = tuple(str(role) for role in saved_trainable)
        if set(roles) - {"cloud", "road", "vehicle"}:
            raise ValueError("checkpoint trainable actor role metadata mismatch")
        _last_trainable_actor_roles = roles
    _parent_provenance_verified = True


def _maybe_load_checkpoint() -> None:
    global _loaded_checkpoint
    model_path = os.environ.get("COV2X_MODEL_PATH")
    parent_path = os.environ.get("COV2X_PARENT_MODEL_PATH")
    local_parent_path = os.environ.get("COV2X_LOCAL_CREDIT_PARENT_MODEL_PATH")
    provided = [value for value in (model_path, parent_path, local_parent_path) if value]
    if len(provided) > 1:
        raise ValueError("set exactly one CoV2X model/parent path")
    path = provided[0] if provided else None
    if not path or path == _loaded_checkpoint:
        return
    target = Path(path)
    state = torch.load(target, map_location="cpu", weights_only=False)
    if local_parent_path:
        actual_sha = sha256(target.read_bytes()).hexdigest()
        _migrate_g30_local_credit(state, actual_sha)
    elif parent_path:
        actual_sha = sha256(target.read_bytes()).hexdigest()
        _migrate_parent_checkpoint(state, actual_sha)
    elif state.get("candidate_id") in {
        LOCAL_CREDIT_CANDIDATE_ID, TEMPORARY_SPEED_CAP_CANDIDATE_ID
    }:
        _load_local_credit_checkpoint(state)
    else:
        _load_v7_checkpoint(state)
    _loaded_checkpoint = path


def restore_full_checkpoint(path: str) -> None:
    """Restore an exact legacy, local-credit, or temporary-cap full state."""
    global _delta_R, _loaded_checkpoint, _mode
    target = Path(path).resolve()
    state = torch.load(target, map_location="cpu", weights_only=False)
    if state.get("candidate_id") in {
        LOCAL_CREDIT_CANDIDATE_ID, TEMPORARY_SPEED_CAP_CANDIDATE_ID
    }:
        os.environ["COV2X_LOCAL_CREDIT_V1"] = "1"
    else:
        os.environ.pop("COV2X_LOCAL_CREDIT_V1", None)
    if state.get("candidate_id") == TEMPORARY_SPEED_CAP_CANDIDATE_ID:
        os.environ["COV2X_TEMPORARY_SPEED_CAP_V1"] = "1"
        schedule_id = state.get("actor_update_schedule_id")
        if schedule_id is not None:
            os.environ["COV2X_ACTOR_UPDATE_SCHEDULE_ID"] = str(schedule_id)
    else:
        os.environ.pop("COV2X_TEMPORARY_SPEED_CAP_V1", None)
    _delta_R = float(os.environ.get("COV2X_DELTA_R", state.get("delta_R", 0.5)))
    _mode = os.environ.get("COV2X_MODE", "eval").lower()
    _initialize_models()
    if state.get("candidate_id") in {
        LOCAL_CREDIT_CANDIDATE_ID, TEMPORARY_SPEED_CAP_CANDIDATE_ID
    }:
        _load_local_credit_checkpoint(state)
    else:
        _load_v7_checkpoint(state)
    _loaded_checkpoint = str(target)


def initialize(payload: Mapping[str, Any]) -> dict[str, Any]:
    global _initialized, _episode_id, _period, _seed, _duration_s, _decision_interval
    global _minimum_green, _intersection_metadata, _vehicle_min_gaps, _vehicle_types
    global _phase_orders, _mode, _delta_R, _authority_gains
    global _ledger, _transport, _leaders, _rollout
    global _pending, _last_total, _cloud_priority, _commanded, _speed_advice, _authority
    global _blocked_vehicle_ids, _movement_corridor
    global _movement_local_credit
    global _corridor_stats, _fail_closed_reasons
    global _pressure_oracle, _strong_mp_controller
    global _vehicle_execution_audit, _safety, _base_policy
    global _phase_history_audit, _phase_a_joint_action_override
    _episode_id = str(payload.get("episode_id", ""))
    _period = str(payload.get("period", ""))
    _seed = int(payload.get("seed", 0) or 0)
    _duration_s = float(
        payload.get(
            "duration_seconds",
            os.environ.get("COV2X_EPISODE_DURATION", "0"),
        )
        or 0.0
    )
    _decision_interval = float(payload.get("decision_interval", 5.0) or 5.0)
    _minimum_green = float(payload.get("minimum_green", 5.0) or 5.0)
    _mode = os.environ.get("COV2X_MODE", "eval").lower()
    _delta_R = float(os.environ.get("COV2X_DELTA_R", "0.5") or 0.5)
    _authority_gains = _read_authority_gains()
    _intersection_metadata = dict(payload.get("intersections", {}) or {})
    _vehicle_types = {
        str(type_id): dict(item or {})
        for type_id, item in (payload.get("vehicle_types", {}) or {}).items()
    }
    _vehicle_min_gaps = {
        str(type_id): float((item or {}).get("min_gap_m", 2.5) or 2.5)
        for type_id, item in (payload.get("vehicle_types", {}) or {}).items()
    }
    _phase_orders = {}
    for tls_id, meta in _intersection_metadata.items():
        order = tuple(int(item) for item in (meta.get("phase_order") or ()))
        if order:
            _phase_orders[str(tls_id)] = order
    _pressure_oracle = StrongMPPressureOracle(payload)
    _movement_corridor = MovementApproachCorridor(payload)
    _movement_local_credit = MovementLocalCreditLedger(
        interval_s=_decision_interval
    )
    strong_mp_metadata = dict(payload)
    strong_mp_metadata["intersections"] = {
        str(tls_id): {"intersection_id": str(tls_id), **dict(item)}
        for tls_id, item in (payload.get("intersections", {}) or {}).items()
    }
    _strong_mp_controller = MaxPressureController(strong_mp_metadata)
    _initialize_models(); _maybe_load_checkpoint()
    if os.environ.get("COV2X_EPISODE_RESEED_AFTER_RESTORE", "0") == "1":
        random.seed(_seed)
        np.random.seed(_seed)
        torch.manual_seed(_seed)
    _ledger, _transport, _leaders = (
        VehicleLifecycleLedger(),
        IdealPhasedTransport(event_sink=_v2x_event_sink),
        StickyLeadCAV(),
    )
    _rollout = MVPRollout(
        _episode_id,
        _period,
        _seed,
        _duration_s,
        _policy_generation,
        authority_gains=dict(_authority_gains),
    )
    _pending, _cloud_priority, _commanded, _speed_advice = {}, {}, {}, {}
    _blocked_vehicle_ids = set()
    _corridor_stats = Counter()
    _fail_closed_reasons = Counter()
    _authority = {
        "opportunities": 0,
        "advice_transitions": 0,
        "eligible_action_opportunities": 0,
        "stochastic_active_cap": 0,
        "active_cap": 0,
        "native_release": 0,
        "authority_zero_release": 0,
        "fail_closed": 0,
        "commands_submitted": 0,
        "commands_audited": 0,
        "commands_accepted": 0,
        "commands_rejected": 0,
        "tracking_miss": 0,
        "observable_native_limit_proxy": 0,
        "vehicle_arrived": 0,
        "terminal_unaudited": 0,
    }
    _vehicle_execution_audit = []
    _safety = {"red_crossing_proxy": 0, "dangerous_gap": 0}
    _base_policy = {
        "road_decisions": 0,
        "road_mismatches": 0,
        "cloud_abs_max": 0.0,
        "vehicle_commands": 0,
    }
    with _phase_history_audit_lock:
        _phase_history_audit = {"policy_snapshots": [], "proxy_events": []}
    _phase_a_joint_action_override = None
    _last_total = 0.0
    _initialized = True
    return {
        "protocol_version": "2.0",
        "candidate_id": _runtime_candidate_id(),
        "episode_id": _episode_id,
        "policy_generation": _policy_generation,
        "ready": True,
        "v2x_event_export": {
            "schema": "cov2x.v2x.event_batch",
            "schema_version": "1.0",
            "inline_step_field": "v2x",
            "drain_api": "traffic_control.cov2x.drain_v2x_events",
        },
    }


def _active_time_loss(payload: Mapping[str, Any]) -> dict[str, float]:
    values = {}
    for vehicle_id, vehicle in (payload.get("vehicles", {}) or {}).items():
        if isinstance(vehicle, Mapping):
            traffic = vehicle.get("traffic", {}) or {}
            values[str(vehicle_id)] = float(traffic.get("time_loss_s", traffic.get("time_loss", 0.0)) or 0.0)
    return values


def _movement_context(payload: Mapping[str, Any]) -> dict[str, np.ndarray]:
    grouped: dict[tuple[str, str], list[np.ndarray]] = {}
    for vehicle in (payload.get("vehicles", {}) or {}).values():
        if not isinstance(vehicle, Mapping):
            continue
        next_signal, motion = vehicle.get("next_signal", {}) or {}, vehicle.get("motion", {}) or {}
        tls_id = str(next_signal.get("intersection_id") or next_signal.get("tls_id") or "")
        if not tls_id:
            continue
        movement = _resolve_vehicle_movement(tls_id, vehicle)
        if movement is None:
            continue
        grouped.setdefault((tls_id, movement), []).append(np.asarray([
            float(motion.get("speed_mps", 0.0) or 0.0) / 30.0,
            float(motion.get("acceleration_mps2", 0.0) or 0.0) / 5.0,
            float(next_signal.get("distance_m", 150.0) or 150.0) / 150.0,
            float(str(next_signal.get("state", "")).upper() in {"G", "GREEN"}),
            float(_cloud_priority.get(tls_id, 0.0)),
        ], dtype=np.float32))
    per_intersection: dict[str, list[np.ndarray]] = {}
    for (tls_id, _), rows in grouped.items():
        per_intersection.setdefault(tls_id, []).append(np.stack(rows).mean(0))
    result = {}
    for tls_id in _phase_orders:
        tokens = per_intersection.get(tls_id, [])[:8]
        matrix = np.zeros((8, 5), dtype=np.float32); mask = np.zeros(8, dtype=bool)
        if tokens:
            matrix[:len(tokens)] = np.stack(tokens); mask[:len(tokens)] = True
        result[tls_id] = masked_movement_pool(matrix, mask)
    return result


def _resolve_vehicle_movement(
    tls_id: str, vehicle: Mapping[str, Any]
) -> str | None:
    if _movement_corridor is None:
        return None
    return _movement_corridor.resolve(
        str(tls_id), vehicle
    ).resolved_movement_id




def _global_state(payload: Mapping[str, Any], movement_context: Mapping[str, Sequence[float]]) -> np.ndarray:
    return global_state_vector(payload, tuple(sorted(_phase_orders)), _cloud_priority, movement_context)


def _movement_local_context(
    payload: Mapping[str, Any],
) -> dict[tuple[str, str], np.ndarray]:
    grouped: dict[tuple[str, str], list[np.ndarray]] = {}
    for vehicle in (payload.get("vehicles", {}) or {}).values():
        if not isinstance(vehicle, Mapping):
            continue
        next_signal = vehicle.get("next_signal", {}) or {}
        motion = vehicle.get("motion", {}) or {}
        tls_id = str(
            next_signal.get("intersection_id") or next_signal.get("tls_id") or ""
        )
        if not tls_id:
            continue
        movement = _resolve_vehicle_movement(tls_id, vehicle)
        if movement is None:
            continue
        distance = float(next_signal.get("distance_m", 150.0) or 150.0)
        speed = float(motion.get("speed_mps", 0.0) or 0.0)
        grouped.setdefault((tls_id, movement), []).append(
            np.asarray(
                [
                    speed / 30.0,
                    float(motion.get("acceleration_mps2", 0.0) or 0.0) / 5.0,
                    distance / 150.0,
                    float(str(next_signal.get("state", "")).upper() in {"G", "GREEN"}),
                    float(_cloud_priority.get(tls_id, 0.0)),
                    distance / 150.0,
                    float(speed <= 0.1),
                    1.0,
                ],
                dtype=np.float32,
            )
        )
    result: dict[tuple[str, str], np.ndarray] = {}
    for key, rows in grouped.items():
        matrix = np.stack(rows)
        context = matrix.mean(0)
        context[5] = matrix[:, 5].min()
        context[7] = min(len(rows), 20) / 20.0
        result[key] = context.astype(np.float32)
    return result


def _critic_value(
    state: np.ndarray,
    *,
    role: str = "value",
    intersection_id: str = "",
    movement_id: str = "",
    movement_local_context: Sequence[float] = (),
    critic_context: np.ndarray | None = None,
) -> float:
    if _critic is None:
        return 0.0
    state_batch = np.asarray(state, dtype=np.float32).reshape(1, -1)
    with torch.no_grad():
        if _local_credit_enabled():
            context = (
                critic_context
                if critic_context is not None
                else critic_context_vector(
                    role=role,
                    intersection_id=intersection_id,
                    movement_id=movement_id,
                    movement_local_context=movement_local_context,
                    config=_config,
                )
            )
            normalized = float(
                _critic(
                    state_batch,
                    np.asarray(context, dtype=np.float32).reshape(1, -1),
                )
                .cpu()
                .item()
            )
        else:
            normalized = float(_critic(state_batch).cpu().item())
    return normalized * float(np.sqrt(_value_normalizer.var + 1e-8)) + _value_normalizer.mean


def _send_state_messages(payload: Mapping[str, Any], snapshot_id: str, sim_time: float) -> None:
    for vehicle_id, vehicle in (payload.get("vehicles", {}) or {}).items():
        if isinstance(vehicle, Mapping):
            _transport.send(TypedEnvelope("VehicleStateV1", f"{snapshot_id}:vehicle:{vehicle_id}", snapshot_id, str(vehicle_id), "cloud", sim_time, 5.0, "state", {"vehicle_id": str(vehicle_id), "motion": vehicle.get("motion", {}), "location": vehicle.get("location", {}), "next_signal": vehicle.get("next_signal", {}) or {}}))
    for tls_id, item in (payload.get("intersections", {}) or {}).items():
        if not isinstance(item, Mapping):
            continue
        _transport.send(TypedEnvelope("IntersectionSummaryV1", f"{snapshot_id}:intersection:{tls_id}", snapshot_id, str(tls_id), "cloud", sim_time, 5.0, "state", {"intersection_id": str(tls_id), "lanes": item.get("lanes", {}), "current_phase": int(item.get("current_phase", 0) or 0)}))
        static = _intersection_metadata.get(str(tls_id), {}) or {}
        _transport.send(TypedEnvelope("MAPV1", f"{snapshot_id}:map:{tls_id}", snapshot_id, str(tls_id), "vehicle", sim_time, 5.0, "state", {"intersection_id": str(tls_id), "phases": static.get("phases", {}), "lanes": static.get("lanes", {}), "connections": static.get("connections", ()), "direct_neighbors": static.get("direct_neighbors", ())}))


def _cloud_step(
    payload: Mapping[str, Any],
    snapshot_id: str,
    sim_time: float,
    state: np.ndarray,
    *,
    generate: bool,
) -> list[SMDPTransition]:
    global _cloud_priority
    ids = tuple(sorted(_phase_orders))
    if not ids or _cloud_actor is None:
        return []
    features = cloud_feature_matrix(payload, ids)
    expected_features = (len(ids), _config.cloud_feature_dim)
    if tuple(features.shape) != expected_features:
        raise ValueError(
            f"Cloud obs shape must be {expected_features}, got {tuple(features.shape)}"
        )
    topology = {
        tls_id: tuple(
            (_intersection_metadata.get(tls_id, {}) or {}).get(
                "direct_neighbors", ()
            )
        )
        for tls_id in ids
    }
    edges = directed_physical_edges(ids, topology)
    transitions: list[SMDPTransition] = []
    gain = _authority_gains["cloud"]
    if _phase_a_joint_action_override is not None:
        _cloud_priority = dict(
            _phase_a_joint_action_override["cloud_priority"]
        )
    elif os.environ.get("COV2X_CLOUD_MODE", "learned") == "off" or gain == 0.0:
        _cloud_priority = {tls_id: 0.0 for tls_id in ids}
    elif generate:
        with torch.no_grad():
            actions, logprobs, _ = _cloud_actor.sample(
                features,
                edges,
                deterministic=_mode != "train",
                authority_gain=gain,
            )
        expected_actions = (len(ids), 1)
        expected_logprobs = (len(ids),)
        if tuple(actions.shape) != expected_actions:
            raise ValueError(
                f"Cloud sampled action shape must be {expected_actions}, "
                f"got {tuple(actions.shape)}"
            )
        if tuple(logprobs.shape) != expected_logprobs:
            raise ValueError(
                f"Cloud sampled log_prob shape must be {expected_logprobs}, "
                f"got {tuple(logprobs.shape)}"
            )
        action_batch = actions.detach().cpu().numpy().copy()
        executed_action_batch = action_batch.copy()
        logprob_batch = logprobs.detach().cpu().numpy().copy()
        if not bool(
            np.isfinite(action_batch).all()
            and np.isfinite(executed_action_batch).all()
            and np.isfinite(logprob_batch).all()
        ):
            raise FloatingPointError("non-finite Cloud runtime sample")
        if not np.array_equal(action_batch, executed_action_batch):
            raise ValueError("Cloud sampled/stored/executed action mismatch")
        _cloud_priority = {
            tls_id: float(executed_action_batch[index, 0])
            for index, tls_id in enumerate(ids)
        }
        value = _critic_value(state, role="cloud")
        causal_parents = tuple(
            f"{snapshot_id}:intersection:{item}" for item in ids
        )
        for agent_slot, tls_id in enumerate(ids):
            stored_action = action_batch[agent_slot].copy()
            executed_action = executed_action_batch[agent_slot].copy()
            if stored_action.shape != (1,) or executed_action.shape != (1,):
                raise ValueError("Cloud transition action shape must be exactly (1,)")
            if not np.array_equal(stored_action, executed_action):
                raise ValueError("Cloud sampled/stored/executed action mismatch")
            transitions.append(
                SMDPTransition(
                    role="cloud",
                    snapshot_id=snapshot_id,
                    observation={
                        "features": features,
                        "edges": edges,
                        "authority_gain": gain,
                        "intersection_ids": ids,
                        "agent_slot": agent_slot,
                    },
                    action=stored_action,
                    old_logprob=float(logprob_batch[agent_slot]),
                    value=value,
                    reward=0.0,
                    duration_s=15.0,
                    policy_generation=_policy_generation,
                    causal_parents=causal_parents,
                    executed_action=executed_action,
                    entity_id=tls_id,
                    agent_slot=agent_slot,
                    decision_time_s=sim_time,
                    global_state=state.copy(),
                )
            )
    elif not _cloud_priority:
        raise RuntimeError("Cloud held priority is unavailable before generation")
    for tls_id, value in _cloud_priority.items():
        _base_policy["cloud_abs_max"] = max(
            float(_base_policy["cloud_abs_max"]), abs(float(value))
        )
        parent = (f"{snapshot_id}:intersection:{tls_id}",)
        common = {"intersection_id": tls_id, "priority": value}
        _transport.send(TypedEnvelope("RegionalPriorityV1", f"{snapshot_id}:cloud-road:{tls_id}", snapshot_id, "cloud", tls_id, sim_time, 5.0, "cloud", common, causal_parents=parent))
        _transport.send(TypedEnvelope("RegionalPriorityV1", f"{snapshot_id}:cloud-vehicle:{tls_id}", snapshot_id, "cloud", "vehicle", sim_time, 5.0, "cloud", common, causal_parents=parent))
    return transitions


def _send_spat(
    *,
    tls_id: str,
    snapshot_id: str,
    sim_time: float,
    current: int,
    item: Mapping[str, Any],
    sent_ids: set[str],
) -> tuple[str, ...]:
    cloud_id = f"{snapshot_id}:cloud-road:{tls_id}"
    parent = (
        cloud_id
        if cloud_id in sent_ids
        else f"{snapshot_id}:intersection:{tls_id}"
    )
    _transport.send(
        TypedEnvelope(
            "SPaTV2",
            f"{snapshot_id}:spat:{tls_id}",
            snapshot_id,
            tls_id,
            "vehicle",
            sim_time,
            5.0,
            "road",
            {
                "intersection_id": tls_id,
                "current_phase": current,
                "stage": str(item.get("stage", "GREEN")),
                "remaining_time_s": float(
                    item.get("remaining_time_s", item.get("stage_remaining", 0.0))
                    or 0.0
                ),
            },
            causal_parents=(parent,),
        )
    )
    return (parent,)


def _road_actions(payload: Mapping[str, Any], snapshot_id: str, sim_time: float, state: np.ndarray) -> tuple[dict[str, dict[str, int]], list[SMDPTransition]]:
    actions: dict[str, dict[str, int]] = {}; transitions = []
    if _road_actor is None or _pressure_oracle is None:
        return actions, transitions
    sent_ids = {event.message_id for event in _transport.events if event.event == "SEND"}
    if _phase_a_joint_action_override is not None:
        frozen = _phase_a_joint_action_override["signal_actions"]
        for tls_id in _phase_orders:
            item = (payload.get("intersections", {}) or {}).get(tls_id, {}) or {}
            current = int(item.get("current_phase", _phase_orders[tls_id][0]) or 0)
            actions[tls_id] = {
                "target_phase": int(frozen[tls_id]["target_phase"])
            }
            _send_spat(
                tls_id=tls_id,
                snapshot_id=snapshot_id,
                sim_time=sim_time,
                current=current,
                item=item,
                sent_ids=sent_ids,
            )
        return actions, transitions
    gain = _authority_gains["road"]
    baseline_actions = (
        _strong_mp_controller.compute_actions(dict(payload))
        if gain == 0.0 and _strong_mp_controller is not None
        else {}
    )
    for tls_id, order in _phase_orders.items():
        item = (payload.get("intersections", {}) or {}).get(tls_id, {}) or {}
        current = int(item.get("current_phase", order[0]) or order[0])
        if gain == 0.0:
            expected = baseline_actions.get(tls_id, current)
            target = current if expected is None else int(expected)
            actions[tls_id] = {"target_phase": target}
            _base_policy["road_decisions"] += 1
            _base_policy["road_mismatches"] += int(
                expected is not None and target != int(expected)
            )
            _send_spat(
                tls_id=tls_id,
                snapshot_id=snapshot_id,
                sim_time=sim_time,
                current=current,
                item=item,
                sent_ids=sent_ids,
            )
            continue
        stage = str(item.get("stage", "GREEN")).upper()
        pending_phase = item.get("pending_phase")
        stage_elapsed = float(item.get("stage_elapsed", 0.0) or 0.0)
        if stage not in {"G", "GREEN"} or pending_phase is not None or stage_elapsed + 1e-9 < _minimum_green:
            legal = (int(pending_phase) if pending_phase is not None else current,)
        else:
            legal = tuple(int(p) for p in (item.get("legal_phases") or order))
        q_map = normalized_mp_scores(
            phase_pressures_from_payload(
                payload, tls_id, order, oracle=_pressure_oracle
            ),
            legal_phases=legal,
        )
        phases = tuple(q_map)
        if not phases:
            actions[tls_id] = {"target_phase": current}; continue
        mask = np.asarray([phase in legal for phase in phases], dtype=bool)
        if not mask.any():
            actions[tls_id] = {"target_phase": current}; continue
        q_values = np.asarray([q_map[phase] for phase in phases], dtype=np.float32)
        road_obs = road_feature_vector(payload, tls_id)
        phase_features = phase_feature_matrix(phases)
        movement_features = movement_feature_matrix(
            {"intersections": {tls_id: _intersection_metadata.get(tls_id, {})}},
            tls_id,
            phases,
        )
        cloud = np.asarray([_cloud_priority.get(tls_id, 0.0)], dtype=np.float32)
        with torch.no_grad():
            action, logprob, _ = _road_actor.sample(
                q_values,
                road_obs,
                cloud,
                phase_features,
                movement_features,
                mask,
                deterministic=_mode != "train",
                authority_gain=gain,
            )
        action_index = int(action.cpu().item()); target = int(phases[action_index])
        actions[tls_id] = {"target_phase": target}
        parents = _send_spat(
            tls_id=tls_id,
            snapshot_id=snapshot_id,
            sim_time=sim_time,
            current=current,
            item=item,
            sent_ids=sent_ids,
        )
        transitions.append(SMDPTransition("road", snapshot_id, {"q": q_values, "road": road_obs, "cloud": cloud, "phase": phase_features, "movement": movement_features, "mask": mask, "authority_gain": gain}, action_index, float(logprob.cpu().item()), _critic_value(state, role="road", intersection_id=tls_id), 0.0, 5.0, policy_generation=_policy_generation, causal_parents=parents, executed_action=action_index, entity_id=tls_id, decision_time_s=sim_time, global_state=state.copy()))
    return actions, transitions


def _advice_key(
    tls_id: str, movement: str, vehicle_id: str, assignment_epoch: int
) -> tuple[str, str, str, int]:
    return str(tls_id), str(movement), str(vehicle_id), int(assignment_epoch)


def _selected_leaders(
    payload: Mapping[str, Any], sim_time: float
) -> list[tuple[str, Mapping[str, Any], str, str, int, float]]:
    groups: dict[tuple[str, str], list[tuple[str, Mapping[str, Any]]]] = {}
    for vehicle_id, vehicle in (payload.get("vehicles", {}) or {}).items():
        if not isinstance(vehicle, Mapping) or str(vehicle_id) in _blocked_vehicle_ids:
            continue
        next_signal = vehicle.get("next_signal", {}) or {}
        tls_id = str(next_signal.get("intersection_id") or next_signal.get("tls_id") or "")
        if tls_id not in _phase_orders:
            continue
        _corridor_stats["vehicle_candidates"] += 1
        if _movement_corridor is None:
            _corridor_stats["failure:resolver_uninitialized"] += 1
            continue
        resolution = _movement_corridor.resolve(tls_id, vehicle)
        if resolution.predecessor_depth is not None:
            depth = int(resolution.predecessor_depth)
            bucket = str(depth) if depth < 3 else "3+"
            _corridor_stats[f"predecessor_depth:{bucket}"] += 1
        if not resolution.resolved:
            reason = resolution.failure_reason or "movement_unresolved"
            _corridor_stats[f"failure:{reason}"] += 1
            continue
        _corridor_stats["resolved_vehicle_candidates"] += 1
        movement = str(resolution.resolved_movement_id)
        groups.setdefault((tls_id, movement), []).append(
            (str(vehicle_id), vehicle)
        )
    selected = []
    for (tls_id, movement), candidates in groups.items():
        candidates.sort(key=lambda item: float((item[1].get("next_signal", {}) or {}).get("distance_m", 1e9) or 1e9))
        lease = _leaders.get(tls_id, movement)
        ids = {vehicle_id for vehicle_id, _ in candidates}
        if lease and lease.vehicle_id not in ids:
            _speed_advice.pop(
                _advice_key(
                    tls_id, movement, lease.vehicle_id, lease.assignment_epoch
                ),
                None,
            )
            _leaders.release(tls_id, movement, "vehicle_departed")
            lease = None
        if lease and sim_time >= lease.expires_at:
            _speed_advice.pop(
                _advice_key(
                    tls_id, movement, lease.vehicle_id, lease.assignment_epoch
                ),
                None,
            )
            _leaders.release(tls_id, movement, "lease_completion")
            lease = None
        if lease is None:
            lease = _leaders.assign(tls_id, movement, candidates[0][0], now=sim_time, lease_s=15.0)
        for vehicle_id, vehicle in candidates:
            if vehicle_id == lease.vehicle_id:
                selected.append(
                    (vehicle_id, vehicle, tls_id, movement, lease.assignment_epoch, lease.issued_at)
                )
                break
    return selected


def _vehicle_uses_stochastic_action() -> bool:
    return _mode == "train" or os.environ.get(
        "COV2X_VEHICLE_STOCHASTIC_SCREEN", "0"
    ) == "1"


def _phase_history_audit_enabled() -> bool:
    return os.environ.get("COV2X_PHASE_HISTORY_AUDIT", "0") == "1"


def phase_history_audit_snapshot() -> dict[str, list[dict[str, Any]]]:
    """Return observer-only policy/proxy evidence without changing execution."""
    with _phase_history_audit_lock:
        return deepcopy(_phase_history_audit)


def set_phase_a_joint_action_override(
    *,
    cloud_priority: Mapping[str, float],
    signal_actions: Mapping[str, Mapping[str, int]],
) -> None:
    """Freeze one Road/Cloud decision for an opt-in Phase A replay step."""
    global _phase_a_joint_action_override
    expected = set(_phase_orders)
    if set(cloud_priority) != expected or set(signal_actions) != expected:
        raise ValueError("Phase A joint action must cover every intersection exactly")
    normalized_cloud: dict[str, float] = {}
    normalized_signals: dict[str, dict[str, int]] = {}
    for tls_id in sorted(expected):
        priority = float(cloud_priority[tls_id])
        if not np.isfinite(priority) or not -1.0 <= priority <= 1.0:
            raise ValueError("Phase A Cloud priority must be finite and in [-1, 1]")
        action = signal_actions[tls_id]
        if not isinstance(action, Mapping) or set(action) != {"target_phase"}:
            raise TypeError("Phase A Road tape action must contain only target_phase")
        target = action["target_phase"]
        if isinstance(target, bool) or not isinstance(target, int):
            raise TypeError("Phase A Road target_phase must be an integer")
        if int(target) not in _phase_orders[tls_id]:
            raise ValueError("Phase A Road target_phase is outside frozen phase order")
        normalized_cloud[tls_id] = priority
        normalized_signals[tls_id] = {"target_phase": int(target)}
    _phase_a_joint_action_override = {
        "cloud_priority": normalized_cloud,
        "signal_actions": normalized_signals,
    }


def clear_phase_a_joint_action_override() -> None:
    global _phase_a_joint_action_override
    _phase_a_joint_action_override = None


def _phase_a_evidence_snapshot(step_id: int) -> dict[str, Any]:
    snapshot_id = f"{_episode_id}:{int(step_id)}"

    def normalize_identifier(value: str) -> str:
        prefix = f"{_episode_id}:"
        return value[len(prefix):] if value.startswith(prefix) else value

    trace = [
        {
            "event": event.event,
            "message_id": normalize_identifier(event.message_id),
            "snapshot_id": normalize_identifier(event.snapshot_id),
            "logical_phase": event.logical_phase,
            "sim_time": float(event.sim_time),
            "causal_parents": [
                normalize_identifier(parent) for parent in event.causal_parents
            ],
        }
        for event in _transport.trace()
        if event.snapshot_id == snapshot_id
    ]
    return {
        "cloud_priority": dict(_cloud_priority),
        "transport_trace": trace,
        "ledger": _ledger.snapshot(),
        "safety": {
            "red_crossing_proxy": int(_safety["red_crossing_proxy"]),
            "dangerous_gap": int(_safety["dangerous_gap"]),
            "active_speed_advice_states": len(_speed_advice),
        },
    }


def _record_phase_history_policy_snapshot(
    *,
    snapshot_id: str,
    step_id: int,
    sim_time: float,
    signal_actions: Mapping[str, Mapping[str, int]],
) -> None:
    if not _phase_history_audit_enabled():
        return
    active_caps = {
        vehicle_id: {
            "intersection_id": tls_id,
            "movement_id": movement,
            "assignment_epoch": assignment_epoch,
        }
        for tls_id, movement, vehicle_id, assignment_epoch in _speed_advice
    }
    active_leases = [
        {
            "vehicle_id": lease.vehicle_id,
            "intersection_id": lease.intersection_id,
            "movement_id": lease.movement,
            "assignment_epoch": lease.assignment_epoch,
            "issued_at_s": lease.issued_at,
            "expires_at_s": lease.expires_at,
            "active_cap": lease.vehicle_id in active_caps,
        }
        for lease in _leaders.active()
    ]
    row = {
        "simulation_time": float(sim_time),
        "step_id": int(step_id),
        "spat_source_epoch": str(snapshot_id),
        "committed_signal_actions": {
            str(tls_id): int(action["target_phase"])
            for tls_id, action in signal_actions.items()
        },
        "active_leases": active_leases,
        "active_caps": active_caps,
    }
    with _phase_history_audit_lock:
        _phase_history_audit["policy_snapshots"].append(row)

def _vehicle_actions(payload: Mapping[str, Any], signal_actions: Mapping[str, Mapping[str, int]], snapshot_id: str, sim_time: float, state: np.ndarray, movement_context: Mapping[str, Sequence[float]]) -> tuple[dict[str, dict[str, float]], list[SMDPTransition], dict[str, Any]]:
    global _commanded, _speed_advice
    commands: dict[str, dict[str, float]] = {}; transitions = []
    local_contexts = _movement_local_context(payload) if _local_credit_enabled() else {}
    diagnostics = {
        "opportunities": 0,
        "eligible_action_opportunities": 0,
        "active_cap": 0,
        "native_release": 0,
        "fail_closed": 0,
    }
    phase_a_rows: list[dict[str, Any]] | None = None
    if os.environ.get("COV2X_PHASE_A_DIAGNOSTICS") == "1":
        phase_a_rows = []
        diagnostics["phase_a_opportunities"] = phase_a_rows

    def fail_closed(reason: str) -> None:
        diagnostics["fail_closed"] += 1
        _authority["fail_closed"] += 1
        _fail_closed_reasons[str(reason)] += 1

    gain = _authority_gains["vehicle"]
    inactive = (
        _vehicle_actor is None
        or os.environ.get("COV2X_VEHICLE_MODE", "learned") == "off"
    )
    if inactive or gain == 0.0:
        released = len(_speed_advice)
        _speed_advice.clear()
        if gain == 0.0:
            _authority["authority_zero_release"] += released
        return commands, transitions, diagnostics
    for vehicle_id, vehicle, tls_id, movement, assignment_epoch, issued_at in _selected_leaders(payload, sim_time):
        advice_key = _advice_key(tls_id, movement, vehicle_id, assignment_epoch)
        diagnostics["opportunities"] += 1
        _authority["opportunities"] += 1
        if tls_id not in signal_actions:
            _leaders.release(tls_id, movement, "hard_invalidation")
            _speed_advice.pop(advice_key, None)
            fail_closed("road_commit_missing")
            continue
        if vehicle.get("control_authority", True) is False:
            _leaders.release(tls_id, movement, "control_authority_lost")
            _speed_advice.pop(advice_key, None)
            _blocked_vehicle_ids.add(vehicle_id)
            fail_closed("control_authority_lost")
            continue
        motion = vehicle.get("motion", {}) or {}
        next_signal = vehicle.get("next_signal", {}) or {}
        limits = vehicle_limits(vehicle, _vehicle_types)
        speed = float(motion.get("speed_mps", 0.0) or 0.0)
        raw_allowed = motion.get("allowed_speed_mps")
        try:
            allowed = float(raw_allowed)
        except (TypeError, ValueError):
            allowed = float("nan")
        if (
            not np.isfinite(allowed)
            or allowed <= 0.0
            or not np.isfinite(limits.max_speed_mps)
            or limits.max_speed_mps <= 0.0
        ):
            _leaders.release(tls_id, movement, "hard_invalidation")
            _speed_advice.pop(advice_key, None)
            fail_closed("speed_ceiling_missing_or_invalid")
            continue
        base_speed = reference_base_speed(allowed, limits.max_speed_mps)
        raw_delta_v_max_mps = (
            base_speed * DELTA_V_MAX_SPEED_CEILING_FRACTION
        )
        delta_v_max_mps = (
            raw_delta_v_max_mps * gain
            if _temporary_speed_cap_enabled()
            else raw_delta_v_max_mps
        )
        signal_state = str(next_signal.get("state", ""))
        try:
            distance_m = float(next_signal.get("distance_m"))
        except (TypeError, ValueError):
            distance_m = float("nan")
        if not signal_state or not np.isfinite(distance_m) or distance_m < 0.0:
            _leaders.release(tls_id, movement, "hard_invalidation")
            _speed_advice.pop(advice_key, None)
            fail_closed("spat_context_invalid")
            continue
        green = signal_state.upper() in {"G", "GREEN"}
        leader_gap = vehicle.get("leader_gap_m")
        minimum_gap = limits.min_gap_m
        if leader_gap is not None and float(leader_gap) + 1e-9 < minimum_gap:
            _leaders.release(tls_id, movement, "safety_empty")
            _speed_advice.pop(advice_key, None)
            fail_closed("dangerous_leader_gap")
            continue
        diagnostics["eligible_action_opportunities"] += 1
        _authority["eligible_action_opportunities"] += 1
        advice_state = _speed_advice.get(advice_key)
        previous_advice = None if advice_state is None else advice_state["cap_mps"]
        effective_advice = (
            base_speed
            if previous_advice is None
            else min(previous_advice, base_speed)
        )
        features = vehicle_feature_vector(
            speed_mps=speed,
            accel_mps2=float(motion.get("acceleration_mps2", 0.0) or 0.0),
            allowed_speed_mps=allowed,
            base_speed_mps=base_speed,
            advice_speed_mps=effective_advice,
            advice_active=previous_advice is not None,
            distance_m=distance_m,
            green=green,
            signal_remaining_s=float(next_signal.get("remaining_time_s", 0.0) or 0.0),
            cloud_priority=_cloud_priority.get(tls_id, 0.0),
            leader_gap_m=None if leader_gap is None else float(leader_gap),
            relative_speed_mps=vehicle.get(
                "leader_relative_speed_mps", vehicle.get("relative_speed_mps")
            ),
            previous_delta_v_mps=float((advice_state or {}).get("previous_delta_mps", 0.0)),
            previous_realized_speed_mps=(advice_state or {}).get("previous_realized_speed_mps"),
            reference_tracking_error_mps=float((advice_state or {}).get("tracking_error_mps", 0.0)),
            native_limited=bool((advice_state or {}).get("native_limited", 0.0)),
            assignment_age_s=max(0.0, sim_time - issued_at),
            lease_age_s=max(0.0, sim_time - issued_at),
            message_age_s=float(next_signal.get("message_age_s", 0.0) or 0.0),
            movement_code=int.from_bytes(
                sha256(movement.encode("utf-8")).digest()[:2], "big"
            ) / 65535.0,
            pooled_context=movement_context.get(tls_id, ()),
        )
        movement_local = local_contexts.get(
            (tls_id, movement), np.zeros(8, dtype=np.float32)
        )
        value_context = critic_context_vector(
            role="vehicle",
            intersection_id=tls_id,
            movement_id=movement,
            movement_local_context=movement_local,
            config=_config,
        )
        with torch.no_grad():
            action, logprob, _ = _vehicle_actor.sample(
                features.reshape(1, -1),
                deterministic=not _vehicle_uses_stochastic_action(),
            )
        latent_u = float(action.cpu().item())
        if _temporary_speed_cap_enabled():
            advice = apply_temporary_base_relative_speed_advice(
                previous_advice_mps=previous_advice,
                base_speed_mps=base_speed,
                latent_u=latent_u,
                delta_v_max_mps=delta_v_max_mps,
            )
        else:
            advice = apply_incremental_speed_advice(
                previous_advice_mps=previous_advice,
                base_speed_mps=base_speed,
                latent_u=latent_u,
                delta_v_max_mps=delta_v_max_mps,
                authority_gain=gain,
                native_release_tolerance_mps=NATIVE_RELEASE_TOLERANCE_MPS,
            )
        _authority["advice_transitions"] += 1
        diagnostics[advice.transition_kind] += 1
        _authority[advice.transition_kind] += 1
        if _vehicle_uses_stochastic_action() and not advice.release_native:
            _authority["stochastic_active_cap"] += 1
        if advice.release_native:
            _speed_advice.pop(advice_key, None)
        else:
            assert advice.target_speed_mps is not None
            target_speed = float(advice.target_speed_mps)
            next_advice_state = dict(advice_state or {})
            next_advice_state.update(
                cap_mps=target_speed,
                previous_delta_mps=float(advice.effective_delta_mps),
                previous_realized_speed_mps=float(
                    next_advice_state.get("previous_realized_speed_mps", speed)
                ),
            )
            _speed_advice[advice_key] = next_advice_state
            commands[vehicle_id] = {"target_speed_mps": target_speed}
            classification = classify_constraint(vehicle, limits)
            signal_id = next_signal.get("intersection_id", next_signal.get("tls_id"))
            _commanded[vehicle_id] = {
                "advice_key": advice_key,
                "assignment_epoch": assignment_epoch,
                "delta_v_max_mps": delta_v_max_mps,
                "raw_delta_v_max_mps": raw_delta_v_max_mps,
                "target_speed_mps": target_speed,
                "latent_u": latent_u,
                "requested_delta_mps": advice.requested_delta_mps,
                "effective_delta_mps": advice.effective_delta_mps,
                "base_projection_delta_mps": advice.base_projection_delta_mps,
                "speed_before_mps": speed,
                "issued_at_s": sim_time,
                "tls_id": tls_id,
                "movement": movement,
                "constraint_state": classification.state.value,
                "minimum_gap_m": minimum_gap,
                "next_signal_id": "" if signal_id is None else str(signal_id),
                "next_signal_state": str(next_signal.get("state", "")),
                "next_signal_distance_m": float(next_signal.get("distance_m", 0.0) or 0.0),
            }
            _authority["commands_submitted"] += 1
            _base_policy["vehicle_commands"] += 1
        if phase_a_rows is not None:
            phase_a_rows.append(
                {
                    "vehicle_id": str(vehicle_id),
                    "intersection_id": str(tls_id),
                    "movement_id": str(movement),
                    "assignment_epoch": int(assignment_epoch),
                    "issued_at_s": float(issued_at),
                    "previous_advice_mps": (
                        None
                        if previous_advice is None
                        else float(previous_advice)
                    ),
                    "base_speed_mps": float(base_speed),
                    "delta_v_max_mps": float(delta_v_max_mps),
                    "speed_mps": float(speed),
                    "allowed_speed_mps": float(allowed),
                    "signal_state": signal_state,
                    "signal_distance_m": float(distance_m),
                    "leader_gap_m": (
                        None if leader_gap is None else float(leader_gap)
                    ),
                    "minimum_gap_m": float(minimum_gap),
                    "latent_u": float(latent_u),
                    "transition_kind": str(advice.transition_kind),
                }
            )
        transitions.append(
            SMDPTransition(
                "vehicle",
                snapshot_id,
                {
                    "features": features,
                    "authority_gain": gain,
                    "vehicle_id": str(vehicle_id),
                    "intersection_id": tls_id,
                    "movement_id": movement,
                    "movement_local_context": movement_local.copy(),
                    "critic_context": value_context.copy(),
                    "deterministic_actor_mean_u": (
                        float(latent_u) if not _vehicle_uses_stochastic_action() else None
                    ),
                    "sampled_u": float(latent_u),
                    "delta_v_max_mps": delta_v_max_mps,
                    "raw_delta_v_max_mps": raw_delta_v_max_mps,
                    "base_speed_mps": base_speed,
                    "previous_advice_mps": previous_advice,
                    "projected_advice_mps": advice.projected_advice_mps,
                    "v_adv_mps": (
                        float(base_speed)
                        if advice.release_native
                        else float(advice.target_speed_mps)
                    ),
                    "cap_gap_mps": float(
                        base_speed
                        - (
                            base_speed
                            if advice.release_native
                            else float(advice.target_speed_mps)
                        )
                    ),
                    "active_cap_before": previous_advice is not None,
                    "active_cap_after": not advice.release_native,
                    "actual_command": (
                        None
                        if advice.release_native
                        else {"target_speed_mps": float(advice.target_speed_mps)}
                    ),
                    "speed_before_mps": float(speed),
                    "acceleration_before_mps2": float(
                        motion.get("acceleration_mps2", 0.0) or 0.0
                    ),
                    "leader_gap_m": (
                        None if leader_gap is None else float(leader_gap)
                    ),
                    "signal_distance_m": float(distance_m),
                    "assignment_epoch": assignment_epoch,
                    "assignment_issued_at_s": float(issued_at),
                    "native_release_tolerance_mps": (
                        None
                        if _temporary_speed_cap_enabled()
                        else NATIVE_RELEASE_TOLERANCE_MPS
                    ),
                    "requested_delta_mps": advice.requested_delta_mps,
                    "effective_delta_mps": advice.effective_delta_mps,
                    "base_projection_delta_mps": advice.base_projection_delta_mps,
                    "transition_kind": advice.transition_kind,
                },
                latent_u,
                float(logprob.cpu().item()),
                _critic_value(
                    state,
                    role="vehicle",
                    intersection_id=tls_id,
                    movement_id=movement,
                    movement_local_context=movement_local,
                    critic_context=value_context,
                ),
                0.0,
                5.0,
                policy_generation=_policy_generation,
                causal_parents=(
                    f"{snapshot_id}:cloud-vehicle:{tls_id}",
                    f"{snapshot_id}:spat:{tls_id}",
                    f"{snapshot_id}:map:{tls_id}",
                ),
                executed_action=latent_u,
                entity_id=(
                    f"{tls_id}:{movement}"
                    if _local_credit_enabled()
                    else f"{tls_id}:{movement}:{vehicle_id}:{assignment_epoch}"
                ),
                decision_time_s=sim_time,
                global_state=state.copy(),
            )
        )
    return commands, transitions, diagnostics


def _audit_previous_actions(payload: Mapping[str, Any]) -> None:
    global _vehicle_execution_audit, _speed_advice, _blocked_vehicle_ids
    _blocked_vehicle_ids.clear()
    previous = (payload.get("previous_action_results") or {}).get("vehicles", {}) or {}
    current_vehicles = payload.get("vehicles", {}) or {}
    now_s = float(payload.get("simulation_time", 0.0) or 0.0)
    for vehicle_id, result in previous.items():
        command = _commanded.pop(str(vehicle_id), None)
        if command is None or not isinstance(result, Mapping):
            continue
        status = result.get("speed_status")
        advice_key = tuple(command["advice_key"])
        if status == "vehicle_arrived":
            _authority["vehicle_arrived"] += 1
            _speed_advice.pop(advice_key, None)
            _leaders.release(
                str(command["tls_id"]),
                str(command["movement"]),
                "vehicle_departed",
            )
            _blocked_vehicle_ids.add(str(vehicle_id))
            _vehicle_execution_audit.append({
                "vehicle_id": str(vehicle_id),
                "assignment_epoch": int(command["assignment_epoch"]),
                "decision_time_s": float(command["issued_at_s"]),
                "latent_u": float(command["latent_u"]),
                "target_speed_mps": float(command["target_speed_mps"]),
                "actual_speed_mps": None,
                "target_speed_error_mps": None,
                "speed_status": status,
                "reason": "vehicle_arrived_before_audit",
                "observable_native_limit_proxy": False,
                "red_crossing_proxy": False,
                "dangerous_gap": False,
            })
            continue
        _authority["commands_audited"] += 1
        actual = result.get("actual_speed_mps")
        target = float(command["target_speed_mps"])
        accepted = status == "applied"
        if accepted:
            _authority["commands_accepted"] += 1
            reason = "command_accepted"
        else:
            _authority["commands_rejected"] += 1
            reason = "command_rejected"
            _leaders.release(
                str(command["tls_id"]),
                str(command["movement"]),
                "control_authority_lost",
            )
            _speed_advice.pop(advice_key, None)
            _blocked_vehicle_ids.add(str(vehicle_id))
        error = None if actual is None else float(actual) - target
        tracking_miss = bool(
            accepted and error is not None and abs(error) > 0.5
        )
        if tracking_miss:
            _authority["tracking_miss"] += 1
        current = current_vehicles.get(str(vehicle_id), {}) or {}
        limits = vehicle_limits(current, _vehicle_types) if current else None
        current_classification = (
            classify_constraint(current, limits).state
            if current and limits is not None
            else ConstraintState.UNCLASSIFIED
        )
        constrained = (
            str(command["constraint_state"]) != ConstraintState.FREE_FLOW.value
            or current_classification
            in {ConstraintState.LEADER_LIMITED, ConstraintState.SIGNAL_LIMITED}
        )
        observable_proxy = bool(tracking_miss and constrained and float(error) < -0.5)
        if observable_proxy:
            _authority["observable_native_limit_proxy"] += 1
        if accepted:
            current_advice_state = _speed_advice.get(advice_key)
            if current_advice_state is not None:
                if actual is not None:
                    current_advice_state["previous_realized_speed_mps"] = float(actual)
                    current_advice_state["tracking_error_mps"] = target - float(actual)
                current_advice_state["native_limited"] = float(
                    observable_proxy
                )
        current_signal = current.get("next_signal", {}) if current else {}
        current_signal_id = (
            current_signal.get("intersection_id", current_signal.get("tls_id"))
            if isinstance(current_signal, Mapping)
            else None
        )
        restrictive_before = str(command["next_signal_state"]).upper() not in {
            "G",
            "GREEN",
        }
        elapsed = max(0.0, now_s - float(command["issued_at_s"]))
        crossing_distance = max(
            1.0, float(command["speed_before_mps"]) * elapsed + 0.5
        )
        red_proxy = bool(
            restrictive_before
            and float(command["next_signal_distance_m"]) <= crossing_distance
            and str(current_signal_id or "") != str(command["next_signal_id"])
            and actual is not None
            and float(actual) > 0.1
        )
        if red_proxy and _phase_history_audit_enabled():
            with _phase_history_audit_lock:
                _phase_history_audit["proxy_events"].append({
                    "vehicle_id": str(vehicle_id),
                    "assignment_epoch": int(command["assignment_epoch"]),
                    "intersection_id": str(command["tls_id"]),
                    "movement_id": str(command["movement"]),
                    "issued_at_s": float(command["issued_at_s"]),
                    "audit_time_s": now_s,
                    "signal_state_at_issue": str(
                        command["next_signal_state"]
                    ),
                    "signal_distance_at_issue_m": float(
                        command["next_signal_distance_m"]
                    ),
                    "signal_id_at_issue": str(command["next_signal_id"]),
                    "signal_id_at_audit": str(current_signal_id or ""),
                    "actual_speed_at_audit_mps": float(actual),
                })
        current_gap = current.get("leader_gap_m") if current else None
        dangerous_gap = bool(
            current_gap is not None
            and float(current_gap) + 1e-9 < float(command["minimum_gap_m"])
        )
        _safety["red_crossing_proxy"] += int(red_proxy)
        _safety["dangerous_gap"] += int(dangerous_gap)
        _vehicle_execution_audit.append({
            "vehicle_id": str(vehicle_id),
            "assignment_epoch": int(command["assignment_epoch"]),
            "decision_time_s": float(command["issued_at_s"]),
            "latent_u": float(command["latent_u"]),
            "requested_delta_mps": float(command["requested_delta_mps"]),
            "effective_delta_mps": float(command["effective_delta_mps"]),
            "base_projection_delta_mps": float(command["base_projection_delta_mps"]),
            "target_speed_mps": target,
            "actual_speed_mps": None if actual is None else float(actual),
            "target_speed_error_mps": error,
            "speed_status": status,
            "command_accepted": accepted,
            "tracking_miss": tracking_miss,
            "observable_native_limit_proxy": observable_proxy,
            "native_safety_intervention": observable_proxy,
            "red_crossing_proxy": red_proxy,
            "dangerous_gap": dangerous_gap,
            "reason": reason,
        })


def _flush_unaudited_commands() -> None:
    for vehicle_id, command in list(_commanded.items()):
        _authority["terminal_unaudited"] += 1
        _vehicle_execution_audit.append({
            "vehicle_id": str(vehicle_id),
            "assignment_epoch": int(command["assignment_epoch"]),
            "decision_time_s": float(command["issued_at_s"]),
            "latent_u": float(command["latent_u"]),
            "target_speed_mps": float(command["target_speed_mps"]),
            "actual_speed_mps": None,
            "target_speed_error_mps": None,
            "speed_status": None,
            "command_accepted": False,
            "tracking_miss": False,
            "observable_native_limit_proxy": False,
            "red_crossing_proxy": False,
            "dangerous_gap": False,
            "reason": "episode_ended_before_audit",
        })
    _commanded.clear()


def _execution_reason_counts() -> dict[str, int]:
    return dict(Counter(str(item["reason"]) for item in _vehicle_execution_audit))


def _consume_phase(snapshot_id: str, phase: str, sim_time: float) -> None:
    for envelope in _transport.deliver(snapshot_id, phase, sim_time):
        _transport.consume(envelope, sim_time=sim_time)


def _register(transitions: Sequence[SMDPTransition], sim_time: float) -> None:
    if _rollout is None:
        return
    for transition in transitions:
        key = f"{transition.role}:{transition.entity_id}"
        previous = _pending.pop(key, None)
        if previous is not None:
            previous.close(next_value=transition.value, reward=previous.reward, duration_s=max(sim_time - previous.decision_time_s, _decision_interval))
            _rollout.transitions.append(previous)
        _pending[key] = transition


def step(payload: Mapping[str, Any]) -> dict[str, Any]:
    global _last_total
    if not _initialized:
        raise RuntimeError("CoV2X MVP runtime is not initialized")
    step_id = int(payload.get("step_id", 0) or 0); snapshot_id = f"{_episode_id}:{step_id}"
    sim_time = float(payload.get("simulation_time", 0.0) or 0.0)
    _audit_previous_actions(payload); _send_state_messages(payload, snapshot_id, sim_time)
    _consume_phase(snapshot_id, "state", sim_time)
    current_total = _ledger.observe(_active_time_loss(payload), sim_time=sim_time)
    reward = network_time_loss_reward(
        _last_total, current_total, max(_duration_s, 1.0)
    ).reward
    _last_total = current_total
    local_credits: dict[tuple[str, str], float] = {}
    if _local_credit_enabled():
        if _movement_corridor is None:
            raise RuntimeError("movement-local credit resolver is unavailable")
        local_credits = _movement_local_credit.observe(payload, _movement_corridor)
    for pending in _pending.values():
        if _local_credit_enabled() and pending.role == "vehicle":
            observation = pending.observation or {}
            key = (
                str(observation.get("intersection_id", "")),
                str(observation.get("movement_id", "")),
            )
            pending.reward += float(local_credits.get(key, 0.0))
        else:
            pending.reward += float(reward)
    movement_context = _movement_context(payload); state_before = _global_state(payload, movement_context)
    transitions: list[SMDPTransition] = []
    generate_cloud = int(round(sim_time / max(_decision_interval, 1e-6))) % 3 == 0
    transitions.extend(
        _cloud_step(
            payload, snapshot_id, sim_time, state_before, generate=generate_cloud
        )
    )
    _consume_phase(snapshot_id, "cloud", sim_time)
    movement_context = _movement_context(payload); state = _global_state(payload, movement_context)
    if _mode == "train" and _is_base_policy():
        transitions.append(
            SMDPTransition(
                "value",
                snapshot_id,
                {"global_state": state.copy()},
                0.0,
                0.0,
                _critic_value(state, role="value"),
                0.0,
                _decision_interval,
                policy_generation=_policy_generation,
                causal_parents=tuple(
                    f"{snapshot_id}:intersection:{tls_id}"
                    for tls_id in sorted(_phase_orders)
                ),
                executed_action=0.0,
                entity_id="central",
                decision_time_s=sim_time,
                global_state=state.copy(),
            )
        )
    signal_actions, road_transitions = _road_actions(payload, snapshot_id, sim_time, state); transitions.extend(road_transitions)
    _consume_phase(snapshot_id, "road", sim_time)
    vehicle_actions, vehicle_transitions, diagnostics = _vehicle_actions(payload, signal_actions, snapshot_id, sim_time, state, movement_context); transitions.extend(vehicle_transitions)
    _record_phase_history_policy_snapshot(
        snapshot_id=snapshot_id,
        step_id=step_id,
        sim_time=sim_time,
        signal_actions=signal_actions,
    )
    _consume_phase(snapshot_id, "vehicle", sim_time)
    _register(transitions, sim_time)
    _consume_phase(snapshot_id, "commit", sim_time)
    response_diagnostics: dict[str, Any] = {
        "ledger_total": current_total,
        **diagnostics,
        "authority": dict(_authority),
    }
    if os.environ.get("COV2X_PHASE_A_DIAGNOSTICS") == "1":
        response_diagnostics["phase_a_evidence"] = _phase_a_evidence_snapshot(
            step_id
        )
    return {
        "protocol_version": "2.0",
        "candidate_id": _runtime_candidate_id(),
        "episode_id": _episode_id,
        "step_id": step_id,
        "actions": {
            "signals": signal_actions,
            "vehicles": vehicle_actions,
        },
        "diagnostics": response_diagnostics,
        "v2x": _transport.event_batch(snapshot_id=snapshot_id),
    }


def finish(payload: Mapping[str, Any]) -> None:
    global _initialized, _rollout, _pending, _cloud_priority
    global _phase_a_joint_action_override
    _audit_previous_actions(payload)
    _flush_unaudited_commands()
    sim_time = float(payload.get("simulation_time", _duration_s) or _duration_s)
    current_total = _ledger.observe(_active_time_loss(payload), arrived_ids=set(_ledger.active) - set(_active_time_loss(payload)), sim_time=sim_time)
    terminal_reward = network_time_loss_reward(
        _last_total, current_total, max(_duration_s, 1.0)
    ).reward
    terminal_local_credits: dict[tuple[str, str], float] = {}
    if _local_credit_enabled():
        if _movement_corridor is None:
            raise RuntimeError("movement-local credit resolver is unavailable")
        terminal_local_credits = _movement_local_credit.observe(
            payload, _movement_corridor
        )
    for transition in _pending.values():
        if _local_credit_enabled() and transition.role == "vehicle":
            observation = transition.observation or {}
            key = (
                str(observation.get("intersection_id", "")),
                str(observation.get("movement_id", "")),
            )
            transition.reward += float(terminal_local_credits.get(key, 0.0))
        else:
            transition.reward += terminal_reward
        transition.close(next_value=None, reward=transition.reward, done=True, duration_s=max(sim_time - transition.decision_time_s, _decision_interval))
        if _rollout is not None:
            _rollout.transitions.append(transition)
    if _rollout is not None:
        assert_closed(_rollout.transitions)
        advantages, returns = role_entity_gae(_rollout.transitions, gamma=_config.gamma, lam=_config.lam)
        for index, transition in enumerate(_rollout.transitions):
            transition.advantage = float(advantages[index]); transition.return_ = float(returns[index])
        trace_events = _transport.trace()
        vehicle_transitions = [
            item for item in _rollout.transitions if item.role == "vehicle"
        ]
        sampled_equals_executed = all(
            np.allclose(item.action, item.executed_action)
            for item in _rollout.transitions
        )
        causal_cloud_road = any(
            event.message_id.find(":spat:") >= 0
            and any(":cloud-road:" in parent for parent in event.causal_parents)
            for event in trace_events
            if event.event == "SEND"
        )
        causal_road_vehicle = bool(vehicle_transitions) and all(
            any(":spat:" in parent for parent in item.causal_parents)
            for item in vehicle_transitions
        )
        causal_cloud_vehicle = bool(vehicle_transitions) and all(
            any(":cloud-vehicle:" in parent for parent in item.causal_parents)
            for item in vehicle_transitions
        )
        ttl_expired = sum(
            event.event == "TTL_EXPIRED" for event in trace_events
        )
        opportunities = int(_authority["opportunities"])
        advice_transitions = int(_authority["advice_transitions"])
        commands_audited = int(_authority["commands_audited"])
        tracking_misses = int(_authority["tracking_miss"])
        tracking_errors = [
            abs(float(item["target_speed_error_mps"]))
            for item in _vehicle_execution_audit
            if item.get("target_speed_error_mps") is not None
        ]
        advice_coverage = (
            advice_transitions / opportunities if opportunities else 0.0
        )
        corridor_candidates = int(_corridor_stats["vehicle_candidates"])
        corridor_resolved = int(
            _corridor_stats["resolved_vehicle_candidates"]
        )
        corridor_coverage = (
            corridor_resolved / corridor_candidates
            if corridor_candidates
            else 0.0
        )
        eligible_opportunities = int(
            _authority["eligible_action_opportunities"]
        )
        stochastic_active_rate = (
            int(_authority["stochastic_active_cap"]) / eligible_opportunities
            if eligible_opportunities else None
        )
        command_acceptance = (
            int(_authority["commands_accepted"]) / commands_audited
            if commands_audited
            else None
        )
        observable_proxy_rate = (
            int(_authority["observable_native_limit_proxy"]) / tracking_misses
            if tracking_misses
            else None
        )
        native_safety_intervention_rate = (
            int(_authority["observable_native_limit_proxy"])
            / int(_authority["commands_accepted"])
            if int(_authority["commands_accepted"])
            else None
        )
        _rollout.metrics.update({
            "candidate_id": _runtime_candidate_id(),
            "duration_seconds": int(round(sim_time)),
            "ppo_updates": 0,
            "ledger": _ledger.snapshot(),
            "movement_local_credit": (
                _movement_local_credit.snapshot()
                if _local_credit_enabled()
                else None
            ),
            "transport_events": len(trace_events),
            "ttl_expired": ttl_expired,
            "expired_message_consumes": 0,
            "unknown_message_consumes": 0,
            "schema_ok": True,
            "ttl_ok": ttl_expired == 0,
            "logical_phase_ok": True,
            "causal_cloud_road": causal_cloud_road,
            "causal_road_vehicle": causal_road_vehicle,
            "causal_cloud_vehicle": causal_cloud_vehicle,
            "role_steps": {role: sum(item.role == role for item in _rollout.transitions) for role in ("cloud", "road", "vehicle")},
            "value_steps": sum(item.role == "value" for item in _rollout.transitions),
            "vehicle_action_semantics": _runtime_action_semantics(),
            "advice_opportunity_count": opportunities,
            "eligible_action_opportunities": eligible_opportunities,
            "stochastic_active_cap_rate": stochastic_active_rate,
            "speed_ceiling_source": "vehicle.motion.allowed_speed_mps",
            "route_intent_contract": {
                "telemetry": "Vehicle telemetry / BSM-like state",
                "route_intent": "simulation_side_route_intent",
            },
            "movement_corridor": {
                "vehicle_candidates": corridor_candidates,
                "resolved_vehicle_candidates": corridor_resolved,
                "coverage": corridor_coverage,
                "ambiguous_movement": int(
                    _corridor_stats["failure:ambiguous_movement"]
                ),
                "failures": {
                    key.removeprefix("failure:"): int(value)
                    for key, value in _corridor_stats.items()
                    if key.startswith("failure:")
                },
                "predecessor_depth": {
                    key.removeprefix("predecessor_depth:"): int(value)
                    for key, value in _corridor_stats.items()
                    if key.startswith("predecessor_depth:")
                },
            },
            "leader_pool": _leaders.diagnostics(),
            "fail_closed_reasons": dict(_fail_closed_reasons),
            "active_speed_advice_states": len(_speed_advice),
            "advice_opportunity_nonzero": opportunities > 0,
            "policy_advice_coverage": advice_coverage,
            "advice_command_acceptance": command_acceptance,
            "advice_command_acceptance_denominator": commands_audited,
            "advice_transition_counts": {
                "active_cap": int(_authority["active_cap"]),
                "native_release": int(_authority["native_release"]),
                "fail_closed": int(_authority["fail_closed"]),
                "authority_zero_release": int(_authority["authority_zero_release"]),
            },
            "observable_native_limit_proxy_rate": observable_proxy_rate,
            "native_safety_intervention_rate": native_safety_intervention_rate,
            "reference_tracking_mae_mps": (
                float(np.mean(tracking_errors)) if tracking_errors else None
            ),
            "vehicle_authority": dict(_authority),
            "vehicle_advice_reasons": _execution_reason_counts(),
            "vehicle_advice_audit_count": len(_vehicle_execution_audit),
            "red_crossing_proxy_events": _safety["red_crossing_proxy"],
            "dangerous_gap_events": _safety["dangerous_gap"],
            "illegal_actions": 0,
            "phase_order_violations": 0,
            "spat_map_semantic_violations": 0,
            "causal_violations": 0,
            "sampled_equals_executed": sampled_equals_executed,
            "action_selection": {
                "cloud": "base_policy" if _authority_gains["cloud"] == 0.0 else ("stochastic_sample" if _mode == "train" else "mean"),
                "road": "strong_mp" if _authority_gains["road"] == 0.0 else ("stochastic_sample" if _mode == "train" else "argmax"),
                "vehicle": "native" if _authority_gains["vehicle"] == 0.0 else ("stochastic_sample" if _vehicle_uses_stochastic_action() else "mean"),
            },
            "authority_gains": dict(_authority_gains),
            "base_policy_recovery": {
                **dict(_base_policy),
                "active": _is_base_policy(),
                "strict": (
                    _base_policy["road_mismatches"] == 0
                    and float(_base_policy["cloud_abs_max"]) == 0.0
                    and _base_policy["vehicle_commands"] == 0
                )
                if _is_base_policy()
                else None,
            },
            "advantages_finite": bool(np.isfinite(advantages).all() and np.isfinite(returns).all()),
            "ppo_numerically_healthy": bool(np.isfinite(advantages).all() and np.isfinite(returns).all()),
        })
        if os.environ.get("COV2X_TRACE_EXECUTION") == "1":
            _rollout.metrics["vehicle_advice_audit"] = list(
                _vehicle_execution_audit
            )
        _collected_rollouts.append(_rollout)
    _pending, _rollout, _initialized, _cloud_priority = {}, None, False, {}
    _phase_a_joint_action_override = None


def take_collected_rollout() -> MVPRollout | None:
    return _collected_rollouts.pop(0) if _collected_rollouts else None


def _normalized_advantages(transitions: Sequence[SMDPTransition]) -> torch.Tensor:
    values = torch.as_tensor([float(item.advantage) for item in transitions], dtype=torch.float32)
    if values.numel() > 1:
        values = (values - values.mean()) / (values.std(unbiased=False) + 1e-8)
    return values


def _cloud_actor_logprob_entropy(
    transitions: Sequence[SMDPTransition],
) -> tuple[torch.Tensor, torch.Tensor]:
    if _cloud_actor is None:
        raise RuntimeError("Cloud actor is unavailable")
    grouped: dict[tuple[str, float, int], list[tuple[int, SMDPTransition]]] = {}
    for index, transition in enumerate(transitions):
        key = (
            str(transition.snapshot_id),
            float(transition.decision_time_s),
            int(transition.policy_generation),
        )
        grouped.setdefault(key, []).append((index, transition))
    logprob_by_index: list[torch.Tensor | None] = [None] * len(transitions)
    entropy_by_index: list[torch.Tensor | None] = [None] * len(transitions)
    for group in grouped.values():
        first = group[0][1]
        if not isinstance(first.observation, Mapping):
            raise ValueError("Cloud transition observation must be a mapping")
        features = np.asarray(first.observation.get("features"), dtype=np.float32)
        if (
            features.ndim != 2
            or features.shape[0] <= 0
            or features.shape[1] != _config.cloud_feature_dim
        ):
            raise ValueError("Cloud transition obs shape must be [N_cloud, obs_dim]")
        n_cloud = int(features.shape[0])
        intersection_ids = tuple(
            str(item) for item in first.observation.get("intersection_ids", ())
        )
        if len(intersection_ids) != n_cloud or len(set(intersection_ids)) != n_cloud:
            raise ValueError("Cloud intersection_ids must uniquely cover N_cloud")
        if len(group) != n_cloud:
            raise ValueError(
                f"Cloud snapshot must contain {n_cloud} agent records, got {len(group)}"
            )
        edges = tuple(first.observation.get("edges", ()))
        gain = float(first.observation.get("authority_gain", 0.0))
        shared_global_state = np.asarray(first.global_state, dtype=np.float32)
        shared_reward = float(first.reward)
        actions_by_slot: dict[int, np.ndarray] = {}
        group_indices: dict[int, int] = {}
        for output_index, transition in group:
            observation = transition.observation
            if not isinstance(observation, Mapping):
                raise ValueError("Cloud transition observation must be a mapping")
            slot = transition.agent_slot
            observation_slot = observation.get("agent_slot")
            if slot is None or not isinstance(slot, (int, np.integer)):
                raise ValueError("Cloud transition agent_slot must be an integer")
            slot = int(slot)
            if observation_slot != slot or not 0 <= slot < n_cloud:
                raise ValueError("Cloud transition agent_slot mismatch")
            if transition.entity_id != intersection_ids[slot]:
                raise ValueError("Cloud transition entity_id/agent_slot mismatch")
            if slot in actions_by_slot:
                raise ValueError("duplicate Cloud transition agent_slot")
            current_features = np.asarray(
                observation.get("features"), dtype=np.float32
            )
            if current_features.shape != features.shape or not np.array_equal(
                current_features, features
            ):
                raise ValueError("Cloud snapshot records must share exact obs")
            if tuple(observation.get("edges", ())) != edges:
                raise ValueError("Cloud snapshot records must share exact graph edges")
            current_ids = tuple(
                str(item) for item in observation.get("intersection_ids", ())
            )
            if current_ids != intersection_ids:
                raise ValueError("Cloud snapshot records must share intersection ids")
            if float(observation.get("authority_gain", 0.0)) != gain:
                raise ValueError("Cloud snapshot records must share authority gain")
            current_global_state = np.asarray(
                transition.global_state, dtype=np.float32
            )
            if (
                current_global_state.shape != shared_global_state.shape
                or not np.array_equal(current_global_state, shared_global_state)
            ):
                raise ValueError("Cloud snapshot records must share global state")
            if float(transition.reward) != shared_reward:
                raise ValueError("Cloud snapshot records must share team reward")
            action = np.asarray(transition.action, dtype=np.float32)
            executed = np.asarray(transition.executed_action, dtype=np.float32)
            if action.shape != (1,) or executed.shape != (1,):
                raise ValueError("Cloud transition action shape must be exactly (1,)")
            if not np.array_equal(action, executed):
                raise ValueError("Cloud sampled/stored/executed action mismatch")
            if np.asarray(transition.old_logprob).shape != ():
                raise ValueError("Cloud transition old_logprob must be scalar")
            actions_by_slot[slot] = action
            group_indices[slot] = output_index
        if set(actions_by_slot) != set(range(n_cloud)):
            raise ValueError("Cloud snapshot agent_slot coverage mismatch")
        action_batch = np.stack(
            [actions_by_slot[slot] for slot in range(n_cloud)], axis=0
        )
        if action_batch.shape != (n_cloud, 1):
            raise ValueError("Cloud action batch shape must be [N_cloud, 1]")
        logprob_batch, entropy_batch = _cloud_actor.log_prob(
            features,
            edges,
            action_batch,
            authority_gain=gain,
        )
        expected = (n_cloud,)
        if tuple(logprob_batch.shape) != expected:
            raise ValueError("Cloud evaluated log_prob shape must be [N_cloud]")
        if tuple(entropy_batch.shape) != expected:
            raise ValueError("Cloud evaluated entropy shape must be [N_cloud]")
        for slot in range(n_cloud):
            output_index = group_indices[slot]
            logprob_by_index[output_index] = logprob_batch[slot]
            entropy_by_index[output_index] = entropy_batch[slot]
    if any(item is None for item in logprob_by_index + entropy_by_index):
        raise ValueError("Cloud actor evaluation did not cover every transition")
    return (
        torch.stack([item for item in logprob_by_index if item is not None]),
        torch.stack([item for item in entropy_by_index if item is not None]),
    )


def _actor_logprob_entropy(
    role: str, transitions: Sequence[SMDPTransition]
) -> tuple[torch.Tensor, torch.Tensor]:
    if role == "vehicle":
        features = np.stack([item.observation["features"] for item in transitions])
        actions = [item.action for item in transitions]
        return _vehicle_actor.log_prob(features, actions)  # type: ignore[union-attr]
    if role == "road":
        pairs = [
            _road_actor.log_prob(  # type: ignore[union-attr]
                item.observation["q"],
                item.observation["road"],
                item.observation["cloud"],
                item.observation["phase"],
                item.observation["movement"],
                item.observation["mask"],
                item.action,
                authority_gain=item.observation["authority_gain"],
            )
            for item in transitions
        ]
        return (
            torch.stack([pair[0] for pair in pairs]),
            torch.stack([pair[1] for pair in pairs]),
        )
    if role == "cloud":
        return _cloud_actor_logprob_entropy(transitions)
    raise ValueError(f"unknown actor role: {role}")


def _actor_sample_preflight(
    role: str, transitions: Sequence[SMDPTransition]
) -> tuple[dict[str, float | int | bool], torch.Tensor, torch.Tensor]:
    if not transitions:
        empty = torch.empty(0, dtype=torch.float32)
        return {"samples": 0, "semantic_valid": False}, empty, empty
    advantages = _normalized_advantages(transitions)
    old = torch.as_tensor(
        [item.old_logprob for item in transitions], dtype=torch.float32
    )
    initial_logprob, initial_entropy = _actor_logprob_entropy(role, transitions)
    expected = (len(transitions),)
    if tuple(old.shape) != expected:
        raise ValueError(f"{role} old_logprob shape must be {expected}")
    if tuple(initial_logprob.shape) != expected:
        raise ValueError(f"{role} evaluated logprob shape must be {expected}")
    if tuple(initial_entropy.shape) != expected:
        raise ValueError(f"{role} entropy shape must be {expected}")
    if tuple(advantages.shape) != expected:
        raise ValueError(f"{role} advantage shape must be {expected}")
    if not bool(
        torch.isfinite(old).all()
        and torch.isfinite(initial_logprob).all()
        and torch.isfinite(initial_entropy).all()
        and torch.isfinite(advantages).all()
    ):
        raise FloatingPointError(f"non-finite {role} PPO input")
    semantic_error = float((initial_logprob.detach() - old).abs().max().item())
    same_policy_ratio = torch.exp(initial_logprob.detach() - old)
    if tuple(same_policy_ratio.shape) != expected:
        raise ValueError(f"{role} same-policy ratio shape must be {expected}")
    if same_policy_ratio.numel() != len(transitions):
        raise ValueError(
            f"{role} ratio.numel() must equal N_valid samples"
        )
    if not bool(torch.isfinite(same_policy_ratio).all()):
        raise FloatingPointError(f"non-finite {role} same-policy ratio")
    ratio_error = float((same_policy_ratio - 1.0).abs().max().item())
    if semantic_error > 1e-5:
        raise ValueError(
            f"{role} sample/logprob semantic mismatch: {semantic_error:.8g}"
        )
    if ratio_error > 1e-5:
        raise ValueError(
            f"{role} same-policy ratio mismatch: {ratio_error:.8g}"
        )
    return (
        {
            "samples": len(transitions),
            "transitions": len(transitions),
            "ratio_numel": int(same_policy_ratio.numel()),
            "semantic_valid": True,
            "sample_logprob_max_abs_error": semantic_error,
            "same_policy_ratio_max_abs_error": ratio_error,
            "entropy": float(initial_entropy.detach().mean()),
        },
        old,
        advantages,
    )


def _actor_update(
    role: str, transitions: Sequence[SMDPTransition]
) -> dict[str, float | int | bool]:
    if not transitions:
        return {"samples": 0, "updated": False}
    optimizer = _optimizers[role]
    sample_health, old, advantages = _actor_sample_preflight(role, transitions)
    semantic_error = float(sample_health["sample_logprob_max_abs_error"])
    same_policy_ratio_error = float(
        sample_health["same_policy_ratio_max_abs_error"]
    )
    expected = (len(transitions),)

    last_loss = torch.tensor(0.0)
    gradient_norm = torch.tensor(0.0)
    for _ in range(_config.ppo_epochs):
        logprob, entropy_values = _actor_logprob_entropy(role, transitions)
        if tuple(logprob.shape) != expected or tuple(entropy_values.shape) != expected:
            raise ValueError(f"{role} PPO actor output shape mismatch")
        log_ratio = logprob - old
        if tuple(log_ratio.shape) != expected:
            raise ValueError(f"{role} PPO log ratio shape mismatch")
        if not bool(torch.isfinite(log_ratio).all()):
            raise FloatingPointError(f"non-finite {role} PPO log ratio")
        ratio = torch.exp(log_ratio)
        if tuple(ratio.shape) != expected or ratio.numel() != len(transitions):
            raise ValueError(
                f"{role} ratio.numel() must equal N_valid samples"
            )
        clipped = torch.clamp(
            ratio, 1.0 - _config.clip_eps, 1.0 + _config.clip_eps
        )
        entropy = entropy_values.mean()
        last_loss = -(
            torch.minimum(ratio * advantages, clipped * advantages).mean()
        ) - _config.entropy_coef * entropy
        if not bool(torch.isfinite(last_loss) and torch.isfinite(entropy)):
            raise FloatingPointError(f"non-finite {role} PPO loss/entropy")
        optimizer.zero_grad()
        last_loss.backward()
        gradient_norm = torch.nn.utils.clip_grad_norm_(
            optimizer.param_groups[0]["params"],
            _config.max_grad_norm,
            error_if_nonfinite=True,
        )
        if not bool(torch.isfinite(gradient_norm)):
            raise FloatingPointError(f"non-finite {role} PPO gradient")
        optimizer.step()

    final_logprob, final_entropy_values = _actor_logprob_entropy(role, transitions)
    if (
        tuple(final_logprob.shape) != expected
        or tuple(final_entropy_values.shape) != expected
    ):
        raise ValueError(f"{role} PPO final actor output shape mismatch")
    final_log_ratio = final_logprob - old
    final_ratio = torch.exp(final_log_ratio)
    if tuple(final_ratio.shape) != expected or final_ratio.numel() != len(transitions):
        raise ValueError(
            f"{role} final ratio.numel() must equal N_valid samples"
        )
    approx_kl = ((final_ratio - 1.0) - final_log_ratio).mean()
    final_entropy = final_entropy_values.mean()
    if not bool(
        torch.isfinite(final_log_ratio).all()
        and torch.isfinite(final_ratio).all()
        and torch.isfinite(approx_kl)
        and torch.isfinite(final_entropy)
    ):
        raise FloatingPointError(f"non-finite {role} PPO post-update diagnostics")
    return {
        "samples": int(old.numel()),
        "transitions": len(transitions),
        "updated": True,
        "semantic_valid": True,
        "loss": float(last_loss.detach()),
        "entropy": float(final_entropy.detach()),
        "gradient_norm": float(gradient_norm.detach()),
        "approx_kl": float(approx_kl.detach()),
        "clip_fraction": float(
            (
                (final_ratio.detach() - 1.0).abs() > _config.clip_eps
            ).float().mean()
        ),
        "sample_logprob_max_abs_error": semantic_error,
        "same_policy_ratio_max_abs_error": same_policy_ratio_error,
        "ratio_numel": int(old.numel()),
    }


def _critic_update(transitions: Sequence[SMDPTransition]) -> dict[str, float | int | bool]:
    if not transitions or _critic is None:
        return {"samples": 0, "updated": False}
    states = np.stack(
        [np.asarray(item.global_state, dtype=np.float32) for item in transitions]
    )
    contexts: np.ndarray | None = None
    if _local_credit_enabled():
        raw_contexts = []
        for item in transitions:
            if not isinstance(item.observation, Mapping):
                raise ValueError("local-credit critic observation must be a mapping")
            context = np.asarray(
                item.observation.get("critic_context"), dtype=np.float32
            )
            if context.shape != (_config.critic_context_dim,):
                raise ValueError("local-credit critic context shape mismatch")
            raw_contexts.append(context)
        contexts = np.stack(raw_contexts)
    returns = np.asarray(
        [float(item.return_) for item in transitions], dtype=np.float32
    )
    if not bool(
        np.isfinite(states).all()
        and np.isfinite(returns).all()
        and (contexts is None or np.isfinite(contexts).all())
    ):
        raise FloatingPointError("non-finite critic input")
    _value_normalizer.update(returns)
    targets = torch.as_tensor(
        _value_normalizer.normalize(returns), dtype=torch.float32
    )
    optimizer = _optimizers["critic"]
    loss = torch.tensor(0.0)
    gradient_norm = torch.tensor(0.0)
    for _ in range(_config.ppo_epochs):
        prediction = (
            _critic(states, contexts)
            if contexts is not None
            else _critic(states)
        )
        loss = torch.nn.functional.mse_loss(prediction, targets)
        if not bool(torch.isfinite(loss) and torch.isfinite(prediction).all()):
            raise FloatingPointError("non-finite critic loss/value")
        optimizer.zero_grad()
        loss.backward()
        gradient_norm = torch.nn.utils.clip_grad_norm_(
            _critic.parameters(),
            _config.max_grad_norm,
            error_if_nonfinite=True,
        )
        if not bool(torch.isfinite(gradient_norm)):
            raise FloatingPointError("non-finite critic gradient")
        optimizer.step()
    final_prediction = (
        _critic(states, contexts) if contexts is not None else _critic(states)
    )
    final_loss = torch.nn.functional.mse_loss(final_prediction, targets)
    if not bool(torch.isfinite(final_loss) and torch.isfinite(final_prediction).all()):
        raise FloatingPointError("non-finite critic post-update diagnostics")
    return {
        "samples": len(transitions),
        "updated": True,
        "loss": float(final_loss.detach()),
        "gradient_norm": float(gradient_norm.detach()),
        "prediction_mean": float(final_prediction.detach().mean()),
        "prediction_std": float(final_prediction.detach().std(unbiased=False)),
        "value_mean": _value_normalizer.mean,
        "value_std": float(np.sqrt(_value_normalizer.var + 1e-8)),
    }


def preflight_actor_samples(
    rollouts: Sequence[MVPRollout | None],
    *,
    required_roles: Sequence[str] = (),
) -> dict[str, dict[str, float | int | bool]]:
    """Recompute actor logprobs without updating any model parameters."""
    valid = [item for item in rollouts if item is not None]
    if not valid:
        raise ValueError("actor sample preflight requires at least one rollout")
    if any(item.policy_generation != _policy_generation for item in valid):
        raise ValueError("actor sample preflight policy generation mismatch")
    required = tuple(dict.fromkeys(str(role) for role in required_roles))
    unknown = set(required) - {"cloud", "road", "vehicle"}
    if unknown:
        raise ValueError(f"unknown required actor roles: {sorted(unknown)}")
    transitions = [item for rollout in valid for item in rollout.transitions]
    health = {
        role: _actor_sample_preflight(
            role, [item for item in transitions if item.role == role]
        )[0]
        for role in ("cloud", "road", "vehicle")
    }
    missing = [
        role for role in required if int(health[role].get("samples", 0)) <= 0
    ]
    if missing:
        raise ValueError(f"required actor roles have no samples: {missing}")
    return health


def train_on_rollout(rollout: MVPRollout | None) -> dict[str, Any] | None:
    return train_on_rollouts([rollout])


def train_on_rollouts(
    rollouts: list[MVPRollout | None],
    *,
    trainable_roles: Sequence[str] = ("vehicle",),
) -> dict[str, Any] | None:
    """Exactly one unified PPO update for a complete episode set."""
    global _policy_generation, _critic_updates, _last_trainable_actor_roles
    global _authority_gains
    valid = [item for item in rollouts if item is not None]
    if not valid:
        return None
    if any(item.policy_generation != _policy_generation for item in valid):
        raise ValueError("rollout policy generation mismatch")
    gain_sets = {
        tuple(sorted(item.authority_gains.items())) for item in valid
    }
    if len(gain_sets) != 1:
        raise ValueError("one PPO batch must use one frozen authority-gain level")
    _authority_gains = dict(valid[0].authority_gains)
    requested_roles = tuple(dict.fromkeys(str(role) for role in trainable_roles))
    unknown_roles = set(requested_roles) - {"cloud", "road", "vehicle"}
    if unknown_roles:
        raise ValueError(f"unknown trainable actor roles: {sorted(unknown_roles)}")
    transitions = [transition for item in valid for transition in item.transitions]
    if _mode != "train":
        return {
            "candidate_id": _runtime_candidate_id(),
            "policy_generation": _policy_generation,
            "episodes": len(valid),
            "updates": 0,
        }
    actor_transitions = {
        role: [item for item in transitions if item.role == role]
        for role in ("cloud", "road", "vehicle")
    }
    sample_preflight = preflight_actor_samples(valid)
    diagnostics: dict[str, Any] = {}
    for role in ("cloud", "road", "vehicle"):
        if role in requested_roles:
            diagnostics[role] = _actor_update(role, actor_transitions[role])
        else:
            diagnostics[role] = {
                **sample_preflight[role],
                "updated": False,
                "reason": "generation_schedule_frozen",
            }
    critic_transitions = (
        [item for item in transitions if item.role == "vehicle"]
        if _local_credit_enabled()
        else transitions
    )
    diagnostics["critic"] = _critic_update(critic_transitions)
    _last_trainable_actor_roles = requested_roles
    _critic_updates += 1
    _policy_generation += 1
    return {
        "candidate_id": _runtime_candidate_id(),
        "policy_generation": _policy_generation,
        "critic_updates": _critic_updates,
        "authority_gains": dict(valid[0].authority_gains),
        "trainable_roles": list(requested_roles),
        "steps": len(transitions),
        "episodes": len(valid),
        "updates": 1,
        "roles": diagnostics,
        "metrics": [item.metrics for item in valid],
    }


def train_vehicle_critic_on_rollouts(
    rollouts: list[MVPRollout | None],
) -> dict[str, Any] | None:
    """One frozen-actor burn-in update on Vehicle local-credit transitions."""

    global _critic_updates, _last_trainable_actor_roles, _authority_gains
    valid = [item for item in rollouts if item is not None]
    if not valid:
        return None
    if not _local_credit_enabled() or _mode != "train":
        raise ValueError("Vehicle critic burn-in requires local-credit TRAIN mode")
    if any(item.policy_generation != _policy_generation for item in valid):
        raise ValueError("Vehicle critic burn-in policy generation mismatch")
    expected_gains = {"road": 1.0, "cloud": 1.0, "vehicle": 1.0 / 3.0}
    if any(
        any(
            abs(float(item.authority_gains.get(role, -1.0)) - value) > 1e-12
            for role, value in expected_gains.items()
        )
        for item in valid
    ):
        raise ValueError("Vehicle critic burn-in authority schedule mismatch")
    _authority_gains = dict(valid[0].authority_gains)
    preflight = preflight_actor_samples(valid, required_roles=("vehicle",))
    transitions = [
        transition
        for item in valid
        for transition in item.transitions
        if transition.role == "vehicle"
    ]
    if not transitions:
        raise ValueError("Vehicle critic burn-in has no local-credit transitions")
    diagnostics = _critic_update(transitions)
    _critic_updates += 1
    _last_trainable_actor_roles = ()
    return {
        "candidate_id": _runtime_candidate_id(),
        "policy_generation": _policy_generation,
        "critic_updates": _critic_updates,
        "episodes": len(valid),
        "steps": len(transitions),
        "updates": 1,
        "actor_updates": 0,
        "authority_gains": dict(valid[0].authority_gains),
        "sample_preflight": preflight,
        "roles": {"critic": diagnostics},
        "metrics": [item.metrics for item in valid],
    }


def train_critic_on_rollouts(
    rollouts: list[MVPRollout | None],
) -> dict[str, Any] | None:
    """Anchor value scale on gain-zero base-policy data without actor updates."""
    global _critic_updates
    valid = [item for item in rollouts if item is not None]
    if not valid:
        return None
    if _mode != "train":
        raise ValueError("critic anchor requires COV2X_MODE=train")
    if any(item.policy_generation != _policy_generation for item in valid):
        raise ValueError("critic-anchor rollout policy generation mismatch")
    if any(any(value != 0.0 for value in item.authority_gains.values()) for item in valid):
        raise ValueError("critic anchor requires Road/Cloud/Vehicle gain=0")
    transitions = [
        transition
        for item in valid
        for transition in item.transitions
        if transition.role == "value"
    ]
    if not transitions:
        raise ValueError("critic anchor has no value transitions")
    diagnostics = _critic_update(transitions)
    _critic_updates += 1
    return {
        "candidate_id": _runtime_candidate_id(),
        "policy_generation": _policy_generation,
        "critic_updates": _critic_updates,
        "episodes": len(valid),
        "steps": len(transitions),
        "updates": 1,
        "actor_updates": 0,
        "authority_gains": dict(valid[0].authority_gains),
        "roles": {"critic": diagnostics},
        "metrics": [item.metrics for item in valid],
    }


def save_checkpoint(path: str) -> str:
    if any(
        model is None
        for model in (_cloud_actor, _road_actor, _vehicle_actor, _critic)
    ):
        raise RuntimeError("MVP models are not initialized")
    if not _parent_provenance_verified:
        raise RuntimeError(
            "corridor checkpoint requires verified format-v6 parent provenance"
        )
    assert _cloud_actor is not None
    assert _road_actor is not None
    assert _vehicle_actor is not None
    assert _critic is not None
    if _local_credit_enabled():
        if not isinstance(_critic, ConditionedCentralizedCritic):
            raise RuntimeError("local-credit checkpoint critic architecture mismatch")
        if set(_last_trainable_actor_roles or ()) - {"vehicle"}:
            raise RuntimeError("local-credit checkpoint has non-Vehicle trainable actor")
        if _policy_generation == 0:
            target_bias = float(
                np.arctanh(_runtime_initial_vehicle_mean())
            )
            if (
                torch.count_nonzero(_vehicle_actor.mean.weight).item() != 0
                or abs(float(_vehicle_actor.mean.bias.item()) - target_bias) > 1e-7
                or float(_vehicle_actor.log_std.item()) != -1.0
            ):
                raise RuntimeError(
                    "local-credit generation-0 Vehicle initialization mismatch"
                )
    elif _policy_generation == 0 and (
        torch.count_nonzero(_vehicle_actor.mean.weight).item() != 0
        or torch.count_nonzero(_vehicle_actor.mean.bias).item() != 0
        or float(_vehicle_actor.log_std.item()) != -1.0
    ):
        raise RuntimeError("generation-0 Vehicle actor is not exact zero-mean")

    components = {
        "cloud": _cloud_actor,
        "road": _road_actor,
        "vehicle": _vehicle_actor,
        "critic": _critic,
    }
    trainable_at_save = tuple(_last_trainable_actor_roles or ())
    current_frozen = [
        role
        for role in ("cloud", "road", "vehicle")
        if role not in trainable_at_save and role in FROZEN_ACTOR_ROLES
    ]
    local_credit = _local_credit_enabled()
    state = {
        "format_version": (
            _runtime_checkpoint_format_version()
            if local_credit
            else CHECKPOINT_FORMAT_VERSION
        ),
        "candidate_id": _runtime_candidate_id(),
        "vehicle_action_semantics": _runtime_action_semantics(),
        "policy_generation": _policy_generation,
        "critic_updates": _critic_updates,
        "parent_candidate_id": (
            LOCAL_CREDIT_G30_PARENT_CANDIDATE_ID
            if local_credit
            else PARENT_CANDIDATE_ID
        ),
        "parent_checkpoint_format_version": (
            LOCAL_CREDIT_G30_PARENT_CHECKPOINT_FORMAT_VERSION
            if local_credit
            else PARENT_CHECKPOINT_FORMAT_VERSION
        ),
        "parent_checkpoint_generation": (
            LOCAL_CREDIT_G30_PARENT_CHECKPOINT_GENERATION
            if local_credit
            else PARENT_CHECKPOINT_GENERATION
        ),
        "parent_checkpoint_sha256": (
            LOCAL_CREDIT_G30_PARENT_CHECKPOINT_SHA256
            if local_credit
            else PARENT_CHECKPOINT_SHA256
        ),
        "delta_R": _delta_R,
        "delta_v_max_speed_ceiling_fraction": DELTA_V_MAX_SPEED_CEILING_FRACTION,
        "native_release_tolerance_mps": (
            None
            if _temporary_speed_cap_enabled()
            else NATIVE_RELEASE_TOLERANCE_MPS
        ),
        "shaping_coefficients": dict(SHAPING_COEFFICIENTS),
        "screen_config_hash": os.environ.get("COV2X_SCREEN_CONFIG_HASH"),
        "corridor_config_hash": os.environ.get(
            "COV2X_CORRIDOR_CONFIG_HASH"
        ),
        "critic_lineage": (
            LOCAL_CREDIT_CRITIC_LINEAGE if local_credit else CRITIC_LINEAGE
        ),
        "local_credit_reward_semantics": (
            LOCAL_CREDIT_REWARD_SEMANTICS if local_credit else None
        ),
        "local_credit_config_hash": (
            os.environ.get("COV2X_LOCAL_CREDIT_CONFIG_HASH")
            if local_credit
            else None
        ),
        "temporary_speed_cap_config_hash": (
            os.environ.get("COV2X_TEMPORARY_SPEED_CAP_CONFIG_HASH")
            if _temporary_speed_cap_enabled()
            else None
        ),
        "initial_deterministic_vehicle_mean": (
            _runtime_initial_vehicle_mean() if local_credit else 0.0
        ),
        "popart": None,
        "frozen_actor_roles": list(FROZEN_ACTOR_ROLES),
        "generation_zero_frozen_actor_roles": list(FROZEN_ACTOR_ROLES),
        "current_frozen_actor_roles": current_frozen,
        "trainable_actor_roles_at_save": list(trainable_at_save),
        "actor_update_schedule_id": (
            _runtime_actor_update_schedule_id()
            if local_credit
            else os.environ.get("COV2X_ACTOR_UPDATE_SCHEDULE_ID")
        ),
        "optimizer_roles": sorted(_optimizers),
        "component_schema": {
            name: _module_schema(module) for name, module in components.items()
        },
        "initialization_seed": _initialization_seed,
        "initialization_seed_role": _initialization_seed_role,
        "seed_role": _initialization_seed_role,
        "last_rollout_seed": _seed,
        "last_rollout_seed_role": os.environ.get("COV2X_SEED_ROLE"),
        "authority_gains_at_save": dict(_authority_gains),
        "cloud_actor": _cloud_actor.state_dict(),
        "road_actor": _road_actor.state_dict(),
        "vehicle_actor": _vehicle_actor.state_dict(),
        "critic": _critic.state_dict(),
        "optimizers": {
            name: _optimizers[name].state_dict()
            for name in ("cloud", "road", "vehicle", "critic")
        },
        "value_normalizer": _value_normalizer.state_dict(),
        "rng_state": {
            "python": random.getstate(),
            "numpy": np.random.get_state(),
            "torch_cpu": torch.get_rng_state(),
        },
    }
    target = Path(path)
    target.parent.mkdir(parents=True, exist_ok=True)
    descriptor, temporary_name = tempfile.mkstemp(
        dir=target.parent,
        prefix=f".{target.name}.",
        suffix=".tmp",
    )
    temporary = Path(temporary_name)
    try:
        with os.fdopen(descriptor, "wb") as handle:
            torch.save(state, handle)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary, target)
        directory_descriptor = os.open(target.parent, os.O_RDONLY)
        try:
            os.fsync(directory_descriptor)
        finally:
            os.close(directory_descriptor)
    finally:
        if temporary.exists():
            temporary.unlink()
    return str(target)


def diagnostics() -> dict[str, Any]:
    return {"candidate_id": _runtime_candidate_id(), "policy_generation": _policy_generation, "critic_updates": _critic_updates, "initialized": _initialized, "mode": _mode, "delta_R": _delta_R, "authority_gains": dict(_authority_gains), "queued_rollouts": len(_collected_rollouts), "authority": dict(_authority)}
