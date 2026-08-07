"""Celery task that owns one isolated libsumo session."""

from __future__ import annotations

import os
import threading
import time
from dataclasses import replace
from pathlib import Path

from ..scenario import DEFAULT_GENERATED_DIR, DEFAULT_SESSION_ROOT, load_compiled_scenario
from ..session import SimulationManager, _SessionRecord
from .celery_app import app
from .commands import RedisCommandQueue
from .store import RedisSessionStore, TERMINAL_STATES


class _RedisWorkerManager(SimulationManager):
    def __init__(self, store, **kwargs) -> None:
        super().__init__(**kwargs)
        self.store = store

    def _publish(self, record, snapshot) -> None:
        deadline = time.monotonic() + 10.0
        delay = 0.1
        while True:
            try:
                self.store.publish(snapshot)
                record.snapshot = snapshot
                return
            except Exception:
                if time.monotonic() >= deadline:
                    raise
                time.sleep(delay)
                delay = min(delay * 2.0, 1.0)


class _Heartbeat:
    def __init__(self, store, session_id: str, interval: float, ttl: int) -> None:
        self.store = store
        self.session_id = session_id
        self.interval = interval
        self.ttl = ttl
        self._stop = threading.Event()
        self._thread = threading.Thread(
            target=self._run,
            name=f"sumo-heartbeat-{session_id[:8]}",
            daemon=True,
        )

    def start(self) -> None:
        self.store.heartbeat(self.session_id, self.ttl)
        self._thread.start()

    def close(self) -> None:
        self._stop.set()
        self._thread.join(timeout=self.interval + 1.0)

    def _run(self) -> None:
        while not self._stop.wait(self.interval):
            try:
                self.store.heartbeat(self.session_id, self.ttl)
            except Exception:
                pass


@app.task(bind=True, name="citypulse.sumo.run_session", max_retries=0)
def run_session(self, session_id: str):
    state_url = os.getenv(
        "CITYPULSE_REDIS_STATE_URL", "redis://127.0.0.1:6379/1"
    )
    generated_dir = Path(
        os.getenv("CITYPULSE_SUMO_GENERATED_DIR", str(DEFAULT_GENERATED_DIR))
    )
    session_root = Path(
        os.getenv("CITYPULSE_SUMO_SESSION_ROOT", str(DEFAULT_SESSION_ROOT))
    )
    ttl = int(os.getenv("CITYPULSE_SESSION_TTL_SECONDS", "86400"))
    heartbeat_interval = float(
        os.getenv("CITYPULSE_WORKER_HEARTBEAT_SECONDS", "5")
    )
    heartbeat_ttl = int(
        os.getenv("CITYPULSE_WORKER_HEARTBEAT_TTL_SECONDS", "15")
    )
    if heartbeat_interval <= 0 or heartbeat_ttl <= heartbeat_interval:
        raise RuntimeError(
            "Worker heartbeat TTL must be greater than its positive interval."
        )
    store = RedisSessionStore(
        state_url,
        key_prefix=os.getenv("CITYPULSE_REDIS_KEY_PREFIX", "citypulse"),
        terminal_ttl_seconds=ttl,
    )
    initial = store.snapshot(session_id)
    config = store.config(session_id)
    if initial is None or config is None:
        raise RuntimeError(f"Redis session {session_id!r} does not exist.")
    stored_ttl = store.session_ttl(session_id)
    if stored_ttl is not None:
        store.terminal_ttl_seconds = stored_ttl

    heartbeat = _Heartbeat(
        store, session_id, heartbeat_interval, heartbeat_ttl
    )
    heartbeat.start()
    try:
        starting = replace(
            initial,
            state="STARTING",
            sequence=initial.sequence + 1,
        )
        if not store.compare_and_publish("QUEUED", starting):
            return {"session_id": session_id, "state": store.state(session_id)}

        scenario = load_compiled_scenario(
            session_id,
            generated_dir=generated_dir,
            session_root=session_root,
        )
        manager = _RedisWorkerManager(
            store,
            generated_dir=generated_dir,
            session_root=session_root,
        )
        record = _SessionRecord(
            session_id=session_id,
            config=config,
            scenario=scenario,
            commands=RedisCommandQueue(store, session_id),
            snapshot=starting,
            paused=config.start_paused,
            playback_speed=starting.playback_speed,
        )
        manager._run_worker(record)
        final = store.snapshot(session_id)
        return {
            "session_id": session_id,
            "state": None if final is None else final.state,
        }
    except BaseException as exc:
        try:
            latest = store.snapshot(session_id) or initial
        except Exception:
            latest = initial
        if latest.state not in TERMINAL_STATES:
            try:
                store.publish(
                    replace(
                        latest,
                        state="FAILED",
                        sequence=latest.sequence + 1,
                        error=str(exc),
                    )
                )
            except Exception:
                pass
        try:
            store.fail_pending_commands(
                session_id, "Session failed before the command was processed."
            )
        except Exception:
            pass
        raise
    finally:
        heartbeat.close()
