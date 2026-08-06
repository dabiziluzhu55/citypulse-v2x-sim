"""Local-algorithm metadata recorder used by M0 tooling.

Implements the Protocol 2.0 local-algorithm contract (initialize/step/finish)
so a short SimulationManager session can be run with
``algorithm_module=\"tools.metadata_recorder\"`` to capture the exact initialize
metadata (per-intersection topology facts) that the controller later validates.
"""

from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any, Mapping

_OUTPUT_ENV = "IPPO_METADATA_OUTPUT"
_WRITTEN = False


def _output_path() -> Path:
    value = os.environ.get(_OUTPUT_ENV, "").strip()
    if not value:
        raise RuntimeError(f"{_OUTPUT_ENV} is not set")
    return Path(value)


def initialize(payload: Mapping[str, Any]) -> dict[str, Any]:
    global _WRITTEN
    path = _output_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    _WRITTEN = True
    return {
        "protocol_version": "2.0",
        "episode_id": str(payload["episode_id"]),
        "ready": True,
    }


def step(payload: Mapping[str, Any]) -> dict[str, Any]:
    return {
        "protocol_version": "2.0",
        "episode_id": str(payload["episode_id"]),
        "step_id": payload["step_id"],
        "actions": {"signals": {}, "vehicles": {}},
    }


def finish(payload: Mapping[str, Any]) -> dict[str, Any]:
    return {"ok": True}
