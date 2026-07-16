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
                    "demo_5": {"junction_id": "3807"},
                    "demo_6": {"junction_id": "3936"},
                    "demo_9": {"junction_id": "3864"},
                    "demo_12": {"junction_id": "182"},
                    "demo_14": {"junction_id": "882"},
                    "demo_15": {"junction_id": "1117"},
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
        self.assertEqual(demo_4.topology.u_turn_policy, "with_left")
        self.assertEqual(demo_4.topology.direction_mapping["t"], "uturn")

        demo_5 = self.load().intersections["demo_5"]
        self.assertEqual(demo_5.junction_ids, ("3807",))
        self.assertEqual(
            demo_5.topology.approaches,
            {
                "east": ("-50182",),
                "west": ("-56392",),
                "south": ("-57586",),
            },
        )
        self.assertEqual(
            {key: value.cycle_duration for key, value in demo_5.programs.items()},
            {
                "demo_5_morning_peak": 77,
                "demo_5_off_peak": 77,
                "demo_5_evening_peak": 77,
            },
        )
        for program in demo_5.programs.values():
            self.assertEqual([phase.number for phase in program.phases], [1, 2])
            self.assertEqual([phase.green for phase in program.phases], [34, 37])
            self.assertTrue(all(phase.yellow == 3 for phase in program.phases))
            self.assertTrue(all(phase.clearance == 0 for phase in program.phases))

        demo_6 = self.load().intersections["demo_6"]
        self.assertEqual(demo_6.junction_ids, ("3936",))
        self.assertEqual(
            demo_6.topology.approaches,
            {
                "east": ("-56623",),
                "west": ("-50334",),
                "north": ("-50819",),
                "south": ("-57584",),
            },
        )
        self.assertEqual(
            {key: value.cycle_duration for key, value in demo_6.programs.items()},
            {
                "demo_6_morning_peak": 137,
                "demo_6_off_peak": 137,
                "demo_6_evening_peak": 137,
            },
        )
        for program in demo_6.programs.values():
            self.assertEqual([phase.number for phase in program.phases], [1, 2, 3])
            self.assertEqual([phase.green for phase in program.phases], [57, 26, 45])
            self.assertTrue(all(phase.yellow == 3 for phase in program.phases))
            self.assertTrue(all(phase.clearance == 0 for phase in program.phases))
        self.assertEqual(demo_6.topology.phases[1].priority, "permissive")

        demo_9 = self.load().intersections["demo_9"]
        self.assertEqual(demo_9.junction_ids, ("3864",))
        self.assertEqual(
            demo_9.topology.approaches,
            {
                "east": ("-56619",),
                "west": ("-50339",),
                "north": ("-57214",),
                "northeast": ("-50241",),
                "south": ("-56369",),
            },
        )
        for program in demo_9.programs.values():
            self.assertEqual(program.cycle_duration, 145)
            self.assertEqual([phase.number for phase in program.phases], [1, 2, 3])
            self.assertEqual([phase.green for phase in program.phases], [56, 48, 32])
            self.assertTrue(all(phase.yellow == 3 for phase in program.phases))
            self.assertTrue(all(phase.clearance == 0 for phase in program.phases))
        self.assertEqual(
            demo_9.topology.movement_for_direction("-56619", "R"), "right"
        )
        self.assertEqual(
            demo_9.topology.movement_for_direction("-56369", "R"), "blocked"
        )
        self.assertEqual(
            demo_9.topology.movement_for_direction("-50241", "s"), "blocked"
        )

        demo_12 = self.load().intersections["demo_12"]
        self.assertEqual(demo_12.junction_ids, ("182",))
        self.assertEqual(
            demo_12.topology.approaches,
            {
                "east": ("-50293",),
                "west": ("-51273",),
                "north": ("-51253",),
                "south": ("-56345",),
            },
        )
        self.assertEqual(
            {key: value.cycle_duration for key, value in demo_12.programs.items()},
            {
                "demo_12_morning_peak": 170,
                "demo_12_off_peak": 215,
                "demo_12_evening_peak": 180,
            },
        )
        self.assertEqual(
            {
                key: [phase.green for phase in value.phases]
                for key, value in demo_12.programs.items()
            },
            {
                "demo_12_morning_peak": [55, 30, 50, 15],
                "demo_12_off_peak": [65, 35, 60, 35],
                "demo_12_evening_peak": [58, 32, 53, 17],
            },
        )
        self.assertEqual(demo_12.topology.phases[3].priority, "permissive")

        demo_14 = self.load().intersections["demo_14"]
        self.assertEqual(demo_14.junction_ids, ("882",))
        self.assertEqual(
            demo_14.topology.approaches,
            {
                "east": ("-46786",),
                "west": ("-52559",),
                "north": ("-52202",),
                "south": ("-46529",),
            },
        )
        for program in demo_14.programs.values():
            self.assertEqual(program.cycle_duration, 75)
            self.assertEqual([phase.green for phase in program.phases], [22, 47])
            self.assertTrue(all(phase.yellow == 3 for phase in program.phases))
            self.assertTrue(all(phase.clearance == 0 for phase in program.phases))
        self.assertEqual(
            demo_14.topology.movement_for_direction("-52559", "s"), "blocked"
        )
        self.assertEqual(
            demo_14.topology.movement_for_direction("-46786", "s"), "blocked"
        )

        demo_15 = self.load().intersections["demo_15"]
        self.assertEqual(demo_15.junction_ids, ("1117",))
        self.assertEqual(
            demo_15.topology.approaches,
            {
                "east": ("-46787",),
                "west": ("-52560",),
                "north": ("-56026",),
                "south": ("-52227",),
            },
        )
        for program in demo_15.programs.values():
            self.assertEqual(program.cycle_duration, 115)
            self.assertEqual([phase.green for phase in program.phases], [50, 59])
            self.assertTrue(all(phase.yellow == 3 for phase in program.phases))
            self.assertTrue(all(phase.clearance == 0 for phase in program.phases))

    def test_demo_9_templates_follow_five_arm_topology_and_real_foes(self):
        config = self.load().intersections["demo_9"]
        definitions = (
            (0, "south", "-56369", "-50338", "r"),
            (1, "south", "-56369", "-56496", "R"),
            (2, "south", "-56369", "-56370", "s"),
            (4, "south", "-56369", "-56620", "l"),
            (5, "south", "-56369", "-56715", "t"),
            (6, "west", "-50339", "-56715", "r"),
            (7, "west", "-50339", "-50338", "s"),
            (8, "west", "-50339", "-56496", "L"),
            (9, "west", "-50339", "-56370", "l"),
            (10, "north", "-57214", "-56620", "r"),
            (11, "north", "-57214", "-56715", "s"),
            (13, "north", "-57214", "-50338", "l"),
            (14, "north", "-57214", "-56496", "l"),
            (15, "north", "-57214", "-56370", "t"),
            (16, "northeast", "-50241", "-56370", "r"),
            (17, "northeast", "-50241", "-56620", "R"),
            (18, "northeast", "-50241", "-56715", "s"),
            (19, "northeast", "-50241", "-50338", "l"),
            (20, "east", "-56619", "-56496", "r"),
            (21, "east", "-56619", "-56370", "R"),
            (22, "east", "-56619", "-56620", "s"),
            (23, "east", "-56619", "-56715", "l"),
        )
        connections = [
            ControlledConnection(
                intersection_id="demo_9",
                junction_id="3864",
                tls_id="3864",
                link_index=request_index,
                approach=approach,
                movement=config.topology.movement_for_direction(from_edge, direction),
                from_edge=from_edge,
                from_lane=0,
                to_edge=to_edge,
                to_lane=0,
                direction=direction,
                via=f":3864_{request_index}_0",
                request_index=request_index,
            )
            for request_index, approach, from_edge, to_edge, direction in definitions
        ]
        foes = {
            0: "000010000010000010000000",
            1: "111110000110000110000000",
            2: "111001111110001110000000",
            3: "111001111110001110000000",
            4: "110001100001111110000000",
            5: "100001000001100000000000",
            6: "000000000001100000000000",
            7: "100011000011100000011111",
            8: "111111000111100000011110",
            9: "010000101111100000011100",
            10: "010000100000000000010000",
            11: "110001100000001111110000",
            12: "110001100000001111110000",
            13: "110011100000001110001111",
            14: "001111100000001100001110",
            15: "000000000000001000001100",
            16: "001000000000000000001100",
            17: "011000000111111000011100",
            18: "111000000111100110111100",
            19: "111000000110000110000011",
            20: "000000000100000100000010",
            21: "000011110100000100001110",
            22: "000011100011111100011110",
            23: "000011000011100110111110",
        }
        templates = _build_templates(
            config,
            connections,
            {"3864": 24},
            {"3864": foes},
        )
        phase_one = templates[1]["3864"]["green"]
        phase_two = templates[2]["3864"]["green"]
        phase_three = templates[3]["3864"]["green"]
        self.assertTrue(all(phase_one[index] == "G" for index in (2, 11)))
        self.assertTrue(all(phase_one[index] == "g" for index in (4, 13, 14)))
        self.assertTrue(all(phase_two[index] == "G" for index in (7, 22)))
        self.assertTrue(all(phase_two[index] == "g" for index in (9, 23)))
        self.assertEqual(phase_three[19], "G")
        right_turns = (0, 6, 10, 16, 20, 21)
        blocked = (1, 5, 8, 15, 17, 18)
        for phase in templates.values():
            for stage in ("green", "yellow", "clearance"):
                state = phase["3864"][stage]
                self.assertTrue(all(state[index] == "g" for index in right_turns))
                self.assertTrue(all(state[index] == "r" for index in blocked))

    def test_demo_12_14_15_templates_follow_real_foe_matrices(self):
        def connections_for(config, intersection_id, junction_id, definitions):
            return [
                ControlledConnection(
                    intersection_id=intersection_id,
                    junction_id=junction_id,
                    tls_id=junction_id,
                    link_index=request_index,
                    approach=approach,
                    movement=config.topology.movement_for_direction(
                        from_edge, direction
                    ),
                    from_edge=from_edge,
                    from_lane=0,
                    to_edge=f"out_{request_index}",
                    to_lane=0,
                    direction=direction,
                    via=f":{junction_id}_{request_index}_0",
                    request_index=request_index,
                )
                for request_index, approach, from_edge, direction in definitions
            ]

        demo_12 = self.load().intersections["demo_12"]
        connections = connections_for(
            demo_12,
            "demo_12",
            "182",
            (
                (0, "south", "-56345", "r"),
                (1, "south", "-56345", "s"),
                (3, "south", "-56345", "l"),
                (4, "south", "-56345", "t"),
                (5, "west", "-51273", "r"),
                (6, "west", "-51273", "s"),
                (8, "west", "-51273", "l"),
                (9, "north", "-51253", "r"),
                (10, "north", "-51253", "s"),
                (12, "north", "-51253", "l"),
                (13, "north", "-51253", "t"),
                (14, "east", "-50293", "r"),
                (15, "east", "-50293", "s"),
                (17, "east", "-50293", "l"),
            ),
        )
        foes = {
            0: "000000000011000000",
            1: "111111000111000000",
            2: "111111000111000000",
            3: "111001110111000000",
            4: "100000110000000000",
            5: "000000110000000000",
            6: "100001110000001111",
            7: "100001110000001111",
            8: "011011110000001110",
            9: "011000000000000000",
            10: "111000000111111000",
            11: "111000000111111000",
            12: "111000000111001110",
            13: "000000000100000110",
            14: "000000000000000110",
            15: "000001111100001110",
            16: "000001111100001110",
            17: "000001110011011110",
        }
        templates = _build_templates(
            demo_12, connections, {"182": 18}, {"182": foes}
        )
        self.assertTrue(
            all(templates[1]["182"]["green"][index] == "G" for index in (6, 15))
        )
        self.assertTrue(
            all(templates[2]["182"]["green"][index] == "G" for index in (8, 17))
        )
        self.assertTrue(
            all(templates[3]["182"]["green"][index] == "G" for index in (1, 10))
        )
        self.assertTrue(
            all(templates[4]["182"]["green"][index] == "g" for index in (3, 12))
        )
        for phase in templates.values():
            self.assertTrue(
                all(phase["182"]["green"][index] == "g" for index in (0, 5, 9, 14))
            )
            self.assertTrue(
                all(phase["182"]["green"][index] == "r" for index in (4, 13))
            )

        demo_14 = self.load().intersections["demo_14"]
        connections = connections_for(
            demo_14,
            "demo_14",
            "882",
            (
                (0, "south", "-46529", "r"),
                (2, "south", "-46529", "s"),
                (3, "south", "-46529", "l"),
                (4, "west", "-52559", "r"),
                (6, "west", "-52559", "s"),
                (7, "west", "-52559", "l"),
                (8, "north", "-52202", "r"),
                (10, "north", "-52202", "s"),
                (11, "north", "-52202", "l"),
                (12, "east", "-46786", "r"),
                (14, "east", "-46786", "s"),
                (15, "east", "-46786", "l"),
            ),
        )
        foes = {
            0: "0000000000000000",
            1: "0000100001000000",
            2: "1110100011000000",
            3: "1100111011000000",
            4: "0000000000000000",
            5: "1000010000000000",
            6: "1000110000001110",
            7: "1110110000001100",
            8: "0000000000000000",
            9: "0100000000001000",
            10: "1100000011101000",
            11: "1100000011001110",
            12: "0000000000000000",
            13: "0000000010000100",
            14: "0000111010001100",
            15: "0000110011101100",
        }
        templates = _build_templates(
            demo_14, connections, {"882": 16}, {"882": foes}
        )
        self.assertEqual(templates[1]["882"]["green"][10], "G")
        self.assertTrue(
            all(templates[1]["882"]["green"][index] == "g" for index in (11, 15))
        )
        self.assertEqual(templates[2]["882"]["green"][2], "G")
        for phase in templates.values():
            self.assertTrue(
                all(phase["882"]["green"][index] == "g" for index in (0, 12))
            )
            self.assertTrue(
                all(
                    phase["882"]["green"][index] == "r"
                    for index in (3, 4, 6, 7, 8, 14)
                )
            )

        demo_15 = self.load().intersections["demo_15"]
        connections = connections_for(
            demo_15,
            "demo_15",
            "1117",
            (
                (0, "south", "-52227", "r"),
                (1, "south", "-52227", "s"),
                (3, "south", "-52227", "l"),
                (4, "south", "-52227", "t"),
                (5, "west", "-52560", "r"),
                (6, "west", "-52560", "s"),
                (7, "west", "-52560", "l"),
                (8, "north", "-56026", "r"),
                (9, "north", "-56026", "s"),
                (11, "north", "-56026", "l"),
                (12, "north", "-56026", "t"),
                (13, "east", "-46787", "r"),
                (14, "east", "-46787", "s"),
                (15, "east", "-46787", "l"),
            ),
        )
        foes = {
            0: "0000100001000000",
            1: "1111100011000000",
            2: "1111100011000000",
            3: "1100011111000000",
            4: "1000011000000000",
            5: "0000011000000000",
            6: "1000111000001111",
            7: "1101111000001110",
            8: "0100000000001000",
            9: "1100000011111000",
            10: "1100000011111000",
            11: "1100000011000111",
            12: "0000000010000110",
            13: "0000000000000110",
            14: "0000111110001110",
            15: "0000111011011110",
        }
        templates = _build_templates(
            demo_15, connections, {"1117": 16}, {"1117": foes}
        )
        self.assertTrue(
            all(templates[1]["1117"]["green"][index] == "G" for index in (1, 9))
        )
        self.assertTrue(
            all(templates[1]["1117"]["green"][index] == "g" for index in (3, 11))
        )
        self.assertTrue(
            all(templates[2]["1117"]["green"][index] == "G" for index in (6, 14))
        )
        self.assertTrue(
            all(templates[2]["1117"]["green"][index] == "g" for index in (7, 15))
        )
        for phase in templates.values():
            self.assertTrue(
                all(
                    phase["1117"]["green"][index] == "g"
                    for index in (0, 5, 8, 13)
                )
            )
            self.assertTrue(
                all(phase["1117"]["green"][index] == "r" for index in (4, 12))
            )

    def test_demo_6_templates_cover_all_four_way_movements(self):
        config = self.load().intersections["demo_6"]
        definitions = tuple(
            (approach, direction, movement)
            for approach in ("east", "west", "north", "south")
            for direction, movement in (
                ("l", "left"),
                ("s", "through"),
                ("r", "right"),
            )
        )
        connections = [
            ControlledConnection(
                intersection_id="demo_6",
                junction_id="3936",
                tls_id="3936",
                link_index=index,
                approach=approach,
                movement=movement,
                from_edge=config.topology.approaches[approach][0],
                from_lane=0,
                to_edge=f"out_{approach}_{movement}",
                to_lane=0,
                direction=direction,
                via=f":3936_{index}_0",
                request_index=index,
            )
            for index, (approach, direction, movement) in enumerate(definitions)
        ]
        state_length = len(connections)
        templates = _build_templates(
            config,
            connections,
            {"3936": state_length},
            {"3936": {index: "0" * state_length for index in range(state_length)}},
        )
        phase_one = templates[1]["3936"]["green"]
        phase_two = templates[2]["3936"]["green"]
        phase_three = templates[3]["3936"]["green"]
        self.assertTrue(all(phase_one[index] == "G" for index in (1, 4)))
        self.assertTrue(all(phase_one[index] == "g" for index in (0, 3)))
        self.assertTrue(all(phase_two[index] == "g" for index in (6, 9)))
        self.assertTrue(all(phase_three[index] == "G" for index in (7, 10)))
        for phase in (phase_one, phase_two, phase_three):
            self.assertTrue(all(phase[index] == "g" for index in (2, 5, 8, 11)))

    def test_demo_5_templates_cover_all_t_junction_movements(self):
        config = self.load().intersections["demo_5"]
        definitions = (
            ("east", "l", "left"),
            ("east", "s", "through"),
            ("west", "s", "through"),
            ("west", "r", "right"),
            ("south", "l", "left"),
            ("south", "r", "right"),
        )
        connections = [
            ControlledConnection(
                intersection_id="demo_5",
                junction_id="3807",
                tls_id="3807",
                link_index=index,
                approach=approach,
                movement=movement,
                from_edge=config.topology.approaches[approach][0],
                from_lane=0,
                to_edge=f"out_{approach}_{movement}",
                to_lane=0,
                direction=direction,
                via=f":3807_{index}_0",
                request_index=index,
            )
            for index, (approach, direction, movement) in enumerate(definitions)
        ]
        state_length = len(connections)
        templates = _build_templates(
            config,
            connections,
            {"3807": state_length},
            {"3807": {index: "0" * state_length for index in range(state_length)}},
        )
        phase_one = templates[1]["3807"]["green"]
        phase_two = templates[2]["3807"]["green"]
        self.assertEqual(phase_one[0], "g")
        self.assertEqual(phase_one[1], "G")
        self.assertEqual(phase_one[2], "G")
        self.assertEqual(phase_two[4], "G")
        self.assertEqual(phase_one[3], "g")
        self.assertEqual(phase_one[5], "g")

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
        for approach in ("north", "south"):
            connections.append(
                ControlledConnection(
                    intersection_id="demo_4",
                    junction_id="3935",
                    tls_id="3935",
                    link_index=index,
                    approach=approach,
                    movement="uturn",
                    from_edge=config.topology.approaches[approach][0],
                    from_lane=0,
                    to_edge=f"out_{approach}_uturn",
                    to_lane=0,
                    direction="t",
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
        north_uturn = 12
        south_uturn = 13
        self.assertEqual(morning[3]["3935"]["green"][west_through], "G")
        self.assertEqual(morning[3]["3935"]["green"][west_left], "G")
        self.assertEqual(morning[2]["3935"]["green"][north_uturn], "G")
        self.assertEqual(morning[2]["3935"]["green"][south_uturn], "G")

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
        self.assertEqual(off_peak[1]["3935"]["green"][north_uturn], "g")
        self.assertEqual(off_peak[1]["3935"]["green"][south_uturn], "g")

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

