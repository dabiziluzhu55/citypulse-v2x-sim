"""Backward-compatible re-export of the protocol Redis client."""

from simulation_protocol.client import (
    RedisSimulationClient,
    RedisSnapshotSubscription,
)
from simulation_protocol.exceptions import RedisUnavailableError

RedisSimulationManager = RedisSimulationClient

__all__ = [
    "RedisSimulationClient",
    "RedisSimulationManager",
    "RedisSnapshotSubscription",
    "RedisUnavailableError",
]
