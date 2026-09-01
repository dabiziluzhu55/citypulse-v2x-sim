"""Backend-facing sinks for versioned CoV2X V2X event records."""

from __future__ import annotations

from collections import deque
from copy import deepcopy
from threading import Lock
from typing import Any, Mapping, Protocol, runtime_checkable

V2X_EVENT_SCHEMA = "cov2x.v2x.event"
V2X_EVENT_SCHEMA_VERSION = "1.0"
V2X_EVENT_BATCH_SCHEMA = "cov2x.v2x.event_batch"


@runtime_checkable
class V2XEventSink(Protocol):
    """Consumer implemented by an in-process backend adapter."""

    def emit(self, event: Mapping[str, Any]) -> None:
        """Accept one JSON-shaped event record."""


class V2XEventDrain:
    """Bounded, thread-safe event sink with explicit destructive drain."""

    def __init__(self, *, max_events: int = 10_000) -> None:
        if int(max_events) <= 0:
            raise ValueError("max_events must be positive")
        self._events: deque[dict[str, Any]] = deque(maxlen=int(max_events))
        self._lock = Lock()
        self._dropped_events = 0

    @property
    def dropped_events(self) -> int:
        with self._lock:
            return self._dropped_events

    def emit(self, event: Mapping[str, Any]) -> None:
        record = dict(event)
        if record.get("schema") != V2X_EVENT_SCHEMA:
            raise ValueError("unexpected V2X event schema")
        if record.get("schema_version") != V2X_EVENT_SCHEMA_VERSION:
            raise ValueError("unexpected V2X event schema version")
        with self._lock:
            if len(self._events) == self._events.maxlen:
                self._dropped_events += 1
            self._events.append(deepcopy(record))

    def drain(self, *, limit: int | None = None) -> list[dict[str, Any]]:
        if limit is not None and int(limit) <= 0:
            raise ValueError("limit must be positive when provided")
        with self._lock:
            count = len(self._events) if limit is None else min(
                int(limit), len(self._events)
            )
            return [self._events.popleft() for _ in range(count)]

    def snapshot(self) -> list[dict[str, Any]]:
        with self._lock:
            return deepcopy(list(self._events))
