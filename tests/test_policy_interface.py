import unittest

from simulation.sumo.external_policy import HttpControlPolicy
from simulation.sumo.policy import (
    ControlAction,
    SimulationObservation,
    VehicleAdvice,
    VehicleObservation,
)
from simulation.sumo.run import _validate_actions


def vehicle(vehicle_id="car_1", allowed_speed=13.9):
    return VehicleObservation(
        vehicle_id=vehicle_id,
        road_id="edge_in",
        lane_id="edge_in_0",
        lane_index=0,
        lane_position=10.0,
        speed=8.0,
        allowed_speed=allowed_speed,
        waiting_time=0.0,
        route=("edge_in", "edge_out"),
    )


class StubHttpPolicy(HttpControlPolicy):
    def __init__(self, response):
        super().__init__("http://algorithm.test")
        self.response = response

    def _post(self, path, payload):
        return self.response


class PolicyInterfaceTests(unittest.TestCase):
    def test_legacy_phase_mapping_remains_supported(self):
        action = _validate_actions({"demo_2": 2}, ["demo_2"], {})
        self.assertEqual(action.signal_phases, {"demo_2": 2})
        self.assertEqual(action.vehicle_advisories, {})

    def test_vehicle_advice_is_validated_against_live_vehicle_speed_limit(self):
        vehicles = {"car_1": vehicle()}
        action = _validate_actions(
            ControlAction(
                signal_phases={"demo_2": 1},
                vehicle_advisories={
                    "car_1": VehicleAdvice(
                        target_speed=10.0,
                        lane_index=1,
                        duration=2.0,
                    )
                },
            ),
            ["demo_2"],
            vehicles,
        )
        self.assertEqual(action.vehicle_advisories["car_1"].target_speed, 10.0)
        with self.assertRaisesRegex(ValueError, "allowed speed"):
            _validate_actions(
                ControlAction(
                    vehicle_advisories={
                        "car_1": VehicleAdvice(target_speed=20.0)
                    }
                ),
                ["demo_2"],
                vehicles,
            )
        with self.assertRaisesRegex(ValueError, "inactive vehicles"):
            _validate_actions(
                ControlAction(
                    vehicle_advisories={
                        "departed": VehicleAdvice(target_speed=5.0)
                    }
                ),
                ["demo_2"],
                vehicles,
            )

    def test_http_response_is_converted_to_public_action_types(self):
        policy = StubHttpPolicy(
            {
                "signal_phases": {"demo_2": 2},
                "vehicle_advisories": {
                    "car_1": {
                        "target_speed": 9.5,
                        "lane_index": 1,
                        "duration": 3.0,
                    }
                },
            }
        )
        action = policy.act(SimulationObservation(0.0, {}))
        self.assertEqual(action.signal_phases["demo_2"], 2)
        self.assertEqual(action.vehicle_advisories["car_1"].target_speed, 9.5)


if __name__ == "__main__":
    unittest.main()
