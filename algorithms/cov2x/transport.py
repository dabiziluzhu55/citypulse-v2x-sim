"""Typed, phased V2X transport trace for the CoV2X MVP."""
from __future__ import annotations
from dataclasses import dataclass
from typing import Any, Mapping

LOGICAL_PHASES = ("state", "cloud", "road", "vehicle", "commit")
MESSAGE_TYPES = {
    "VehicleStateV1": {"vehicle_id", "motion", "location", "next_signal"},
    "IntersectionSummaryV1": {"intersection_id", "lanes", "current_phase"},
    "RegionalPriorityV1": {"intersection_id", "priority"},
    "SPaTV2": {"intersection_id", "current_phase", "stage", "remaining_time_s"},
    "MAPV1": {"intersection_id", "phases", "lanes", "connections", "direct_neighbors"},
}


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
    event: str
    message_id: str
    snapshot_id: str
    logical_phase: str
    sim_time: float
    causal_parents: tuple[str, ...] = ()


class IdealPhasedTransport:
    """Same-snapshot transport with fail-closed schema, TTL and causal DAG checks."""

    def __init__(self) -> None:
        self._pending: dict[str, list[TypedEnvelope]] = {}
        self._sent: dict[str, TypedEnvelope] = {}
        self._delivered: set[str] = set()
        self._consumed: set[str] = set()
        self._expired: set[str] = set()
        self.events: list[TraceEvent] = []

    def send(self, envelope: TypedEnvelope) -> None:
        if envelope.message_id in self._sent:
            raise ValueError(f"duplicate message id: {envelope.message_id}")
        child_phase = LOGICAL_PHASES.index(envelope.logical_phase)
        for parent_id in envelope.causal_parents:
            parent = self._sent.get(parent_id)
            if parent is None:
                raise ValueError(f"unknown causal parent: {parent_id}")
            if parent.snapshot_id != envelope.snapshot_id:
                raise ValueError("causal parent must belong to the same frozen snapshot")
            if LOGICAL_PHASES.index(parent.logical_phase) > child_phase:
                raise ValueError("causal phase order would form a cycle")
        self._sent[envelope.message_id] = envelope
        self._pending.setdefault(envelope.snapshot_id, []).append(envelope)
        self.events.append(TraceEvent("SEND", envelope.message_id, envelope.snapshot_id, envelope.logical_phase, envelope.sim_time, envelope.causal_parents))

    def deliver(self, snapshot_id: str, phase: str, sim_time: float) -> tuple[TypedEnvelope, ...]:
        if phase not in LOGICAL_PHASES:
            raise ValueError(f"unknown phase {phase}")
        delivered = []
        for envelope in self._pending.get(str(snapshot_id), []):
            if envelope.logical_phase != phase or envelope.message_id in self._consumed or envelope.message_id in self._expired:
                continue
            if float(sim_time) > envelope.expires_at + 1e-9:
                self._expired.add(envelope.message_id)
                self.events.append(TraceEvent("TTL_EXPIRED", envelope.message_id, envelope.snapshot_id, phase, float(sim_time), envelope.causal_parents))
                continue
            self._delivered.add(envelope.message_id); delivered.append(envelope)
            self.events.append(TraceEvent("DELIVER", envelope.message_id, envelope.snapshot_id, phase, float(sim_time), envelope.causal_parents))
        return tuple(delivered)

    def consume(self, envelope: TypedEnvelope, *, sim_time: float) -> Mapping[str, Any]:
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
            raise ValueError(f"causal parents not consumed: {sorted(missing_parents)}")
        self._consumed.add(envelope.message_id)
        self.events.append(TraceEvent("CONSUME", envelope.message_id, envelope.snapshot_id, envelope.logical_phase, float(sim_time), envelope.causal_parents))
        return envelope.payload

    def trace(self) -> tuple[TraceEvent, ...]:
        return tuple(self.events)
