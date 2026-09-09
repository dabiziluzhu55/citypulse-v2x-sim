"""Offline rollout buffers and strict episode finalization for CV Joint V1."""
from __future__ import annotations

import copy
import math
import numbers
from collections.abc import Mapping, Sequence
from typing import Any

import numpy as np

from ..contracts import (
    CALLBACK_INTERVAL,
    CONTEXT_DIM,
    HORIZON,
    LANE_SLOTS,
    MOVEMENT_DIM,
    PERIODS,
    SCHEMA_VERSION,
    VEHICLE_DIM,
)


_REQUIRED_RECEIPT_FIELDS = (
    "actual_speed_mps",
    "actual_lane_index",
    "speed_status",
    "lane_change_status",
)


def _clone(value: Any) -> Any:
    """Copy rows without retaining mutable callback-owned arrays."""
    if isinstance(value, np.ndarray):
        return value.copy()
    if isinstance(value, Mapping):
        return {key: _clone(item) for key, item in value.items()}
    if isinstance(value, list):
        return [_clone(item) for item in value]
    if isinstance(value, tuple):
        return tuple(_clone(item) for item in value)
    return copy.deepcopy(value)


def _finite(value: Any, label: str) -> float:
    try:
        number = float(value)
    except (TypeError, ValueError) as exc:
        raise ValueError(f"{label} must be finite") from exc
    if not math.isfinite(number):
        raise ValueError(f"{label} must be finite")
    return number


def _integer(value: Any, label: str) -> int:
    if isinstance(value, (bool, np.bool_)) or not isinstance(
        value, numbers.Integral
    ):
        raise ValueError(f"{label} must be an integer")
    return int(value)


def _boolean(value: Any, label: str) -> bool:
    if not isinstance(value, (bool, np.bool_)):
        raise ValueError(f"{label} must be boolean")
    return bool(value)


def _vector(row: Mapping[str, Any], names: tuple[str, ...], size: int,
            label: str) -> np.ndarray:
    value = next((row[name] for name in names if name in row), None)
    if not any(name in row for name in names):
        raise ValueError(f"{label} is missing")
    try:
        result = np.asarray(value, dtype=np.float32)
    except (TypeError, ValueError) as exc:
        raise ValueError(f"{label} must be a finite vector") from exc
    if result.shape != (size,) or not np.isfinite(result).all():
        raise ValueError(f"{label} must be a finite vector of length {size}")
    return result.astype(np.float32, copy=True)


def _identity(row: dict[str, Any], episode_id: str, policy_version: str) -> None:
    if row.get("schema_version", SCHEMA_VERSION) != SCHEMA_VERSION:
        raise ValueError("row schema_version conflicts with episode schema")
    if row.get("episode_id", episode_id) != episode_id:
        raise ValueError("row episode_id conflicts with episode identity")
    if row.get("policy_version", policy_version) != policy_version:
        raise ValueError("row policy_version conflicts with episode policy")
    row["schema_version"] = SCHEMA_VERSION
    row["episode_id"] = episode_id
    row["policy_version"] = policy_version


def _request_key(item: Mapping[str, Any]) -> tuple[int, str]:
    return int(item["step_id"]), str(item["vehicle_id"])


def _is_implicit_release(value: Any) -> bool:
    return isinstance(value, Mapping) and not value


def _same_value(left: Any, right: Any) -> bool:
    if isinstance(left, Mapping) and isinstance(right, Mapping):
        return (
            set(left) == set(right)
            and all(_same_value(left[key], right[key]) for key in left)
        )
    if isinstance(left, np.ndarray) or isinstance(right, np.ndarray):
        try:
            return bool(np.array_equal(left, right))
        except (TypeError, ValueError):
            return False
    if isinstance(left, (list, tuple)) and isinstance(right, (list, tuple)):
        return len(left) == len(right) and all(
            _same_value(a, b) for a, b in zip(left, right)
        )
    try:
        result = left == right
        return bool(result)
    except (TypeError, ValueError):
        return False


def _validate_receipt_actual(
    actual: Mapping[str, Any],
    request: Mapping[str, Any],
) -> dict[str, Any]:
    if not isinstance(actual, Mapping):
        raise ValueError("receipt vehicle output must be a mapping")
    for name in _REQUIRED_RECEIPT_FIELDS + ("requested",):
        if name not in actual:
            raise ValueError(f"receipt is missing {name}")
    requested = actual["requested"]
    expected_requested = request.get("requested")
    if not isinstance(requested, Mapping) or not isinstance(
        expected_requested, Mapping
    ):
        raise ValueError("receipt requested echo must be a mapping")
    if not _same_value(requested, expected_requested):
        raise ValueError("receipt requested echo does not match request")

    actual_speed = actual["actual_speed_mps"]
    if actual_speed is not None:
        actual_speed = _finite(actual_speed, "actual_speed_mps")
    actual_lane = actual["actual_lane_index"]
    if actual_lane is not None:
        actual_lane = _integer(actual_lane, "actual_lane_index")

    speed_status = actual["speed_status"]
    lane_status = actual["lane_change_status"]
    for name, status in (
        ("speed_status", speed_status),
        ("lane_change_status", lane_status),
    ):
        if status is not None and (
            not isinstance(status, str) or not status.strip()
        ):
            raise ValueError(f"receipt {name} must be a non-empty string or None")
    speed_requested = any(
        key in requested for key in ("target_speed_mps", "speed_mps")
    )
    lane_requested = any(
        key in requested for key in ("target_lane_index", "lane_index")
    )
    if speed_requested != (speed_status is not None):
        raise ValueError("receipt speed_status does not match requested action")
    if lane_requested != (lane_status is not None):
        raise ValueError("receipt lane_change_status does not match requested action")
    return {
        "requested": _clone(requested),
        "actual_speed_mps": actual_speed,
        "actual_lane_index": actual_lane,
        "speed_status": _clone(speed_status),
        "lane_change_status": _clone(lane_status),
    }


def _validate_row(
    value: Mapping[str, Any],
    *,
    role: str,
    episode_id: str,
    policy_version: str,
) -> dict[str, Any]:
    if not isinstance(value, Mapping):
        raise ValueError("sample row must be a mapping")
    row = _clone(value)
    _identity(row, episode_id, policy_version)

    decision_time = _finite(row.get("decision_time"), "decision_time")
    if decision_time < 0 or decision_time >= HORIZON:
        raise ValueError("decision_time must be in [0, 900)")
    step_id = _integer(row.get("step_id"), "step_id")
    if step_id < 0:
        raise ValueError("step_id must be non-negative")
    for name in ("agent_id", "movement_token"):
        item = row.get(name)
        if not isinstance(item, str) or not item.strip():
            raise ValueError(f"{name} must be a non-empty string")
    movement = _integer(row.get("movement"), "movement")
    if movement < 0:
        raise ValueError("movement must be non-negative")

    row["decision_time"] = decision_time
    row["step_id"] = step_id
    row["movement"] = movement
    row["context"] = _vector(
        row, ("context", "context3300"), CONTEXT_DIM, "context"
    )
    expected_obs = MOVEMENT_DIM if role == "cloud" else VEHICLE_DIM
    row["obs"] = _vector(
        row,
        ("obs", f"obs{expected_obs}"),
        expected_obs,
        f"obs{expected_obs}",
    )
    if "old_logp" not in row and "logp" in row:
        row["old_logp"] = row["logp"]
    if "old_value" not in row and "value" in row:
        row["old_value"] = row["value"]
    row["old_logp"] = _finite(row.get("old_logp"), "old_logp")
    row["old_value"] = _finite(row.get("old_value"), "old_value")
    row["actor_mask"] = _boolean(row.get("actor_mask"), "actor_mask")

    if role == "cloud":
        if "action" not in row and "cloud_action" in row:
            row["action"] = row["cloud_action"]
        action = _integer(row.get("action"), "action")
        if action not in (0, 1):
            raise ValueError("cloud action must be 0 or 1")
        row["action"] = action
        return row

    try:
        lane_mask = np.asarray(row.get("lane_mask"))
    except (TypeError, ValueError) as exc:
        raise ValueError("lane_mask must be boolean with four slots") from exc
    if lane_mask.dtype != np.bool_ or lane_mask.shape != (LANE_SLOTS,):
        raise ValueError("lane_mask must be boolean with four slots")
    if not bool(lane_mask[0]):
        raise ValueError("lane_mask must keep KEEP legal")
    row["lane_mask"] = lane_mask.astype(np.bool_, copy=True)
    row["speed_mask"] = _boolean(row.get("speed_mask"), "speed_mask")
    row["raw_speed"] = _finite(row.get("raw_speed"), "raw_speed")
    if "reduction" in row:
        row["reduction"] = _finite(row["reduction"], "reduction")
    if not row["speed_mask"] and (
        abs(row["raw_speed"]) > 1e-7
        or ("reduction" in row and abs(row["reduction"]) > 1e-7)
    ):
        raise ValueError("masked vehicle speed action must be zero")
    lane_action = _integer(row.get("lane_action"), "lane_action")
    if lane_action < 0 or lane_action >= LANE_SLOTS:
        raise ValueError("lane_action is outside lane mask")
    if not bool(row["lane_mask"][lane_action]):
        raise ValueError("lane_action is not legal under lane_mask")
    row["lane_action"] = lane_action

    for name in ("cloud_message_id", "cloud_generated_at", "cloud_valid_until"):
        if name not in row:
            raise ValueError(f"vehicle row is missing {name}")
    message_id = row["cloud_message_id"]
    if message_id is not None and (
        not isinstance(message_id, str) or not message_id.strip()
    ):
        raise ValueError("cloud_message_id must be a non-empty string or None")
    row["cloud_message_id"] = message_id
    generated = row["cloud_generated_at"]
    valid_until = row["cloud_valid_until"]
    if generated is None or valid_until is None:
        if generated is not None or valid_until is not None:
            raise ValueError("cloud message times must be both present or absent")
    else:
        generated = _finite(generated, "cloud_generated_at")
        valid_until = _finite(valid_until, "cloud_valid_until")
        if valid_until <= generated:
            raise ValueError("cloud_valid_until must be after cloud_generated_at")
        row["cloud_generated_at"] = generated
        row["cloud_valid_until"] = valid_until
    return row


class EpisodeBuffer:
    """Own callback samples, requests, receipts, and terminal bookkeeping."""

    def __init__(
        self,
        episode_id: str,
        policy_version: str,
        period: str,
        seed: int | None = None,
    ) -> None:
        if not isinstance(episode_id, str) or not episode_id.strip():
            raise ValueError("episode_id must be a non-empty string")
        if not isinstance(policy_version, str) or not policy_version.strip():
            raise ValueError("policy_version must be a non-empty string")
        if period not in PERIODS:
            raise ValueError(f"period must be one of {PERIODS}")
        if seed is not None:
            seed = _integer(seed, "seed")
        self.episode_id = episode_id
        self.policy_version = policy_version
        self.period = period
        self.collection_mode = "sampled"
        self.seed = seed
        self.schema_version = SCHEMA_VERSION
        self.cloud_rows: list[dict[str, Any]] = []
        self.vehicle_rows: list[dict[str, Any]] = []
        self.requests: list[dict[str, Any]] = []
        self.receipts: list[dict[str, Any]] = []
        self.last_observation_time: float | None = None
        self.last_observation_step: int | None = None
        self.observation_history: list[dict[str, Any]] = []
        self.finished = False
        self.finish_reason: str | None = None
        self.finish_time: float | None = None

    def _ensure_open(self) -> None:
        if self.finished:
            raise ValueError("episode is already finished")

    def _pending_observation(self, payload: Mapping[str, Any]) -> dict[str, Any] | None:
        if "step_id" not in payload or "simulation_time" not in payload:
            return None
        raw_step = payload.get("step_id")
        raw_time = payload.get("simulation_time")
        if raw_step is None or raw_time is None:
            return None
        step = _integer(raw_step, "observation step_id")
        time = _finite(raw_time, "observation simulation_time")
        if step < 0 or time < 0:
            raise ValueError("observation provenance is outside the episode")
        if self.observation_history and step <= self.observation_history[-1]["step_id"]:
            raise ValueError("observation callback step is not increasing")
        return {"step_id": step, "simulation_time": time}

    def _commit_observation(self, observation: Mapping[str, Any] | None) -> None:
        if observation is None:
            return
        item = _clone(observation)
        self.last_observation_step = int(item["step_id"])
        self.last_observation_time = float(item["simulation_time"])
        self.observation_history.append(item)

    def add_cloud(self, row: Mapping[str, Any]) -> None:
        self._ensure_open()
        self.cloud_rows.append(
            _validate_row(
                row,
                role="cloud",
                episode_id=self.episode_id,
                policy_version=self.policy_version,
            )
        )

    def add_vehicle(self, row: Mapping[str, Any]) -> None:
        self._ensure_open()
        self.vehicle_rows.append(
            _validate_row(
                row,
                role="vehicle",
                episode_id=self.episode_id,
                policy_version=self.policy_version,
            )
        )

    def record_request(
        self,
        step_id: int,
        vehicle_id: str,
        requested: Any,
        cloud_message_id: str | None = None,
    ) -> None:
        self._ensure_open()
        step_id = _integer(step_id, "step_id")
        if step_id < 0:
            raise ValueError("step_id must be non-negative")
        if not isinstance(vehicle_id, str) or not vehicle_id.strip():
            raise ValueError("vehicle_id must be a non-empty string")
        if cloud_message_id is not None and (
            not isinstance(cloud_message_id, str) or not cloud_message_id.strip()
        ):
            raise ValueError("cloud_message_id must be a non-empty string or None")
        item = {
            "step_id": step_id,
            "vehicle_id": vehicle_id,
            "requested": _clone(requested),
            "cloud_message_id": cloud_message_id,
        }
        if any(_request_key(previous) == _request_key(item)
               for previous in self.requests):
            raise ValueError("duplicate request pair")
        self.requests.append(item)

    def observe_receipts(self, payload: Mapping[str, Any]) -> list[dict[str, Any]]:
        if not isinstance(payload, Mapping):
            raise ValueError("receipt payload must be a mapping")
        observation = self._pending_observation(payload)
        previous = payload.get("previous_action_results")
        if not isinstance(previous, Mapping):
            raise ValueError("receipt payload is missing previous_action_results")
        raw_step_id = previous.get("step_id")
        vehicles = previous.get("vehicles")
        if not isinstance(vehicles, Mapping):
            raise ValueError("previous_action_results.vehicles must be a mapping")
        if raw_step_id is None:
            if vehicles:
                raise ValueError(
                    "previous_action_results.step_id is required with vehicles"
                )
            request_keys = {_request_key(request) for request in self.requests}
            receipt_keys = {_request_key(receipt) for receipt in self.receipts}
            if not request_keys.issubset(receipt_keys):
                raise ValueError(
                    "previous_action_results.step_id is required for pending requests"
                )
            self._commit_observation(observation)
            return []

        step_id = _integer(raw_step_id, "previous_action_results.step_id")
        expected = {
            _request_key(request): request
            for request in self.requests
            if int(request["step_id"]) == step_id
        }
        actual_by_key = {
            (step_id, str(vehicle_id)): actual
            for vehicle_id, actual in vehicles.items()
        }
        if len(actual_by_key) != len(vehicles):
            raise ValueError("receipt vehicle IDs are ambiguous")
        allowed_keys = set(expected)
        received_keys = set(actual_by_key)
        unexpected = received_keys - allowed_keys
        required_keys = {
            key for key, request in expected.items()
            if not _is_implicit_release(request.get("requested"))
        }
        if unexpected or not required_keys.issubset(received_keys):
            raise ValueError("receipt pairing is incomplete or unexpected")
        implicit_keys = {
            key for key, request in expected.items()
            if key not in received_keys
            and _is_implicit_release(request.get("requested"))
        }
        existing = {_request_key(receipt) for receipt in self.receipts}
        if existing.intersection(received_keys | implicit_keys):
            raise ValueError("duplicate receipt pair")

        result: list[dict[str, Any]] = []
        for key, request in expected.items():
            vehicle_id = key[1]
            if (
                key not in actual_by_key
                and _is_implicit_release(request.get("requested"))
            ):
                receipt = {
                    "step_id": step_id,
                    "vehicle_id": vehicle_id,
                    "requested": _clone(request["requested"]),
                    "cloud_message_id": request.get("cloud_message_id"),
                    "actual_speed_mps": None,
                    "actual_lane_index": None,
                    "speed_status": None,
                    "lane_change_status": None,
                    "implicit_release": True,
                }
            elif key in actual_by_key:
                validated = _validate_receipt_actual(actual_by_key[key], request)
                receipt = {
                    "step_id": step_id,
                    "vehicle_id": vehicle_id,
                    "cloud_message_id": request.get("cloud_message_id"),
                    **validated,
                }
            else:
                raise ValueError("receipt pairing is incomplete or unexpected")
            self.receipts.append(receipt)
            result.append(_clone(receipt))
        self._commit_observation(observation)
        return result

    def finish(self, reason: str, simulation_time: float) -> None:
        if self.finished:
            raise ValueError("episode is already finished")
        if not isinstance(reason, str) or not reason.strip():
            raise ValueError("finish reason must be a non-empty string")
        simulation_time = _finite(simulation_time, "finish_time")
        if simulation_time < 0 or simulation_time > HORIZON + 1e-6:
            raise ValueError("finish_time is outside the episode horizon")
        self.finished = True
        self.finish_reason = reason
        self.finish_time = simulation_time

    def to_dict(self) -> dict[str, Any]:
        return _clone({
            "schema_version": self.schema_version,
            "episode_id": self.episode_id,
            "collection_mode": self.collection_mode,
            "policy_version": self.policy_version,
            "period": self.period,
            "seed": self.seed,
            "cloud_rows": self.cloud_rows,
            "vehicle_rows": self.vehicle_rows,
            "requests": self.requests,
            "receipts": self.receipts,
            "last_observation_time": self.last_observation_time,
            "last_observation_step": self.last_observation_step,
            "observation_history": self.observation_history,
            "terminal_unobserved_requests": [],
            "finished": self.finished,
            "finish_reason": self.finish_reason,
            "finish_time": self.finish_time,
        })



__all__ = ["EpisodeBuffer"]
