"""Backend 会话元数据仓库：独立于 simulation Redis key 命名空间。

Key 约定（redis 模式）：
  {prefix}:backend:sessions                 ZSET score=created_at
  {prefix}:backend:session:{id}             HASH 元数据
  {prefix}:backend:session:{id}:metrics     STRING JSON 指标
  {prefix}:backend:metrics_lock:{id}        指标 watcher 分布式锁
"""

from __future__ import annotations

import json
import logging
import threading
import time
import uuid
from dataclasses import asdict, dataclass, field
from typing import Any, Optional, Protocol

logger = logging.getLogger(__name__)

TERMINAL_STATES = frozenset({"STOPPED", "COMPLETED", "FAILED"})
NON_TERMINAL_STATES = frozenset(
    {"QUEUED", "STARTING", "RUNNING", "PAUSED", "STOPPING"}
)


@dataclass
class SessionMetadata:
    session_id: str
    control_mode: str
    scenario_preset_id: str
    state: str
    created_at: float
    updated_at: float
    metrics_status: str = "pending"  # pending | collecting | finalized | aborted
    progress: float = 0.0

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


class SessionMetadataStore(Protocol):
    def ping(self) -> None: ...

    def upsert(
        self,
        session_id: str,
        *,
        control_mode: str,
        scenario_preset_id: str,
        state: str,
        progress: float = 0.0,
        metrics_status: str | None = None,
    ) -> SessionMetadata: ...

    def update(
        self,
        session_id: str,
        *,
        state: str | None = None,
        progress: float | None = None,
        metrics_status: str | None = None,
    ) -> SessionMetadata | None: ...

    def get(self, session_id: str) -> SessionMetadata | None: ...

    def list_sessions(
        self,
        *,
        state: str | None = None,
        offset: int = 0,
        limit: int = 50,
    ) -> tuple[list[SessionMetadata], int]: ...

    def list_non_terminal(self) -> list[SessionMetadata]: ...

    def save_metrics(self, session_id: str, payload: dict[str, Any]) -> None: ...

    def get_metrics(self, session_id: str) -> dict[str, Any] | None: ...

    def try_acquire_metrics_lock(
        self, session_id: str, *, owner: str, ttl_seconds: int = 30
    ) -> bool: ...

    def refresh_metrics_lock(
        self, session_id: str, *, owner: str, ttl_seconds: int = 30
    ) -> bool: ...

    def release_metrics_lock(self, session_id: str, *, owner: str) -> None: ...


def create_session_metadata_store(
    *,
    mode: str,
    redis_url: str,
    key_prefix: str,
    terminal_ttl_seconds: int,
    client: Any = None,
) -> SessionMetadataStore:
    if mode == "redis":
        return RedisSessionMetadataStore(
            redis_url,
            key_prefix=key_prefix,
            terminal_ttl_seconds=terminal_ttl_seconds,
            client=client,
        )
    return InMemorySessionMetadataStore(terminal_ttl_seconds=terminal_ttl_seconds)


class InMemorySessionMetadataStore:
    """local 模式与单元测试使用的进程内元数据仓库。"""

    def __init__(self, *, terminal_ttl_seconds: int = 86400) -> None:
        self.terminal_ttl_seconds = int(terminal_ttl_seconds)
        self._lock = threading.RLock()
        self._sessions: dict[str, SessionMetadata] = {}
        self._metrics: dict[str, dict[str, Any]] = {}
        self._locks: dict[str, tuple[str, float]] = {}

    def ping(self) -> None:
        return None

    def upsert(
        self,
        session_id: str,
        *,
        control_mode: str,
        scenario_preset_id: str,
        state: str,
        progress: float = 0.0,
        metrics_status: str | None = None,
    ) -> SessionMetadata:
        now = time.time()
        with self._lock:
            existing = self._sessions.get(session_id)
            meta = SessionMetadata(
                session_id=session_id,
                control_mode=control_mode,
                scenario_preset_id=scenario_preset_id,
                state=state,
                created_at=existing.created_at if existing else now,
                updated_at=now,
                metrics_status=(
                    metrics_status
                    if metrics_status is not None
                    else (existing.metrics_status if existing else "pending")
                ),
                progress=float(progress),
            )
            self._sessions[session_id] = meta
            return meta

    def update(
        self,
        session_id: str,
        *,
        state: str | None = None,
        progress: float | None = None,
        metrics_status: str | None = None,
    ) -> SessionMetadata | None:
        with self._lock:
            meta = self._sessions.get(session_id)
            if meta is None:
                return None
            if state is not None:
                meta.state = state
            if progress is not None:
                meta.progress = float(progress)
            if metrics_status is not None:
                meta.metrics_status = metrics_status
            meta.updated_at = time.time()
            return meta

    def get(self, session_id: str) -> SessionMetadata | None:
        with self._lock:
            return self._sessions.get(session_id)

    def list_sessions(
        self,
        *,
        state: str | None = None,
        offset: int = 0,
        limit: int = 50,
    ) -> tuple[list[SessionMetadata], int]:
        with self._lock:
            items = list(self._sessions.values())
        items.sort(key=lambda item: item.created_at, reverse=True)
        if state:
            items = [item for item in items if item.state == state]
        total = len(items)
        return items[offset : offset + max(0, limit)], total

    def list_non_terminal(self) -> list[SessionMetadata]:
        with self._lock:
            return [
                meta
                for meta in self._sessions.values()
                if meta.state not in TERMINAL_STATES
            ]

    def save_metrics(self, session_id: str, payload: dict[str, Any]) -> None:
        with self._lock:
            self._metrics[session_id] = dict(payload)
            self.update(session_id, metrics_status="finalized")

    def get_metrics(self, session_id: str) -> dict[str, Any] | None:
        with self._lock:
            payload = self._metrics.get(session_id)
            return None if payload is None else dict(payload)

    def try_acquire_metrics_lock(
        self, session_id: str, *, owner: str, ttl_seconds: int = 30
    ) -> bool:
        now = time.time()
        with self._lock:
            current = self._locks.get(session_id)
            if current is not None and current[1] > now and current[0] != owner:
                return False
            self._locks[session_id] = (owner, now + ttl_seconds)
            return True

    def refresh_metrics_lock(
        self, session_id: str, *, owner: str, ttl_seconds: int = 30
    ) -> bool:
        now = time.time()
        with self._lock:
            current = self._locks.get(session_id)
            if current is None or current[0] != owner:
                return False
            self._locks[session_id] = (owner, now + ttl_seconds)
            return True

    def release_metrics_lock(self, session_id: str, *, owner: str) -> None:
        with self._lock:
            current = self._locks.get(session_id)
            if current is not None and current[0] == owner:
                self._locks.pop(session_id, None)


class RedisSessionMetadataStore:
    """redis 模式：backend 独立命名空间的会话元数据。"""

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
            raise ValueError("terminal_ttl_seconds must be positive.")
        if client is None:
            import redis

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

    def _session_key(self, session_id: str) -> str:
        return f"{self.key_prefix}:session:{session_id}"

    def _metrics_key(self, session_id: str) -> str:
        return f"{self.key_prefix}:session:{session_id}:metrics"

    def _lock_key(self, session_id: str) -> str:
        return f"{self.key_prefix}:metrics_lock:{session_id}"

    @property
    def _index_key(self) -> str:
        return f"{self.key_prefix}:sessions"

    def upsert(
        self,
        session_id: str,
        *,
        control_mode: str,
        scenario_preset_id: str,
        state: str,
        progress: float = 0.0,
        metrics_status: str | None = None,
    ) -> SessionMetadata:
        now = time.time()
        key = self._session_key(session_id)
        existing = self.client.hgetall(key)
        created_at = float(existing.get("created_at", now)) if existing else now
        status = (
            metrics_status
            if metrics_status is not None
            else existing.get("metrics_status", "pending")
            if existing
            else "pending"
        )
        mapping = {
            "session_id": session_id,
            "control_mode": control_mode,
            "scenario_preset_id": scenario_preset_id,
            "state": state,
            "created_at": repr(created_at),
            "updated_at": repr(now),
            "metrics_status": status,
            "progress": repr(float(progress)),
        }
        pipe = self.client.pipeline(transaction=True)
        pipe.hset(key, mapping=mapping)
        pipe.zadd(self._index_key, {session_id: created_at})
        if state in TERMINAL_STATES:
            pipe.expire(key, self.terminal_ttl_seconds)
            pipe.expire(self._metrics_key(session_id), self.terminal_ttl_seconds)
        pipe.execute()
        return SessionMetadata(
            session_id=session_id,
            control_mode=control_mode,
            scenario_preset_id=scenario_preset_id,
            state=state,
            created_at=created_at,
            updated_at=now,
            metrics_status=status,
            progress=float(progress),
        )

    def update(
        self,
        session_id: str,
        *,
        state: str | None = None,
        progress: float | None = None,
        metrics_status: str | None = None,
    ) -> SessionMetadata | None:
        key = self._session_key(session_id)
        if not self.client.exists(key):
            return None
        mapping: dict[str, str] = {"updated_at": repr(time.time())}
        if state is not None:
            mapping["state"] = state
        if progress is not None:
            mapping["progress"] = repr(float(progress))
        if metrics_status is not None:
            mapping["metrics_status"] = metrics_status
        pipe = self.client.pipeline(transaction=True)
        pipe.hset(key, mapping=mapping)
        if state in TERMINAL_STATES:
            pipe.expire(key, self.terminal_ttl_seconds)
            pipe.expire(self._metrics_key(session_id), self.terminal_ttl_seconds)
        pipe.execute()
        return self.get(session_id)

    def get(self, session_id: str) -> SessionMetadata | None:
        raw = self.client.hgetall(self._session_key(session_id))
        if not raw:
            return None
        return _meta_from_mapping(raw)

    def list_sessions(
        self,
        *,
        state: str | None = None,
        offset: int = 0,
        limit: int = 50,
    ) -> tuple[list[SessionMetadata], int]:
        # newest first
        ids = [
            _text(item)
            for item in self.client.zrevrange(self._index_key, 0, -1)
        ]
        items: list[SessionMetadata] = []
        for session_id in ids:
            meta = self.get(session_id)
            if meta is None:
                self.client.zrem(self._index_key, session_id)
                continue
            if state and meta.state != state:
                continue
            items.append(meta)
        total = len(items)
        return items[offset : offset + max(0, limit)], total

    def list_non_terminal(self) -> list[SessionMetadata]:
        items, _ = self.list_sessions(limit=10_000)
        return [item for item in items if item.state not in TERMINAL_STATES]

    def save_metrics(self, session_id: str, payload: dict[str, Any]) -> None:
        key = self._metrics_key(session_id)
        self.client.set(
            key,
            json.dumps(payload, ensure_ascii=False),
            ex=self.terminal_ttl_seconds,
        )
        self.update(session_id, metrics_status="finalized")

    def get_metrics(self, session_id: str) -> dict[str, Any] | None:
        raw = self.client.get(self._metrics_key(session_id))
        if raw is None:
            return None
        return json.loads(raw)

    def try_acquire_metrics_lock(
        self, session_id: str, *, owner: str, ttl_seconds: int = 30
    ) -> bool:
        return bool(
            self.client.set(
                self._lock_key(session_id),
                owner,
                nx=True,
                ex=max(1, int(ttl_seconds)),
            )
        )

    def refresh_metrics_lock(
        self, session_id: str, *, owner: str, ttl_seconds: int = 30
    ) -> bool:
        key = self._lock_key(session_id)
        current = self.client.get(key)
        if current is None or _text(current) != owner:
            return False
        return bool(self.client.expire(key, max(1, int(ttl_seconds))))

    def release_metrics_lock(self, session_id: str, *, owner: str) -> None:
        key = self._lock_key(session_id)
        current = self.client.get(key)
        if current is not None and _text(current) == owner:
            self.client.delete(key)


def new_lock_owner() -> str:
    return f"{uuid.uuid4().hex}"


def _meta_from_mapping(raw: dict[str, Any]) -> SessionMetadata:
    return SessionMetadata(
        session_id=_text(raw.get("session_id", "")),
        control_mode=_text(raw.get("control_mode", "")),
        scenario_preset_id=_text(raw.get("scenario_preset_id", "")),
        state=_text(raw.get("state", "")),
        created_at=float(_text(raw.get("created_at", "0"))),
        updated_at=float(_text(raw.get("updated_at", "0"))),
        metrics_status=_text(raw.get("metrics_status", "pending")),
        progress=float(_text(raw.get("progress", "0"))),
    )


def _text(value: Any) -> str:
    if isinstance(value, bytes):
        return value.decode("utf-8")
    return str(value)
