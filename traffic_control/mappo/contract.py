"""Checkpoint contract for deployable MAPPO cooperative checkpoints.

MAPPO checkpoints are self-describing (format versions 2 and 3): the top-level dict
carries ``metadata`` and ``policy_state_dict``.  This module pins the
deployment contract shared with the fixed 20-slot IPPO-v8 identity schema:
controlled intersections must be a subset of the checkpoint training IDs and
the local observation must be the 132-dim 20-slot IPPO-v8 schema.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any, Mapping

import torch

from traffic_control.common.environment_contract import (
    validate_checkpoint_binding,
    validate_checkpoint_environment,
)
from traffic_control.ippo.contract import (
    MAX_LANES,
    NORMALIZATION,
    OBSERVATION_SCHEMA,
    SATURATION_FLOW_PER_LANE,
    VEHICLE_LENGTH_WITH_GAP_M,
)

from traffic_control.ippo.identity import IDENTITY_SLOT_IDS
from traffic_control.ippo.model import PHASE_FEATURES as IPPO_PHASE_FEATURES

CHECKPOINT_CONTRACT_VERSION = 2
MULTIPERIOD_CHECKPOINT_CONTRACT_VERSION = 3
MULTISCENARIO_CHECKPOINT_CONTRACT_VERSION = 4
SUPPORTED_FORMAT_VERSIONS = frozenset({2, 3, 4})

EXPECTED_OBS_DIM = 8 + 1 + len(IDENTITY_SLOT_IDS) + 5 * 20 + 3  # 132
EXPECTED_PHASE_FEATURES = int(IPPO_PHASE_FEATURES)  # 11
EXPECTED_PHASE_FEATURE_SCHEMA = "connection_pressure_service_age_eta_demand_v2"
EXPECTED_IDENTITY_OFFSET = 9


def load_contract(
    checkpoint_path: str | Path,
    checkpoint: Mapping[str, Any],
) -> tuple[int, dict[str, Any]]:
    """Return ``(contract_version, view)`` for a MAPPO checkpoint payload."""
    if not isinstance(checkpoint, Mapping):
        raise ValueError("MAPPO checkpoint must be a dictionary")
    if "metadata" not in checkpoint:
        raise ValueError("MAPPO checkpoint is missing its metadata dict")
    metadata = checkpoint["metadata"]
    if not isinstance(metadata, Mapping):
        raise ValueError("MAPPO checkpoint metadata must be a dictionary")
    if "policy_state_dict" not in checkpoint:
        raise ValueError("MAPPO checkpoint is missing policy_state_dict")
    format_version = int(checkpoint.get("checkpoint_format_version", 0))
    if format_version not in SUPPORTED_FORMAT_VERSIONS:
        raise ValueError(
            f"unsupported MAPPO checkpoint format version {format_version}; "
            f"supported: {sorted(SUPPORTED_FORMAT_VERSIONS)}"
        )
    environment_contract = None
    if format_version in {
        MULTIPERIOD_CHECKPOINT_CONTRACT_VERSION,
        MULTISCENARIO_CHECKPOINT_CONTRACT_VERSION,
    }:
        environment_contract = checkpoint.get("environment_contract")
        if not isinstance(environment_contract, Mapping):
            raise ValueError(
                f"MAPPO format v{format_version} checkpoint is missing "
                "environment_contract"
            )
        if (
            int(environment_contract.get("environment_contract_version", 0))
            != format_version
        ):
            raise ValueError(
                "checkpoint format and environment contract version must match"
            )
        validate_checkpoint_binding(
            environment_contract,
            periods=metadata.get("training_periods", ()),
            policy_spec=build_mappo_policy_spec(metadata),
            intersection_ids=metadata.get("intersection_ids", ()),
        )
        if format_version == MULTISCENARIO_CHECKPOINT_CONTRACT_VERSION:
            from traffic_control.common.checkpoint_package import (
                CAPABILITY_SCHEMA_VERSION,
                state_dict_sha256,
            )

            if int(checkpoint.get("capability_schema_version", 0)) != (
                CAPABILITY_SCHEMA_VERSION
            ):
                raise ValueError("MAPPO v4 capability schema version mismatch")
            if not str(checkpoint.get("model_id", "")).strip():
                raise ValueError("MAPPO v4 checkpoint is missing model_id")
            expected_weights = str(checkpoint.get("weights_sha256", ""))
            live_weights = state_dict_sha256(
                checkpoint.get("policy_state_dict", {})
            )
            if expected_weights != live_weights:
                raise ValueError(
                    "MAPPO v4 checkpoint policy weight digest mismatch"
                )
    view = dict(metadata)
    view["checkpoint_format_version"] = format_version
    view["checkpoint_path"] = str(Path(checkpoint_path).resolve())
    if environment_contract is not None:
        view["environment_contract"] = dict(environment_contract)
    return format_version, view


def _require(view: Mapping[str, Any], name: str) -> Any:
    if name not in view or view[name] is None:
        raise ValueError(f"MAPPO checkpoint metadata is missing {name!r}")
    return view[name]

def build_mappo_policy_spec(metadata: Mapping[str, Any]) -> dict[str, Any]:
    """Build the network and tensor-space facts signed by a v3 checkpoint."""
    return {
        "algorithm_family": "mappo_cooperative_ppo",
        "model_version": str(_require(metadata, "model_version")),
        "actor_variant": str(_require(metadata, "actor_variant")),
        "critic_scope": str(_require(metadata, "critic_scope")),
        "hidden_dim": int(_require(metadata, "hidden_dim")),
        "identity_slots": list(IDENTITY_SLOT_IDS),
        "identity_offset": int(_require(metadata, "identity_offset")),
        "observation_schema": dict(OBSERVATION_SCHEMA),
        "local_observation_schema": str(
            _require(metadata, "local_observation_schema")
        ),
        "centralized_state_schema": str(
            _require(metadata, "centralized_state_schema")
        ),
        "obs_dim": int(_require(metadata, "obs_dim")),
        "phase_feature_schema": str(_require(metadata, "phase_feature_schema")),
        "phase_feature_dim": int(_require(metadata, "phase_feature_dim")),
        "max_action_dim": int(_require(metadata, "max_action_dim")),
        "max_lanes": MAX_LANES,
        "vehicle_length_with_gap_m": VEHICLE_LENGTH_WITH_GAP_M,
        "saturation_flow_per_lane": SATURATION_FLOW_PER_LANE,
        "action_interval_s": float(_require(metadata, "action_interval_s")),
        "max_green_factor": float(_require(metadata, "max_green_factor")),
        "effective_demand_enabled": bool(
            _require(metadata, "effective_demand_enabled")
        ),
        "normalization": dict(NORMALIZATION),
        "action_representation": "program_local_candidate_offset_v1",
    }




def validate_contract(
    view: Mapping[str, Any],
    *,
    intersection_ids: Any,
    obs_dim: int,
    action_interval: float,
    max_green_factor: float,
    effective_demand_enabled: bool,
    live_metadata: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    """Validate a loaded MAPPO checkpoint against live deployment facts.

    Returns the normalized metadata view.  Raises ``ValueError`` on any
    contract violation.
    """
    metadata = dict(view)

    saved_ids = tuple(str(value) for value in _require(metadata, "intersection_ids"))
    current_ids = tuple(str(value) for value in intersection_ids)
    unknown = [iid for iid in current_ids if iid not in set(saved_ids)]
    if unknown:
        raise ValueError(
            f"MAPPO checkpoint was not trained on intersections {unknown}; "
            f"controlled subset must be a subset of {saved_ids}"
        )

    saved_obs_dim = int(_require(metadata, "obs_dim"))
    if saved_obs_dim != EXPECTED_OBS_DIM:
        raise ValueError(
            f"MAPPO checkpoint obs_dim {saved_obs_dim} does not match the "
            f"fixed 20-slot identity contract {EXPECTED_OBS_DIM}"
        )
    if obs_dim != EXPECTED_OBS_DIM:
        raise ValueError(
            f"live obs_dim {obs_dim} does not match the fixed 20-slot "
            f"identity contract {EXPECTED_OBS_DIM}"
        )

    saved_phase_features = int(_require(metadata, "phase_feature_dim"))
    if saved_phase_features != EXPECTED_PHASE_FEATURES:
        raise ValueError(
            f"MAPPO checkpoint phase_feature_dim {saved_phase_features} "
            f"does not match {EXPECTED_PHASE_FEATURES}"
        )

    checks = (
        ("phase feature schema", metadata.get("phase_feature_schema"), EXPECTED_PHASE_FEATURE_SCHEMA),
        ("identity offset", metadata.get("identity_offset"), EXPECTED_IDENTITY_OFFSET),
        ("actor variant", metadata.get("actor_variant", "shared"), "shared"),
        ("action interval", float(metadata.get("action_interval_s", 0.0)), action_interval),
        ("maximum green factor", float(metadata.get("max_green_factor", -1.0)), max_green_factor),
        ("effective demand flag", bool(metadata.get("effective_demand_enabled", True)), effective_demand_enabled),
    )
    for label, saved, expected in checks:
        if saved != expected:
            raise ValueError(
                f"MAPPO checkpoint {label} mismatch: saved={saved!r}, "
                f"expected={expected!r}"
            )

    if int(metadata.get("checkpoint_format_version", 0)) in {
        MULTIPERIOD_CHECKPOINT_CONTRACT_VERSION,
        MULTISCENARIO_CHECKPOINT_CONTRACT_VERSION,
    }:
        if live_metadata is None:
            raise ValueError(
                "MAPPO format v3/v4 validation requires live metadata"
            )
        environment_contract = _require(metadata, "environment_contract")
        validation = validate_checkpoint_environment(
            environment_contract,
            live_metadata,
            policy_spec=build_mappo_policy_spec(metadata),
            controlled_intersection_ids=current_ids,
        )
        metadata["environment_validation"] = validation

    return metadata


def load_checkpoint_metadata(path: str | Path) -> dict[str, Any]:
    """Lightweight metadata reader for CLI preflight (no torch model)."""
    payload = torch.load(Path(path), map_location="cpu", weights_only=False)
    if not isinstance(payload, Mapping) or "metadata" not in payload:
        raise ValueError(f"Not a MAPPO checkpoint: {path}")
    metadata = payload["metadata"]
    if not isinstance(metadata, Mapping):
        raise ValueError(f"MAPPO checkpoint metadata is malformed: {path}")
    return dict(metadata)
