"""Synchronous movement-local metrics and phase safety for Phase A arms."""
from __future__ import annotations

from copy import deepcopy
from hashlib import sha256
from pathlib import Path
from threading import RLock
from typing import Any, Mapping
import json
import math
import os
import tempfile

from algorithms.cov2x.phase_history_audit import (
    PhaseHistoryCrossingObserver,
    TRUE_RED_ENTRY,
    TRUE_RED_YELLOW_ENTRY,
    observer_integrity_failures,
)
from algorithms.cov2x.vehicle.movement_corridor import MovementApproachCorridor


def _mapping(value: object) -> Mapping[str, Any]:
    return value if isinstance(value, Mapping) else {}


def _finite_metric(vehicle: Mapping[str, Any], name: str) -> float:
    traffic = _mapping(vehicle.get("traffic"))
    aliases = {
        "time_loss": ("time_loss_s", "time_loss"),
        "waiting": ("accumulated_waiting_time_s", "waiting_time_s", "waiting_time"),
    }
    for key in aliases[name]:
        value = traffic.get(key)
        if value is not None:
            number = float(value)
            if not math.isfinite(number):
                raise ValueError(f"vehicle traffic metric {key} must be finite")
            return max(0.0, number)
    return 0.0


class PhaseAHorizonMetrics:
    """Accumulate paired deltas while membership is route-resolved locally."""

    def __init__(
        self, metadata: Mapping[str, Any], *, treatment: Mapping[str, Any]
    ) -> None:
        self.metadata = deepcopy(dict(metadata))
        self.treatment = deepcopy(dict(treatment))
        opportunity = dict(self.treatment.get("opportunity") or {})
        self.target_vehicle_id = str(opportunity.get("vehicle_id", ""))
        self.intersection_id = str(opportunity.get("intersection_id", ""))
        self.movement_id = str(opportunity.get("movement_id", ""))
        self.start_s = float(self.treatment["simulation_time"])
        self.horizon_s = float(self.treatment.get("horizon_s", 20.0))
        self.end_s = self.start_s + self.horizon_s
        self.corridor = MovementApproachCorridor(metadata)
        self.vehicle_types = {
            str(key): dict(value or {})
            for key, value in _mapping(metadata.get("vehicle_types")).items()
        }
        self.previous: dict[str, dict[str, float]] = {}
        self.last_time_s: float | None = None
        self.movement_local_time_loss_s = 0.0
        self.movement_waiting_s = 0.0
        self.network_time_loss_s = 0.0
        self.movement_stop_count = 0
        self.minimum_gap_breaches = 0
        self.hard_braking_events = 0
        self.target_trajectory: list[dict[str, Any]] = []
        self.command_audit: list[dict[str, Any]] = []
        self._seen_action_steps: set[int] = set()
        self.frames_in_window = 0

    def _is_local(self, vehicle: Mapping[str, Any]) -> bool:
        resolved = self.corridor.resolve(self.intersection_id, vehicle)
        return bool(
            resolved.resolved
            and resolved.resolved_movement_id == self.movement_id
        )

    @staticmethod
    def _vehicle_state(vehicle: Mapping[str, Any]) -> dict[str, float]:
        motion = _mapping(vehicle.get("motion"))
        driving = _mapping(vehicle.get("driving_events"))
        return {
            "time_loss": _finite_metric(vehicle, "time_loss"),
            "waiting": _finite_metric(vehicle, "waiting"),
            "speed": float(motion.get("speed_mps", 0.0) or 0.0),
            "hard_braking": float(driving.get("hard_braking_total", 0) or 0),
        }

    def on_frame(self, frame: Mapping[str, Any]) -> None:
        now = float(frame.get("simulation_time", 0.0) or 0.0)
        vehicles = {
            str(key): dict(value)
            for key, value in _mapping(frame.get("vehicles")).items()
            if isinstance(value, Mapping)
        }
        current = {
            vehicle_id: self._vehicle_state(vehicle)
            for vehicle_id, vehicle in vehicles.items()
        }
        in_window = self.start_s - 1e-9 <= now <= self.end_s + 1e-9
        if in_window:
            self.frames_in_window += 1
            previous_actions = _mapping(frame.get("previous_action_results"))
            raw_action_step = previous_actions.get("step_id")
            if (
                isinstance(raw_action_step, int)
                and not isinstance(raw_action_step, bool)
                and raw_action_step not in self._seen_action_steps
            ):
                result = _mapping(
                    _mapping(previous_actions.get("vehicles")).get(
                        self.target_vehicle_id
                    )
                )
                if result:
                    self._seen_action_steps.add(raw_action_step)
                    self.command_audit.append(
                        {
                            "step_id": raw_action_step,
                            "requested": deepcopy(dict(_mapping(result.get("requested")))),
                            "actual_speed_mps": result.get("actual_speed_mps"),
                            "speed_status": result.get("speed_status"),
                        }
                    )
            for vehicle_id, state in current.items():
                previous = self.previous.get(vehicle_id)
                if previous is None:
                    continue
                time_loss_delta = max(0.0, state["time_loss"] - previous["time_loss"])
                waiting_delta = max(0.0, state["waiting"] - previous["waiting"])
                hard_delta = max(0, int(state["hard_braking"] - previous["hard_braking"]))
                self.network_time_loss_s += time_loss_delta
                if self._is_local(vehicles[vehicle_id]):
                    self.movement_local_time_loss_s += time_loss_delta
                    self.movement_waiting_s += waiting_delta
                    self.hard_braking_events += hard_delta
                    if previous["speed"] > 0.1 and state["speed"] <= 0.1:
                        self.movement_stop_count += 1

            target = vehicles.get(self.target_vehicle_id)
            if target is not None:
                motion = _mapping(target.get("motion"))
                location = _mapping(target.get("location"))
                position = _mapping(target.get("position"))
                next_signal = _mapping(target.get("next_signal"))
                gap = target.get("leader_gap_m")
                type_id = str(target.get("type_id", ""))
                minimum_gap = float(
                    self.vehicle_types.get(type_id, {}).get("min_gap_m", 2.5)
                    or 2.5
                )
                breach = gap is not None and float(gap) + 1e-9 < minimum_gap
                self.minimum_gap_breaches += int(breach)
                self.target_trajectory.append(
                    {
                        "frame_id": int(frame.get("frame_id", -1)),
                        "simulation_time": now,
                        "vehicle_id": self.target_vehicle_id,
                        "x_m": position.get("x_m"),
                        "y_m": position.get("y_m"),
                        "road_id": str(location.get("road_id", "")),
                        "lane_id": str(location.get("lane_id", "")),
                        "route_index": location.get("route_index"),
                        "speed_mps": float(motion.get("speed_mps", 0.0) or 0.0),
                        "acceleration_mps2": float(
                            motion.get("acceleration_mps2", 0.0) or 0.0
                        ),
                        "leader_gap_m": None if gap is None else float(gap),
                        "minimum_gap_m": minimum_gap,
                        "minimum_gap_breach": breach,
                        "signal_state": str(next_signal.get("state", "")),
                        "signal_distance_m": next_signal.get("distance_m"),
                        "movement_local": self._is_local(target),
                    }
                )
        self.previous = current
        self.last_time_s = now

    def summary(self) -> dict[str, Any]:
        return {
            "treatment_time_s": self.start_s,
            "horizon_s": self.horizon_s,
            "horizon_end_s": self.end_s,
            "frames_in_window": self.frames_in_window,
            "window_complete": bool(
                self.last_time_s is not None
                and self.last_time_s + 1e-9 >= self.end_s
            ),
            "movement_local_time_loss_s": self.movement_local_time_loss_s,
            "movement_waiting_s": self.movement_waiting_s,
            "movement_stop_count": self.movement_stop_count,
            "network_time_loss_s": self.network_time_loss_s,
            "hard_braking_events": self.hard_braking_events,
            "minimum_gap_breaches": self.minimum_gap_breaches,
            "command_audit": deepcopy(self.command_audit),
            "target_trajectory": deepcopy(self.target_trajectory),
        }


def _atomic_write(path: Path, payload: Mapping[str, Any]) -> None:
    if path.exists():
        raise FileExistsError(f"Phase A observer summary already exists: {path}")
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary: Path | None = None
    try:
        with tempfile.NamedTemporaryFile(
            mode="w",
            encoding="utf-8",
            dir=path.parent,
            prefix=f".{path.name}.",
            suffix=".tmp",
            delete=False,
        ) as handle:
            temporary = Path(handle.name)
            json.dump(dict(payload), handle, sort_keys=True, indent=2)
            handle.write("\n")
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary, path)
    except BaseException:
        if temporary is not None and temporary.exists():
            temporary.unlink()
        raise


_lock = RLock()
_configuration: dict[str, Any] | None = None
_metrics: PhaseAHorizonMetrics | None = None
_phase: PhaseHistoryCrossingObserver | None = None
_completed: list[dict[str, Any]] = []


def configure_run(
    *, treatment: Mapping[str, Any], summary_path: str | Path
) -> None:
    global _configuration
    with _lock:
        _configuration = {
            "treatment": deepcopy(dict(treatment)),
            "summary_path": str(Path(summary_path)),
        }


def initialize(metadata: Mapping[str, Any]) -> None:
    global _metrics, _phase
    with _lock:
        if _configuration is None:
            raise RuntimeError("Phase A observer is not configured")
        _metrics = PhaseAHorizonMetrics(
            metadata, treatment=_configuration["treatment"]
        )
        step_length = float(
            os.environ.get("COV2X_PHASE_HISTORY_STEP_LENGTH", "0.05") or 0.05
        )
        _phase = PhaseHistoryCrossingObserver(
            metadata, step_length_s=step_length
        )


def on_frame(frame: Mapping[str, Any]) -> None:
    with _lock:
        phase = _phase
        metrics = _metrics
    if phase is None or metrics is None:
        raise RuntimeError("Phase A observer is not initialized")
    phase.on_frame(frame)
    metrics.on_frame(frame)


def finish(summary: Mapping[str, Any]) -> None:
    global _phase, _metrics
    with _lock:
        phase = _phase
        metrics = _metrics
        configuration = deepcopy(_configuration)
        _phase = None
        _metrics = None
    if phase is None or metrics is None or configuration is None:
        raise RuntimeError("Phase A observer is not initialized")
    phase_result = phase.finish(summary)
    horizon = metrics.summary()
    start = float(horizon["treatment_time_s"])
    end = float(horizon["horizon_end_s"])
    window_crossings = [
        event
        for event in phase_result.get("crossing_events", ())
        if start - 1e-9
        <= float(event.get("crossing_event_time_s", -1.0))
        <= end + 1e-9
    ]
    true_red = sum(
        event.get("crossing_class") == TRUE_RED_ENTRY
        for event in window_crossings
    )
    red_yellow = sum(
        event.get("crossing_class") == TRUE_RED_YELLOW_ENTRY
        for event in window_crossings
    )
    result = {
        "episode_id": phase_result.get("episode_id"),
        "horizon_metrics": horizon,
        "phase_history": phase_result,
        "window_crossing_events": window_crossings,
        "true_red_entry": true_red,
        "true_red_yellow_entry": red_yellow,
        "phase_integrity_failures": observer_integrity_failures(phase_result),
    }
    canonical = json.dumps(
        result,
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=False,
        allow_nan=False,
    ).encode("utf-8")
    result["canonical_sha256"] = sha256(canonical).hexdigest()
    _atomic_write(Path(configuration["summary_path"]), result)
    with _lock:
        _completed.append(result)


def take_completed(episode_id: str | None = None) -> dict[str, Any] | None:
    with _lock:
        if episode_id is None:
            return _completed.pop(0) if _completed else None
        for index, result in enumerate(_completed):
            if result.get("episode_id") == episode_id:
                return _completed.pop(index)
    return None


def reset_completed() -> None:
    with _lock:
        _completed.clear()
