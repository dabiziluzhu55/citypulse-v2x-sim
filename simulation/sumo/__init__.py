"""Official CityPulse SUMO signal build and runtime package."""

from .algorithm import (
    AIFrameObservation,
    AlgorithmDecision,
    IntersectionMetadata,
    IntersectionObservation,
    LaneMetadata,
    LocalAIObserver,
    LocalAlgorithmClient,
    PhaseMetadata,
    PROTOCOL_VERSION,
    SimulationMetadata,
    SimulationObservation,
    SimulationTimeFrameClock,
    VehicleAction,
    VehicleObservation,
    VehicleTypeMetadata,
    to_protocol_payload,
)
from .building.tls import load_signal_configuration
from .engine.signal import SafePhaseController, SignalStage
from .engine.ai_control import AIControlConfig, AIControlPlan, AIControlStatus
from .engine.events import (
    AccidentEvent,
    LaneClosureEvent,
    MajorEventClosingEvent,
    MajorEventOpeningEvent,
    SpeedLimitEvent,
)
from .engine.session import PLAYBACK_SPEEDS, SimulationConfig, SimulationManager
from .engine.distributed import RedisSimulationManager, RedisUnavailableError

__all__ = [
    "AccidentEvent",
    "AIControlConfig",
    "AIControlPlan",
    "AIControlStatus",
    "AIFrameObservation",
    "AlgorithmDecision",
    "IntersectionMetadata",
    "IntersectionObservation",
    "LaneClosureEvent",
    "LaneMetadata",
    "LocalAIObserver",
    "LocalAlgorithmClient",
    "MajorEventClosingEvent",
    "MajorEventOpeningEvent",
    "PLAYBACK_SPEEDS",
    "PROTOCOL_VERSION",
    "PhaseMetadata",
    "RedisSimulationManager",
    "RedisUnavailableError",
    "SafePhaseController",
    "SignalStage",
    "SimulationConfig",
    "SimulationMetadata",
    "SimulationManager",
    "SimulationObservation",
    "SimulationTimeFrameClock",
    "SpeedLimitEvent",
    "VehicleAction",
    "VehicleObservation",
    "VehicleTypeMetadata",
    "load_signal_configuration",
    "to_protocol_payload",
]
