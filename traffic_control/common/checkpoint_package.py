"""Immutable v3-to-v4 multiscenario checkpoint packaging."""

from __future__ import annotations

from copy import deepcopy
import hashlib
import json
import os
from pathlib import Path
from typing import Any, Mapping

import torch

from traffic_control.common.environment_contract import (
    JOINT_PERIODS,
    MULTISCENARIO_ENVIRONMENT_CONTRACT_VERSION,
    upgrade_environment_contract_v4,
)


CAPABILITY_SCHEMA_VERSION = 1
SUPPORTED_ALGORITHMS = frozenset({"ippo", "mappo"})


def file_sha256(path: str | Path) -> str:
    """Return the SHA-256 digest of file bytes."""
    digest = hashlib.sha256()
    with Path(path).open("rb") as stream:
        for chunk in iter(lambda: stream.read(1 << 20), b""):
            digest.update(chunk)
    return digest.hexdigest()


def state_dict_sha256(state_dict: Mapping[str, torch.Tensor]) -> str:
    """Return a deterministic digest of tensor names, schemas, and bytes."""
    if not isinstance(state_dict, Mapping) or not state_dict:
        raise ValueError("checkpoint policy state_dict must be a non-empty mapping")
    digest = hashlib.sha256()
    for name, tensor in sorted(state_dict.items()):
        if not isinstance(tensor, torch.Tensor):
            raise TypeError(f"policy state {name!r} must be a tensor")
        value = tensor.detach().cpu().contiguous()
        digest.update(str(name).encode("utf-8"))
        digest.update(b"\0")
        digest.update(str(value.dtype).encode("ascii"))
        digest.update(b"\0")
        digest.update(json.dumps(tuple(value.shape)).encode("ascii"))
        digest.update(b"\0")
        digest.update(value.view(torch.uint8).numpy().tobytes())
    return digest.hexdigest()


def _source_contract(
    checkpoint_path: Path,
    checkpoint: Mapping[str, Any],
    *,
    algorithm: str,
) -> tuple[Mapping[str, Any], Mapping[str, torch.Tensor], str]:
    if algorithm == "ippo":
        from traffic_control.ippo.contract import load_contract

        version, contract = load_contract(checkpoint_path, checkpoint)
        if version != 3:
            raise ValueError(
                "IPPO multiscenario packaging requires an inline v3 checkpoint"
            )
        state_key = "model_state_dict"
    else:
        from traffic_control.mappo.contract import load_contract

        version, view = load_contract(checkpoint_path, checkpoint)
        if version != 3:
            raise ValueError(
                "MAPPO multiscenario packaging requires a format v3 checkpoint"
            )
        contract = view.get("environment_contract")
        if not isinstance(contract, Mapping):
            raise ValueError("MAPPO v3 checkpoint has no environment contract")
        state_key = "policy_state_dict"

    state_dict = checkpoint.get(state_key)
    if not isinstance(state_dict, Mapping):
        raise ValueError(f"checkpoint is missing {state_key}")
    return contract, state_dict, state_key


def package_checkpoint_payload(
    checkpoint_path: str | Path,
    checkpoint: Mapping[str, Any],
    *,
    algorithm: str,
    supported_presets: Mapping[str, Any],
    source_checkpoint_sha256: str,
    model_id: str | None = None,
) -> dict[str, Any]:
    """Return a v4 package payload while preserving every tensor byte."""
    normalized_algorithm = str(algorithm).strip().lower()
    if normalized_algorithm not in SUPPORTED_ALGORITHMS:
        raise ValueError(
            f"algorithm must be one of {sorted(SUPPORTED_ALGORITHMS)}"
        )
    if not isinstance(checkpoint, Mapping):
        raise ValueError("checkpoint payload must be a mapping")

    v3_contract, state_dict, state_key = _source_contract(
        Path(checkpoint_path),
        checkpoint,
        algorithm=normalized_algorithm,
    )
    weights_sha256 = state_dict_sha256(state_dict)
    v4_contract = upgrade_environment_contract_v4(
        v3_contract,
        supported_presets=supported_presets,
    )
    resolved_model_id = (
        str(model_id).strip()
        if model_id is not None
        else f"{normalized_algorithm}-{weights_sha256[:16]}-v4"
    )
    if not resolved_model_id:
        raise ValueError("model_id must not be empty")

    packaged = deepcopy(dict(checkpoint))
    if normalized_algorithm == "ippo":
        packaged["checkpoint_contract_version"] = (
            MULTISCENARIO_ENVIRONMENT_CONTRACT_VERSION
        )
    else:
        packaged["checkpoint_format_version"] = (
            MULTISCENARIO_ENVIRONMENT_CONTRACT_VERSION
        )
    packaged["environment_contract"] = v4_contract
    packaged["capability_schema_version"] = CAPABILITY_SCHEMA_VERSION
    packaged["model_id"] = resolved_model_id
    packaged["weights_sha256"] = weights_sha256
    packaged["source_checkpoint_sha256"] = str(source_checkpoint_sha256)
    packaged["performance_validation"] = {
        period: {
            preset_id: "unvalidated"
            for preset_id in v4_contract["supported_presets"]
        }
        for period in JOINT_PERIODS
    }

    packaged_state = packaged.get(state_key)
    if not isinstance(packaged_state, Mapping):
        raise AssertionError("packaged checkpoint lost its policy state")
    if state_dict_sha256(packaged_state) != weights_sha256:
        raise AssertionError("checkpoint packaging changed policy tensor bytes")
    return packaged


def _atomic_torch_save(payload: Mapping[str, Any], destination: Path) -> None:
    temporary = destination.with_name(
        f"{destination.name}.tmp-{os.getpid()}"
    )
    try:
        with temporary.open("wb") as stream:
            torch.save(dict(payload), stream)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, destination)
    finally:
        temporary.unlink(missing_ok=True)


def _atomic_json_save(payload: Mapping[str, Any], destination: Path) -> None:
    temporary = destination.with_name(
        f"{destination.name}.tmp-{os.getpid()}"
    )
    try:
        with temporary.open("w", encoding="utf-8") as stream:
            json.dump(
                dict(payload),
                stream,
                ensure_ascii=False,
                indent=2,
                sort_keys=True,
            )
            stream.write("\n")
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, destination)
    finally:
        temporary.unlink(missing_ok=True)


def manifest_path(checkpoint_path: str | Path) -> Path:
    path = Path(checkpoint_path)
    return path.with_suffix(path.suffix + ".manifest.json")


def package_checkpoint(
    source: str | Path,
    output: str | Path,
    *,
    algorithm: str,
    supported_presets: Mapping[str, Any],
    model_id: str | None = None,
    force: bool = False,
) -> dict[str, Any]:
    """Write one v4 checkpoint plus a non-self-referential hash manifest."""
    source_path = Path(source).expanduser().resolve()
    output_path = Path(output).expanduser().resolve()
    output_manifest = manifest_path(output_path)
    if not source_path.is_file():
        raise FileNotFoundError(f"checkpoint does not exist: {source_path}")
    if source_path == output_path:
        raise ValueError("v4 package output must differ from source checkpoint")
    if not force and (output_path.exists() or output_manifest.exists()):
        raise FileExistsError(
            f"v4 package output already exists: {output_path}"
        )
    output_path.parent.mkdir(parents=True, exist_ok=True)

    source_hash = file_sha256(source_path)
    checkpoint = torch.load(
        source_path,
        map_location="cpu",
        weights_only=False,
    )
    packaged = package_checkpoint_payload(
        source_path,
        checkpoint,
        algorithm=algorithm,
        supported_presets=supported_presets,
        source_checkpoint_sha256=source_hash,
        model_id=model_id,
    )
    _atomic_torch_save(packaged, output_path)

    reloaded = torch.load(
        output_path,
        map_location="cpu",
        weights_only=False,
    )
    state_key = (
        "model_state_dict"
        if str(algorithm).strip().lower() == "ippo"
        else "policy_state_dict"
    )
    reloaded_weights = state_dict_sha256(reloaded[state_key])
    if reloaded_weights != packaged["weights_sha256"]:
        output_path.unlink(missing_ok=True)
        raise AssertionError("written v4 package changed policy tensor bytes")

    manifest = {
        "capability_schema_version": CAPABILITY_SCHEMA_VERSION,
        "algorithm": str(algorithm).strip().lower(),
        "model_id": packaged["model_id"],
        "source_checkpoint_sha256": source_hash,
        "checkpoint_package_sha256": file_sha256(output_path),
        "weights_sha256": reloaded_weights,
        "contract_version": "v4_multiscenario",
        "contract_hash": packaged["environment_contract"]["sha256"],
    }
    _atomic_json_save(manifest, output_manifest)
    return manifest
