"""Redis persistence primitives for distributed simulation sessions."""

from __future__ import annotations

import json
import time
from typing import Any

from .codec import dumps_config, dumps_snapshot, loads_config, loads_snapshot

TERMINAL_STATES = frozenset({"STOPPED", "COMPLETED", "FAILED"})

class RedisSessionStore:
    def __init__(
        self,
        redis_url: str,
        *,
        key_prefix: str = "citypulse",
        terminal_ttl_seconds: int = 86400,
        socket_timeout_seconds: float = 5.0,
        client: Any = None,
    ) -> None:
        if terminal_ttl_seconds <= 0:
            raise ValueError("terminal_ttl_seconds must be positive.")
        if client is None:
            try:
                import redis
            except ImportError as exc:
                raise RuntimeError(
                    "Redis support requires the 'redis' package."
                ) from exc
            client = redis.Redis.from_url(
                redis_url,
                socket_connect_timeout=socket_timeout_seconds,
                socket_timeout=socket_timeout_seconds,
                health_check_interval=15,
            )
        self.client = client
        self.key_prefix = key_prefix.rstrip(":")
        self.terminal_ttl_seconds = int(terminal_ttl_seconds)

    def ping(self) -> None:
        self.client.ping()

    def key(self, session_id: str, suffix: str) -> str:
        return f"{self.key_prefix}:session:{session_id}:{suffix}"

    @property
    def sessions_key(self) -> str:
        return f"{self.key_prefix}:sessions"

    def create(self, session_id: str, config, snapshot) -> None:
        now = time.time()
        meta_key = self.key(session_id, "meta")
        with self.client.pipeline(transaction=True) as pipe:
            pipe.hset(
                meta_key,
                mapping={
                    "state": snapshot.state,
                    "task_id": session_id,
                    "created_at": repr(now),
                    "updated_at": repr(now),
                    "config": dumps_config(config),
                    "terminal_ttl_seconds": str(self.terminal_ttl_seconds),
                },
            )
            pipe.set(self.key(session_id, "snapshot"), dumps_snapshot(snapshot))
            pipe.zadd(self.sessions_key, {session_id: now})
            pipe.execute()
        self.cleanup_index()

    def cleanup_index(self) -> None:
        cutoff = time.time() - self.terminal_ttl_seconds
        for raw_id in self.client.zrangebyscore(self.sessions_key, 0, cutoff):
            session_id = _text(raw_id)
            if not self.client.exists(self.key(session_id, "meta")):
                self.client.zrem(self.sessions_key, session_id)

    def exists(self, session_id: str) -> bool:
        return bool(self.client.exists(self.key(session_id, "meta")))

    def state(self, session_id: str) -> str | None:
        raw = self.client.hget(self.key(session_id, "meta"), "state")
        return None if raw is None else _text(raw)

    def updated_at(self, session_id: str) -> float | None:
        raw = self.client.hget(self.key(session_id, "meta"), "updated_at")
        return None if raw is None else float(_text(raw))

    def config(self, session_id: str):
        raw = self.client.hget(self.key(session_id, "meta"), "config")
        if raw is None:
            return None
        return loads_config(raw)

    def session_ttl(self, session_id: str) -> int | None:
        raw = self.client.hget(
            self.key(session_id, "meta"), "terminal_ttl_seconds"
        )
        return None if raw is None else int(_text(raw))

    def snapshot(self, session_id: str):
        raw = self.client.get(self.key(session_id, "snapshot"))
        if raw is None:
            return None
        return loads_snapshot(raw)

    def publish(self, snapshot) -> None:
        raw = dumps_snapshot(snapshot)
        now = repr(time.time())
        with self.client.pipeline(transaction=True) as pipe:
            pipe.hset(
                self.key(snapshot.session_id, "meta"),
                mapping={"state": snapshot.state, "updated_at": now},
            )
            pipe.set(self.key(snapshot.session_id, "snapshot"), raw)
            pipe.publish(self.key(snapshot.session_id, "updates"), raw)
            pipe.execute()
        if snapshot.state in TERMINAL_STATES:
            self.expire_session(snapshot.session_id)

    def compare_and_publish(self, expected_state: str, snapshot) -> bool:
        raw = dumps_snapshot(snapshot)
        meta_key = self.key(snapshot.session_id, "meta")
        while True:
            pipe = self.client.pipeline()
            try:
                pipe.watch(meta_key)
                current = pipe.hget(meta_key, "state")
                if current is None or _text(current) != expected_state:
                    pipe.unwatch()
                    return False
                pipe.multi()
                pipe.hset(
                    meta_key,
                    mapping={
                        "state": snapshot.state,
                        "updated_at": repr(time.time()),
                    },
                )
                pipe.set(self.key(snapshot.session_id, "snapshot"), raw)
                pipe.publish(self.key(snapshot.session_id, "updates"), raw)
                pipe.execute()
                changed = True
                break
            except Exception as exc:
                if type(exc).__name__ != "WatchError":
                    raise
            finally:
                pipe.reset()
        if changed and snapshot.state in TERMINAL_STATES:
            self.expire_session(snapshot.session_id)
        return changed

    def expire_session(self, session_id: str) -> None:
        keys = (
            self.key(session_id, "meta"),
            self.key(session_id, "snapshot"),
            self.key(session_id, "commands"),
            self.key(session_id, "heartbeat"),
            self.key(session_id, "processed_commands"),
        )
        with self.client.pipeline(transaction=True) as pipe:
            for key in keys:
                pipe.expire(key, self.terminal_ttl_seconds)
            pipe.execute()

    def enqueue_command(
        self, session_id: str, command_id: str, name: str, payload: object
    ) -> None:
        raw = json.dumps(
            {
                "schema_version": 1,
                "command_id": command_id,
                "name": name,
                "payload": payload,
            },
            ensure_ascii=False,
            separators=(",", ":"),
            allow_nan=False,
        )
        self.client.rpush(self.key(session_id, "commands"), raw)

    def pop_command(self, session_id: str, timeout: float = 0.0):
        key = self.key(session_id, "commands")
        if timeout > 0:
            result = self.client.blpop(key, timeout=timeout)
            raw = None if result is None else result[1]
        else:
            raw = self.client.lpop(key)
        if raw is None:
            return None
        value = json.loads(raw)
        if int(value.get("schema_version", 0)) != 1:
            raise ValueError("Unsupported command schema version.")
        return value

    def claim_command(self, session_id: str, command_id: str) -> bool:
        return bool(
            self.client.sadd(
                self.key(session_id, "processed_commands"), command_id
            )
        )

    def acknowledge(
        self, session_id: str, command_id: str, error: BaseException | None
    ) -> None:
        payload = {
            "ok": error is None,
            "error_type": None if error is None else type(error).__name__,
            "error": None if error is None else str(error),
        }
        key = self.key(session_id, f"ack:{command_id}")
        with self.client.pipeline(transaction=True) as pipe:
            pipe.rpush(key, json.dumps(payload, ensure_ascii=False))
            pipe.expire(key, max(60, self.terminal_ttl_seconds))
            pipe.execute()

    def wait_for_ack(self, session_id: str, command_id: str, timeout: float):
        result = self.client.blpop(
            self.key(session_id, f"ack:{command_id}"), timeout=timeout
        )
        if result is None:
            return None
        return json.loads(result[1])

    def fail_pending_commands(self, session_id: str, message: str) -> None:
        while True:
            value = self.pop_command(session_id)
            if value is None:
                return
            self.acknowledge(
                session_id,
                str(value["command_id"]),
                RuntimeError(message),
            )

    def heartbeat(self, session_id: str, ttl_seconds: int) -> None:
        self.client.set(
            self.key(session_id, "heartbeat"), repr(time.time()), ex=ttl_seconds
        )

    def heartbeat_alive(self, session_id: str) -> bool:
        return bool(self.client.exists(self.key(session_id, "heartbeat")))

    def pubsub(self, session_id: str):
        value = self.client.pubsub(ignore_subscribe_messages=True)
        value.subscribe(self.key(session_id, "updates"))
        return value


def _text(value: str | bytes) -> str:
    return value.decode("utf-8") if isinstance(value, bytes) else str(value)
