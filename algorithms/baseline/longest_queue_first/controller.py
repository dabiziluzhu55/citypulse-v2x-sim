import logging
import traci
from sumoITScontrol import Intersection
from sumoITScontrol.control.intersection_management import MaxPressure_Flex

from algorithms.baseline.max_pressure.intersection_config import INTERSECTIONS

LOGGER = logging.getLogger(__name__)

# 防止SUMO自动推进信号相位
_HOLD_PHASE_DURATION = 1_000_000.0

DEFAULT_MAXPRESSURE_FLEX_PARAMS = {
    "T_L": 3,
    "T_A": 2,
    "G_T_MIN": 5,
    "G_T_MAX": 45,
    "measurement_period": 1,
}


class TrafficController:
    """Wraps sumoITScontrol MaxPressure_Flex for one or more intersections."""

    def __init__(self, tls_ids, params=None):
        self.tls_ids = list(tls_ids)
        self.params = dict(DEFAULT_MAXPRESSURE_FLEX_PARAMS)
        if params:
            self.params.update(params)

        self._intersections = {}
        self._controllers = {}
        for tl_id in self.tls_ids:
            if tl_id not in INTERSECTIONS:
                raise KeyError(f"Unknown traffic light id: {tl_id}")
            cfg = INTERSECTIONS[tl_id]
            intersection = Intersection(
                tl_id=tl_id,
                phases=cfg["phases"],
                links=cfg["links"],
                green_states=cfg["green_states"],
                yellow_states=cfg["yellow_states"],
            )
            self._intersections[tl_id] = intersection
            self._controllers[tl_id] = MaxPressure_Flex(self.params, intersection)

    def prepare(self):
        """Initialize controlled signals for external TraCI control."""
        for tl_id in self.tls_ids:
            traci.trafficlight.setPhase(tl_id, 0)
            traci.trafficlight.setPhaseDuration(tl_id, _HOLD_PHASE_DURATION)
            LOGGER.info("Prepared TLS %s for MaxPressure_Flex control", tl_id)

    def step(self, current_time):
        """Run one control step for every managed intersection."""
        for tl_id, controller in self._controllers.items():
            controller.execute_control(current_time)
            traci.trafficlight.setPhaseDuration(tl_id, _HOLD_PHASE_DURATION)

    def get_controllers(self):
        return self._controllers
