"""Compatibility alias; implementation is SUMO-independent."""
import sys
from simulation_protocol import vehicle_profiles as _implementation
sys.modules[__name__] = _implementation
