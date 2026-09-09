"""Deployment adapter for the explicit CV Joint V1 inference candidate."""

from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import Any, Mapping
import os

import torch

from traffic_control.cov2x.aliases import ModelAlias
from traffic_control.cov2x import controller as _runtime
from traffic_control.cov2x.model import JointPolicy

MODEL_ALIAS = "cv_joint_v1"
CANDIDATE_ID = "cv_joint_v1_generation_003"
GENERATION = 3
MOVEMENT_COUNT = 199
SCHEMA_VERSION = "cv_joint_v1.0"
CHECKPOINT_SHA256 = "76762b6917598350cc8448c9f82eafcaacc58a5cc581f19aa5ca3e3c5a26feac"
IPPO_SHA256 = "4055ec30bcd03c65572720cea38e51a338f466c351e21124be5fa683e6339449"
CANONICAL_SHA256 = "026a9d2c884a8722a5e85021a11220cd7b25cc46f5b10118850f0bc1118adddc"
_EXPECTED_CHECKPOINT_SCHEMA = "cv_joint_v1.checkpoint.v1"
_FULL_TLS = frozenset(f"demo_{index}" for index in range(1, 21))
_REPO = Path(__file__).resolve().parents[2]

_configured = False
_model: ModelAlias | None = None
_policy: JointPolicy | None = None
_manifest: dict[str, Any] | None = None


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1 << 20), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _load_manifest(path: Path) -> dict[str, Any]:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise ValueError(f"invalid CV Joint V1 manifest: {path}") from exc
    if not isinstance(value, dict):
        raise ValueError("CV Joint V1 manifest must be an object")
    return value


def _validate_deployment_hashes(manifest: Mapping[str, Any]) -> None:
    dependency_hashes = manifest.get("deployment_dependency_hashes")
    if not isinstance(dependency_hashes, Mapping):
        raise ValueError("CV Joint V1 deployment dependency hashes are missing")
    for relative, expected in dependency_hashes.items():
        target = _REPO / str(relative)
        if not target.is_file() or _sha256(target) != str(expected):
            raise ValueError(
                f"CV Joint V1 deployment dependency hash mismatch: {relative}"
            )
    runtime_hashes = manifest.get("deployment_runtime_source_hashes")
    if not isinstance(runtime_hashes, Mapping):
        raise ValueError("CV Joint V1 runtime source hashes are missing")
    for relative, expected in runtime_hashes.items():
        target = _REPO / str(relative)
        if not target.is_file() or _sha256(target) != str(expected):
            raise ValueError(
                f"CV Joint V1 runtime source hash mismatch: {relative}"
            )
    candidate_expected = manifest.get("deployment_candidate_source_sha256")
    bridge_expected = manifest.get("deployment_bridge_source_sha256")
    bridge_path = _REPO / "traffic_control/cov2x/communication/bridge.py"
    if candidate_expected != _sha256(Path(__file__).resolve()):
        raise ValueError("CV Joint V1 candidate source hash mismatch")
    if bridge_expected != _sha256(bridge_path):
        raise ValueError("CV Joint V1 bridge source hash mismatch")


def _load_policy(model: ModelAlias) -> tuple[JointPolicy, dict[str, Any]]:
    if model.alias != MODEL_ALIAS:
        raise ValueError(f"unexpected CV Joint V1 model alias: {model.alias!r}")
    checkpoint = model.checkpoint_path.resolve()
    if not checkpoint.is_file():
        raise FileNotFoundError(f"CV Joint V1 checkpoint does not exist: {checkpoint}")
    if _sha256(checkpoint) != CHECKPOINT_SHA256:
        raise ValueError("CV Joint V1 checkpoint SHA-256 mismatch")
    if model.manifest_path is None:
        raise ValueError("CV Joint V1 manifest is required")
    manifest_path = model.manifest_path.resolve()
    if not manifest_path.is_file():
        raise FileNotFoundError(f"CV Joint V1 manifest does not exist: {manifest_path}")
    manifest = _load_manifest(manifest_path)
    expected_manifest = {
        "schema_version": 1,
        "model_alias": MODEL_ALIAS,
        "candidate_id": CANDIDATE_ID,
        "generation": GENERATION,
        "checkpoint_filename": checkpoint.name,
        "checkpoint_sha256": CHECKPOINT_SHA256,
        "n_movements": MOVEMENT_COUNT,
        "cv_schema_version": SCHEMA_VERSION,
        "ippo_checkpoint_sha256": IPPO_SHA256,
        "canonical_topology_sha256": CANONICAL_SHA256,
        "canonical_topology_filename": "cv_joint_v1_canonical_topology.json",
        "inference_only": True,
    }
    for field, expected in expected_manifest.items():
        if manifest.get(field) != expected:
            raise ValueError(f"CV Joint V1 manifest {field} mismatch")
    _validate_deployment_hashes(manifest)
    try:
        raw = torch.load(checkpoint, map_location="cpu", weights_only=True)
    except Exception as exc:
        raise ValueError("CV Joint V1 checkpoint could not be loaded safely") from exc
    if not isinstance(raw, Mapping):
        raise ValueError("CV Joint V1 checkpoint must be a mapping")
    if raw.get("schema") != _EXPECTED_CHECKPOINT_SCHEMA:
        raise ValueError("CV Joint V1 checkpoint schema mismatch")
    if raw.get("generation") != GENERATION:
        raise ValueError("CV Joint V1 checkpoint generation mismatch")
    if raw.get("n_movements") != MOVEMENT_COUNT:
        raise ValueError("CV Joint V1 checkpoint movement count mismatch")
    metadata = raw.get("metadata")
    if not isinstance(metadata, Mapping):
        raise ValueError("CV Joint V1 checkpoint metadata is missing")
    if manifest.get("movement_catalog") != list(metadata.get("movement_catalog", ())):
        raise ValueError("CV Joint V1 manifest movement catalog mismatch")
    if manifest.get("source_checkpoint_metadata") != dict(metadata):
        raise ValueError("CV Joint V1 manifest checkpoint metadata mismatch")
    if metadata.get("schema_version") != SCHEMA_VERSION:
        raise ValueError("CV Joint V1 checkpoint schema version mismatch")
    if metadata.get("IPPO_SHA256") != IPPO_SHA256:
        raise ValueError("CV Joint V1 checkpoint IPPO identity mismatch")
    catalog = metadata.get("movement_catalog")
    if not isinstance(catalog, list) or len(catalog) != MOVEMENT_COUNT:
        raise ValueError("CV Joint V1 checkpoint movement catalog mismatch")
    canonical = checkpoint.parent / "cv_joint_v1_canonical_topology.json"
    if not canonical.is_file() or _sha256(canonical) != CANONICAL_SHA256:
        raise ValueError("CV Joint V1 canonical topology SHA-256 mismatch")
    state = raw.get("policy_state_dict")
    if not isinstance(state, Mapping):
        raise ValueError("CV Joint V1 policy_state_dict is missing")
    with torch.random.fork_rng(devices=[]):
        policy = JointPolicy(MOVEMENT_COUNT)
    try:
        policy.load_state_dict(state, strict=True)
    except (RuntimeError, ValueError) as exc:
        raise ValueError("CV Joint V1 policy state schema mismatch") from exc
    policy.eval()
    for parameter in policy.parameters():
        parameter.requires_grad_(False)
    return policy, manifest


def configure(model: ModelAlias) -> None:
    global _configured, _model, _policy, _manifest
    if os.environ.get("COV2X_MODE", "eval").strip().lower() != "eval":
        raise ValueError("cv_joint_v1 deployment is inference-only")
    if _configured:
        raise RuntimeError("CV Joint V1 is already configured")
    if model.alias != MODEL_ALIAS:
        raise ValueError(f"unexpected CV Joint V1 model alias: {model.alias!r}")
    if model.checkpoint_path.name != "cv_joint_v1_generation_003.pt":
        raise ValueError("CV Joint V1 checkpoint filename mismatch")
    if model.manifest_path is None or model.manifest_path.name != "cv_joint_v1_manifest.json":
        raise ValueError("CV Joint V1 manifest filename mismatch")
    if _runtime._event_sink is not None:
        _runtime.set_v2x_event_sink(_runtime._event_sink)
    _policy, _manifest = _load_policy(model)
    _model = model
    _configured = True


def _require_full_tls(payload: Mapping[str, Any]) -> None:
    intersections = payload.get("intersections")
    if not isinstance(intersections, Mapping):
        raise ValueError("CV Joint V1 requires all 20 TLS intersections")
    actual = {str(item) for item in intersections}
    if actual != _FULL_TLS:
        raise ValueError("CV Joint V1 requires exactly demo_1..demo_20")


def _decorate(response: Mapping[str, Any]) -> dict[str, Any]:
    result = dict(response)
    result.update({
        "candidate_id": CANDIDATE_ID,
        "deployment_model_alias": MODEL_ALIAS,
        "policy_generation": GENERATION,
        "checkpoint_generation": GENERATION,
        "checkpoint_sha256": CHECKPOINT_SHA256,
        "frozen_ippo_sha256": IPPO_SHA256,
        "v2x_event_export": {
            "schema": "cov2x.v2x.event_batch",
            "schema_version": "1.0",
            "inline_step_field": "v2x",
            "drain_api": "traffic_control.cov2x.drain_v2x_events",
            "message_type": "CVJointV1",
            "original_kind_field": "original_kind",
        },
    })
    return result


def initialize(payload: Mapping[str, Any]) -> dict[str, Any]:
    if not _configured or _policy is None:
        raise RuntimeError("configure CV Joint V1 before initialize")
    if not isinstance(payload, Mapping):
        raise TypeError("CV Joint V1 metadata must be a mapping")
    _require_full_tls(payload)
    period = payload.get("period")
    if period not in _runtime.PERIODS:
        raise ValueError("CV Joint V1 requires a known period")
    _runtime.configure(
        policy=_policy,
        model_seed=0,
        sampling_seed=0,
        training=False,
        period=str(period),
    )
    return _decorate(_runtime.initialize(payload))


def step(payload: Mapping[str, Any]) -> dict[str, Any]:
    if not _configured:
        raise RuntimeError("CV Joint V1 is not configured")
    return _decorate(_runtime.step(payload))


def set_v2x_event_sink(sink: Any | None) -> None:
    _runtime.set_v2x_event_sink(sink)


def drain_v2x_events() -> dict[str, Any]:
    return _runtime.drain_v2x_events()


def collected() -> dict[str, Any]:
    return _runtime.collected()


def reset() -> None:
    global _configured, _model, _policy, _manifest
    if _runtime._state.get("active") or _runtime._state.get("ippo_initialized"):
        _runtime._cleanup()
    _runtime.set_v2x_event_sink(None)
    _configured = False
    _model = None
    _policy = None
    _manifest = None


def finish(payload: Mapping[str, Any]) -> dict[str, Any]:
    global _configured, _model, _policy, _manifest
    try:
        return _runtime.finish(payload)
    finally:
        _runtime.set_v2x_event_sink(None)
        _configured = False
        _model = None
        _policy = None
        _manifest = None
