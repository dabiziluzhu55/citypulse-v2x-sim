"""Pure simulation contracts for Backend ↔ Redis ↔ SUMO Worker boundaries.

This package must not import libsumo, traci, sumolib, SimulationManager,
traffic_control algorithm implementations, or backend.
"""

from .ai_control import (
    AIControlConfig,
    AIControlPlan,
    AIControlStatus,
    AIControlValidationError,
)
from .catalog import load_catalog
from .client import RedisSimulationClient
from .codec import (
    SCHEMA_VERSION,
    CodecError,
    decode_command_payload,
    dumps_config,
    dumps_snapshot,
    encode_command_payload,
    loads_config,
    loads_snapshot,
)
from .dto import (
    IntersectionCapability,
    IntersectionRuntimeSnapshot,
    LaneCapability,
    LaneRuntimeSnapshot,
    OriginCapability,
    ScenarioScopeCapability,
    SessionMetrics,
    SimulationCatalog,
    SimulationConfig,
    SimulationSnapshot,
    VehicleRuntimeSnapshot,
)
from .events import (
    AccidentEvent,
    DisturbanceEvent,
    EventSnapshot,
    EventValidationError,
    LaneClosureEvent,
    MajorEventClosingEvent,
    MajorEventOpeningEvent,
    SpeedLimitEvent,
)
from .exceptions import (
    RedisUnavailableError,
    SessionBusyError,
    SessionError,
    UnknownSessionError,
)
from .playback import PLAYBACK_SPEEDS, normalize_playback_speed
from .store import RedisSessionStore, TERMINAL_STATES
from .validation import ScenarioCompilationError, validate_simulation_config

# Backward-compatible alias used by existing imports.
RedisSimulationManager = RedisSimulationClient

__all__ = [
    "AIControlConfig",
    "AIControlPlan",
    "AIControlStatus",
    "AIControlValidationError",
    "AccidentEvent",
    "CodecError",
    "DisturbanceEvent",
    "EventSnapshot",
    "EventValidationError",
    "IntersectionCapability",
    "IntersectionRuntimeSnapshot",
    "LaneCapability",
    "LaneClosureEvent",
    "LaneRuntimeSnapshot",
    "MajorEventClosingEvent",
    "MajorEventOpeningEvent",
    "OriginCapability",
    "PLAYBACK_SPEEDS",
    "RedisSessionStore",
    "RedisSimulationClient",
    "RedisSimulationManager",
    "RedisUnavailableError",
    "SCHEMA_VERSION",
    "ScenarioCompilationError",
    "ScenarioScopeCapability",
    "SessionBusyError",
    "SessionError",
    "SessionMetrics",
    "SimulationCatalog",
    "SimulationConfig",
    "SimulationSnapshot",
    "SpeedLimitEvent",
    "TERMINAL_STATES",
    "UnknownSessionError",
    "VehicleRuntimeSnapshot",
    "decode_command_payload",
    "dumps_config",
    "dumps_snapshot",
    "encode_command_payload",
    "load_catalog",
    "loads_config",
    "loads_snapshot",
    "normalize_playback_speed",
    "validate_simulation_config",
]
