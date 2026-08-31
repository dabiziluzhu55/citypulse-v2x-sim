"""Lazy Protocol 2.0 facade for versioned CoV2X candidates.

The stable product entry point stays traffic_control.cov2x. Candidate
selection, checkpoint validation, and runtime-specific configuration live
behind the dispatcher so legacy EP12 and the frozen update-24 candidate do not
share loaders or action semantics.
"""

from __future__ import annotations

from typing import Any


def __getattr__(name: str) -> Any:
    if name in {
        "initialize",
        "step",
        "finish",
        "set_v2x_event_sink",
        "drain_v2x_events",
    }:
        from traffic_control.cov2x.dispatch import (
            drain_v2x_events,
            finish,
            initialize,
            set_v2x_event_sink,
            step,
        )

        return {
            "initialize": initialize,
            "step": step,
            "finish": finish,
            "set_v2x_event_sink": set_v2x_event_sink,
            "drain_v2x_events": drain_v2x_events,
        }[name]
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")


__all__ = ["initialize", "step", "finish"]
