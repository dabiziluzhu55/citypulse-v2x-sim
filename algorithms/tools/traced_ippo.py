"""Wrap traffic_control.ippo.controller and record every signal decision.

Used by the L3 regression harness.  The wrapper is a drop-in Protocol 2.0
local algorithm module: it forwards to the real controller and appends
``{step_id, simulation_time, signals}`` to the file in ``IPPO_TRACE_OUTPUT``.
The decision trace is written after every step and once more at finish.
"""

from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any, Mapping

from traffic_control.ippo import controller as _real

_OUTPUT_ENV = "IPPO_TRACE_OUTPUT"
_trace: list[dict[str, Any]] = []


def _write() -> None:
    value = os.environ.get(_OUTPUT_ENV, "").strip()
    if not value:
        return
    path = Path(value)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(_trace, ensure_ascii=False, indent=2), encoding="utf-8"
    )


def initialize(payload: Mapping[str, Any]) -> dict[str, Any]:
    return _real.initialize(dict(payload))


def step(payload: Mapping[str, Any]) -> dict[str, Any]:
    response = _real.step(dict(payload))
    signals = response.get("actions", {}).get("signals", {})
    _trace.append(
        {
            "step_id": payload.get("step_id"),
            "simulation_time": payload.get("simulation_time"),
            "signals": signals,
        }
    )
    _write()
    return response


def finish(payload: Mapping[str, Any]) -> dict[str, Any]:
    try:
        return _real.finish(dict(payload))
    finally:
        _write()
