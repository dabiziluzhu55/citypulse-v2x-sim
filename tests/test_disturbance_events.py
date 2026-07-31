import unittest

from simulation.sumo.events import (
    AccidentEvent,
    DisturbanceScheduler,
    EventState,
    EventValidationError,
    LaneClosureEvent,
    LaneTarget,
    MajorEventClosingEvent,
    MajorEventOpeningEvent,
    SpeedLimitEvent,
)


class FakeLane:
    def __init__(self):
        lane_ids = (
            "edge_0",
            "edge_1",
            "source_0",
            "source_1",
            "venue_0",
            "dest_0",
            "dest_1",
        )
        self.allowed = {lane_id: [] for lane_id in lane_ids}
        self.disallowed = {lane_id: [] for lane_id in lane_ids}
        self.speeds = {lane_id: 13.9 for lane_id in lane_ids}
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
    def add(self, vehicle_id, route_id, **kwargs): self.vehicles[vehicle_id] = {"route_id": route_id, **kwargs}
    def setStop(self, vehicle_id, edge_id, **kwargs): self.stops[vehicle_id] = (edge_id, kwargs)
    def getIDList(self): return list(self.vehicles)
    def remove(self, vehicle_id): self.vehicles.pop(vehicle_id, None)


class FakeRouteResult:
    def __init__(self, edges): self.edges = edges


class FakeSimulation:
    def __init__(self): self.unreachable = set()
    def findRoute(self, source_edge, destination_edge, **kwargs):
        if (source_edge, destination_edge) in self.unreachable:
            return FakeRouteResult(())
        return FakeRouteResult((source_edge, destination_edge))


class FakeTraci:
    def __init__(self):
        self.lane = FakeLane()
        self.route = FakeRoute()
        self.vehicle = FakeVehicle()
        self.simulation = FakeSimulation()


class DisturbanceEventTests(unittest.TestCase):
    def make_scheduler(self):
        traci = FakeTraci()
        scheduler = DisturbanceScheduler(
            traci,
            {
                "edge_0": LaneTarget("edge_0", "edge", 0, 100.0),
                "edge_1": LaneTarget("edge_1", "edge", 1, 100.0),
                "source_0": LaneTarget("source_0", "source_edge_0", 0, 100.0, "incoming"),
                "source_1": LaneTarget("source_1", "source_edge_1", 0, 100.0, "incoming"),
                "venue_0": LaneTarget("venue_0", "venue_edge", 0, 100.0, "outgoing"),
                "dest_0": LaneTarget("dest_0", "dest_edge_0", 0, 100.0, "outgoing"),
                "dest_1": LaneTarget("dest_1", "dest_edge_1", 0, 100.0, "outgoing"),
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

    def test_major_event_opening_spawns_evenly_from_sources_to_venue(self):
        traci, scheduler = self.make_scheduler()
        scheduler.schedule(
            MajorEventOpeningEvent(
                "show",
                1,
                5,
                "venue_0",
                4,
                source_lane_ids=("source_0", "source_1"),
            )
        )
        scheduler.tick(1)
        self.assertEqual(traci.vehicle.vehicles, {})

        scheduler.tick(2.6)
        self.assertEqual(
            sorted(traci.vehicle.vehicles),
            ["event_vehicle_show_000001", "event_vehicle_show_000002"],
        )
        self.assertEqual(
            traci.route.routes["event_route_show_source_edge_0_venue_edge"],
            ["source_edge_0", "venue_edge"],
        )
        self.assertEqual(
            traci.vehicle.vehicles["event_vehicle_show_000001"]["departLane"],
            "0",
        )

        scheduler.tick(5)
        self.assertEqual(len(traci.vehicle.vehicles), 4)
        snapshot = scheduler.snapshots()[0]
        self.assertEqual(snapshot.state, EventState.COMPLETED.value)
        self.assertEqual(snapshot.details["spawned_vehicle_count"], 4)
        self.assertEqual(snapshot.details["planned_vehicle_count"], 4)
        self.assertEqual(snapshot.details["reachable_route_count"], 2)
        self.assertEqual(snapshot.details["unreachable_route_count"], 0)

    def test_major_event_closing_spawns_from_venue_to_destinations(self):
        traci, scheduler = self.make_scheduler()
        scheduler.schedule(
            MajorEventClosingEvent(
                "close",
                1,
                3,
                "venue_0",
                2,
                destination_lane_ids=("dest_0", "dest_1"),
            )
        )
        scheduler.tick(1.5)
        scheduler.tick(3)
        self.assertEqual(len(traci.vehicle.vehicles), 2)
        self.assertEqual(
            traci.route.routes["event_route_close_venue_edge_dest_edge_0"],
            ["venue_edge", "dest_edge_0"],
        )
        self.assertTrue(
            all(
                vehicle["route_id"].startswith("event_route_close_venue_edge_dest_edge_")
                for vehicle in traci.vehicle.vehicles.values()
            )
        )

    def test_activity_event_cancel_stops_future_spawns(self):
        traci, scheduler = self.make_scheduler()
        scheduler.schedule(
            MajorEventOpeningEvent(
                "cancelled_show",
                1,
                5,
                "venue_0",
                4,
                source_lane_ids=("source_0",),
            )
        )
        scheduler.tick(1.6)
        scheduler.cancel("cancelled_show")
        scheduler.tick(5)
        self.assertEqual(sorted(traci.vehicle.vehicles), ["event_vehicle_cancelled_show_000001"])
        self.assertEqual(scheduler.snapshots()[0].state, EventState.CANCELLED.value)

    def test_activity_event_validation_rejects_bad_inputs(self):
        _, scheduler = self.make_scheduler()
        with self.assertRaisesRegex(EventValidationError, "vehicle_count"):
            scheduler.schedule(MajorEventOpeningEvent("bad_count", 1, 2, "venue_0", 0))
        with self.assertRaisesRegex(EventValidationError, "Unknown event lanes"):
            scheduler.schedule(MajorEventOpeningEvent("bad_venue", 1, 2, "missing", 1))
        with self.assertRaisesRegex(EventValidationError, "unique"):
            scheduler.schedule(
                MajorEventOpeningEvent(
                    "dup",
                    1,
                    2,
                    "venue_0",
                    1,
                    source_lane_ids=("source_0", "source_0"),
                )
            )

    def test_activity_event_without_reachable_routes_fails_on_activation(self):
        traci, scheduler = self.make_scheduler()
        traci.simulation.unreachable.add(("source_edge_0", "venue_edge"))
        scheduler.schedule(
            MajorEventOpeningEvent(
                "blocked_show",
                1,
                2,
                "venue_0",
                1,
                source_lane_ids=("source_0",),
            )
        )
        scheduler.tick(1)
        snapshot = scheduler.snapshots()[0]
        self.assertEqual(snapshot.state, EventState.FAILED.value)
        self.assertIn("reachable", snapshot.error)


if __name__ == "__main__":
    unittest.main()
