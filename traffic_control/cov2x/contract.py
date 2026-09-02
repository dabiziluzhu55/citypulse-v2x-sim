"""Checkpoint contracts for versioned CoV2X deployment candidates."""

from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import Any, Mapping

CHECKPOINT_CONTRACT_VERSION = 2
JOINT_CHECKPOINT_FORMAT_VERSION = 2
DEFAULT_JOINT_MODEL_FILENAME = "cov2x_joint_ep12.pt"

TEMPORARY_CAP_CHECKPOINT_CONTRACT_VERSION = 3
TEMPORARY_CAP_CHECKPOINT_FORMAT_VERSION = 9
TEMPORARY_CAP_MODEL_FILENAME = "cov2x_g30_temporary_cap_u24.pt"
TEMPORARY_CAP_MANIFEST_FILENAME = "cov2x_g30_temporary_cap_u24.json"
TEMPORARY_CAP_CHECKPOINT_SHA256 = (
    "820cf477beb2fd08226038611a4c47f555a746d0f78ddd85f0cabe2092804d7c"
)
TEMPORARY_CAP_SEMANTIC_FINGERPRINT_SHA256 = (
    "8c55902d740a22480b2a00a58296fc7cdac4ce0330350aa9142eae79b4ea5a64"
)
TEMPORARY_CAP_PROTOCOL_CONFIG_HASH = (
    "276d0a81ce43f0c5de3bcb698bf7f1e0176d4c71f6dafb3be8ec5c6e133b59e3"
)
TEMPORARY_CAP_ACTOR_UPDATE_SCHEDULE_ID = (
    "temporary_base_relative_three_scope_latin_gain_1_3_2_3_1_x22_v1"
)
TEMPORARY_CAP_RUNTIME_CANDIDATE_ID = "cov2x_temporary_speed_cap_final_v1"
TEMPORARY_CAP_DEPLOYMENT_CANDIDATE_ID = (
    "cov2x_temporary_speed_cap_three_scope_train_v1_update_24"
)
TEMPORARY_CAP_ACTION_SEMANTICS = "temporary_base_relative_speed_cap_v1"
TEMPORARY_CAP_PARENT_SHA256 = (
    "8f6674465ce44150b83e5e4789ccecdddb7471e39e35a4b9eb40801e44dfe271"
)

TRAINING_INTERSECTION_IDS: tuple[str, ...] = tuple(
    f"demo_{i}" for i in range(1, 21)
)

EXPECTED_JOINT_MODEL_CONFIG: dict[str, int] = {
    "vehicle_obs_dim": 44,
    "signal_obs_dim": 37,
    "cloud_obs_dim": 86,
    "global_state_dim": 146,
    "hidden_dim": 128,
    "critic_hidden_dim": 256,
    "lane_action_dim": 3,
    "speed_action_dim": 5,
    "signal_action_dim": 2,
    "cloud_priority_classes": 3,
    "max_intersections": 20,
}


def checkpoint_sha256(path: str | Path) -> str:
    digest = hashlib.sha256()
    with open(path, "rb") as handle:
        for chunk in iter(lambda: handle.read(1 << 20), b""):
            digest.update(chunk)
    return digest.hexdigest()


def load_joint_contract(
    checkpoint_path: str | Path,
    checkpoint: Mapping[str, Any],
) -> tuple[int, dict[str, Any]]:
    """Validate and describe the legacy EP12 joint checkpoint."""
    if not isinstance(checkpoint, Mapping):
        raise ValueError("CoV2X joint checkpoint must be a dictionary")
    format_version = int(checkpoint.get("format_version", 0))
    if format_version != JOINT_CHECKPOINT_FORMAT_VERSION:
        raise ValueError(
            f"CoV2X joint checkpoint format_version={format_version!r} is not "
            f"supported; expected {JOINT_CHECKPOINT_FORMAT_VERSION}"
        )
    joint_policy = checkpoint.get("joint_policy")
    if not isinstance(joint_policy, Mapping):
        raise ValueError(
            "CoV2X joint checkpoint is missing joint_policy mapping"
        )
    for family in ("vehicle_actor", "signal_actor", "cloud_actor", "critic"):
        if family not in joint_policy:
            raise ValueError(
                f"CoV2X joint checkpoint joint_policy is missing {family!r}"
            )
    config = checkpoint.get("config")
    if not isinstance(config, Mapping):
        raise ValueError("CoV2X joint checkpoint is missing config")
    actual = {
        name: int(config[name])
        for name in EXPECTED_JOINT_MODEL_CONFIG
        if name in config
    }
    missing = sorted(set(EXPECTED_JOINT_MODEL_CONFIG) - set(actual))
    if missing:
        raise ValueError(
            f"CoV2X joint checkpoint config is missing: {missing}"
        )
    if actual != EXPECTED_JOINT_MODEL_CONFIG:
        raise ValueError(
            f"CoV2X joint checkpoint config mismatch: expected "
            f"{EXPECTED_JOINT_MODEL_CONFIG}, got {actual}"
        )
    view = {
        "checkpoint_contract_version": CHECKPOINT_CONTRACT_VERSION,
        "checkpoint_filename": Path(checkpoint_path).name,
        "sha256": checkpoint_sha256(checkpoint_path),
        "format_version": format_version,
        "model_family": "joint",
        "config": dict(config),
        "phase_orders": checkpoint.get("phase_orders"),
        "episode_count": checkpoint.get("episode_count"),
        "policy_generation": checkpoint.get("policy_generation"),
    }
    return CHECKPOINT_CONTRACT_VERSION, view


def _load_manifest(path: str | Path) -> dict[str, Any]:
    manifest_path = Path(path)
    try:
        data = json.loads(manifest_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise ValueError(
            f"Invalid CoV2X candidate manifest: {manifest_path}"
        ) from exc
    if not isinstance(data, dict):
        raise ValueError("CoV2X candidate manifest must be a JSON object")
    return data


def load_temporary_cap_contract(
    checkpoint_path: str | Path,
    checkpoint: Mapping[str, Any],
    *,
    manifest_path: str | Path | None = None,
) -> tuple[int, dict[str, Any]]:
    """Validate the exact frozen update-24 temporary-cap checkpoint."""
    path = Path(checkpoint_path)
    if not isinstance(checkpoint, Mapping):
        raise ValueError("CoV2X temporary-cap checkpoint must be a dictionary")

    actual_sha256 = checkpoint_sha256(path)
    if actual_sha256 != TEMPORARY_CAP_CHECKPOINT_SHA256:
        raise ValueError(
            "CoV2X temporary-cap checkpoint SHA-256 mismatch"
        )

    expected = {
        "candidate_id": TEMPORARY_CAP_RUNTIME_CANDIDATE_ID,
        "format_version": TEMPORARY_CAP_CHECKPOINT_FORMAT_VERSION,
        "policy_generation": 24,
        "actor_update_schedule_id": TEMPORARY_CAP_ACTOR_UPDATE_SCHEDULE_ID,
        "vehicle_action_semantics": TEMPORARY_CAP_ACTION_SEMANTICS,
        "temporary_speed_cap_config_hash": TEMPORARY_CAP_PROTOCOL_CONFIG_HASH,
        "local_credit_config_hash": TEMPORARY_CAP_PROTOCOL_CONFIG_HASH,
        "parent_checkpoint_sha256": TEMPORARY_CAP_PARENT_SHA256,
        "frozen_actor_roles": ["cloud", "road"],
        "trainable_actor_roles_at_save": ["vehicle"],
        "critic_lineage": "fresh_role_intersection_movement_context_v1",
        "local_credit_reward_semantics": (
            "movement_local_time_loss_rate_per_vehicle_v1"
        ),
        "initial_deterministic_vehicle_mean": 0.0,
        "delta_v_max_speed_ceiling_fraction": 0.1,
        "native_release_tolerance_mps": None,
    }
    for field, value in expected.items():
        if checkpoint.get(field) != value:
            raise ValueError(
                f"CoV2X temporary-cap checkpoint {field} mismatch"
            )

    for component in ("road_actor", "cloud_actor", "vehicle_actor", "critic"):
        if not isinstance(checkpoint.get(component), Mapping):
            raise ValueError(
                f"CoV2X temporary-cap checkpoint missing {component}"
            )
    schema = checkpoint.get("component_schema")
    if not isinstance(schema, Mapping) or set(schema) != {
        "road", "cloud", "vehicle", "critic"
    }:
        raise ValueError(
            "CoV2X temporary-cap component schema mismatch"
        )
    if set(checkpoint.get("optimizer_roles", ())) != {
        "road", "cloud", "vehicle", "critic"
    }:
        raise ValueError(
            "CoV2X temporary-cap optimizer role set mismatch"
        )

    manifest_file = (
        Path(manifest_path)
        if manifest_path is not None
        else path.with_name(TEMPORARY_CAP_MANIFEST_FILENAME)
    )
    manifest = _load_manifest(manifest_file)
    manifest_expected = {
        "schema_version": 1,
        "model_alias": "cov2x_g30_temp_cap_u24",
        "deployment_candidate_id": TEMPORARY_CAP_DEPLOYMENT_CANDIDATE_ID,
        "runtime_candidate_id": TEMPORARY_CAP_RUNTIME_CANDIDATE_ID,
        "checkpoint_filename": TEMPORARY_CAP_MODEL_FILENAME,
        "checkpoint_sha256": TEMPORARY_CAP_CHECKPOINT_SHA256,
        "semantic_fingerprint_sha256": (
            TEMPORARY_CAP_SEMANTIC_FINGERPRINT_SHA256
        ),
        "format_version": TEMPORARY_CAP_CHECKPOINT_FORMAT_VERSION,
        "policy_generation": 24,
        "actor_update_schedule_id": TEMPORARY_CAP_ACTOR_UPDATE_SCHEDULE_ID,
        "vehicle_action_semantics": TEMPORARY_CAP_ACTION_SEMANTICS,
        "protocol_config_hash": TEMPORARY_CAP_PROTOCOL_CONFIG_HASH,
        "performance_validation_status": "NOT_RUN",
    }
    for field, value in manifest_expected.items():
        if manifest.get(field) != value:
            raise ValueError(
                f"CoV2X temporary-cap manifest {field} mismatch"
            )

    view = {
        "checkpoint_contract_version": (
            TEMPORARY_CAP_CHECKPOINT_CONTRACT_VERSION
        ),
        "checkpoint_filename": path.name,
        "sha256": actual_sha256,
        "format_version": int(checkpoint["format_version"]),
        "model_family": "g30_temporary_base_relative_cap",
        "candidate_id": checkpoint["candidate_id"],
        "deployment_candidate_id": TEMPORARY_CAP_DEPLOYMENT_CANDIDATE_ID,
        "policy_generation": int(checkpoint["policy_generation"]),
        "actor_update_schedule_id": checkpoint["actor_update_schedule_id"],
        "vehicle_action_semantics": checkpoint["vehicle_action_semantics"],
        "semantic_fingerprint_sha256": (
            TEMPORARY_CAP_SEMANTIC_FINGERPRINT_SHA256
        ),
        "manifest": manifest,
    }
    return TEMPORARY_CAP_CHECKPOINT_CONTRACT_VERSION, view
