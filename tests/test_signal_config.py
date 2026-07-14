import json
import tempfile
import unittest
from pathlib import Path

from simulation.sumo.build_tls import ControlledConnection, _build_templates
from simulation.sumo.config import load_signal_configuration


ROOT = Path(__file__).resolve().parents[1]
PLANS = ROOT / "data" / "maps" / "sumo" / "official_tls_plans.json"
TOPOLOGY = ROOT / "data" / "maps" / "sumo" / "official_tls_topology.json"


class SignalConfigurationTests(unittest.TestCase):
    def setUp(self):
        self.temp_directory = tempfile.TemporaryDirectory()
        self.mapping = Path(self.temp_directory.name) / "mapping.json"
        self.mapping.write_text(
            json.dumps(
                {
                    "demo_1": {"junction_id": "4427"},
                    "demo_2": {"junction_id": "317"},
                }
            ),
            encoding="utf-8",
        )

    def tearDown(self):
        self.temp_directory.cleanup()

    def load(self):
        return load_signal_configuration(self.mapping, PLANS, TOPOLOGY)

    def test_official_cycles_and_mapping(self):
        config = self.load().intersections["demo_1"]
        self.assertEqual(config.junction_ids, ("4427",))
        self.assertEqual(config.topology.approaches["east"], ("-56384",))
        self.assertEqual(config.topology.approaches["west"], ("-57217",))
        self.assertEqual(config.topology.approaches["north"], ("-56907",))
        self.assertEqual(
            config.topology.approaches["south"],
            ("-manual_demo1_missing_arm",),
        )
        self.assertEqual(
            {key: value.cycle_duration for key, value in config.programs.items()},
            {
                "demo_1_morning_peak": 160,
                "demo_1_off_peak": 140,
                "demo_1_evening_peak": 160,
            },
        )
        for program in config.programs.values():
            self.assertEqual([phase.number for phase in program.phases], [1, 2, 3, 4])
            self.assertTrue(all(phase.yellow == 3 for phase in program.phases))
            self.assertTrue(all(phase.clearance == 2 for phase in program.phases))

        demo_2 = self.load().intersections["demo_2"]
        self.assertEqual(demo_2.junction_ids, ("317",))
        self.assertEqual(
            demo_2.topology.approaches["northeast_main"],
            ("-56734",),
        )
        self.assertEqual(
            demo_2.topology.approaches["southwest_main"],
            ("-57228",),
        )
        self.assertEqual(
            demo_2.topology.approaches["southeast_branch"],
            ("-51425",),
        )
        self.assertEqual(
            {key: value.cycle_duration for key, value in demo_2.programs.items()},
            {
                "demo_2_morning_peak": 80,
                "demo_2_off_peak": 80,
                "demo_2_evening_peak": 80,
            },
        )
        for program in demo_2.programs.values():
            self.assertEqual([phase.number for phase in program.phases], [1, 2])
            self.assertTrue(all(phase.yellow == 3 for phase in program.phases))
            self.assertTrue(all(phase.clearance == 0 for phase in program.phases))

    def test_templates_keep_right_turns_permissive(self):
        config = self.load().intersections["demo_1"]
        connections = []
        index = 0
        for approach in ("east", "west", "north", "south"):
            from_edge = config.topology.approaches[approach][0]
            for direction, movement in (("s", "through"), ("l", "left"), ("r", "right")):
                connections.append(
                    ControlledConnection(
                        intersection_id="demo_1",
                        junction_id="4427",
                        tls_id="4427",
                        link_index=index,
                        approach=approach,
                        movement=movement,
                        from_edge=from_edge,
                        from_lane=0,
                        to_edge=f"out_{approach}_{movement}",
                        to_lane=0,
                        direction=direction,
                        via=f":4427_{index}_0",
                        request_index=index,
                    )
                )
                index += 1
        templates = _build_templates(
            config,
            connections,
            {"4427": len(connections)},
            {"4427": {value: "0" * len(connections) for value in range(len(connections))}},
        )
        right_indices = [item.link_index for item in connections if item.movement == "right"]
        for phase_templates in templates.values():
            for stage in ("green", "yellow", "clearance"):
                state = phase_templates["4427"][stage]
                self.assertEqual(len(state), len(connections))
                self.assertTrue(all(state[value] == "g" for value in right_indices))
        phase_one = templates[1]["4427"]
        protected = [
            item.link_index
            for item in connections
            if item.approach in {"east", "west"} and item.movement == "through"
        ]
        self.assertTrue(all(phase_one["green"][value] == "G" for value in protected))
        self.assertTrue(all(phase_one["yellow"][value] == "y" for value in protected))
        self.assertTrue(all(phase_one["clearance"][value] == "r" for value in protected))

    def test_demo_2_templates_cover_normal_movements_and_block_uturns(self):
        config = self.load().intersections["demo_2"]
        definitions = (
            ("northeast_main", "s", "through"),
            ("northeast_main", "s", "through"),
            ("northeast_main", "l", "left"),
            ("northeast_main", "t", "blocked"),
            ("southeast_branch", "r", "right"),
            ("southeast_branch", "l", "left"),
            ("southwest_main", "r", "right"),
            ("southwest_main", "s", "through"),
            ("southwest_main", "s", "through"),
            ("southwest_main", "t", "blocked"),
        )
        connections = []
        for index, (approach, direction, movement) in enumerate(definitions):
            connections.append(
                ControlledConnection(
                    intersection_id="demo_2",
                    junction_id="317",
                    tls_id="317",
                    link_index=index,
                    approach=approach,
                    movement=movement,
                    from_edge=config.topology.approaches[approach][0],
                    from_lane=0,
                    to_edge=f"out_{approach}_{movement}",
                    to_lane=0,
                    direction=direction,
                    via=f":317_{index}_0",
                    request_index=index,
                )
            )
        templates = _build_templates(
            config,
            connections,
            {"317": len(connections)},
            {
                "317": {
                    0: "1000100000",
                    1: "1000100000",
                    2: "0110100000",
                    3: "0110000000",
                    4: "0110000000",
                    5: "1110000111",
                    6: "0000000000",
                    7: "0000111100",
                    8: "0000111100",
                    9: "0000100011",
                }
            },
        )
        phase_one = templates[1]["317"]
        phase_two = templates[2]["317"]
        northeast_left = 2
        branch_left = 5
        blocked_uturns = (3, 9)
        self.assertEqual(phase_one["green"][northeast_left], "g")
        self.assertEqual(phase_one["yellow"][northeast_left], "y")
        self.assertEqual(phase_two["green"][branch_left], "G")
        self.assertEqual(phase_two["yellow"][branch_left], "y")
        for phase in templates.values():
            for tls_states in phase.values():
                for stage in ("green", "yellow", "clearance"):
                    self.assertTrue(
                        all(tls_states[stage][index] == "r" for index in blocked_uturns)
                    )


if __name__ == "__main__":
    unittest.main()

