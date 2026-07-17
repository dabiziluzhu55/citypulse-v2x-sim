import unittest
from pathlib import Path

from simulation.sumo.config import load_signal_configuration
from simulation.sumo.controller import SafePhaseController
from simulation.sumo.run import (
    _build_metadata,
    _observe,
    _select_program_manifests,
    _select_programs,
)


ROOT = Path(__file__).resolve().parents[1]
SUMO_DATA = ROOT / "data" / "maps" / "sumo"


class FakeLaneDomain:
    def getLength(self, lane_id):
        return 100.0

    def getMaxSpeed(self, lane_id):
        return 13.9

    def getLastStepVehicleNumber(self, lane_id):
        return 0 if "out" in lane_id else 3

    def getLastStepHaltingNumber(self, lane_id):
        return 0 if "out" in lane_id else 2

    def getLastStepMeanSpeed(self, lane_id):
        return -1.0 if "out" in lane_id else 4.5

    def getWaitingTime(self, lane_id):
        return 0.0 if "out" in lane_id else 12.0

    def getLastStepOccupancy(self, lane_id):
        return 0.0 if "out" in lane_id else 20.0


class FakeVehicleDomain:
    def getIDCount(self):
        return 6


class FakeSimulationDomain:
    def getMinExpectedNumber(self):
        return 20


class FakeTraci:
    lane = FakeLaneDomain()
    vehicle = FakeVehicleDomain()
    simulation = FakeSimulationDomain()


def manifest():
    connections = [
        {
            "tls_id": "317",
            "link_index": 0,
            "approach": "northeast_main",
            "movement": "through",
            "from_edge": "north_in",
            "from_lane": 0,
            "to_edge": "south_out",
            "to_lane": 0,
            "direction": "s",
        },
        {
            "tls_id": "317",
            "link_index": 1,
            "approach": "northeast_main",
            "movement": "left",
            "from_edge": "north_in",
            "from_lane": 1,
            "to_edge": "branch_out",
            "to_lane": 0,
            "direction": "l",
        },
        {
            "tls_id": "317",
            "link_index": 2,
            "approach": "southeast_branch",
            "movement": "left",
            "from_edge": "branch_in",
            "from_lane": 0,
            "to_edge": "north_out",
            "to_lane": 0,
            "direction": "l",
        },
    ]
    return {
        "demo_2": {
            "phase_order": [1, 2],
            "phase_movements": [
                {"phase_number": 1, "movement": "through", "approaches": ["northeast_main"]},
                {"phase_number": 2, "movement": "left", "approaches": ["southeast_branch"]},
            ],
            "connections": connections,
            "templates": {
                "1": {"317": {"green": "Ggr"}},
                "2": {"317": {"green": "rrG"}},
            },
        }
    }


class AlgorithmMetadataTests(unittest.TestCase):
    def load_configuration(self):
        return load_signal_configuration(
            SUMO_DATA / "TotalMap_20.intersections.json",
            SUMO_DATA / "official_tls_plans.json",
            SUMO_DATA / "official_tls_topology.json",
        )

    def test_period_selects_each_intersections_own_program(self):
        configs = self.load_configuration().select(["demo_1", "demo_2"])
        programs = _select_programs(configs, "", "morning_peak")
        self.assertEqual(programs["demo_1"].program_id, "demo_1_morning_peak")
        self.assertEqual(programs["demo_2"].program_id, "demo_2_morning_peak")

    def test_program_specific_manifest_view_matches_selected_period(self):
        configs = self.load_configuration().select(["demo_4"])
        programs = _select_programs(configs, "", "off_peak")
        selected = _select_program_manifests(
            {
                "demo_4": {
                    "connections": [],
                    "programs": {
                        "demo_4_morning_peak": {"phase_order": [1, 2, 3, 4]},
                        "demo_4_off_peak": {"phase_order": [1, 2, 3]},
                    },
                }
            },
            programs,
        )
        self.assertEqual(selected["demo_4"]["phase_order"], [1, 2, 3])
        self.assertEqual(selected["demo_4"]["connections"], [])

    def test_metadata_contains_upstream_downstream_and_phase_connections(self):
        configuration = self.load_configuration()
        configs = configuration.select(["demo_2"])
        programs = _select_programs(configs, "", "morning_peak")
        metadata = _build_metadata(
            FakeTraci(),
            manifest(),
            programs,
            period="morning_peak",
            seed=42,
            decision_interval=5.0,
            minimum_green=5.0,
            episode_id="episode-test",
        )
        intersection = metadata.intersections["demo_2"]
        self.assertEqual(metadata.seed, 42)
        self.assertEqual(
            intersection.incoming_lanes,
            ("branch_in_0", "north_in_0", "north_in_1"),
        )
        self.assertEqual(
            intersection.outgoing_lanes,
            ("branch_out_0", "north_out_0", "south_out_0"),
        )
        self.assertEqual(len(intersection.connections), 3)
        phase_one = intersection.phases[1]
        self.assertEqual(phase_one.name, "南北向直行")
        self.assertEqual(
            set(phase_one.connection_priorities.values()),
            {"protected", "permissive"},
        )
        phase_two = intersection.phases[2]
        self.assertEqual(
            list(phase_two.connection_priorities.values()),
            ["protected"],
        )
        self.assertTrue(
            all(lane.length == 100.0 for lane in intersection.lanes.values())
        )

        controller = SafePhaseController(
            (1, 2),
            {1: (3.0, 0.0), 2: (3.0, 0.0)},
        )
        observation = _observe(
            FakeTraci(),
            simulation_time=5.0,
            step_id=1,
            metadata=metadata,
            controllers={"demo_2": controller},
            departed_vehicles=4,
            arrived_vehicles=2,
        )
        state = observation.intersections["demo_2"]
        self.assertIsNone(state.pending_phase)
        self.assertEqual(set(state.lanes), set(intersection.lanes))
        self.assertEqual(state.lanes["north_in_0"].halting_count, 2)
        self.assertEqual(state.lanes["south_out_0"].mean_speed, 0.0)
        self.assertEqual(observation.traffic.departed_vehicles, 4)
        self.assertEqual(observation.traffic.arrived_vehicles, 2)


if __name__ == "__main__":
    unittest.main()
