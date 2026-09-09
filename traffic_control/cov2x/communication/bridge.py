"""Bridge CV Joint V1 MessageBus traces to the product V2X batch ABI.

The CV controller keeps its audited MessageBus semantics.  This bridge only
adapts the already emitted records for the product's batch, drain, and sink
surfaces; it never delivers, consumes, or mutates a source message.
"""

from __future__ import annotations

from copy import deepcopy
import hashlib
import json
import math
from typing import Any, Mapping, Sequence

from traffic_control.cov2x.communication.export import (
    V2X_EVENT_BATCH_SCHEMA,
    V2X_EVENT_SCHEMA,
    V2X_EVENT_SCHEMA_VERSION,
    V2XEventSink,
)

CV_JOINT_V1_MESSAGE_TYPE = "CVJointV1"
_SOURCE_EVENTS = frozenset({"SEND", "DELIVER", "CONSUME", "EXPIRE"})
_ROLES = frozenset({"vehicle", "road", "cloud"})
_PHASES = {
    "vehicle_feedback": "state",
    "vehicle_state": "state",
    "road_state": "road",
    "road_feedback": "road",
    "coordination_context": "cloud",
    "speed_permission": "cloud",
}


def _number(value: Any, label: str) -> float:
    try:
        result = float(value)
    except (TypeError, ValueError) as exc:
        raise ValueError(f"{label} must be finite") from exc
    if not math.isfinite(result):
        raise ValueError(f"{label} must be finite")
    return result


class CVJointV1EventBridge:
    """Cursor-safe adapter from the audited CV MessageBus to product V2X."""

    def __init__(
        self,
        episode_id: str,
        *,
        event_sink: V2XEventSink | Any | None = None,
    ) -> None:
        if not isinstance(episode_id, str) or not episode_id.strip():
            raise ValueError("episode_id must be a non-empty string")
        self.episode_id = episode_id
        self.events: list[dict[str, Any]] = []
        self._source_cursor = 0
        self._drain_cursor = 0
        self._message_metadata: dict[str, dict[str, Any]] = {}
        self._event_sink: V2XEventSink | Any | None = None
        self._sink_error_count = 0
        self._last_sink_error: str | None = None
        self.set_event_sink(event_sink)

    @property
    def sink_error_count(self) -> int:
        return self._sink_error_count

    @property
    def last_sink_error(self) -> str | None:
        return self._last_sink_error

    def observe_message(self, message: Any, payload: Mapping[str, Any]) -> None:
        """Observe payload metadata without retaining or mutating control input."""
        message_id = getattr(message, "message_id", message)
        if not isinstance(message_id, str) or not message_id.strip():
            raise ValueError("message_id must be a non-empty string")
        if not isinstance(payload, Mapping):
            raise TypeError("payload must be a mapping")
        try:
            canonical = json.dumps(
                dict(payload), sort_keys=True, separators=(",", ":"), allow_nan=False
            )
            payload_sha256 = hashlib.sha256(
                canonical.encode("utf-8")
            ).hexdigest()
        except (TypeError, ValueError):
            # Runtime payloads may contain NumPy observations. The authoritative
            # MessageBus event carries their digest; use it during sync.
            payload_sha256 = None
        self._message_metadata[message_id] = {
            "payload_fields": sorted(str(key) for key in payload),
            "payload_sha256": payload_sha256,
            "payload_projection": self._projection(
                str(getattr(message, "kind", "")), payload
            ),
        }

    @staticmethod
    def _projection(kind: str, payload: Mapping[str, Any]) -> dict[str, Any]:
        if kind == "speed_permission":
            return {
                key: deepcopy(payload[key])
                for key in ("movement", "permit", "policy_version")
                if key in payload
            }
        if kind in {"vehicle_state", "vehicle_feedback"}:
            vehicles = payload.get("vehicles")
            result = {
                "vehicle_count": len(vehicles)
                if isinstance(vehicles, Mapping)
                else 0
            }
            if kind == "vehicle_feedback":
                previous = payload.get("previous_action_results")
                if isinstance(previous, Mapping):
                    result["feedback_step_id"] = previous.get("step_id")
            return result
        if kind == "road_state":
            intersections = payload.get("intersections")
            return {
                "intersection_count": len(intersections)
                if isinstance(intersections, Mapping)
                else 0,
                "intersection_ids": sorted(str(key) for key in intersections)
                if isinstance(intersections, Mapping)
                else [],
            }
        if kind == "coordination_context":
            permissions = payload.get("movement_permissions")
            return {
                "movement_permission_count": len(permissions)
                if isinstance(permissions, Mapping)
                else 0,
                "phase_authority": bool(payload.get("phase_authority", False)),
            }
        if kind == "road_feedback":
            observed = payload.get("observed")
            requested = payload.get("requested")
            return {
                "observed_intersection_count": len(observed)
                if isinstance(observed, Mapping)
                else 0,
                "requested_intersection_count": len(requested)
                if isinstance(requested, Mapping)
                else 0,
            }
        return {}

    def reset(self) -> None:
        self.events.clear()
        self._source_cursor = 0
        self._drain_cursor = 0
        self._message_metadata.clear()
        self._sink_error_count = 0
        self._last_sink_error = None

    def set_event_sink(self, sink: V2XEventSink | Any | None) -> None:
        if sink is not None and not callable(sink) and not callable(
            getattr(sink, "emit", None)
        ):
            raise TypeError("V2X event sink must be callable or provide emit()")
        self._event_sink = sink

    def sync(self, source_events: Sequence[Mapping[str, Any]]) -> None:
        """Append only source records not seen by this bridge."""
        if not isinstance(source_events, Sequence) or isinstance(
            source_events, (str, bytes)
        ):
            raise TypeError("source_events must be a sequence")
        if len(source_events) < self._source_cursor:
            raise ValueError("source event history was truncated")
        for source in source_events[self._source_cursor :]:
            record = self._adapt(source)
            self.events.append(record)
            self._emit(record)
        self._source_cursor = len(source_events)

    def _adapt(self, source: Mapping[str, Any]) -> dict[str, Any]:
        if not isinstance(source, Mapping):
            raise ValueError("source event must be a mapping")
        event = source.get("event")
        if event not in _SOURCE_EVENTS:
            raise ValueError(f"unsupported CV Joint V1 event: {event!r}")
        episode_id = str(source.get("episode_id", self.episode_id))
        if episode_id != self.episode_id:
            raise ValueError("source event crosses episode identity")
        kind = source.get("kind")
        if not isinstance(kind, str) or not kind.strip():
            raise ValueError("source event kind must be a non-empty string")
        if kind not in _PHASES:
            raise ValueError(f"unsupported CV Joint V1 message kind: {kind!r}")
        source_role = source.get("source")
        destination_role = source.get("destination")
        if source_role not in _ROLES or destination_role not in _ROLES:
            raise ValueError("source and destination must be CV role names")
        if source_role == destination_role:
            raise ValueError("source and destination roles must differ")
        message_id = source.get("message_id")
        if not isinstance(message_id, str) or not message_id.strip():
            raise ValueError("source event message_id must be non-empty")
        step_id = source.get("step_id")
        if isinstance(step_id, bool):
            raise ValueError("source event step_id must be an integer")
        try:
            step = int(step_id)
        except (TypeError, ValueError) as exc:
            raise ValueError("source event step_id must be an integer") from exc
        if step < 0 or float(step) != float(step_id):
            raise ValueError("source event step_id must be a non-negative integer")
        event_time = _number(source.get("time"), "source event time")
        sent_time = _number(source.get("generated_at"), "source generated_at")
        expires_at = _number(source.get("valid_until"), "source valid_until")
        if event_time < 0.0 or sent_time < 0.0:
            raise ValueError("source event times must be non-negative")
        if expires_at < sent_time:
            raise ValueError("source event expiry precedes generation")
        payload = source.get("payload", {})
        if not isinstance(payload, Mapping):
            raise ValueError("source event payload must be a mapping")
        metadata = self._message_metadata.get(message_id, {})
        parents = source.get("parents", ())
        if not isinstance(parents, Sequence) or isinstance(parents, (str, bytes)):
            raise ValueError("source event parents must be a sequence")
        snapshot_id = f"{self.episode_id}:{step}"
        output_event = "TTL_EXPIRED" if event == "EXPIRE" else str(event)
        return {
            "schema": V2X_EVENT_SCHEMA,
            "schema_version": V2X_EVENT_SCHEMA_VERSION,
            "sequence": len(self.events) + 1,
            "event": output_event,
            "message_type": CV_JOINT_V1_MESSAGE_TYPE,
            "original_kind": kind,
            "message_id": message_id,
            "episode_id": self.episode_id,
            "snapshot_id": snapshot_id,
            "source_role": source_role,
            "source_id": str(source.get("source_id", source_role)),
            "destination_role": destination_role,
            "destination_id": str(source.get("destination_id", destination_role)),
            "logical_phase": _PHASES[kind],
            "event_time_s": event_time,
            "sent_time_s": sent_time,
            "message_age_s": max(0.0, event_time - sent_time),
            "ttl_s": max(0.0, expires_at - sent_time),
            "expires_at_s": expires_at,
            "causal_parent_ids": [str(item) for item in parents],
            "payload_fields": list(
                metadata.get("payload_fields")
                or sorted(str(key) for key in payload)
            ),
            "payload_sha256": source.get(
                "payload_sha256", metadata.get("payload_sha256")
            ),
            "payload_projection": deepcopy(
                metadata.get("payload_projection", {})
            ),
            "drop_reason": "ttl_expired" if event == "EXPIRE" else None,
        }

    def _emit(self, record: Mapping[str, Any]) -> None:
        if self._event_sink is None:
            return
        try:
            emit = getattr(self._event_sink, "emit", None)
            if callable(emit):
                emit(deepcopy(dict(record)))
            else:
                self._event_sink(deepcopy(dict(record)))
        except Exception as exc:
            self._sink_error_count += 1
            self._last_sink_error = f"{type(exc).__name__}: {exc}"

    def event_batch(
        self,
        *,
        snapshot_id: str | None = None,
        after_sequence: int = 0,
    ) -> dict[str, Any]:
        cursor = int(after_sequence)
        if cursor < 0:
            raise ValueError("after_sequence must be non-negative")
        if snapshot_id is None:
            records = [
                deepcopy(item)
                for item in self.events
                if int(item["sequence"]) > cursor
            ]
        else:
            records = [
                deepcopy(item)
                for item in self.events
                if item["snapshot_id"] == str(snapshot_id)
                and int(item["sequence"]) > cursor
            ]
        return {
            "schema": V2X_EVENT_BATCH_SCHEMA,
            "schema_version": V2X_EVENT_SCHEMA_VERSION,
            "episode_id": self.episode_id if records else None,
            "snapshot_id": str(snapshot_id) if snapshot_id is not None else None,
            "event_count": len(records),
            "last_sequence": (
                int(records[-1]["sequence"]) if records else cursor
            ),
            "sink_error_count": self._sink_error_count,
            "events": records,
        }

    def inline_batch(
        self,
        current_snapshot_id: str,
        *,
        after_sequence: int = 0,
    ) -> dict[str, Any]:
        """Return newly emitted events while labeling the current callback."""
        if not isinstance(current_snapshot_id, str) or not current_snapshot_id:
            raise ValueError("current_snapshot_id must be a non-empty string")
        batch = self.event_batch(after_sequence=after_sequence)
        batch["snapshot_id"] = current_snapshot_id
        return batch

    def drain(self) -> dict[str, Any]:
        batch = self.event_batch(after_sequence=self._drain_cursor)
        self._drain_cursor = int(batch["last_sequence"])
        return batch
