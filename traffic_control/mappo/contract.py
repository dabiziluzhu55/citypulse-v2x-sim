"""Checkpoint contract for deployable MAPPO cooperative checkpoints.

MAPPO checkpoints are self-describing (format version 2): the top-level dict
carries ``metadata`` and ``policy_state_dict``.  This module pins the
deployment contract shared with the fixed 20-slot IPPO-v8 identity schema:
controlled intersections must be a subset of the checkpoint training IDs and
the local observation must be the 132-dim 20-slot IPPO-v8 schema.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any, Mapping

import torch

from traffic_control.ippo.identity import IDENTITY_SLOT_IDS
from traffic_control.ippo.model import PHASE_FEATURES as IPPO_PHASE_FEATURES

CHECKPOINT_CONTRACT_VERSION = 2
SUPPORTED_FORMAT_VERSIONS = frozenset({2})

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
    view = dict(metadata)
    view["checkpoint_format_version"] = format_version
    view["checkpoint_path"] = str(Path(checkpoint_path).resolve())
    return CHECKPOINT_CONTRACT_VERSION, view


def _require(view: Mapping[str, Any], name: str) -> Any:
    if name not in view or view[name] is None:
        raise ValueError(f"MAPPO checkpoint metadata is missing {name!r}")
    return view[name]


def validate_contract(
    view: Mapping[str, Any],
    *,
    intersection_ids: Any,
    obs_dim: int,
    action_interval: float,
    max_green_factor: float,
    effective_demand_enabled: bool,
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
