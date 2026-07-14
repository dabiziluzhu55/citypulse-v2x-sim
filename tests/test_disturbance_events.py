import unittest

from simulation.sumo.events import (
    AccidentEvent,
    DisturbanceScheduler,
    EventState,
    EventValidationError,
    LaneClosureEvent,
    LaneTarget,
    SpeedLimitEvent,
)


class FakeLane:
    def __init__(self):
        self.allowed = {"edge_0": [], "edge_1": []}
        self.disallowed = {"edge_0": [], "edge_1": []}
        self.speeds = {"edge_0": 13.9, "edge_1": 13.9}
        self.fail_disallowed = False
        self.fail_lane = None

    def getAllowed(self, lane): return self.allowed[lane]
    def getDisallowed(self, lane): return self.disallowed[lane]
    def getMaxSpeed(self, lane): return self.speeds[lane]
    def setAllowed(self, lane, values): self.allowed[lane] = list(values); self.disallowed[lane] = []
    def setDisallowed(self, lane, values):
        if self.fail_disallowed or lane == self.fail_lane: raise RuntimeError("permission failure")
        self.disallowed[lane] = list(values); self.allowed[lane] = []
    def setMaxSpeed(self, lane, value): self.speeds[lane] = value


class FakeRoute:
    def __init__(self): self.routes = {}
    def add(self, route_id, edges): self.routes[route_id] = list(edges)


class FakeVehicle:
    def __init__(self): self.vehicles = {}; self.stops = {}
    def add(self, vehicle_id, route_id, **kwargs): self.vehicles[vehicle_id] = kwargs
    def setStop(self, vehicle_id, edge_id, **kwargs): self.stops[vehicle_id] = (edge_id, kwargs)
    def getIDList(self): return list(self.vehicles)
    def remove(self, vehicle_id): self.vehicles.pop(vehicle_id, None)


class FakeTraci:
    def __init__(self):
        self.lane = FakeLane()
        self.route = FakeRoute()
        self.vehicle = FakeVehicle()


class DisturbanceEventTests(unittest.TestCase):
    def make_scheduler(self):
        traci = FakeTraci()
        scheduler = DisturbanceScheduler(
            traci,
            {
                "edge_0": LaneTarget("edge_0", "edge", 0, 100.0),
                "edge_1": LaneTarget("edge_1", "edge", 1, 100.0),
            },
            100.0,
        )
        return traci, scheduler

    def test_overlapping_speed_limits_restore_effective_value(self):
        traci, scheduler = self.make_scheduler()
        scheduler.schedule(SpeedLimitEvent("a", 1, 8, ("edge_0",), 8.0))
        scheduler.schedule(SpeedLimitEvent("b", 2, 5, ("edge_0",), 5.0))
        scheduler.tick(1)
        self.assertEqual(traci.lane.speeds["edge_0"], 8.0)
        scheduler.tick(2)
        self.assertEqual(traci.lane.speeds["edge_0"], 5.0)
        scheduler.tick(5)
        self.assertEqual(traci.lane.speeds["edge_0"], 8.0)
        scheduler.tick(8)
        self.assertEqual(traci.lane.speeds["edge_0"], 13.9)

    def test_overlapping_closures_do_not_restore_early(self):
        traci, scheduler = self.make_scheduler()
        scheduler.schedule(LaneClosureEvent("a", 1, 5, ("edge_0",)))
        scheduler.schedule(LaneClosureEvent("b", 2, 8, ("edge_0",)))
        scheduler.tick(2)
        self.assertIn("passenger", traci.lane.disallowed["edge_0"])
        scheduler.tick(5)
        self.assertIn("passenger", traci.lane.disallowed["edge_0"])
        scheduler.tick(8)
        self.assertNotIn("passenger", traci.lane.disallowed["edge_0"])

    def test_accident_vehicle_is_visible_and_removed(self):
        traci, scheduler = self.make_scheduler()
        scheduler.schedule(AccidentEvent("crash", 1, 5, "edge_0", 0.5))
        scheduler.tick(1)
        self.assertIn("event_vehicle_crash", traci.vehicle.vehicles)
        self.assertAlmostEqual(traci.vehicle.stops["event_vehicle_crash"][1]["pos"], 50.0)
        scheduler.tick(5)
        self.assertNotIn("event_vehicle_crash", traci.vehicle.vehicles)
        self.assertEqual(scheduler.snapshots()[0].state, EventState.COMPLETED.value)

    def test_accident_and_closure_overlap_is_rejected(self):
        _, scheduler = self.make_scheduler()
        scheduler.schedule(LaneClosureEvent("work", 1, 5, ("edge_0",)))
        with self.assertRaisesRegex(EventValidationError, "cannot overlap"):
            scheduler.schedule(AccidentEvent("crash", 2, 4, "edge_0", 0.5))

    def test_permission_failure_marks_event_failed(self):
        traci, scheduler = self.make_scheduler()
        traci.lane.fail_disallowed = True
        scheduler.schedule(LaneClosureEvent("work", 1, 5, ("edge_0",)))
        scheduler.tick(1)
        snapshot = scheduler.snapshots()[0]
        self.assertEqual(snapshot.state, EventState.FAILED.value)
        self.assertIn("permission failure", snapshot.error)

    def test_multi_lane_failure_rolls_back_lanes_already_changed(self):
        traci, scheduler = self.make_scheduler()
        traci.lane.fail_lane = "edge_1"
        scheduler.schedule(LaneClosureEvent("work", 1, 5, ("edge_0", "edge_1")))
        scheduler.tick(1)
        self.assertEqual(scheduler.snapshots()[0].state, EventState.FAILED.value)
        self.assertNotIn("passenger", traci.lane.disallowed["edge_0"])


if __name__ == "__main__":
    unittest.main()
