import unittest
from pathlib import Path

from simulation.sumo.config import load_signal_configuration
from simulation.sumo.controller import SafePhaseController
from simulation.sumo.policy import VehicleTypeMetadata
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

    def getAllowed(self, lane_id):
        if lane_id == "mixed_0":
            return ("bicycle",)
        return ()

    def getDisallowed(self, lane_id):
        return ("passenger",) if lane_id == "branch_in_0" else ()


class FakeEdgeDomain:
    def getIDList(self):
        return ("mixed", "branch_in", "north_in", ":internal")

    def getLaneNumber(self, edge_id):
        return {
            "mixed": 2,
            "branch_in": 1,
            "north_in": 1,
            ":internal": 1,
        }[edge_id]


class FakeTrafficLightDomain:
    def getRedYellowGreenState(self, tls_id):
        return "Gry"


class FakeVehicleDomain:
    def getIDCount(self):
        return 6


class FakeSimulationDomain:
    def getMinExpectedNumber(self):
        return 20


class FakeTraci:
    edge = FakeEdgeDomain()
    lane = FakeLaneDomain()
    trafficlight = FakeTrafficLightDomain()
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
            "from_lane": 0,
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


def vehicle_types():
    return {
        "official_bus": VehicleTypeMetadata(
            type_id="official_bus",
            profile_id="bus",
            pcu_factor=2.0,
            vehicle_class="bus",
            powertrain="diesel",
            emission_class="HBEFA3/Bus",
            accel_mps2=1.2,
            decel_mps2=4.5,
            length_m=12.0,
            width_m=2.5,
            min_gap_m=3.0,
            max_speed_mps=13.9,
            fuel_density_mg_per_ml=832.0,
            hard_braking_threshold_mps2=-2.5,
        ),
        "official_electric_bicycle": VehicleTypeMetadata(
            type_id="official_electric_bicycle",
            profile_id="electric_bicycle",
            pcu_factor=0.5,
            vehicle_class="bicycle",
            powertrain="electric",
            emission_class="HBEFA3/zero",
            accel_mps2=1.5,
            decel_mps2=3.0,
            length_m=1.8,
            width_m=0.65,
            min_gap_m=0.5,
            max_speed_mps=6.94,
            fuel_density_mg_per_ml=1.0,
            hard_braking_threshold_mps2=-2.0,
        ),
        "official_passenger": VehicleTypeMetadata(
            type_id="official_passenger",
            profile_id="passenger",
            pcu_factor=1.0,
            vehicle_class="passenger",
            powertrain="gasoline",
            emission_class="HBEFA3/PC_G_EU4",
            accel_mps2=2.6,
            decel_mps2=4.5,
            length_m=5.0,
            width_m=1.8,
            min_gap_m=2.5,
            max_speed_mps=13.9,
            fuel_density_mg_per_ml=745.0,
            hard_braking_threshold_mps2=-3.0,
        ),
        "official_truck": VehicleTypeMetadata(
            type_id="official_truck",
            profile_id="truck",
            pcu_factor=2.5,
            vehicle_class="truck",
            powertrain="diesel",
            emission_class="HBEFA3/HDV_D_EU4",
            accel_mps2=1.3,
            decel_mps2=4.5,
            length_m=10.0,
            width_m=2.5,
            min_gap_m=3.0,
            max_speed_mps=13.9,
            fuel_density_mg_per_ml=832.0,
            hard_braking_threshold_mps2=-2.5,
        ),
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
            ("branch_in_0", "north_in_0"),
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
        north_lane = intersection.lanes["north_in_0"]
        self.assertEqual(north_lane.intersection_id, "demo_2")
        self.assertEqual(north_lane.approach_id, "demo_2_northeast_main_in")
        self.assertEqual(north_lane.movements, ("through", "left"))
        self.assertEqual(north_lane.length_m, north_lane.length)
        self.assertEqual(north_lane.speed_limit_mps, north_lane.max_speed)
        self.assertEqual(
            north_lane.downstream_lane_ids,
            ("branch_out_0", "south_out_0"),
        )
        outgoing = intersection.lanes["south_out_0"]
        self.assertIsNone(outgoing.approach_id)
        self.assertEqual(outgoing.movements, ())
        self.assertEqual(outgoing.downstream_lane_ids, ())

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
        self.assertTrue(state.lanes["north_in_0"].lane_has_green)
        self.assertEqual(state.lanes["north_in_0"].signal_state, "mixed")
        self.assertEqual(
            {
                item.movement: item.signal_state
                for item in state.lanes["north_in_0"].connection_signal_states
            },
            {"through": "G", "left": "r"},
        )
        self.assertEqual(
            {
                item.movement
                for item in state.lanes["north_in_0"].connection_signal_states
            },
            {"through", "left"},
        )
        self.assertGreater(state.lanes["north_in_0"].queue_length_m, 0.0)
        self.assertEqual(state.lanes["north_in_0"].current_allowed_speed_mps, 13.9)
        self.assertEqual(state.lanes["branch_in_0"].current_allowed_speed_mps, 0.0)
        self.assertEqual(state.lanes["south_out_0"].mean_speed, 0.0)
        self.assertIsNone(state.lanes["south_out_0"].signal_state)
        self.assertEqual(observation.traffic.departed_vehicles, 4)
        self.assertEqual(observation.traffic.arrived_vehicles, 2)

    def test_metadata_contains_global_lane_permissions(self):
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
            vehicle_types=vehicle_types(),
        )

        self.assertNotIn(":internal", metadata.edge_lanes)
        self.assertEqual(
            [lane.lane_index for lane in metadata.edge_lanes["mixed"]],
            [0, 1],
        )
        bicycle_lane = metadata.edge_lanes["mixed"][0]
        self.assertEqual(bicycle_lane.lane_id, "mixed_0")
        self.assertEqual(bicycle_lane.allowed_vehicle_classes, ("bicycle",))
        self.assertEqual(
            bicycle_lane.allowed_vehicle_type_ids,
            ("official_electric_bicycle",),
        )

        electric_bicycle = metadata.vehicle_types["official_electric_bicycle"]
        self.assertEqual(electric_bicycle.profile_id, "electric_bicycle")
        self.assertEqual(electric_bicycle.pcu_factor, 0.5)
        self.assertEqual(electric_bicycle.vehicle_class, "bicycle")
        self.assertEqual(electric_bicycle.powertrain, "electric")

        default_lane = metadata.edge_lanes["north_in"][0]
        self.assertEqual(
            default_lane.allowed_vehicle_type_ids,
            (
                "official_bus",
                "official_electric_bicycle",
                "official_passenger",
                "official_truck",
            ),
        )

        blocked_lane = metadata.edge_lanes["branch_in"][0]
        self.assertEqual(blocked_lane.disallowed_vehicle_classes, ("passenger",))
        self.assertEqual(
            blocked_lane.allowed_vehicle_type_ids,
            ("official_bus", "official_electric_bicycle", "official_truck"),
        )
        self.assertEqual(
            metadata.intersections["demo_2"]
            .lanes["branch_in_0"]
            .allowed_vehicle_type_ids,
            ("official_bus", "official_electric_bicycle", "official_truck"),
        )


if __name__ == "__main__":
    unittest.main()
