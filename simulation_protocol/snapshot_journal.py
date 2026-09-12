"""Ordered evaluation samples on the shared session volume, independent of UI delivery."""
from __future__ import annotations

from contextlib import closing
import queue
import sqlite3
import time
import zlib
from pathlib import Path

from .codec import dumps_snapshot, loads_snapshot
from .store import TERMINAL_STATES


def append_snapshot(directory: Path, snapshot, *, raw: str | None = None) -> None:
    directory.mkdir(parents=True, exist_ok=True)
    with closing(sqlite3.connect(directory / "evaluation.sqlite", timeout=10)) as db:
        db.execute("CREATE TABLE IF NOT EXISTS snapshots (sequence INTEGER PRIMARY KEY, payload BLOB NOT NULL)")
        db.execute(
            "INSERT OR IGNORE INTO snapshots VALUES (?, ?)",
            (snapshot.sequence, zlib.compress((raw if raw is not None else dumps_snapshot(snapshot)).encode("utf-8"), level=1)),
        )
        db.commit()


class JournalSubscription:
    def __init__(self, manager, session_id: str, directory: Path):
        self.manager = manager
        self.session_id = session_id
        self.path = directory / "evaluation.sqlite"
        self.sequence = -1
        self.closed = False
        self.last_state = None

    def get(self, timeout: float | None = None):
        deadline = None if timeout is None else time.monotonic() + timeout
        while not self.closed:
            if self.path.exists():
                with closing(sqlite3.connect(f"{self.path.resolve().as_uri()}?mode=ro", uri=True, timeout=10)) as db:
                    # The first writer creates the schema and first sample in one transaction.
                    exists = db.execute("SELECT 1 FROM sqlite_master WHERE name='snapshots'").fetchone()
                    row = db.execute(
                        "SELECT sequence, payload FROM snapshots WHERE sequence > ? ORDER BY sequence LIMIT 1",
                        (self.sequence,),
                    ).fetchone() if exists else None
                if row:
                    self.sequence = row[0]
                    value = loads_snapshot(zlib.decompress(row[1]).decode("utf-8"))
                    self.last_state = value.state
                    return value
            latest = self.manager.snapshot(self.session_id)
            # Covers failure before SUMO starts, queued cancellation, and worker death.
            if latest.state in TERMINAL_STATES and (latest.sequence > self.sequence or (latest.sequence == self.sequence and self.last_state not in TERMINAL_STATES)):
                # Re-read after observing terminal: the worker journals before publishing it.
                if self.path.exists() and latest.sequence != getattr(self, "_terminal_seen", None):
                    self._terminal_seen = latest.sequence
                    continue
                self.sequence = latest.sequence
                self.last_state = latest.state
                return latest
            if deadline is not None and time.monotonic() >= deadline:
                raise queue.Empty
            time.sleep(0.05)
        raise RuntimeError("Subscription is closed")

    def close(self):
        self.closed = True
