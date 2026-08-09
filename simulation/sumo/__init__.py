"""Official CityPulse SUMO signal build and runtime package."""

from .building.tls import load_signal_configuration
from .engine.signal import SafePhaseController, SignalStage
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
    "LaneClosureEvent",
    "MajorEventClosingEvent",
    "MajorEventOpeningEvent",
    "PLAYBACK_SPEEDS",
    "RedisSimulationManager",
    "RedisUnavailableError",
    "SafePhaseController",
    "SignalStage",
    "SimulationConfig",
    "SimulationManager",
    "SpeedLimitEvent",
    "load_signal_configuration",
]
