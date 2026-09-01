"""Typed V2X transport and backend-facing event export for CoV2X."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Callable, Mapping

from traffic_control.cov2x.communication.export import (
    V2X_EVENT_BATCH_SCHEMA,
    V2X_EVENT_SCHEMA,
    V2X_EVENT_SCHEMA_VERSION,
    V2XEventSink,
)

LOGICAL_PHASES = ("state", "cloud", "road", "vehicle", "commit")
MESSAGE_TYPES = {
    "VehicleStateV1": {"vehicle_id", "motion", "location", "next_signal"},
    "IntersectionSummaryV1": {
        "intersection_id",
        "lanes",
        "current_phase",
    },
    "RegionalPriorityV1": {"intersection_id", "priority"},
    "SPaTV2": {
        "intersection_id",
        "current_phase",
        "stage",
        "remaining_time_s",
    },
    "MAPV1": {
        "intersection_id",
        "phases",
        "lanes",
        "connections",
        "direct_neighbors",
    },
}
EVENT_TYPES = frozenset({"SEND", "DELIVER", "CONSUME", "TTL_EXPIRED"})


def _source_role(message_type: str) -> str:
    if message_type == "VehicleStateV1":
        return "vehicle"
    if message_type == "RegionalPriorityV1":
        return "cloud"
    return "road"


def _destination_role(destination: str) -> str:
    if destination in {"cloud", "vehicle"}:
        return destination
    return "road"


@dataclass(frozen=True)
class TypedEnvelope:
    message_type: str
    message_id: str
    snapshot_id: str
    source_id: str
    destination: str
    sim_time: float
    ttl_s: float
    logical_phase: str
    payload: Mapping[str, Any]
    causal_parents: tuple[str, ...] = ()
    schema_version: str = "1.0"

    def __post_init__(self) -> None:
        if self.message_type not in MESSAGE_TYPES:
            raise ValueError(f"unknown message type: {self.message_type}")
        if self.logical_phase not in LOGICAL_PHASES:
            raise ValueError(f"unknown logical phase: {self.logical_phase}")
        missing = MESSAGE_TYPES[self.message_type] - set(self.payload)
        if missing:
            raise ValueError(f"{self.message_type} missing {sorted(missing)}")
        if float(self.ttl_s) < 0.0:
            raise ValueError("ttl_s must be non-negative")

    @property
    def expires_at(self) -> float:
        return float(self.sim_time) + float(self.ttl_s)


@dataclass(frozen=True)
class TraceEvent:
    sequence: int
    event: str
    message_id: str
    snapshot_id: str
    logical_phase: str
    sim_time: float
    causal_parents: tuple[str, ...]
    message_type: str
    source_id: str
    destination: str
    sent_time_s: float
    ttl_s: float
    payload_fields: tuple[str, ...]
    drop_reason: str | None = None

    def __post_init__(self) -> None:
        if self.event not in EVENT_TYPES:
            raise ValueError(f"unknown V2X event type: {self.event}")

    @property
    def episode_id(self) -> str:
        head, separator, _ = self.snapshot_id.rpartition(":")
        return head if separator else self.snapshot_id

    @property
    def message_age_s(self) -> float:
        return max(0.0, float(self.sim_time) - float(self.sent_time_s))

    def to_record(self) -> dict[str, Any]:
        return {
            "schema": V2X_EVENT_SCHEMA,
            "schema_version": V2X_EVENT_SCHEMA_VERSION,
            "sequence": int(self.sequence),
            "event": self.event,
            "message_type": self.message_type,
            "message_id": self.message_id,
            "episode_id": self.episode_id,
            "snapshot_id": self.snapshot_id,
            "source_role": _source_role(self.message_type),
            "source_id": self.source_id,
            "destination_role": _destination_role(self.destination),
            "destination_id": self.destination,
            "logical_phase": self.logical_phase,
            "event_time_s": float(self.sim_time),
            "sent_time_s": float(self.sent_time_s),
            "message_age_s": self.message_age_s,
            "ttl_s": float(self.ttl_s),
            "expires_at_s": float(self.sent_time_s) + float(self.ttl_s),
            "causal_parent_ids": list(self.causal_parents),
            "payload_fields": list(self.payload_fields),
            "drop_reason": self.drop_reason,
        }


EventSink = V2XEventSink | Callable[[Mapping[str, Any]], None]


class IdealPhasedTransport:
    """Same-snapshot transport with schema, TTL and causal DAG checks."""

    def __init__(self, *, event_sink: EventSink | None = None) -> None:
        self._pending: dict[str, list[TypedEnvelope]] = {}
        self._sent: dict[str, TypedEnvelope] = {}
        self._delivered: set[str] = set()
        self._consumed: set[str] = set()
        self._expired: set[str] = set()
        self._event_sink: EventSink | None = None
        self._drain_cursor = 0
        self._sink_error_count = 0
        self._last_sink_error: str | None = None
        self.events: list[TraceEvent] = []
        self._events_by_snapshot: dict[str, list[TraceEvent]] = {}
        self.set_event_sink(event_sink)

    @property
    def sink_error_count(self) -> int:
        return self._sink_error_count

    @property
    def last_sink_error(self) -> str | None:
        return self._last_sink_error

    def set_event_sink(self, sink: EventSink | None) -> None:
        if sink is not None and not callable(sink) and not callable(
            getattr(sink, "emit", None)
        ):
            raise TypeError("V2X event sink must be callable or provide emit()")
        self._event_sink = sink

    def _record(
        self,
        event: str,
        envelope: TypedEnvelope,
        *,
        sim_time: float,
        drop_reason: str | None = None,
    ) -> None:
        trace = TraceEvent(
            sequence=len(self.events) + 1,
            event=event,
            message_id=envelope.message_id,
            snapshot_id=envelope.snapshot_id,
            logical_phase=envelope.logical_phase,
            sim_time=float(sim_time),
            causal_parents=envelope.causal_parents,
            message_type=envelope.message_type,
            source_id=envelope.source_id,
            destination=envelope.destination,
            sent_time_s=float(envelope.sim_time),
            ttl_s=float(envelope.ttl_s),
            payload_fields=tuple(sorted(str(key) for key in envelope.payload)),
            drop_reason=drop_reason,
        )
        self.events.append(trace)
        self._events_by_snapshot.setdefault(trace.snapshot_id, []).append(trace)
        if self._event_sink is None:
            return
        try:
            emit = getattr(self._event_sink, "emit", None)
            if callable(emit):
                emit(trace.to_record())
            else:
                self._event_sink(trace.to_record())
        except Exception as exc:
            # An optional monitoring consumer must never change control actions.
            self._sink_error_count += 1
            self._last_sink_error = f"{type(exc).__name__}: {exc}"

    def send(self, envelope: TypedEnvelope) -> None:
        if envelope.message_id in self._sent:
            raise ValueError(f"duplicate message id: {envelope.message_id}")
        child_phase = LOGICAL_PHASES.index(envelope.logical_phase)
        for parent_id in envelope.causal_parents:
            parent = self._sent.get(parent_id)
            if parent is None:
                raise ValueError(f"unknown causal parent: {parent_id}")
            if parent.snapshot_id != envelope.snapshot_id:
                raise ValueError(
                    "causal parent must belong to the same frozen snapshot"
                )
            if LOGICAL_PHASES.index(parent.logical_phase) > child_phase:
                raise ValueError("causal phase order would form a cycle")
        self._sent[envelope.message_id] = envelope
        self._pending.setdefault(envelope.snapshot_id, []).append(envelope)
        self._record("SEND", envelope, sim_time=envelope.sim_time)

    def deliver(
        self,
        snapshot_id: str,
        phase: str,
        sim_time: float,
    ) -> tuple[TypedEnvelope, ...]:
        if phase not in LOGICAL_PHASES:
            raise ValueError(f"unknown phase {phase}")
        delivered = []
        for envelope in self._pending.get(str(snapshot_id), []):
            if (
                envelope.logical_phase != phase
                or envelope.message_id in self._consumed
                or envelope.message_id in self._expired
            ):
                continue
            if float(sim_time) > envelope.expires_at + 1e-9:
                self._expired.add(envelope.message_id)
                self._record(
                    "TTL_EXPIRED",
                    envelope,
                    sim_time=sim_time,
                    drop_reason="ttl_expired",
                )
                continue
            self._delivered.add(envelope.message_id)
            delivered.append(envelope)
            self._record("DELIVER", envelope, sim_time=sim_time)
        return tuple(delivered)

    def consume(
        self,
        envelope: TypedEnvelope,
        *,
        sim_time: float,
    ) -> Mapping[str, Any]:
        if envelope.message_id not in self._sent:
            raise ValueError("cannot consume unknown message")
        if envelope.message_id not in self._delivered:
            raise ValueError("message must be delivered before consume")
        if envelope.message_id in self._consumed:
            raise ValueError("message already consumed")
        if float(sim_time) > envelope.expires_at + 1e-9:
            raise ValueError("cannot consume expired message")
        missing_parents = set(envelope.causal_parents) - self._consumed
        if missing_parents:
            raise ValueError(
                f"causal parents not consumed: {sorted(missing_parents)}"
            )
        self._consumed.add(envelope.message_id)
        self._record("CONSUME", envelope, sim_time=sim_time)
        return envelope.payload

    def trace(self) -> tuple[TraceEvent, ...]:
        return tuple(self.events)

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
            candidates = self.events[cursor:]
        else:
            candidates = self._events_by_snapshot.get(str(snapshot_id), ())
        records = [
            event.to_record()
            for event in candidates
            if event.sequence > cursor
        ]
        episode_id = records[0]["episode_id"] if records else None
        return {
            "schema": V2X_EVENT_BATCH_SCHEMA,
            "schema_version": V2X_EVENT_SCHEMA_VERSION,
            "episode_id": episode_id,
            "snapshot_id": str(snapshot_id) if snapshot_id is not None else None,
            "event_count": len(records),
            "last_sequence": (
                int(records[-1]["sequence"]) if records else int(after_sequence)
            ),
            "sink_error_count": self._sink_error_count,
            "events": records,
        }

    def drain(self) -> dict[str, Any]:
        batch = self.event_batch(after_sequence=self._drain_cursor)
        self._drain_cursor = int(batch["last_sequence"])
        return batch
