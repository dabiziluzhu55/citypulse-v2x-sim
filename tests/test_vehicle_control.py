import unittest
from pathlib import Path

from simulation.sumo.vehicle import (
    VehicleActionController,
    VehicleTelemetryTracker,
    build_vehicle_type_metadata,
)
from simulation.sumo.vehicle_profiles import load_vehicle_profiles


ROOT = Path(__file__).resolve().parents[1]
PROFILES = ROOT / "data" / "maps" / "sumo" / "vehicle_profiles.json"


class FakeConstants:
    VAR_POSITION = 1
    VAR_SPEED = 2
    VAR_ACCELERATION = 3
    VAR_ANGLE = 4
    VAR_ROAD_ID = 5
    VAR_LANE_ID = 6
    VAR_LANEPOSITION = 7
    VAR_ALLOWED_SPEED = 8
    VAR_ROUTE_ID = 9
    VAR_ROUTE_INDEX = 10
    VAR_WAITING_TIME = 11
    VAR_ACCUMULATED_WAITING_TIME = 12
    VAR_TIMELOSS = 13
    VAR_DISTANCE = 14
    VAR_FUELCONSUMPTION = 15
    VAR_LANE_INDEX = 16
    VAR_EDGES = 17
    VAR_NEXT_TLS = 18


class FakeVehicleDomain:
    def __init__(self):
        self.states = {
            "car.0": {
                "type_id": "demo_2_official_passenger",
                "position": (10.0, 20.0),
                "speed": 6.0,
                "acceleration": -3.5,
                "angle": 90.0,
                "road_id": "edge",
                "lane_id": "edge_0",
                "lane_position": 12.0,
                "allowed_speed": 13.9,
                "route_id": "route-1",
                "route_index": 0,
                "waiting_time": 0.0,
                "accumulated_waiting_time": 2.0,
                "time_loss": 3.0,
                "distance": 100.0,
                "fuel_rate": 100.0,
                "lane_index": 0,
                "route": ("edge", "out"),
                "next_tls": (("tls-317", 0, 30.0, "G"),),
            },
            "event_vehicle_crash": {
                "type_id": "citypulse_disturbance_vehicle",
            },
        }
        self.subscriptions = {}
        self.speed_commands = []
        self.lane_commands = []

    def getIDList(self):
        return tuple(self.states)

    def getTypeID(self, vehicle_id):
        return self.states[vehicle_id]["type_id"]

    def subscribe(self, vehicle_id, variables):
        self.subscriptions[vehicle_id] = tuple(variables)

    def getSubscriptionResults(self, vehicle_id):
        state = self.states[vehicle_id]
        names = {
            value: name.removeprefix("VAR_").lower()
            for name, value in vars(FakeConstants).items()
            if name.startswith("VAR_")
        }
        aliases = {
            "laneposition": "lane_position",
            "allowedspeed": "allowed_speed",
            "routeid": "route_id",
            "routeindex": "route_index",
            "waitingtime": "waiting_time",
            "accumulatedwaitingtime": "accumulated_waiting_time",
            "timeloss": "time_loss",
            "fuelconsumption": "fuel_rate",
            "edges": "route",
            "next_tls": "next_tls",
            "lane_index": "lane_index",
            "roadid": "road_id",
            "laneid": "lane_id",
        }
        return {
            variable: state[aliases.get(names[variable], names[variable])]
            for variable in self.subscriptions[vehicle_id]
        }

    def setSpeed(self, vehicle_id, speed):
        self.speed_commands.append((vehicle_id, speed))

    def changeLane(self, vehicle_id, lane_index, duration):
        self.lane_commands.append((vehicle_id, lane_index, duration))


class FakeEdgeDomain:
    def getLaneNumber(self, edge_id):
        return 2


class FakeLaneDomain:
    def getAllowed(self, lane_id):
        return ()

    def getDisallowed(self, lane_id):
        return ()


class FakeTraci:
    constants = FakeConstants()

    def __init__(self):
        self.vehicle = FakeVehicleDomain()
        self.edge = FakeEdgeDomain()
        self.lane = FakeLaneDomain()


class VehicleTelemetryTests(unittest.TestCase):
    def setUp(self):
        self.traci = FakeTraci()
        profiles = load_vehicle_profiles(PROFILES)
        vehicle_types = build_vehicle_type_metadata(
            {"demo_2_official_passenger": "passenger"}, profiles
        )
        self.tracker = VehicleTelemetryTracker(
            self.traci, vehicle_types, {"tls-317": "demo_2"}
        )

    def test_fuel_is_integrated_and_braking_counts_threshold_entries(self):
        self.tracker.tick(1.0)
        first = self.tracker.observations(reset_interval=True)["car.0"]
        self.assertAlmostEqual(first.energy.fuel_since_last_decision_mg, 100.0)
        self.assertAlmostEqual(first.energy.fuel_total_ml, 100.0 / 745.0)
        self.assertEqual(first.driving_events.hard_braking_total, 1)
        self.assertEqual(first.next_signal.intersection_id, "demo_2")
        self.assertNotIn("event_vehicle_crash", self.tracker.observations(reset_interval=False))

        self.tracker.tick(2.0)
        second = self.tracker.observations(reset_interval=True)["car.0"]
        self.assertAlmostEqual(second.energy.fuel_since_last_decision_mg, 100.0)
        self.assertEqual(second.driving_events.hard_braking_total, 1)
        self.assertEqual(second.driving_events.hard_braking_since_last_decision, 0)

        self.traci.vehicle.states["car.0"]["acceleration"] = 0.0
        self.tracker.tick(3.0)
        self.traci.vehicle.states["car.0"]["acceleration"] = -4.0
        self.tracker.tick(4.0)
        fourth = self.tracker.observations(reset_interval=False)["car.0"]
        self.assertEqual(fourth.driving_events.hard_braking_total, 2)
        self.assertAlmostEqual(self.tracker.totals()[0], 400.0)

        self.traci.vehicle.states.pop("car.0")
        self.tracker.tick(5.0)
        self.assertAlmostEqual(self.tracker.totals()[0], 500.0)

    def test_speed_and_lane_actions_are_validated_leased_and_reported(self):
        self.tracker.tick(1.0)
        controller = VehicleActionController(self.traci, self.tracker)
        actions = controller.validate(
            {"car.0": {"target_speed_mps": 8.0, "target_lane_index": 1}}
        )
        controller.apply(3, actions, 5.0)
        self.assertEqual(self.traci.vehicle.speed_commands[-1], ("car.0", 8.0))
        self.assertEqual(self.traci.vehicle.lane_commands[-1], ("car.0", 1, 5.0))
        self.assertEqual(controller.speed_control_summary("edge_0"), (1, 8.0, 8.0))
        self.assertEqual(controller.speed_control_summary("edge_1"), (0, None, None))
        result = controller.previous_results().vehicles["car.0"]
        self.assertEqual(result.lane_change_status, "not_completed")

        self.traci.vehicle.states["car.0"]["lane_index"] = 1
        self.tracker.tick(2.0)
        self.assertEqual(controller.speed_control_summary("edge_1"), (1, 8.0, 8.0))
        self.assertEqual(
            controller.previous_results().vehicles["car.0"].lane_change_status,
            "completed",
        )
        controller.apply(4, {}, 5.0)
        self.assertEqual(self.traci.vehicle.speed_commands[-1], ("car.0", -1))

    def test_invalid_vehicle_action_is_rejected_before_application(self):
        self.tracker.tick(1.0)
        controller = VehicleActionController(self.traci, self.tracker)
        with self.assertRaisesRegex(ValueError, "between 0"):
            controller.validate({"car.0": {"target_speed_mps": 20.0}})
        with self.assertRaisesRegex(ValueError, "unknown vehicle"):
            controller.validate({"missing": {"target_speed_mps": 3.0}})
        self.assertEqual(self.traci.vehicle.speed_commands, [])


if __name__ == "__main__":
    unittest.main()
