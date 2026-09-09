"""Six logical directions with explicit consumption and held speed permissions."""
from __future__ import annotations

from copy import deepcopy
from dataclasses import dataclass, fields, is_dataclass
import hashlib
import json
import math
from typing import Any, Mapping

import numpy as np

from .contracts import CLOUD_INTERVAL, HORIZON, MovementKey

ROLES = frozenset(("vehicle", "road", "cloud"))


def _dataclass_tree(value):
    """Match asdict's recursive container conversion without copying leaves."""
    if is_dataclass(value):
        return {field.name: _dataclass_tree(getattr(value, field.name)) for field in fields(value)}
    if isinstance(value, tuple) and hasattr(value, "_fields"):
        return type(value)(*[_dataclass_tree(item) for item in value])
    if isinstance(value, (list, tuple)):
        return type(value)(_dataclass_tree(item) for item in value)
    if isinstance(value, dict):
        return type(value)((_dataclass_tree(k), _dataclass_tree(v)) for k, v in value.items())
    return value


def jsonable(value):
    if isinstance(value, MovementKey):
        return value.token
    if isinstance(value, np.ndarray):
        return value.tolist()
    if isinstance(value, np.generic):
        return value.item()
    if is_dataclass(value):
        return jsonable(_dataclass_tree(value))
    if isinstance(value, Mapping):
        return {str(k): jsonable(v) for k, v in value.items()}
    if isinstance(value, (list, tuple)):
        return [jsonable(v) for v in value]
    return value


def digest(value):
    data = json.dumps(jsonable(value), sort_keys=True, separators=(",", ":"), allow_nan=False)
    return hashlib.sha256(data.encode()).hexdigest()


@dataclass(frozen=True)
class Message:
    message_id: str
    kind: str
    episode_id: str
    source: str
    destination: str
    step_id: int
    generated_at: float
    valid_until: float
    payload: Mapping[str, Any]
    parents: tuple[str, ...] = ()


class MessageBus:
    def __init__(self, episode_id):
        self.episode_id = str(episode_id)
        self.events = []
        self._sent = {}
        self._sent_at = {}
        self._payload_hashes = {}
        self._consumed = set()
        self._counter = 0

    def _event(self, event, msg, now, consumer=None):
        # SEND creates this cache; consume verifies the complete message before
        # DELIVER/CONSUME. Other event types retain the original fresh digest.
        payload_hash = self._payload_hashes.get(msg.message_id) if event in ("SEND", "DELIVER", "CONSUME") else None
        if payload_hash is None:
            payload_hash = digest(msg.payload)
        self.events.append({"event": event, "message_id": msg.message_id,
                            "episode_id": msg.episode_id, "kind": msg.kind,
                            "source": msg.source, "destination": msg.destination,
                            "step_id": msg.step_id, "time": float(now),
                            "generated_at": msg.generated_at, "valid_until": msg.valid_until,
                            "parents": msg.parents, "consumer": consumer,
                            "payload_sha256": payload_hash})

    def send(self, kind, source, destination, step_id, now, valid_until, payload, *, parents=()):
        now = float(now); valid_until = float(valid_until)
        if source not in ROLES or destination not in ROLES or source == destination:
            raise ValueError("message must use one of the six inter-role directions")
        if not math.isfinite(now) or not math.isfinite(valid_until) or not 0 <= now < valid_until:
            raise ValueError("invalid message lifetime")
        if any(parent not in self._sent for parent in parents):
            raise ValueError("unknown causal parent")
        if any(self._sent_at[parent] > now for parent in parents):
            raise ValueError("causal parent is a future observation")
        self._counter += 1
        msg = Message(f"{self.episode_id}:{self._counter}", str(kind), self.episode_id,
                      source, destination, int(step_id), now, valid_until,
                      deepcopy(dict(payload)), tuple(parents))
        self._sent[msg.message_id] = digest(msg)
        self._payload_hashes[msg.message_id] = digest(msg.payload)
        self._sent_at[msg.message_id] = now
        self._event("SEND", msg, now)
        return msg

    def consume(self, msg, destination, now, *, consumer=None):
        now = float(now)
        if msg.episode_id != self.episode_id or msg.message_id not in self._sent:
            raise ValueError("unknown or cross-episode message")
        if destination != msg.destination:
            raise ValueError("wrong message recipient")
        if not math.isfinite(now) or not msg.generated_at <= now < msg.valid_until:
            raise ValueError("message is not currently valid")
        if self._sent[msg.message_id] != digest(msg):
            raise ValueError("message changed after publication")
        if any(parent not in self._consumed for parent in msg.parents):
            raise ValueError("causal input has not been consumed")
        self._event("DELIVER", msg, now, consumer)
        self._event("CONSUME", msg, now, consumer)
        self._consumed.add(msg.message_id)
        return deepcopy(dict(msg.payload))


class PermissionBook:
    def __init__(self, bus):
        self.bus = bus
        self.permissions = {}
        self.last_grid = None

    def due(self, now):
        now = float(now)
        if not math.isfinite(now) or now < 0:
            raise ValueError("invalid Cloud time")
        return now < HORIZON and int(now // CLOUD_INTERVAL) != self.last_grid

    def mark_grid(self, now):
        self.last_grid = int(float(now) // CLOUD_INTERVAL)

    def publish(self, movement_token, permit, step_id, now, policy_version, parents=()):
        now = float(now)
        if not 0 <= now < HORIZON:
            raise ValueError("cannot issue an unexecutable Cloud action")
        if permit not in (0, 1, False, True):
            raise ValueError("permission action must be binary")
        until = (int(now // CLOUD_INTERVAL) + 1) * CLOUD_INTERVAL
        msg = self.bus.send("speed_permission", "cloud", "vehicle", step_id, now, until,
                            {"movement": str(movement_token), "permit": bool(permit),
                             "policy_version": str(policy_version)}, parents=parents)
        self.permissions[str(movement_token)] = msg
        return msg

    def current(self, movement_token, now):
        msg = self.permissions.get(str(movement_token))
        if msg is None or msg.episode_id != self.bus.episode_id:
            return None
        now = float(now)
        if not math.isfinite(now) or now < msg.generated_at:
            return None
        if now >= msg.valid_until:
            self.permissions.pop(str(movement_token), None)
            self.bus._event("EXPIRE", msg, now)
            return None
        return msg

    def clear(self):
        self.permissions.clear()
        self.last_grid = None
