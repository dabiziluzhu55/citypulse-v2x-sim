"""Official CityPulse SUMO signal build and runtime package."""

from .config import load_signal_configuration
from .controller import SafePhaseController, SignalStage
from .events import (
    AccidentEvent,
    LaneClosureEvent,
    MajorEventClosingEvent,
    MajorEventOpeningEvent,
    SpeedLimitEvent,
)
from .session import PLAYBACK_SPEEDS, SimulationConfig, SimulationManager
from .distributed import RedisSimulationManager, RedisUnavailableError

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
