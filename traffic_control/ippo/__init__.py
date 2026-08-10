"""Lazy Protocol 2.0 exports for IPPO.

Keep package import free of torch so backend scenario resolution can read
aliases without loading the controller.
"""

from __future__ import annotations

from typing import Any


def __getattr__(name: str) -> Any:
    if name in {"initialize", "step", "finish"}:
        from traffic_control.ippo.controller import finish, initialize, step

        exports = {
            "initialize": initialize,
            "step": step,
            "finish": finish,
        }
        return exports[name]
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")


__all__ = ["initialize", "step", "finish"]
