import json
import shutil
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from simulation.sumo.build_tls import (
    ControlledConnection,
    _build_templates,
    _read_junction_types,
    _remove_empty_params,
    _run_netconvert,
)
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
                    "demo_4": {"junction_id": "3935"},
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

        demo_4 = self.load().intersections["demo_4"]
        self.assertEqual(demo_4.junction_ids, ("3935",))
        self.assertEqual(
            demo_4.topology.approaches,
            {
                "east": ("-57186",),
                "west": ("-50333",),
                "north": ("-57229",),
                "south": ("-56732",),
            },
        )
        self.assertEqual(
            {key: value.cycle_duration for key, value in demo_4.programs.items()},
            {
                "demo_4_morning_peak": 180,
                "demo_4_off_peak": 140,
                "demo_4_evening_peak": 180,
            },
        )
        self.assertEqual(
            {
                key: [phase.number for phase in value.phases]
                for key, value in demo_4.programs.items()
            },
            {
                "demo_4_morning_peak": [1, 2, 3, 4],
                "demo_4_off_peak": [1, 2, 3],
                "demo_4_evening_peak": [1, 2, 3, 4],
            },
        )
        self.assertEqual(
            [
                phase.movement
                for phase in demo_4.topology.phases_for("demo_4_off_peak")
            ],
            ["through", "through", "through"],
        )

    def test_demo_4_program_templates_support_protected_and_permissive_groups(self):
        config = self.load().intersections["demo_4"]
        connections = []
        index = 0
        for approach in ("east", "west", "north", "south"):
            for direction, movement in (
                ("r", "right"),
                ("s", "through"),
                ("l", "left"),
            ):
                connections.append(
                    ControlledConnection(
                        intersection_id="demo_4",
                        junction_id="3935",
                        tls_id="3935",
                        link_index=index,
                        approach=approach,
                        movement=movement,
                        from_edge=config.topology.approaches[approach][0],
                        from_lane=0,
                        to_edge=f"out_{approach}_{movement}",
                        to_lane=0,
                        direction=direction,
                        via=f":3935_{index}_0",
                        request_index=index,
                    )
                )
                index += 1
        state_length = len(connections)
        no_foes = {value: "0" * state_length for value in range(state_length)}

        morning = _build_templates(
            config,
            connections,
            {"3935": state_length},
            {"3935": no_foes},
            config.topology.phases_for("demo_4_morning_peak"),
        )
        west_through = 4
        west_left = 5
        self.assertEqual(morning[3]["3935"]["green"][west_through], "G")
        self.assertEqual(morning[3]["3935"]["green"][west_left], "G")

        off_peak = _build_templates(
            config,
            connections,
            {"3935": state_length},
            {"3935": no_foes},
            config.topology.phases_for("demo_4_off_peak"),
        )
        north_through = 7
        north_left = 8
        south_through = 10
        south_left = 11
        self.assertEqual(off_peak[1]["3935"]["green"][north_through], "G")
        self.assertEqual(off_peak[1]["3935"]["green"][south_through], "G")
        self.assertEqual(off_peak[1]["3935"]["green"][north_left], "g")
        self.assertEqual(off_peak[1]["3935"]["green"][south_left], "g")

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

    def test_empty_sumo_params_are_removed_without_touching_nonempty_values(self):
        net_path = Path(self.temp_directory.name) / "test.net.xml"
        net_path.write_text(
            """<?xml version="1.0" encoding="UTF-8"?>
<net>
  <junction id="317">
    <param key="empty" value=""/>
    <param key="whitespace" value="   "/>
    <param key="kept" value="demo_2"/>
  </junction>
</net>
""",
            encoding="utf-8",
        )
        self.assertEqual(_remove_empty_params(net_path), 2)
        self.assertEqual(_remove_empty_params(net_path), 0)
        content = net_path.read_text(encoding="utf-8")
        self.assertNotIn('key="empty"', content)
        self.assertNotIn('key="whitespace"', content)
        self.assertIn('key="kept" value="demo_2"', content)

    def write_source_network(self):
        source = Path(self.temp_directory.name) / "source.net.xml"
        source.write_text(
            """<?xml version="1.0" encoding="UTF-8"?>
<net>
  <junction id="317" type="priority">
    <param key="empty" value=""/>
  </junction>
  <junction id="3935" type="traffic_light">
    <param key="kept" value="demo_4"/>
  </junction>
</net>
""",
            encoding="utf-8",
        )
        return source

    def test_existing_tls_is_copied_without_calling_netconvert(self):
        source = self.write_source_network()
        target = Path(self.temp_directory.name) / "generated" / "target.net.xml"
        self.assertEqual(
            _read_junction_types(source, ["3935"]),
            {"3935": "traffic_light"},
        )
        with patch("simulation.sumo.build_tls.subprocess.run") as run:
            applied, removed = _run_netconvert(
                "netconvert", source, target, ["3935"]
            )
        run.assert_not_called()
        self.assertFalse(applied)
        self.assertEqual(removed, 0)
        self.assertEqual(target.read_bytes(), source.read_bytes())

    def test_netconvert_only_receives_unsignalized_junctions_and_clean_input(self):
        source = self.write_source_network()
        target = Path(self.temp_directory.name) / "generated" / "target.net.xml"

        def fake_run(command, check):
            self.assertTrue(check)
            tls_index = command.index("--tls.set") + 1
            self.assertEqual(command[tls_index], "317")
            input_index = command.index("--sumo-net-file") + 1
            sanitized_source = Path(command[input_index])
            self.assertNotIn('value=""', sanitized_source.read_text(encoding="utf-8"))
            shutil.copy2(sanitized_source, target)

        with patch(
            "simulation.sumo.build_tls.subprocess.run", side_effect=fake_run
        ) as run:
            applied, removed = _run_netconvert(
                "netconvert", source, target, ["317", "3935"]
            )
        self.assertEqual(run.call_count, 1)
        self.assertTrue(applied)
        self.assertEqual(removed, 1)
        self.assertTrue(target.is_file())
        self.assertEqual(
            list(target.parent.glob("*.netconvert-input.net.xml")),
            [],
        )


if __name__ == "__main__":
    unittest.main()

