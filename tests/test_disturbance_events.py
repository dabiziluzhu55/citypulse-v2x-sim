import unittest

from simulation.sumo.events import (
    AccidentEvent,
    CollisionBlockageEvent,
    DisturbanceScheduler,
    EventState,
    EventValidationError,
    LaneClosureEvent,
    LaneTarget,
    QueueSpillbackEvent,
    SpeedLimitEvent,
    StoppedVehicleEvent,
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
    def __init__(self):
        self.vehicles = {}
        self.stops = {}
        self.lanes = {}
        self.positions = {}
        self.resumed = []
        self.lane_changes = []
        self.moves = []
        self.speeds = []
    def add(self, vehicle_id, route_id, **kwargs): self.vehicles[vehicle_id] = kwargs
    def setStop(self, vehicle_id, edge_id, **kwargs): self.stops[vehicle_id] = (edge_id, kwargs)
    def setSpeed(self, vehicle_id, speed): self.speeds.append((vehicle_id, speed))
    def changeLane(self, vehicle_id, lane_index, duration): self.lane_changes.append((vehicle_id, lane_index, duration))
    def moveTo(self, vehicle_id, lane_id, position): self.moves.append((vehicle_id, lane_id, position)); self.lanes[vehicle_id] = lane_id
    def getIDList(self): return list(self.vehicles)
    def getLaneID(self, vehicle_id): return self.lanes.get(vehicle_id, "")
    def getLanePosition(self, vehicle_id): return self.positions[vehicle_id]
    def remove(self, vehicle_id): self.vehicles.pop(vehicle_id, None)
    def resume(self, vehicle_id): self.resumed.append(vehicle_id)


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

    def test_accident_freezes_existing_vehicle_and_restores_it(self):
        traci, scheduler = self.make_scheduler()
        traci.vehicle.vehicles["live_vehicle"] = {}
        traci.vehicle.lanes["live_vehicle"] = "edge_0"
        traci.vehicle.positions["live_vehicle"] = 10.0
        scheduler.schedule(AccidentEvent("crash", 1, 5, "edge_0", 0.5))
        scheduler.tick(1)
        self.assertNotIn("event_vehicle_crash", traci.vehicle.vehicles)
        self.assertIn(("live_vehicle", 0.0), traci.vehicle.speeds)
        scheduler.tick(5)
        self.assertIn(("live_vehicle", -1), traci.vehicle.speeds)
        self.assertEqual(scheduler.snapshots()[0].state, EventState.COMPLETED.value)

    def test_accident_waits_for_a_later_existing_vehicle(self):
        traci, scheduler = self.make_scheduler()
        scheduler.schedule(AccidentEvent("crash", 1, 5, "edge_0", 0.5))
        scheduler.tick(1)

        self.assertEqual(scheduler.snapshots()[0].state, EventState.ACTIVE.value)
        self.assertIsNone(scheduler.snapshots()[0].error)
        traci.vehicle.vehicles["later_vehicle"] = {}
        traci.vehicle.lanes["later_vehicle"] = "edge_0"
        traci.vehicle.positions["later_vehicle"] = 10.0
        scheduler.tick(2)

        self.assertIn(("later_vehicle", 0.0), traci.vehicle.speeds)


    def test_accident_can_reduce_lane_capacity_when_configured(self):
        traci, scheduler = self.make_scheduler()
        scheduler.schedule(AccidentEvent("crash", 1, 5, "edge_0", 0.5, max_speed=0.1))
        scheduler.tick(1)
        self.assertEqual(traci.lane.speeds["edge_0"], 0.1)
        scheduler.tick(5)
        self.assertEqual(traci.lane.speeds["edge_0"], 13.9)

    def test_stopped_vehicle_uses_blocking_vehicle_mechanism(self):
        traci, scheduler = self.make_scheduler()
        traci.vehicle.vehicles["live_vehicle"] = {}
        traci.vehicle.lanes["live_vehicle"] = "edge_0"
        traci.vehicle.positions["live_vehicle"] = 10.0
        scheduler.schedule(StoppedVehicleEvent("stalled", 1, 5, "edge_0", 0.25))
        scheduler.tick(1)
        self.assertNotIn("event_vehicle_stalled", traci.vehicle.vehicles)
        self.assertAlmostEqual(traci.vehicle.stops["live_vehicle"][1]["pos"], 25.0)
        self.assertEqual(scheduler.snapshots()[0].event_type, "stopped_vehicle")
        scheduler.tick(5)
        self.assertIn("live_vehicle", traci.vehicle.resumed)

    def test_stopped_vehicle_fails_when_no_existing_vehicle_is_available(self):
        traci, scheduler = self.make_scheduler()
        scheduler.schedule(StoppedVehicleEvent("stalled", 1, 5, "edge_0", 0.25))
        scheduler.tick(1)
        snapshot = scheduler.snapshots()[0]
        self.assertEqual(snapshot.state, EventState.FAILED.value)
        self.assertIn("No live vehicle", snapshot.error)

    def test_collision_blockage_can_block_multiple_lanes(self):
        traci, scheduler = self.make_scheduler()
        scheduler.schedule(
            CollisionBlockageEvent("collision", 1, 5, ("edge_0", "edge_1"), 0.5)
        )
        scheduler.tick(1)
        self.assertIn("event_vehicle_collision_0", traci.vehicle.vehicles)
        self.assertIn("event_vehicle_collision_1", traci.vehicle.vehicles)
        scheduler.tick(5)
        self.assertNotIn("event_vehicle_collision_0", traci.vehicle.vehicles)
        self.assertNotIn("event_vehicle_collision_1", traci.vehicle.vehicles)

    def test_queue_spillback_blocks_downstream_lanes_but_labels_upstream_lanes(self):
        traci, scheduler = self.make_scheduler()
        traci.vehicle.vehicles["downstream_vehicle"] = {}
        traci.vehicle.lanes["downstream_vehicle"] = "edge_1"
        traci.vehicle.positions["downstream_vehicle"] = 10.0
        scheduler.schedule(
            QueueSpillbackEvent(
                "spillback",
                1,
                5,
                lane_ids=("edge_0",),
                blocked_lane_ids=("edge_1",),
                position_ratio=0.8,
            )
        )
        scheduler.tick(1)

        self.assertNotIn("event_vehicle_spillback", traci.vehicle.vehicles)
        self.assertEqual(
            traci.vehicle.stops["downstream_vehicle"][0],
            "edge",
        )
        self.assertEqual(
            traci.vehicle.stops["downstream_vehicle"][1]["laneIndex"],
            1,
        )
        snapshot = scheduler.snapshots()[0]
        self.assertEqual(snapshot.event_type, "queue_spillback")
        self.assertEqual(snapshot.details["lane_ids"], ("edge_0",))
        self.assertEqual(snapshot.details["blocked_lane_ids"], ("edge_1",))

    def test_queue_spillback_can_reduce_downstream_capacity(self):
        traci, scheduler = self.make_scheduler()
        scheduler.schedule(
            QueueSpillbackEvent(
                "spillback",
                1,
                5,
                lane_ids=("edge_0",),
                blocked_lane_ids=("edge_1",),
                max_speed=0.1,
            )
        )
        scheduler.tick(1)
        self.assertEqual(traci.lane.speeds["edge_1"], 0.1)
        self.assertNotIn("event_vehicle_spillback", traci.vehicle.vehicles)

        scheduler.tick(5)
        self.assertEqual(traci.lane.speeds["edge_1"], 13.9)

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
