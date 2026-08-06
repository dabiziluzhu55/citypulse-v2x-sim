# algorithms/v2x/logger.py
"""JSONL sink 与四类记录构造（episode_start/message/delivery/episode_end）。"""
from __future__ import annotations

import json
from dataclasses import dataclass, field
from typing import Any, Mapping, Optional, Protocol


class MessageSink(Protocol):
    def write(self, record: "LogRecord") -> None: ...
    def flush(self) -> None: ...
    def close(self) -> None: ...


@dataclass(frozen=True, slots=True)
class LogRecord:
    record_type: str
    data: Mapping[str, Any] = field(default_factory=dict)


class JSONLSink:
    def __init__(self, path: str) -> None:
        self._file = open(path, "a", encoding="utf-8")

    def write(self, record: LogRecord) -> None:
        payload = dict(record.data)
        payload["record_type"] = record.record_type
        self._file.write(json.dumps(payload, ensure_ascii=False) + "\n")

    def flush(self) -> None:
        self._file.flush()

    def close(self) -> None:
        self._file.close()


def episode_start_record(
    *, run_id: str, episode_id: str, scenario: Optional[Mapping[str, Any]],
    v2x_config: Mapping[str, Any], capability_seed: int,
    capability_config: Mapping[str, Any], map_versions: Mapping[str, int],
) -> LogRecord:
    return LogRecord("episode_start", {
        "run_id": run_id, "episode_id": episode_id, "scenario": dict(scenario or {}),
        "v2x_config": dict(v2x_config), "capability_seed": capability_seed,
        "capability_config": dict(capability_config), "map_versions": dict(map_versions),
    })


def message_record(
    *, message: Mapping[str, Any], sent_at: float, scheduled_delivery_at: float,
) -> LogRecord:
    return LogRecord("message", {
        "message": dict(message), "sent_at": sent_at,
        "scheduled_delivery_at": scheduled_delivery_at,
    })


def delivery_record(
    *, message_id: str, status: str,
    delivered_at: Optional[float] = None, dropped_at: Optional[float] = None,
    processed_at: Optional[float] = None, actual_latency_ms: Optional[float] = None,
    drop_reason: Optional[str] = None,
) -> LogRecord:
    data: dict[str, Any] = {"message_id": message_id, "status": status}
    if delivered_at is not None:
        data["delivered_at"] = delivered_at
    if dropped_at is not None:
        data["dropped_at"] = dropped_at
    if processed_at is not None:
        data["processed_at"] = processed_at
    if actual_latency_ms is not None:
        data["actual_latency_ms"] = actual_latency_ms
    if drop_reason is not None:
        data["drop_reason"] = drop_reason
    return LogRecord("delivery", data)


def episode_end_record(*, summary: Mapping[str, Any]) -> LogRecord:
    return LogRecord("episode_end", {"summary": dict(summary)})
