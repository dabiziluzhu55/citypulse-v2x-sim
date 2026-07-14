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
            json.dumps({"demo_1": {"junction_id": "4427"}}),
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


if __name__ == "__main__":
    unittest.main()

