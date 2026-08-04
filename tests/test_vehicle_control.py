import unittest
from copy import deepcopy
from pathlib import Path

from simulation.sumo.vehicle import (
    DEFAULT_LANE_CHANGE_MODE,
    STOPPED_LANE_CHANGE_MODE,
    StoppedLaneChangeGuard,
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
        self.lane_change_modes = {"car.0": DEFAULT_LANE_CHANGE_MODE}
        self.lane_change_mode_commands = []

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

    def getLaneChangeMode(self, vehicle_id):
        if vehicle_id not in self.states:
            raise KeyError(vehicle_id)
        return self.lane_change_modes.get(vehicle_id, DEFAULT_LANE_CHANGE_MODE)

    def setLaneChangeMode(self, vehicle_id, mode):
        if vehicle_id not in self.states:
            raise KeyError(vehicle_id)
        self.lane_change_modes[vehicle_id] = int(mode)
        self.lane_change_mode_commands.append((vehicle_id, int(mode)))


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

    def test_neighbor_gaps_and_time_since_lane_change_use_cached_telemetry(self):
        second = deepcopy(self.traci.vehicle.states["car.0"])
        second["lane_position"] = 25.0
        second["position"] = (23.0, 20.0)
        self.traci.vehicle.states["car.1"] = second

        self.tracker.tick(1.0)
        observations = self.tracker.observations(reset_interval=False)
        self.assertEqual(observations["car.0"].leader_gap_m, 8.0)
        self.assertIsNone(observations["car.0"].follower_gap_m)
        self.assertEqual(observations["car.1"].follower_gap_m, 8.0)
        self.assertIsNone(observations["car.1"].leader_gap_m)
        self.assertIsNone(observations["car.0"].time_since_last_lane_change_s)

        self.traci.vehicle.states["car.0"]["lane_id"] = "edge_1"
        self.traci.vehicle.states["car.0"]["lane_index"] = 1
        self.tracker.tick(2.0)
        changed = self.tracker.observations(reset_interval=False)["car.0"]
        self.assertEqual(changed.time_since_last_lane_change_s, 0.0)
        self.assertIsNone(changed.leader_gap_m)

        self.tracker.tick(3.5)
        later = self.tracker.observations(reset_interval=False)["car.0"]
        self.assertEqual(later.time_since_last_lane_change_s, 1.5)

        self.traci.vehicle.states["car.0"]["road_id"] = "out"
        self.traci.vehicle.states["car.0"]["lane_id"] = "out_0"
        self.traci.vehicle.states["car.0"]["lane_index"] = 0
        self.tracker.tick(4.0)
        next_edge = self.tracker.observations(reset_interval=False)["car.0"]
        self.assertEqual(next_edge.time_since_last_lane_change_s, 2.0)

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

    def test_stopped_lane_change_guard_locks_and_restores_stopped_vehicles(self):
        self.traci.vehicle.states["car.0"]["speed"] = 0.0
        self.traci.vehicle.lane_change_modes["car.0"] = 0
        self.tracker.tick(1.0)
        guard = StoppedLaneChangeGuard(self.traci, self.tracker)

        guard.tick()
        guard.tick()
        self.assertEqual(
            self.traci.vehicle.lane_change_mode_commands,
            [("car.0", STOPPED_LANE_CHANGE_MODE)],
        )
        self.assertEqual(
            self.traci.vehicle.lane_change_modes["car.0"], STOPPED_LANE_CHANGE_MODE
        )

        self.traci.vehicle.states["car.0"]["speed"] = 0.2
        self.tracker.tick(2.0)
        guard.tick()
        self.assertEqual(
            self.traci.vehicle.lane_change_mode_commands[-1],
            ("car.0", 0),
        )
        self.assertEqual(self.traci.vehicle.lane_change_modes["car.0"], 0)

    def test_stopped_lane_change_guard_cleans_up_arrived_vehicles(self):
        self.traci.vehicle.states["car.0"]["speed"] = 0.0
        self.tracker.tick(1.0)
        guard = StoppedLaneChangeGuard(self.traci, self.tracker)
        guard.tick()

        self.traci.vehicle.states.pop("car.0")
        self.tracker.tick(2.0)
        guard.tick()

        self.assertEqual(
            self.traci.vehicle.lane_change_mode_commands,
            [("car.0", STOPPED_LANE_CHANGE_MODE)],
        )

    def test_stopped_vehicles_cannot_receive_lane_change_actions(self):
        self.traci.vehicle.states["car.0"]["speed"] = 0.0
        self.tracker.tick(1.0)
        controller = VehicleActionController(self.traci, self.tracker)
        with self.assertRaisesRegex(ValueError, "cannot change lanes while stopped"):
            controller.validate({"car.0": {"target_lane_index": 1}})
        self.assertEqual(self.traci.vehicle.lane_commands, [])

    def test_lane_change_action_cannot_stop_the_vehicle(self):
        self.tracker.tick(1.0)
        controller = VehicleActionController(self.traci, self.tracker)
        with self.assertRaisesRegex(ValueError, "cannot change lanes while stopped"):
            controller.validate(
                {"car.0": {"target_speed_mps": 0.0, "target_lane_index": 1}}
            )
        self.assertEqual(self.traci.vehicle.speed_commands, [])
        self.assertEqual(self.traci.vehicle.lane_commands, [])

    def test_invalid_vehicle_action_is_rejected_before_application(self):
        self.tracker.tick(1.0)
        controller = VehicleActionController(self.traci, self.tracker)
        with self.assertRaisesRegex(ValueError, "between 0"):
            controller.validate({"car.0": {"target_speed_mps": 20.0}})
        with self.assertRaisesRegex(ValueError, "unknown vehicle"):
            controller.validate({"missing": {"target_speed_mps": 3.0}})
        self.assertEqual(self.traci.vehicle.speed_commands, [])

    def test_lane_disallow_all_rejects_vehicle_action(self):
        self.tracker.tick(1.0)
        self.traci.lane.getDisallowed = (
            lambda lane_id: ("all",) if lane_id == "edge_1" else ()
        )
        controller = VehicleActionController(self.traci, self.tracker)
        with self.assertRaisesRegex(ValueError, "does not allow passenger"):
            controller.validate({"car.0": {"target_lane_index": 1}})
        self.assertEqual(self.traci.vehicle.lane_commands, [])


if __name__ == "__main__":
    unittest.main()
