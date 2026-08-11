"""Lazy Protocol 2.0 exports for CoV2X（车路云协同算法）。

独立算法板块：车端/路端/云端三端联合 CTDE 控制器（EP12 初版），
与 ``traffic_control.ippo`` 保持一致的扁平结构和入口契约。

Keep package import free of torch so backend scenario resolution can read
aliases without loading the controller.
"""

from __future__ import annotations

import os
from pathlib import Path
from typing import Any

_DEFAULT_JOINT_MODEL_FILENAME = "cov2x_joint_ep12.pt"


def _prepare_joint_env() -> None:
    """Set deployment-safe joint defaults without overriding explicit env vars."""
    os.environ.setdefault("COV2X_MODE", "eval")
    os.environ.setdefault("COV2X_SIGNAL_MODE", "learned")
    os.environ.setdefault("COV2X_CLOUD_MODE", "learned")
    os.environ.setdefault("COV2X_VEHICLE_MODE", "learned")
    alias = os.environ.get("COV2X_MODEL_ALIAS")
    if alias:
        from traffic_control.cov2x.aliases import resolve_model_path

        os.environ["COV2X_MODEL_PATH"] = str(resolve_model_path(alias))
    os.environ.setdefault(
        "COV2X_MODEL_PATH",
        str(
            Path(__file__).resolve().parent
            / "models"
            / _DEFAULT_JOINT_MODEL_FILENAME
        ),
    )


def __getattr__(name: str) -> Any:
    if name in {"initialize", "step", "finish"}:
        _prepare_joint_env()
        from traffic_control.cov2x.controller import finish, initialize, step

        exports = {
            "initialize": initialize,
            "step": step,
            "finish": finish,
        }
        return exports[name]
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")


__all__ = ["initialize", "step", "finish"]
