"""Eval-only adapter for the frozen update-24 temporary-cap candidate."""

from __future__ import annotations

import os
from typing import TYPE_CHECKING, Any, Mapping

from traffic_control.cov2x.contract import (
    TEMPORARY_CAP_ACTOR_UPDATE_SCHEDULE_ID,
    TEMPORARY_CAP_CHECKPOINT_SHA256,
    TEMPORARY_CAP_PROTOCOL_CONFIG_HASH,
    TEMPORARY_CAP_RUNTIME_CANDIDATE_ID,
    checkpoint_sha256,
    load_temporary_cap_contract,
)

if TYPE_CHECKING:
    from traffic_control.cov2x.aliases import ModelAlias

_model_alias: str | None = None
_contract_view: dict[str, Any] | None = None


def configure(model: "ModelAlias") -> None:
    global _model_alias, _contract_view
    mode = os.environ.get("COV2X_MODE", "eval").strip().lower()
    if mode != "eval":
        raise ValueError(
            "cov2x_g30_temp_cap_u24 is eval-only; training is disabled"
        )
    if model.manifest_path is None:
        raise ValueError("temporary-cap candidate manifest is required")

    if checkpoint_sha256(model.checkpoint_path) != TEMPORARY_CAP_CHECKPOINT_SHA256:
        raise ValueError("temporary-cap checkpoint SHA-256 mismatch")

    import torch

    checkpoint = torch.load(
        model.checkpoint_path,
        map_location="cpu",
        weights_only=False,
    )
    _, _contract_view = load_temporary_cap_contract(
        model.checkpoint_path,
        checkpoint,
        manifest_path=model.manifest_path,
    )
    _model_alias = model.alias

    os.environ.update(
        {
            "COV2X_MODE": "eval",
            "COV2X_DELTA_R": "0.5",
            "COV2X_LOCAL_CREDIT_V1": "1",
            "COV2X_TEMPORARY_SPEED_CAP_V1": "1",
            "COV2X_ACTOR_UPDATE_SCHEDULE_ID": (
                TEMPORARY_CAP_ACTOR_UPDATE_SCHEDULE_ID
            ),
            "COV2X_LOCAL_CREDIT_CONFIG_HASH": (
                TEMPORARY_CAP_PROTOCOL_CONFIG_HASH
            ),
            "COV2X_TEMPORARY_SPEED_CAP_CONFIG_HASH": (
                TEMPORARY_CAP_PROTOCOL_CONFIG_HASH
            ),
            "COV2X_ROAD_GAIN": "1.0",
            "COV2X_CLOUD_GAIN": "1.0",
            "COV2X_VEHICLE_GAIN": "1.0",
            "COV2X_EPISODE_RESEED_AFTER_RESTORE": "1",
            "COV2X_MODEL_PATH": str(model.checkpoint_path),
        }
    )
    for name in (
        "COV2X_PARENT_MODEL_PATH",
        "COV2X_LOCAL_CREDIT_PARENT_MODEL_PATH",
        "COV2X_SCREEN_CONFIG_HASH",
        "COV2X_CORRIDOR_CONFIG_HASH",
        "COV2X_TEMPORARY_SPEED_CAP_SCHEDULE_OVERRIDE",
    ):
        os.environ.pop(name, None)


def initialize(payload: Mapping[str, Any]) -> dict[str, Any]:
    if _contract_view is None or _model_alias is None:
        raise RuntimeError("temporary-cap adapter is not configured")
    from traffic_control.cov2x.runtime import mvp_runtime

    response = dict(mvp_runtime.initialize(payload))
    if response.get("candidate_id") != TEMPORARY_CAP_RUNTIME_CANDIDATE_ID:
        raise ValueError("temporary-cap deployment candidate mismatch")
    if response.get("policy_generation") != 24:
        raise ValueError("temporary-cap deployment generation mismatch")
    response["deployment_model_alias"] = _model_alias
    response["checkpoint_sha256"] = TEMPORARY_CAP_CHECKPOINT_SHA256
    return response


def step(payload: Mapping[str, Any]) -> dict[str, Any]:
    from traffic_control.cov2x.runtime import mvp_runtime

    return mvp_runtime.step(payload)


def set_v2x_event_sink(sink: Any | None) -> None:
    from traffic_control.cov2x.runtime import mvp_runtime

    mvp_runtime.set_v2x_event_sink(sink)


def drain_v2x_events() -> dict[str, Any]:
    from traffic_control.cov2x.runtime import mvp_runtime

    return mvp_runtime.drain_v2x_events()
def reset() -> None:
    global _model_alias, _contract_view
    from traffic_control.cov2x.runtime import mvp_runtime

    mvp_runtime.reset_untrained_state()
    mvp_runtime.set_v2x_event_sink(None)
    _model_alias = None
    _contract_view = None


def finish(payload: Mapping[str, Any]) -> Any:
    from traffic_control.cov2x.runtime import mvp_runtime

    try:
        return mvp_runtime.finish(payload)
    finally:
        reset()
