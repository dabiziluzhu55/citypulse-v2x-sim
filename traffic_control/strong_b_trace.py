"""Algorithm-side Strong-B adapter with auditable action-trace telemetry.

This wrapper deliberately delegates every signal decision to the frozen
``MaxPressureController``.  It exists only so disturbance experiments can
collect the same canonical trace fields as :mod:`safe_max_pressure` without
editing the baseline controller.
"""

from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path
from typing import Any, Mapping

from traffic_control.max_pressure import MaxPressureController
from traffic_control.protocol import (
    finish_response,
    initialize_response,
    signals_from_phase_map,
    step_response,
)


class StrongBTraceController:
    def __init__(self, metadata: Mapping[str, Any]) -> None:
        self._metadata = dict(metadata)
        self._controller = MaxPressureController(dict(metadata))
        self._hasher = hashlib.sha256()
        self._count = 0
        self._initialized = True

    def compute_actions(self, observation: Mapping[str, Any]) -> dict[str, int | None]:
        if not self._initialized:
            raise RuntimeError("Strong B trace adapter is not initialized")
        requested = self._controller.compute_actions(dict(observation))
        trace = {
            "step_id": observation.get("step_id"),
            "requested": requested,
            "final": requested,
        }
        encoded = json.dumps(
            trace,
            sort_keys=True,
            separators=(",", ":"),
            ensure_ascii=False,
        )
        self._hasher.update(encoded.encode("utf-8") + b"\n")
        self._count += 1
        return dict(requested)

    def diagnostics(self) -> dict[str, Any]:
        return {
            "algorithm": "strong_b_trace",
            "signal_mode": "max_pressure",
            "strong_reference": "traffic_control.max_pressure.MaxPressureController",
            "episode_id": str(self._metadata.get("episode_id", "")),
            "steps": self._count,
            "signal_trace_sha256": self._hasher.hexdigest(),
            "signal_trace_count": self._count,
            "vehicle_actions": {},
            "committed_spat_published": False,
            "vehicle_vtc_eligible": False,
            "health_state": "HEALTHY",
        }

    def finish(self) -> dict[str, Any]:
        diagnostics = self.diagnostics()
        output_path = os.environ.get("STRONG_B_DIAGNOSTICS_PATH", "").strip()
        if output_path:
            path = Path(output_path)
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(
                json.dumps(diagnostics, ensure_ascii=False, indent=2, sort_keys=True),
                encoding="utf-8",
            )
        self._initialized = False
        return diagnostics


_controller: StrongBTraceController | None = None
_last_diagnostics: dict[str, Any] | None = None


def initialize(payload: dict[str, Any]) -> dict[str, Any]:
    global _controller, _last_diagnostics
    _controller = StrongBTraceController(payload)
    _last_diagnostics = None
    return initialize_response(episode_id=str(payload.get("episode_id", "")))


def step(payload: dict[str, Any]) -> dict[str, Any]:
    if _controller is None:
        raise RuntimeError("Strong B trace adapter is not initialized")
    actions = _controller.compute_actions(payload)
    return step_response(
        episode_id=str(payload.get("episode_id", "")),
        step_id=payload.get("step_id"),
        signals=signals_from_phase_map(actions),
        vehicles={},
    )


def finish(payload: dict[str, Any]) -> dict[str, Any]:
    global _controller, _last_diagnostics
    if _controller is None:
        return finish_response(already_finished=True)
    _last_diagnostics = _controller.finish()
    _controller = None
    return finish_response()


def diagnostics() -> dict[str, Any]:
    if _controller is not None:
        return _controller.diagnostics()
    return dict(_last_diagnostics or {
        "algorithm": "strong_b_trace",
        "signal_mode": "max_pressure",
        "vehicle_actions": {},
    })


__all__ = ["StrongBTraceController", "diagnostics", "finish", "initialize", "step"]
