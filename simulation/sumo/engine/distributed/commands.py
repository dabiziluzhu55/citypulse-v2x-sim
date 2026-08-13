"""Adapters between Redis commands and the existing simulation command loop."""

from __future__ import annotations

import queue
import time

from ..session import _Command
from .codec import decode_command_payload


class RedisCommandQueue:
    def __init__(self, store, session_id: str) -> None:
        self.store = store
        self.session_id = session_id

    def get(self, timeout: float | None = None) -> _Command:
        deadline = None if timeout is None else time.monotonic() + timeout
        while True:
            remaining = (
                0.0
                if deadline is None
                else max(0.0, deadline - time.monotonic())
            )
            value = self.store.pop_command(self.session_id, remaining)
            try:
                return self._claimed(value)
            except _DuplicateCommand:
                if deadline is not None and time.monotonic() >= deadline:
                    raise queue.Empty

    def get_nowait(self) -> _Command:
        while True:
            value = self.store.pop_command(self.session_id)
            try:
                return self._claimed(value)
            except _DuplicateCommand:
                continue

    def _claimed(self, value) -> _Command:
        if value is None:
            raise queue.Empty
        command_id = str(value["command_id"])
        if not self.store.claim_command(self.session_id, command_id):
            self.store.acknowledge(self.session_id, command_id, None)
            raise _DuplicateCommand
        return self._decode(value)

    def _decode(self, value) -> _Command:
        name = str(value["name"])
        command = _Command(
            name=name,
            payload=decode_command_payload(name, value.get("payload")),
        )
        command.completed = _RedisCompletion(
            self.store,
            self.session_id,
            str(value["command_id"]),
            command,
        )
        return command


class _RedisCompletion:
    def __init__(self, store, session_id: str, command_id: str, command) -> None:
        self.store = store
        self.session_id = session_id
        self.command_id = command_id
        self.command = command

    def set(self) -> None:
        self.store.acknowledge(
            self.session_id, self.command_id, self.command.error
        )


class _DuplicateCommand(Exception):
    pass
