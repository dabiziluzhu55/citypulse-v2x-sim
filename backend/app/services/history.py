"""仿真会话历史交通数据仓库。

历史仓库只保存 Copilot 需要的紧凑交通状态，不保存单车轨迹。写入由
``SimulationService`` 的既有 5 秒 intelligence watcher 驱动，读取则由
``get_traffic_history`` 工具按会话、时间和交通对象筛选。
"""

from __future__ import annotations

import json
import logging
import threading
import time
from dataclasses import asdict, dataclass, is_dataclass
from enum import Enum
from pathlib import Path
from typing import Any, Mapping, Protocol, Sequence

from .traffic_metrics import (
    aggregate_lane_rows as _aggregate_lane_rows,
    lane_payload as _canonical_lane_payload,
)

logger = logging.getLogger(__name__)

HISTORY_SCHEMA_VERSION = 1
DEFAULT_HISTORY_LOOKBACK_SECONDS = 300.0
DEFAULT_HISTORY_MAX_QUERY_SECONDS = 3600.0
DEFAULT_HISTORY_MAX_POINTS = 120

EVENT_CHANGE_TYPES = frozenset(
    {"created", "risk_changed", "trend_changed", "scope_changed", "resolved"}
)


class HistoryUnavailableError(RuntimeError):
    """历史仓库不可用，调用方不应使用当前快照伪造历史。"""


@dataclass(frozen=True)
class HistoryFrame:
    """单个 5 秒历史交通状态帧。

    ``intersections`` 保留车道和路口聚合所需的最小字段；``vehicles`` 被
    有意排除。其它三个 intelligence 字段使用现有运行时输出，保证历史、
    当前页面和事件检测使用同一套口径。
    """

    session_id: str
    sample_bucket: int
    sequence: int
    state: str
    elapsed_seconds: float
    duration_seconds: float
    progress: float
    official_time: str
    intersections: Mapping[str, Any]
    events: Sequence[Mapping[str, Any]]
    event_detection: Mapping[str, Any]
    prediction: Mapping[str, Any]
    traffic_style: Mapping[str, Any]

    @classmethod
    def from_snapshot(
        cls,
        snapshot: Any,
        intelligence: Mapping[str, Any] | None,
        *,
        sample_bucket: int | None = None,
    ) -> "HistoryFrame":
        session_id = str(_field(snapshot, "session_id", "") or "")
        if not session_id:
            raise ValueError("snapshot.session_id is required for history recording")

        elapsed = _number(_field(snapshot, "elapsed_seconds", 0.0))
        intelligence_payload = intelligence if isinstance(intelligence, Mapping) else {}
        raw_style = _mapping_field(
            intelligence_payload.get("traffic_style", _field(snapshot, "traffic_style", {}))
        )
        style_edges = _mapping_field(raw_style, "edges")

        intersections: dict[str, dict[str, Any]] = {}
        for raw_intersection_id, raw_intersection in _mapping_field(
            snapshot, "intersections"
        ).items():
            intersection_id = str(raw_intersection_id)
            lanes: dict[str, dict[str, Any]] = {}
            aggregate_rows: list[Mapping[str, Any]] = []
            for raw_lane_id, raw_lane in _mapping_field(
                raw_intersection, "lanes"
            ).items():
                lane_id = str(raw_lane_id)
                lane_payload = _json_safe(raw_lane)
                if not isinstance(lane_payload, Mapping):
                    continue
                lane_payload = {str(key): value for key, value in lane_payload.items()}
                lane_payload["lane_id"] = lane_id
                lane_payload["downstream_lane_ids"] = list(
                    _string_values(lane_payload.get("downstream_lane_ids", ()))
                )
                edge_id = str(lane_payload.get("edge_id", "") or "")
                style = _mapping_field(style_edges, edge_id)
                if style:
                    if style.get("level") is not None:
                        lane_payload["congestion_level"] = str(style["level"])
                    if style.get("score") is not None:
                        lane_payload["congestion_score"] = _number(style["score"])
                lanes[lane_id] = lane_payload
                aggregate_rows.append(
                    _canonical_lane_payload(
                        intersection_id=intersection_id,
                        lane_id=lane_id,
                        lane=raw_lane,
                        style=style,
                    )
                )

            intersections[intersection_id] = {
                "current_phase": _field(raw_intersection, "current_phase"),
                "pending_phase": _field(raw_intersection, "pending_phase"),
                "stage": str(_field(raw_intersection, "stage", "") or ""),
                "stage_elapsed": _number(
                    _field(raw_intersection, "stage_elapsed", 0.0)
                ),
                "totals": _aggregate_lane_rows(aggregate_rows),
                "lanes": lanes,
            }

        raw_events = _field(snapshot, "events", ())
        events = tuple(
            item
            for item in (_json_safe(value) for value in _sequence_values(raw_events))
            if isinstance(item, Mapping)
            and (
                not str(item.get("state", "") or "").strip()
                or str(item.get("state", "") or "").lower()
                in {"active", "running"}
            )
        )
        event_detection = _mapping_field(
            intelligence_payload.get(
                "event_detection", _field(snapshot, "event_detection", {})
            )
        )
        prediction = _mapping_field(
            intelligence_payload.get("prediction", _field(snapshot, "prediction", {}))
        )

        return cls(
            session_id=session_id,
            sample_bucket=(
                int(sample_bucket)
                if sample_bucket is not None
                else int(elapsed // 5.0)
            ),
            sequence=int(_number(_field(snapshot, "sequence", 0))),
            state=str(_field(snapshot, "state", "") or ""),
            elapsed_seconds=elapsed,
            duration_seconds=_number(_field(snapshot, "duration_seconds", 0.0)),
            progress=_number(_field(snapshot, "progress", 0.0)),
            official_time=str(_field(snapshot, "official_time", "") or ""),
            intersections=intersections,
            events=events,
            event_detection=_json_safe(event_detection),
            prediction=_json_safe(prediction),
            traffic_style=_json_safe(raw_style),
        )

    def to_dict(self) -> dict[str, Any]:
        return {
            "schema_version": HISTORY_SCHEMA_VERSION,
            "session_id": self.session_id,
            "sample_bucket": self.sample_bucket,
            "sequence": self.sequence,
            "state": self.state,
            "elapsed_seconds": self.elapsed_seconds,
            "duration_seconds": self.duration_seconds,
            "progress": self.progress,
            "official_time": self.official_time,
            "intersections": _json_safe(self.intersections),
            "events": _json_safe(self.events),
            "event_detection": _json_safe(self.event_detection),
            "prediction": _json_safe(self.prediction),
            "traffic_style": _json_safe(self.traffic_style),
        }


@dataclass(frozen=True)
class HistoryEventChange:
    """事件时间线中的一个关键状态变化。"""

    session_id: str
    event_id: str
    change_type: str
    elapsed_seconds: float
    event: Mapping[str, Any]
    previous: Mapping[str, Any] | None = None

    def __post_init__(self) -> None:
        if self.change_type not in EVENT_CHANGE_TYPES:
            raise ValueError(f"Unsupported history event change: {self.change_type}")

    def to_dict(self) -> dict[str, Any]:
        return {
            "schema_version": HISTORY_SCHEMA_VERSION,
            "session_id": self.session_id,
            "event_id": self.event_id,
            "change_type": self.change_type,
            "elapsed_seconds": self.elapsed_seconds,
            "event": _json_safe(self.event),
            "previous": _json_safe(self.previous),
        }


@dataclass(frozen=True)
class HistoryQuery:
    """历史查询条件。时间使用仿真 elapsed_seconds，而不是机器墙上时间。"""

    session_id: str
    intersection_ids: tuple[str, ...] = ()
    lane_ids: tuple[str, ...] = ()
    lookback_seconds: float | None = DEFAULT_HISTORY_LOOKBACK_SECONDS
    from_seconds: float | None = None
    to_seconds: float | None = None
    metrics: tuple[str, ...] = ()
    max_points: int = DEFAULT_HISTORY_MAX_POINTS

    def __post_init__(self) -> None:
        if not str(self.session_id).strip():
            raise ValueError("session_id must not be empty")
        if self.max_points <= 0:
            raise ValueError("max_points must be positive")
        if self.from_seconds is not None and self.to_seconds is not None:
            if self.from_seconds > self.to_seconds:
                raise ValueError("from_seconds must not exceed to_seconds")
        if self.lookback_seconds is not None and self.lookback_seconds <= 0:
            raise ValueError("lookback_seconds must be positive")
        if self.from_seconds is not None and self.lookback_seconds is not None:
            raise ValueError("from_seconds and lookback_seconds are mutually exclusive")


@dataclass(frozen=True)
class HistoryQueryResult:
    frames: tuple[Mapping[str, Any], ...]
    events: tuple[Mapping[str, Any], ...]
    total_frames: int
    downsampled: bool


class HistoryRepository(Protocol):
    def append_sample(
        self,
        frame: HistoryFrame,
        event_changes: Sequence[HistoryEventChange] = (),
    ) -> bool:
        """追加采样；同一 session/sample_bucket 重复写入时返回 False。"""

    def query(self, request: HistoryQuery) -> HistoryQueryResult:
        ...

    def latest_frame(self, session_id: str) -> HistoryFrame | None:
        ...

    def expire(self, session_id: str) -> None:
        ...

    def ping(self) -> None:
        ...


class InMemoryHistoryRepository:
    """local 模式和单元测试使用的历史仓库。"""

    def __init__(self, *, terminal_ttl_seconds: int = 86400) -> None:
        self.terminal_ttl_seconds = int(terminal_ttl_seconds)
        self._frames: dict[str, list[HistoryFrame]] = {}
        self._events: dict[str, list[HistoryEventChange]] = {}
        self._sample_keys: set[tuple[str, int]] = set()
        self._expired: set[str] = set()
        self._lock = threading.RLock()

    def ping(self) -> None:
        return None

    def append_sample(
        self,
        frame: HistoryFrame,
        event_changes: Sequence[HistoryEventChange] = (),
    ) -> bool:
        key = (frame.session_id, frame.sample_bucket)
        with self._lock:
            if key in self._sample_keys:
                return False
            self._sample_keys.add(key)
            self._frames.setdefault(frame.session_id, []).append(frame)
            self._events.setdefault(frame.session_id, []).extend(event_changes)
            return True

    def query(self, request: HistoryQuery) -> HistoryQueryResult:
        with self._lock:
            frames = list(self._frames.get(request.session_id, ()))
            events = list(self._events.get(request.session_id, ()))
        return _query_result(frames, events, request)

    def latest_frame(self, session_id: str) -> HistoryFrame | None:
        with self._lock:
            frames = self._frames.get(str(session_id), ())
            return frames[-1] if frames else None

    def expire(self, session_id: str) -> None:
        with self._lock:
            self._expired.add(str(session_id))

    def is_expired(self, session_id: str) -> bool:
        with self._lock:
            return str(session_id) in self._expired


class RedisHistoryRepository:
    """Redis Streams 历史仓库。

    Redis 只保存紧凑历史帧和事件变化，和 simulation 的 latest snapshot 使用
    独立的 ``backend:history`` 命名空间。每个采样桶使用 SETNX marker 做幂等，
    避免 watcher 重试时重复写入。
    """

    def __init__(
        self,
        redis_url: str,
        *,
        key_prefix: str,
        terminal_ttl_seconds: int = 86400,
        socket_timeout_seconds: float = 5.0,
        client: Any = None,
    ) -> None:
        if terminal_ttl_seconds <= 0:
            raise ValueError("terminal_ttl_seconds must be positive")
        if client is None:
            try:
                import redis
            except ImportError as exc:
                raise RuntimeError("Redis history requires the 'redis' package") from exc
            client = redis.Redis.from_url(
                redis_url,
                socket_connect_timeout=socket_timeout_seconds,
                socket_timeout=socket_timeout_seconds,
                health_check_interval=15,
                decode_responses=True,
            )
        self.client = client
        self.key_prefix = key_prefix.rstrip(":")
        self.terminal_ttl_seconds = int(terminal_ttl_seconds)

    def ping(self) -> None:
        self.client.ping()

    def _base_key(self, session_id: str) -> str:
        return f"{self.key_prefix}:history:{session_id}"

    def _frames_key(self, session_id: str) -> str:
        return f"{self._base_key(session_id)}:frames"

    def _events_key(self, session_id: str) -> str:
        return f"{self._base_key(session_id)}:events"

    def _meta_key(self, session_id: str) -> str:
        return f"{self._base_key(session_id)}:meta"

    def _marker_key(self, session_id: str, sample_bucket: int) -> str:
        return f"{self._base_key(session_id)}:sample:{sample_bucket}"

    def append_sample(
        self,
        frame: HistoryFrame,
        event_changes: Sequence[HistoryEventChange] = (),
    ) -> bool:
        marker_key = self._marker_key(frame.session_id, frame.sample_bucket)
        claimed = self.client.set(
            marker_key,
            "1",
            nx=True,
            ex=self.terminal_ttl_seconds,
        )
        if not claimed:
            return False

        try:
            pipe = self.client.pipeline(transaction=True)
            pipe.xadd(
                self._frames_key(frame.session_id),
                {
                    "schema_version": str(HISTORY_SCHEMA_VERSION),
                    "sample_bucket": str(frame.sample_bucket),
                    "elapsed_seconds": repr(frame.elapsed_seconds),
                    "payload": _json_dumps(frame.to_dict()),
                },
            )
            for change in event_changes:
                pipe.xadd(
                    self._events_key(frame.session_id),
                    {
                        "schema_version": str(HISTORY_SCHEMA_VERSION),
                        "event_id": change.event_id,
                        "change_type": change.change_type,
                        "elapsed_seconds": repr(change.elapsed_seconds),
                        "payload": _json_dumps(change.to_dict()),
                    },
                )
            pipe.hset(
                self._meta_key(frame.session_id),
                mapping={
                    "schema_version": str(HISTORY_SCHEMA_VERSION),
                    "session_id": frame.session_id,
                    "latest_bucket": str(frame.sample_bucket),
                    "latest_sequence": str(frame.sequence),
                    "latest_elapsed_seconds": repr(frame.elapsed_seconds),
                    "updated_at": repr(time.time()),
                },
            )
            pipe.execute()
        except Exception:
            # marker 未成功写入数据时允许下一次 watcher 重试。
            try:
                self.client.delete(marker_key)
            except Exception:
                logger.exception("Failed to release history marker %s", marker_key)
            raise
        return True

    def query(self, request: HistoryQuery) -> HistoryQueryResult:
        try:
            frame_entries = self.client.xrange(
                self._frames_key(request.session_id), "-", "+"
            )
            event_entries = self.client.xrange(
                self._events_key(request.session_id), "-", "+"
            )
        except Exception as exc:
            raise HistoryUnavailableError(
                f"Cannot read history for session {request.session_id}"
            ) from exc
        frames = [
            frame
            for frame in (_load_frame(entry) for entry in frame_entries)
            if frame is not None
        ]
        events = [
            event
            for event in (_load_event(entry) for entry in event_entries)
            if event is not None
        ]
        return _query_result(frames, events, request)

    def latest_frame(self, session_id: str) -> HistoryFrame | None:
        try:
            entries = self.client.xrevrange(
                self._frames_key(session_id), "+", "-", count=1
            )
        except Exception as exc:
            raise HistoryUnavailableError(
                f"Cannot read latest history for session {session_id}"
            ) from exc
        if not entries:
            return None
        return _load_frame(entries[0])

    def expire(self, session_id: str) -> None:
        pipe = self.client.pipeline(transaction=True)
        for key in (
            self._frames_key(session_id),
            self._events_key(session_id),
            self._meta_key(session_id),
        ):
            pipe.expire(key, self.terminal_ttl_seconds)
        pipe.execute()


class HistoryRecorder:
    """把原始快照和一次 intelligence 输出转换为历史记录。"""

    def __init__(
        self,
        repository: HistoryRepository,
        *,
        sample_seconds: float = 5.0,
    ) -> None:
        if sample_seconds <= 0:
            raise ValueError("sample_seconds must be positive")
        self.repository = repository
        self.sample_seconds = float(sample_seconds)
        self._last_bucket: dict[str, int] = {}
        self._last_events: dict[str, dict[str, dict[str, Any]]] = {}
        self._lock = threading.RLock()

    def record(self, snapshot: Any, intelligence: Mapping[str, Any] | None) -> bool:
        session_id = str(_field(snapshot, "session_id", "") or "")
        if not session_id:
            logger.warning("Skip history frame without session_id")
            return False
        elapsed = _number(_field(snapshot, "elapsed_seconds", 0.0))
        bucket = int(elapsed // self.sample_seconds)

        with self._lock:
            previous_bucket = self._last_bucket.get(session_id)
            if previous_bucket is None:
                try:
                    previous_frame = self.repository.latest_frame(session_id)
                except Exception:
                    logger.exception(
                        "Failed to load latest history for session %s",
                        session_id,
                    )
                    previous_frame = None
                if previous_frame is not None:
                    previous_bucket = previous_frame.sample_bucket
                    self._last_bucket[session_id] = previous_bucket
                    self._last_events[session_id] = _event_records(previous_frame)
            if previous_bucket is not None and bucket <= previous_bucket:
                return False

            try:
                frame = HistoryFrame.from_snapshot(
                    snapshot,
                    intelligence,
                    sample_bucket=bucket,
                )
                current_events = _event_records(frame)
                event_changes = _diff_event_records(
                    session_id,
                    elapsed,
                    self._last_events.get(session_id, {}),
                    current_events,
                )
            except Exception:
                logger.exception(
                    "Failed to build history frame for session %s",
                    session_id,
                )
                return False
            try:
                inserted = self.repository.append_sample(frame, event_changes)
            except Exception:
                # 历史写入不能中断 SUMO 和事件检测；不前移 bucket，允许
                # 后续 watcher 对同一采样桶重试。
                logger.exception("Failed to persist history for session %s", session_id)
                return False

            # Redis marker 可能已经由上一个 watcher 写入；即使本次没有新增，
            # 本地去重状态也必须前移，否则每个后续快照都会重复尝试。
            self._last_bucket[session_id] = bucket
            self._last_events[session_id] = current_events
            return inserted

    def finalize(self, session_id: str) -> None:
        session_id = str(session_id)
        try:
            self.repository.expire(session_id)
        except Exception:
            logger.exception("Failed to expire history for session %s", session_id)
        with self._lock:
            self._last_bucket.pop(session_id, None)
            self._last_events.pop(session_id, None)


def create_history_repository(
    *,
    mode: str,
    redis_url: str,
    key_prefix: str,
    terminal_ttl_seconds: int,
    client: Any = None,
) -> HistoryRepository:
    """按 simulation manager 模式选择历史仓库。

    redis 模式严格使用 Redis；local 模式使用内存仓库，便于开发和单元测试。
    生产部署使用 redis manager 时不会静默降级为内存历史。
    """

    normalized = str(mode).strip().lower()
    if normalized == "redis":
        return RedisHistoryRepository(
            redis_url,
            key_prefix=key_prefix,
            terminal_ttl_seconds=terminal_ttl_seconds,
            client=client,
        )
    if normalized == "local":
        return InMemoryHistoryRepository(terminal_ttl_seconds=terminal_ttl_seconds)
    raise ValueError(f"Unsupported history repository mode: {mode!r}")


def _query_result(
    frames: Sequence[HistoryFrame],
    events: Sequence[HistoryEventChange],
    request: HistoryQuery,
) -> HistoryQueryResult:
    ordered_frames = sorted(frames, key=lambda item: item.elapsed_seconds)
    if not ordered_frames:
        return HistoryQueryResult((), (), 0, False)

    latest = ordered_frames[-1].elapsed_seconds
    if request.to_seconds is not None:
        to_seconds = float(request.to_seconds)
    else:
        to_seconds = latest
    if request.from_seconds is not None:
        from_seconds = float(request.from_seconds)
    elif request.lookback_seconds is not None:
        from_seconds = to_seconds - float(request.lookback_seconds)
    else:
        from_seconds = float("-inf")

    selected = [
        frame
        for frame in ordered_frames
        if from_seconds <= frame.elapsed_seconds <= to_seconds
    ]
    selected_events = [
        event
        for event in events
        if from_seconds <= event.elapsed_seconds <= to_seconds
    ]
    total_frames = len(selected)
    sampled = _downsample_frames(selected, request.max_points)
    return HistoryQueryResult(
        frames=tuple(frame.to_dict() for frame in sampled),
        events=tuple(event.to_dict() for event in selected_events),
        total_frames=total_frames,
        downsampled=len(sampled) < total_frames,
    )


def _downsample_frames(
    frames: Sequence[HistoryFrame], max_points: int
) -> list[HistoryFrame]:
    if len(frames) <= max_points:
        return list(frames)
    if max_points == 1:
        return [frames[-1]]
    last_index = len(frames) - 1
    indexes = {
        round(index * last_index / (max_points - 1))
        for index in range(max_points)
    }
    return [frames[index] for index in sorted(indexes)]


def _event_records(frame: HistoryFrame) -> dict[str, dict[str, Any]]:
    result: dict[str, dict[str, Any]] = {}
    cards = _field(frame.event_detection, "cards", ())
    card_values = (
        cards.values() if isinstance(cards, Mapping) else _sequence_values(cards)
    )
    for raw_card in card_values:
        if not isinstance(raw_card, Mapping):
            continue
        event_id = str(raw_card.get("event_id", "") or "")
        if not event_id:
            continue
        status = str(raw_card.get("status", "") or "").lower()
        if status not in {"active", "running"}:
            continue
        record = _json_safe(dict(raw_card))
        if isinstance(record, Mapping):
            result[event_id] = {**record, "source": "event_detection"}

    for raw_event in frame.events:
        if not isinstance(raw_event, Mapping):
            continue
        event_id = str(raw_event.get("event_id", "") or "")
        if not event_id:
            continue
        state = str(raw_event.get("state", "") or "").lower()
        if state not in {"active", "running"}:
            continue
        existing = result.get(event_id)
        disturbance_record = dict(raw_event)
        if not disturbance_record.get("intersection_id"):
            owners = _event_intersection_owners(frame, disturbance_record)
            if owners:
                disturbance_record["intersection_id"] = owners[0]
                disturbance_record["related_intersections"] = owners
        if existing is None:
            result[event_id] = {**disturbance_record, "source": "disturbance"}
        else:
            existing["source"] = "combined"
            existing["disturbance"] = disturbance_record
    return result


def _event_intersection_owners(
    frame: HistoryFrame, event: Mapping[str, Any]
) -> list[str]:
    lane_ids: list[str] = []
    details = _mapping_field(event, "details")
    for container in (event, details):
        for name in (
            "lane_ids",
            "lane_id",
            "venue_lane_id",
            "source_lane_ids",
            "destination_lane_ids",
        ):
            raw = _field(container, name, ())
            values = [raw] if isinstance(raw, str) else _sequence_values(raw)
            for value in values:
                lane_id = str(value)
                if lane_id and lane_id not in lane_ids:
                    lane_ids.append(lane_id)
    owners = []
    for intersection_id, intersection in frame.intersections.items():
        lanes = _mapping_field(intersection, "lanes")
        if any(lane_id in lanes for lane_id in lane_ids):
            owners.append(str(intersection_id))
    return sorted(owners)


def _diff_event_records(
    session_id: str,
    elapsed_seconds: float,
    previous: Mapping[str, Mapping[str, Any]],
    current: Mapping[str, Mapping[str, Any]],
) -> list[HistoryEventChange]:
    changes: list[HistoryEventChange] = []
    for event_id in sorted(current):
        event = current[event_id]
        old = previous.get(event_id)
        if old is None:
            changes.append(
                HistoryEventChange(
                    session_id=session_id,
                    event_id=event_id,
                    change_type="created",
                    elapsed_seconds=elapsed_seconds,
                    event=event,
                )
            )
            continue
        if _event_risk(old) != _event_risk(event):
            changes.append(
                HistoryEventChange(
                    session_id=session_id,
                    event_id=event_id,
                    change_type="risk_changed",
                    elapsed_seconds=elapsed_seconds,
                    event=event,
                    previous=old,
                )
            )
        if _event_trend(old) != _event_trend(event):
            changes.append(
                HistoryEventChange(
                    session_id=session_id,
                    event_id=event_id,
                    change_type="trend_changed",
                    elapsed_seconds=elapsed_seconds,
                    event=event,
                    previous=old,
                )
            )
        if _event_scope(old) != _event_scope(event):
            changes.append(
                HistoryEventChange(
                    session_id=session_id,
                    event_id=event_id,
                    change_type="scope_changed",
                    elapsed_seconds=elapsed_seconds,
                    event=event,
                    previous=old,
                )
            )

    for event_id in sorted(set(previous) - set(current)):
        old = dict(previous[event_id])
        old["status"] = "resolved"
        old["end_seconds"] = elapsed_seconds
        changes.append(
            HistoryEventChange(
                session_id=session_id,
                event_id=event_id,
                change_type="resolved",
                elapsed_seconds=elapsed_seconds,
                event=old,
                previous=previous[event_id],
            )
        )
    return changes


def _event_risk(event: Mapping[str, Any]) -> str:
    return str(event.get("risk", event.get("severity", "")) or "")


def _event_trend(event: Mapping[str, Any]) -> str:
    return str(event.get("trend", event.get("traffic_state", "")) or "")


def _event_scope(event: Mapping[str, Any]) -> tuple[Any, ...]:
    lane_ids = event.get("lane_ids", ())
    if isinstance(lane_ids, str):
        lane_ids = (lane_ids,)
    elif not isinstance(lane_ids, Sequence):
        lane_ids = ()
    return (
        str(event.get("intersection_id", "") or ""),
        tuple(sorted(str(item) for item in lane_ids)),
        str(event.get("edge_id", "") or ""),
    )


def _load_frame(entry: Any) -> HistoryFrame | None:
    payload = _entry_payload(entry)
    if not isinstance(payload, Mapping):
        return None
    try:
        return HistoryFrame(
            session_id=str(payload["session_id"]),
            sample_bucket=int(payload.get("sample_bucket", 0)),
            sequence=int(payload.get("sequence", 0)),
            state=str(payload.get("state", "")),
            elapsed_seconds=float(payload.get("elapsed_seconds", 0.0)),
            duration_seconds=float(payload.get("duration_seconds", 0.0)),
            progress=float(payload.get("progress", 0.0)),
            official_time=str(payload.get("official_time", "")),
            intersections=_mapping_field(payload, "intersections"),
            events=tuple(
                item
                for item in _sequence_values(payload.get("events", ()))
                if isinstance(item, Mapping)
            ),
            event_detection=_mapping_field(payload, "event_detection"),
            prediction=_mapping_field(payload, "prediction"),
            traffic_style=_mapping_field(payload, "traffic_style"),
        )
    except (KeyError, TypeError, ValueError):
        logger.warning("Skip malformed Redis history frame")
        return None


def _load_event(entry: Any) -> HistoryEventChange | None:
    payload = _entry_payload(entry)
    if not isinstance(payload, Mapping):
        return None
    try:
        return HistoryEventChange(
            session_id=str(payload["session_id"]),
            event_id=str(payload["event_id"]),
            change_type=str(payload["change_type"]),
            elapsed_seconds=float(payload.get("elapsed_seconds", 0.0)),
            event=_mapping_field(payload, "event"),
            previous=(
                _mapping_field(payload, "previous")
                if payload.get("previous") is not None
                else None
            ),
        )
    except (KeyError, TypeError, ValueError):
        logger.warning("Skip malformed Redis history event")
        return None


def _entry_payload(entry: Any) -> Mapping[str, Any] | None:
    if not isinstance(entry, (tuple, list)) or len(entry) != 2:
        return None
    fields = entry[1]
    if not isinstance(fields, Mapping):
        return None
    raw = fields.get("payload", fields.get(b"payload"))
    if raw is None:
        return None
    try:
        value = json.loads(_text(raw))
    except (TypeError, json.JSONDecodeError):
        return None
    return value if isinstance(value, Mapping) else None


def _field(value: Any, name: str, default: Any = None) -> Any:
    if isinstance(value, Mapping):
        return value.get(name, default)
    return getattr(value, name, default)


def _mapping_field(value: Any, name: str | None = None) -> Mapping[str, Any]:
    target = _field(value, name, {}) if name is not None else value
    return target if isinstance(target, Mapping) else {}


def _sequence_values(value: Any) -> list[Any]:
    if isinstance(value, Sequence) and not isinstance(value, (str, bytes)):
        return list(value)
    return []


def _string_values(value: Any) -> list[str]:
    if isinstance(value, str):
        return [value]
    return [str(item) for item in _sequence_values(value) if str(item)]


def _number(value: Any) -> float:
    try:
        result = float(value)
    except (TypeError, ValueError):
        return 0.0
    return result if result == result and abs(result) != float("inf") else 0.0


def _json_dumps(value: Any) -> str:
    return json.dumps(
        _json_safe(value),
        ensure_ascii=False,
        separators=(",", ":"),
        allow_nan=False,
    )


def _json_safe(value: Any) -> Any:
    if is_dataclass(value):
        return _json_safe(asdict(value))
    if isinstance(value, Enum):
        return value.value
    if isinstance(value, Mapping):
        return {str(key): _json_safe(item) for key, item in value.items()}
    if isinstance(value, (list, tuple, set)):
        return [_json_safe(item) for item in value]
    if isinstance(value, Path):
        return str(value)
    if isinstance(value, float):
        return value if value == value and abs(value) != float("inf") else None
    return value


def _text(value: Any) -> str:
    return value.decode("utf-8") if isinstance(value, bytes) else str(value)


__all__ = [
    "DEFAULT_HISTORY_LOOKBACK_SECONDS",
    "DEFAULT_HISTORY_MAX_POINTS",
    "DEFAULT_HISTORY_MAX_QUERY_SECONDS",
    "EVENT_CHANGE_TYPES",
    "HistoryEventChange",
    "HistoryFrame",
    "HistoryQuery",
    "HistoryQueryResult",
    "HistoryRecorder",
    "HistoryRepository",
    "HistoryUnavailableError",
    "InMemoryHistoryRepository",
    "RedisHistoryRepository",
    "create_history_repository",
]
