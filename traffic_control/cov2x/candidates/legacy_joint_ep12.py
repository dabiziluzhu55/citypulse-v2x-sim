"""Adapter for the immutable legacy EP12 deployment candidate."""

from __future__ import annotations

import os
from typing import TYPE_CHECKING, Any, Mapping

if TYPE_CHECKING:
    from traffic_control.cov2x.aliases import ModelAlias


def configure(model: "ModelAlias") -> None:
    os.environ.setdefault("COV2X_MODE", "eval")
    os.environ.setdefault("COV2X_SIGNAL_MODE", "learned")
    os.environ.setdefault("COV2X_CLOUD_MODE", "learned")
    os.environ.setdefault("COV2X_VEHICLE_MODE", "learned")
    os.environ["COV2X_MODEL_PATH"] = str(model.checkpoint_path)


def initialize(payload: Mapping[str, Any]) -> dict[str, Any]:
    from traffic_control.cov2x import controller

    return controller.initialize(payload)


def step(payload: Mapping[str, Any]) -> dict[str, Any]:
    from traffic_control.cov2x import controller

    return controller.step(payload)


def finish(payload: Mapping[str, Any]) -> Any:
    from traffic_control.cov2x import controller

    return controller.finish(payload)
