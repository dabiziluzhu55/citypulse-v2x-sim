"""Redis/Celery simulation client for Backend (no local SUMO)."""

from __future__ import annotations

import os
import queue
import time
from dataclasses import replace
from pathlib import Path
from typing import Mapping
from uuid import uuid4

from simulation_protocol.ai_control import AIControlStatus
from simulation_protocol.catalog import load_catalog
from simulation_protocol.codec import dumps_snapshot, encode_command_payload, loads_snapshot
from simulation_protocol.dto import SimulationConfig, SimulationSnapshot
from simulation_protocol.events import DisturbanceEvent, EventValidationError
from simulation_protocol.exceptions import SessionError, UnknownSessionError
from simulation_protocol.playback import normalize_playback_speed
from simulation_protocol.store import RedisSessionStore, TERMINAL_STATES
from simulation_protocol.validation import ScenarioCompilationError, validate_simulation_config

DEFAULT_GENERATED_DIR = Path("data/maps/sumo/generated")
DEFAULT_SESSION_ROOT = Path("outputs/sessions")


def _format_clock(seconds: float) -> str:
    value = int(seconds) % 86400
    return f"{value // 3600:02d}:{value % 3600 // 60:02d}:{value % 60:02d}"

ACTIVE_STATES = frozenset({"STARTING", "RUNNING", "PAUSED", "STOPPING"})


class RedisUnavailableError(SessionError):
    pass


class RedisSimulationClient:
    def __init__(
        self,
        *,
        redis_url: str | None = None,
        generated_dir: Path = DEFAULT_GENERATED_DIR,
        session_root: Path = DEFAULT_SESSION_ROOT,
        terminal_ttl_seconds: int | None = None,
        command_timeout_seconds: float | None = None,
        heartbeat_ttl_seconds: int | None = None,
        store=None,
        celery_app=None,
    ) -> None:
        self.generated_dir = Path(generated_dir)
        self.session_root = Path(session_root)
        self.command_timeout_seconds = float(
            command_timeout_seconds
            if command_timeout_seconds is not None
            else os.getenv("CITYPULSE_COMMAND_TIMEOUT_SECONDS", "30")
        )
        self.heartbeat_ttl_seconds = int(
            heartbeat_ttl_seconds
            if heartbeat_ttl_seconds is not None
            else os.getenv("CITYPULSE_WORKER_HEARTBEAT_TTL_SECONDS", "15")
        )
        ttl = int(
            terminal_ttl_seconds
            if terminal_ttl_seconds is not None
            else os.getenv("CITYPULSE_SESSION_TTL_SECONDS", "86400")
        )
        if self.command_timeout_seconds <= 0 or self.heartbeat_ttl_seconds <= 0:
            raise ValueError("Command and heartbeat timeouts must be positive.")
        if store is None:
            state_url = redis_url or os.getenv(
                "CITYPULSE_REDIS_STATE_URL", "redis://127.0.0.1:6379/1"
            )
            try:
                store = RedisSessionStore(
                    state_url,
                    key_prefix=os.getenv(
                        "CITYPULSE_REDIS_KEY_PREFIX", "citypulse"
                    ),
                    terminal_ttl_seconds=ttl,
                )
                store.ping()
            except Exception as exc:
                raise RedisUnavailableError(
                    f"Cannot connect to Redis session store: {exc}"
                ) from exc
        self._store = store
        self._celery_app = celery_app or _default_celery_app()
        self._catalog = None

    def catalog(self):
        if self._catalog is None:
            self._catalog = load_catalog(self.generated_dir)
        return self._catalog

    def start(self, config: SimulationConfig) -> str:
        if config.gui:
            raise ScenarioCompilationError(
                "Redis/Celery sessions are headless; use the local manager for sumo-gui."
            )
        validate_simulation_config(config, self.catalog())
        session_id = str(uuid4())
        playback_speed = (
            normalize_playback_speed(config.playback_speed)
            if config.playback_speed is not None
            else (1.0 if config.realtime or config.start_paused else None)
        )
        duration_seconds = float(config.duration_seconds or 0.0)
        snapshot = SimulationSnapshot(
            session_id=session_id,
            state="QUEUED",
            sequence=0,
            elapsed_seconds=0.0,
            duration_seconds=duration_seconds,
            progress=0.0,
            official_time=_format_clock(config.window_start_seconds),
            playback_speed=playback_speed,
            ai_takeover=AIControlStatus(
                baseline_controller=(
                    config.baseline_controller or config.control_mode
                )
            ),
        )
        try:
            self._store.create(session_id, config, snapshot)
        except Exception as exc:
            raise RedisUnavailableError(
                f"Cannot create Redis session state: {exc}"
            ) from exc
        try:
            self._celery_app.send_task(
                "citypulse.sumo.run_session",
                args=[session_id],
                task_id=session_id,
                queue="citypulse-sumo",
            )
        except Exception as exc:
            failed = replace(snapshot, state="FAILED", error=f"Queue submission failed: {exc}")
            try:
                self._store.publish(failed)
            except Exception:
                pass
            raise SessionError(f"Cannot enqueue SUMO session: {exc}") from exc
        return session_id

    def snapshot(self, session_id: str) -> SimulationSnapshot:
        snapshot = self._get_snapshot(session_id)
        return self._reconcile(snapshot)

    def subscribe(self, session_id: str):
        try:
            exists = self._store.exists(session_id)
        except Exception as exc:
            raise RedisUnavailableError(
                f"Cannot subscribe to Redis session state: {exc}"
            ) from exc
        if not exists:
            raise UnknownSessionError(f"Unknown session: {session_id}")
        try:
            return RedisSnapshotSubscription(self, session_id)
        except Exception as exc:
            if isinstance(exc, (UnknownSessionError, RedisUnavailableError)):
                raise
            raise RedisUnavailableError(
                f"Cannot open Redis snapshot subscription: {exc}"
            ) from exc

    def wait(
        self, session_id: str, timeout: float | None = None
    ) -> SimulationSnapshot:
        current = self.snapshot(session_id)
        if current.state in TERMINAL_STATES:
            return current
        subscription = self.subscribe(session_id)
        deadline = None if timeout is None else time.monotonic() + timeout
        try:
            while True:
                remaining = (
                    None if deadline is None else max(0.0, deadline - time.monotonic())
                )
                if remaining == 0.0:
                    raise TimeoutError(
                        f"Session {session_id} did not finish before the timeout."
                    )
                try:
                    snapshot = subscription.get(timeout=remaining)
                except queue.Empty as exc:
                    raise TimeoutError(
                        f"Session {session_id} did not finish before the timeout."
                    ) from exc
                if snapshot.state in TERMINAL_STATES:
                    return snapshot
        finally:
            subscription.close()

    def stop(self, session_id: str) -> None:
        snapshot = self.snapshot(session_id)
        if snapshot.state == "QUEUED":
            stopped = replace(
                snapshot,
                state="STOPPED",
                sequence=snapshot.sequence + 1,
            )
            if self._store.compare_and_publish("QUEUED", stopped):
                try:
                    self._celery_app.control.revoke(session_id, terminate=False)
                except Exception:
                    # The task also uses a QUEUED -> STARTING compare-and-set, so it
                    # cannot start after this state transition even if revoke fails.
                    pass
                return
            snapshot = self.snapshot(session_id)
        if snapshot.state in ACTIVE_STATES:
            self._command(session_id, "stop")
            return
        raise SessionError(f"Session {session_id} is not active.")

    def pause(self, session_id: str) -> None:
        self.set_playing(session_id, False)

    def resume(self, session_id: str) -> None:
        self.set_playing(session_id, True)

    def set_playing(self, session_id: str, playing: bool) -> None:
        if not isinstance(playing, bool):
            raise ValueError("playing must be a boolean.")
        self._command(session_id, "resume" if playing else "pause")

    def set_playback_speed(self, session_id: str, speed: float) -> None:
        self._command(
            session_id, "set_playback_speed", _normalize_playback_speed(speed)
        )

    def add_event(self, session_id: str, event: DisturbanceEvent) -> str:
        if bool(getattr(event, "ai_control_enabled", False)):
            raise EventValidationError(
                "AI-controlled disturbance events must be configured at session start."
            )
        if not event.event_id:
            event = replace(event, event_id=str(uuid4()))
        self._command(session_id, "add_event", event)
        return event.event_id

    def cancel_event(self, session_id: str, event_id: str) -> None:
        self._command(session_id, "cancel_event", event_id)

    def install_ai_plan(self, session_id: str, payload: Mapping[str, object]) -> None:
        if not isinstance(payload, Mapping):
            raise ValueError("AI plan payload must be an object.")
        self._command(session_id, "install_ai_plan", dict(payload))

    def fallback_ai_control(
        self, session_id: str, payload: Mapping[str, object]
    ) -> None:
        if not isinstance(payload, Mapping):
            raise ValueError("AI fallback payload must be an object.")
        self._command(session_id, "fallback_ai_control", dict(payload))

    def release_ai_control(self, session_id: str, reason: str = "released") -> None:
        self._command(session_id, "release_ai_control", str(reason))

    def _command(self, session_id: str, name: str, payload: object = None) -> None:
        snapshot = self.snapshot(session_id)
        if snapshot.state == "QUEUED":
            raise SessionError(
                f"Session {session_id} is queued; only stop is available before startup."
            )
        if snapshot.state not in ACTIVE_STATES:
            raise SessionError(f"Session {session_id} is not active.")
        command_id = str(uuid4())
        try:
            self._store.enqueue_command(
                session_id,
                command_id,
                name,
                encode_command_payload(name, payload),
            )
            result = self._store.wait_for_ack(
                session_id, command_id, self.command_timeout_seconds
            )
        except Exception as exc:
            raise RedisUnavailableError(f"Redis command failed: {exc}") from exc
        if result is None:
            raise TimeoutError(f"Session command {name} timed out.")
        if not result.get("ok"):
            message = str(result.get("error") or f"Session command {name} failed.")
            error_type = result.get("error_type")
            if error_type == "EventValidationError":
                raise EventValidationError(message)
            if error_type in {"ValueError", "AIControlValidationError"}:
                raise ValueError(message)
            raise SessionError(message)

    def _get_snapshot(self, session_id: str) -> SimulationSnapshot:
        try:
            snapshot = self._store.snapshot(session_id)
        except Exception as exc:
            raise RedisUnavailableError(f"Cannot read Redis session state: {exc}") from exc
        if snapshot is None:
            raise UnknownSessionError(f"Unknown session: {session_id}")
        return snapshot

    def _reconcile(self, snapshot: SimulationSnapshot) -> SimulationSnapshot:
        if snapshot.state not in ACTIVE_STATES:
            return snapshot
        try:
            if self._store.heartbeat_alive(snapshot.session_id):
                return snapshot
            updated_at = self._store.updated_at(snapshot.session_id)
            if updated_at is None or time.time() - updated_at <= self.heartbeat_ttl_seconds:
                return snapshot
            result_state = self._celery_app.AsyncResult(snapshot.session_id).state
            if result_state not in {"FAILURE", "REVOKED", "SUCCESS"}:
                return snapshot
            failed = replace(
                snapshot,
                state="FAILED",
                sequence=snapshot.sequence + 1,
                error=f"SUMO worker stopped unexpectedly (Celery state {result_state}).",
            )
            self._store.publish(failed)
            return failed
        except Exception as exc:
            if type(exc).__module__.startswith("redis"):
                raise RedisUnavailableError(
                    f"Cannot reconcile Redis session state: {exc}"
                ) from exc
            return snapshot


class RedisSnapshotSubscription:
    def __init__(self, manager: RedisSimulationClient, session_id: str) -> None:
        self._manager = manager
        self._session_id = session_id
        self._pubsub = manager._store.pubsub(session_id)
        self._initial = manager.snapshot(session_id)
        self._last_raw = dumps_snapshot(self._initial)
        self._closed = False

    def get(self, timeout: float | None = None) -> SimulationSnapshot:
        if self._closed:
            raise RuntimeError("Subscription is closed.")
        if self._initial is not None:
            value = self._initial
            self._initial = None
            return value
        deadline = None if timeout is None else time.monotonic() + timeout
        while True:
            remaining = (
                1.0
                if deadline is None
                else max(0.0, min(1.0, deadline - time.monotonic()))
            )
            if deadline is not None and remaining == 0.0:
                raise queue.Empty
            try:
                message = self._pubsub.get_message(timeout=remaining)
            except Exception as exc:
                raise RedisUnavailableError(
                    f"Redis snapshot subscription failed: {exc}"
                ) from exc
            if message is not None and message.get("type") == "message":
                raw = message["data"]
                raw_text = raw.decode("utf-8") if isinstance(raw, bytes) else str(raw)
                if raw_text != self._last_raw:
                    self._last_raw = raw_text
                    return loads_snapshot(raw_text)
            current = self._manager.snapshot(self._session_id)
            current_raw = dumps_snapshot(current)
            if current_raw != self._last_raw:
                self._last_raw = current_raw
                return current

    def close(self) -> None:
        if not self._closed:
            self._pubsub.close()
            self._closed = True


def _default_celery_app():
    try:
        from .celery_client import get_celery_client
    except ImportError as exc:
        raise RuntimeError(
            "RedisSimulationClient requires Celery; install celery in Backend."
        ) from exc
    return get_celery_client()
