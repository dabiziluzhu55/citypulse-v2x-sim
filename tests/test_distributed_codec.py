import unittest

from simulation.sumo.distributed.codec import (
    dumps_config,
    dumps_snapshot,
    loads_config,
    loads_snapshot,
)
from simulation.sumo.events import (
    AccidentEvent,
    EventSnapshot,
    LaneClosureEvent,
    MajorEventClosingEvent,
    MajorEventOpeningEvent,
    SpeedLimitEvent,
)
from simulation.sumo.session import (
    IntersectionRuntimeSnapshot,
    LaneRuntimeSnapshot,
    SessionMetrics,
    SimulationConfig,
    SimulationSnapshot,
    VehicleRuntimeSnapshot,
)


class DistributedCodecTests(unittest.TestCase):
    def test_config_round_trip_preserves_all_event_types(self):
        events = (
            LaneClosureEvent("closure", 1, 2, ("in_0",)),
            SpeedLimitEvent("speed", 2, 3, ("in_1",), 5.5),
            AccidentEvent("accident", 3, 4, "in_2", 0.4),
            MajorEventOpeningEvent(
                "opening", 4, 5, "out_0", 10, source_lane_ids=("in_0",)
            ),
            MajorEventClosingEvent(
                "closing",
                5,
                6,
                "in_0",
                12,
                destination_lane_ids=("out_0",),
            ),
        )
        config = SimulationConfig(
            intersection_ids=("demo_2", "demo_3"),
            origins={"demo_2": ("west",)},
            duration_seconds=60,
            control_mode="algorithm",
            algorithm_transport="local",
            algorithm_module="algorithms.local_policy_example",
            initial_events=events,
        )
        self.assertEqual(loads_config(dumps_config(config)), config)

    def test_snapshot_round_trip_preserves_nested_runtime_data(self):
        lane = LaneRuntimeSnapshot(3, 1, 8.5, 12.0, 24.0)
        intersection = IntersectionRuntimeSnapshot(
            current_phase=2,
            pending_phase=3,
            stage="YELLOW",
            stage_elapsed=1.5,
            lanes={"in_0": lane},
        )
        vehicle = VehicleRuntimeSnapshot(
            vehicle_id="vehicle-1",
            x=1.2,
            y=3.4,
            speed=5.6,
            angle=90.0,
            road_id="in",
            lane_id="in_0",
            controllable=True,
            target_speed=8.0,
        )
        snapshot = SimulationSnapshot(
            session_id="session-1",
            state="RUNNING",
            sequence=4,
            elapsed_seconds=12.5,
            duration_seconds=60.0,
            progress=0.2,
            official_time="07:00:12",
            playback_speed=2.0,
            intersections={"demo_2": intersection},
            vehicles=(vehicle,),
            events=(
                EventSnapshot(
                    "closure",
                    "lane_closure",
                    "ACTIVE",
                    1,
                    20,
                    None,
                    {"lane_ids": ["in_0"]},
                ),
            ),
            metrics=SessionMetrics(
                active_vehicles=1,
                departed_vehicles=2,
                fuel_consumed_mg=3.5,
            ),
        )
        self.assertEqual(loads_snapshot(dumps_snapshot(snapshot)), snapshot)


if __name__ == "__main__":
    unittest.main()
