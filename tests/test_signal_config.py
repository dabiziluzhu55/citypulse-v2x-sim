import json
import shutil
import tempfile
import unittest
import xml.etree.ElementTree as ET
from pathlib import Path
from unittest.mock import patch

from simulation.sumo.build_tls import (
    ControlledConnection,
    _blocked_turnaround_deletions,
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
                    "demo_3": {"junction_id": "citypulse_demo_3"},
                    "demo_4": {"junction_id": "3935"},
                    "demo_5": {"junction_id": "3807"},
                    "demo_6": {"junction_id": "3936"},
                    "demo_9": {"junction_id": "3864"},
                    "demo_12": {"junction_id": "182"},
                    "demo_14": {"junction_id": "882"},
                    "demo_15": {"junction_id": "1117"},
                    "demo_16": {"junction_id": "3279"},
                    "demo_17": {"junction_id": "3702"},
                    "demo_18": {"junction_id": "4409"},
                    "demo_19": {"junction_id": "891"},
                    "demo_10": {"junction_id": "4162"},
                    "demo_13": {"junction_id": "1204"},
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
        self.assertEqual(config.topology.approaches["east"], ("-56907",))
        self.assertEqual(
            config.topology.approaches["west"],
            ("-manual_demo1_missing_arm",),
        )
        self.assertEqual(config.topology.approaches["north"], ("-57217",))
        self.assertEqual(config.topology.approaches["south"], ("-56384",))
        self.assertEqual(config.topology.u_turn_policy, "blocked")
        self.assertEqual(
            config.topology.movement_for_direction("-57217", "t"), "blocked"
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
        self.assertEqual(
            [phase.green for phase in config.programs["demo_1_morning_peak"].phases],
            [38, 32, 32, 38],
        )
        self.assertEqual(
            [phase.green for phase in config.programs["demo_1_off_peak"].phases],
            [27, 33, 33, 27],
        )
        self.assertEqual(
            [phase.green for phase in config.programs["demo_1_evening_peak"].phases],
            [37, 34, 37, 32],
        )

        demo_2 = self.load().intersections["demo_2"]
        self.assertEqual(demo_2.junction_ids, ("317",))
        self.assertEqual(
            demo_2.topology.approaches["north"],
            ("-57228",),
        )
        self.assertEqual(
            demo_2.topology.approaches["south"],
            ("-56734",),
        )
        self.assertEqual(
            demo_2.topology.approaches["west"],
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

        demo_3 = self.load().intersections["demo_3"]
        self.assertEqual(demo_3.junction_ids, ("citypulse_demo_3",))
        self.assertEqual(
            demo_3.topology.approaches,
            {
                "east": ("-57582",),
                "west": ("-50816",),
                "north": ("-46791",),
                "south": ("-52565",),
            },
        )
        self.assertEqual(demo_3.topology.u_turn_policy, "blocked")
        self.assertEqual(
            demo_3.topology.movement_for_direction("-57582", "t"), "blocked"
        )
        for program in demo_3.programs.values():
            self.assertEqual(program.cycle_duration, 108)
            self.assertEqual([phase.number for phase in program.phases], [1, 2])
            self.assertEqual([phase.green for phase in program.phases], [55, 47])
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
        self.assertEqual(demo_4.topology.u_turn_policy, "blocked")
        self.assertEqual(demo_4.topology.direction_mapping["t"], "blocked")

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

        demo_16 = self.load().intersections["demo_16"]
        self.assertEqual(demo_16.junction_ids, ("3279",))
        self.assertEqual(
            demo_16.topology.approaches,
            {
                "east": ("-55547",),
                "west": ("-50930",),
                "north": ("-57802",),
                "south": ("-50943",),
            },
        )
        for program in demo_16.programs.values():
            self.assertEqual(program.cycle_duration, 77)
            self.assertEqual([phase.number for phase in program.phases], [1, 2])
            self.assertEqual([phase.green for phase in program.phases], [34, 37])
            self.assertTrue(all(phase.yellow == 3 for phase in program.phases))
            self.assertTrue(all(phase.clearance == 0 for phase in program.phases))
        self.assertEqual(demo_16.topology.u_turn_policy, "blocked")
        self.assertEqual(
            demo_16.topology.movement_for_direction("-50930", "t"), "blocked"
        )

        demo_17 = self.load().intersections["demo_17"]
        self.assertEqual(demo_17.junction_ids, ("3702",))
        self.assertEqual(
            demo_17.topology.approaches,
            {
                "east": ("-56184",),
                "north": ("-57320",),
                "south": ("-57329",),
            },
        )
        for program in demo_17.programs.values():
            self.assertEqual(program.cycle_duration, 96)
            self.assertEqual([phase.number for phase in program.phases], [1, 2])
            self.assertEqual([phase.green for phase in program.phases], [58, 32])
            self.assertTrue(all(phase.yellow == 3 for phase in program.phases))
            self.assertTrue(all(phase.clearance == 0 for phase in program.phases))
        self.assertEqual(
            demo_17.topology.movement_for_direction("-57320", "t"), "blocked"
        )

        demo_18 = self.load().intersections["demo_18"]
        self.assertEqual(demo_18.junction_ids, ("4409",))
        self.assertEqual(
            demo_18.topology.approaches,
            {
                "northeast": ("-56830",),
                "southwest": ("-57077",),
                "northwest": ("-56004",),
                "southeast": ("-57004",),
            },
        )
        for program in demo_18.programs.values():
            self.assertEqual(program.cycle_duration, 80)
            self.assertEqual([phase.number for phase in program.phases], [1, 2])
            self.assertEqual([phase.green for phase in program.phases], [37, 37])
            self.assertTrue(all(phase.yellow == 3 for phase in program.phases))
            self.assertTrue(all(phase.clearance == 0 for phase in program.phases))
        self.assertEqual(
            demo_18.topology.movement_for_direction("-56830", "t"), "blocked"
        )

        demo_19 = self.load().intersections["demo_19"]
        self.assertEqual(demo_19.junction_ids, ("891",))
        self.assertEqual(
            demo_19.topology.approaches,
            {
                "northeast": ("-55837",),
                "southwest": ("-57395",),
                "northwest": ("-52215",),
                "southeast": ("-46538",),
            },
        )
        for program in demo_19.programs.values():
            self.assertEqual(program.cycle_duration, 76)
            self.assertEqual([phase.number for phase in program.phases], [1, 2])
            self.assertEqual([phase.green for phase in program.phases], [35, 35])
            self.assertTrue(all(phase.yellow == 3 for phase in program.phases))
            self.assertTrue(all(phase.clearance == 0 for phase in program.phases))
        self.assertEqual(
            demo_19.topology.movement_for_direction("-57395", "t"), "blocked"
        )

        demo_10 = self.load().intersections["demo_10"]
        self.assertEqual(demo_10.junction_ids, ("4162",))
        self.assertEqual(
            demo_10.topology.approaches,
            {
                "east": ("-50726",),
                "west": ("-57445",),
                "south": ("-50758",),
            },
        )
        self.assertEqual(
            {key: value.cycle_duration for key, value in demo_10.programs.items()},
            {
                "demo_10_morning_peak": 120,
                "demo_10_off_peak": 90,
                "demo_10_evening_peak": 120,
            },
        )
        self.assertEqual(demo_10.topology.phases[1].priority, "permissive")

        demo_13 = self.load().intersections["demo_13"]
        self.assertEqual(demo_13.junction_ids, ("1204",))
        self.assertEqual(
            demo_13.topology.approaches,
            {"east": ("-56457",), "north": ("-46884",)},
        )
        self.assertEqual(
            {key: value.cycle_duration for key, value in demo_13.programs.items()},
            {
                "demo_13_morning_peak": 152,
                "demo_13_off_peak": 147,
                "demo_13_evening_peak": 152,
            },
        )
        self.assertEqual(
            {
                key: [phase.number for phase in value.phases]
                for key, value in demo_13.programs.items()
            },
            {
                "demo_13_morning_peak": [1, 2, 3, 4],
                "demo_13_off_peak": [1, 2, 3],
                "demo_13_evening_peak": [1, 2, 3, 4],
            },
        )
        self.assertEqual(
            demo_13.topology.movement_for_direction("-56457", "r"), "left"
        )
        self.assertEqual(
            demo_13.topology.movement_for_direction("-46884", "t"), "uturn"
        )

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

    def test_demo_16_and_17_templates_follow_real_foe_matrices(self):
        demo_16 = self.load().intersections["demo_16"]
        definitions_16 = (
            (0, "south", "-50943", "r"),
            (1, "south", "-50943", "s"),
            (2, "south", "-50943", "l"),
            (3, "west", "-50930", "r"),
            (4, "west", "-50930", "s"),
            (5, "west", "-50930", "l"),
            (6, "west", "-50930", "t"),
            (7, "north", "-57802", "r"),
            (8, "north", "-57802", "s"),
            (9, "north", "-57802", "l"),
            (10, "east", "-55547", "r"),
            (11, "east", "-55547", "s"),
            (12, "east", "-55547", "l"),
        )
        connections_16 = [
            ControlledConnection(
                intersection_id="demo_16",
                junction_id="3279",
                tls_id="3279",
                link_index=request_index,
                approach=approach,
                movement=demo_16.topology.movement_for_direction(
                    from_edge, direction
                ),
                from_edge=from_edge,
                from_lane=0,
                to_edge=f"out_{request_index}",
                to_lane=0,
                direction=direction,
                via=f":3279_{request_index}_0",
                request_index=request_index,
            )
            for request_index, approach, from_edge, direction in definitions_16
        ]
        foes_16 = {
            0: "0001000010000",
            1: "1111000110000",
            2: "1100111110000",
            3: "1000100000000",
            4: "1001100000111",
            5: "1111100000110",
            6: "0100010000100",
            7: "0100001000100",
            8: "1100000111100",
            9: "1100000110011",
            10: "0000000100010",
            11: "0001111100110",
            12: "0001100111110",
        }
        templates_16 = _build_templates(
            demo_16, connections_16, {"3279": 13}, {"3279": foes_16}
        )
        self.assertTrue(
            all(templates_16[1]["3279"]["green"][index] == "G" for index in (4, 11))
        )
        self.assertTrue(
            all(templates_16[1]["3279"]["green"][index] == "g" for index in (5, 12))
        )
        self.assertTrue(
            all(templates_16[2]["3279"]["green"][index] == "G" for index in (1, 8))
        )
        self.assertTrue(
            all(templates_16[2]["3279"]["green"][index] == "g" for index in (2, 9))
        )
        for phase in templates_16.values():
            self.assertTrue(
                all(phase["3279"]["green"][index] == "g" for index in (0, 3, 7, 10))
            )
            self.assertEqual(phase["3279"]["green"][6], "r")

        demo_17 = self.load().intersections["demo_17"]
        definitions_17 = (
            (8, 1, "east", "-56184", "r"),
            (9, 0, "east", "-56184", "l"),
            (4, 4, "north", "-57320", "s"),
            (5, 5, "north", "-57320", "s"),
            (6, 3, "north", "-57320", "l"),
            (7, 2, "north", "-57320", "t"),
            (0, 9, "south", "-57329", "r"),
            (1, 7, "south", "-57329", "s"),
            (2, 8, "south", "-57329", "s"),
            (3, 6, "south", "-57329", "t"),
        )
        connections_17 = [
            ControlledConnection(
                intersection_id="demo_17",
                junction_id="3702",
                tls_id="3702",
                link_index=link_index,
                approach=approach,
                movement=demo_17.topology.movement_for_direction(
                    from_edge, direction
                ),
                from_edge=from_edge,
                from_lane=0,
                to_edge=f"out_{request_index}",
                to_lane=0,
                direction=direction,
                via=f":3702_{request_index}_0",
                request_index=request_index,
            )
            for request_index, link_index, approach, from_edge, direction in definitions_17
        ]
        foes_17 = {
            0: "0001000000",
            1: "1111000000",
            2: "1111000000",
            3: "1000110000",
            4: "1000001000",
            5: "1000001000",
            6: "1000000111",
            7: "0000000110",
            8: "0000000110",
            9: "0001111110",
        }
        templates_17 = _build_templates(
            demo_17, connections_17, {"3702": 10}, {"3702": foes_17}
        )
        self.assertEqual(templates_17[1]["3702"]["green"][0], "G")
        self.assertTrue(
            all(templates_17[2]["3702"]["green"][index] == "G" for index in (4, 5, 7, 8))
        )
        self.assertEqual(templates_17[2]["3702"]["green"][3], "g")
        for phase in templates_17.values():
            self.assertTrue(
                all(phase["3702"]["green"][index] == "g" for index in (1, 9))
            )
            self.assertTrue(
                all(phase["3702"]["green"][index] == "r" for index in (2, 6))
            )

    def test_demo_18_and_19_templates_follow_real_foe_matrices(self):
        def build(config, intersection_id, junction_id, definitions, foes):
            connections = [
                ControlledConnection(
                    intersection_id=intersection_id,
                    junction_id=junction_id,
                    tls_id=junction_id,
                    link_index=link_index,
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
                for request_index, link_index, approach, from_edge, direction in definitions
            ]
            return _build_templates(
                config,
                connections,
                {junction_id: len(foes)},
                {junction_id: foes},
            )

        demo_18 = self.load().intersections["demo_18"]
        definitions_18 = (
            (0, 21, "southeast", "-57004", "r"),
            (1, 19, "southeast", "-57004", "s"),
            (2, 20, "southeast", "-57004", "s"),
            (3, 18, "southeast", "-57004", "l"),
            (4, 17, "southeast", "-57004", "t"),
            (5, 16, "southwest", "-57077", "r"),
            (6, 14, "southwest", "-57077", "s"),
            (7, 15, "southwest", "-57077", "s"),
            (8, 13, "southwest", "-57077", "l"),
            (9, 12, "southwest", "-57077", "t"),
            (10, 11, "northwest", "-56004", "r"),
            (11, 9, "northwest", "-56004", "s"),
            (12, 10, "northwest", "-56004", "s"),
            (13, 8, "northwest", "-56004", "l"),
            (14, 7, "northwest", "-56004", "t"),
            (15, 6, "northeast", "-56830", "r"),
            (16, 2, "northeast", "-56830", "s"),
            (17, 3, "northeast", "-56830", "s"),
            (18, 4, "northeast", "-56830", "s"),
            (19, 5, "northeast", "-56830", "s"),
            (20, 1, "northeast", "-56830", "l"),
            (21, 0, "northeast", "-56830", "t"),
        )
        foes_18 = {
            0: "0000000000000011000000",
            1: "0111111110000111000000",
            2: "0111111110000111000000",
            3: "0111110001101111000000",
            4: "0100000001100000000000",
            5: "0000000001100000000000",
            6: "1100000011100000001111",
            7: "1100000011100000001111",
            8: "0011110111100000001110",
            9: "0011110000000000001000",
            10: "0011110000000000000000",
            11: "0111110000000111111000",
            12: "0111110000000111111000",
            13: "1111110000000111000110",
            14: "0000000000000100000110",
            15: "0000000000000000000110",
            16: "0000000011111100001110",
            17: "0000000011111100001110",
            18: "0000000011111100001110",
            19: "0000000011111100001110",
            20: "0000000011100011011110",
            21: "0000000010000011000000",
        }
        templates_18 = build(
            demo_18, "demo_18", "4409", definitions_18, foes_18
        )
        self.assertTrue(
            all(
                templates_18[1]["4409"]["green"][index] == "G"
                for index in (2, 3, 4, 5, 14, 15)
            )
        )
        self.assertTrue(
            all(
                templates_18[1]["4409"]["green"][index] == "g"
                for index in (1, 13)
            )
        )
        self.assertTrue(
            all(
                templates_18[2]["4409"]["green"][index] == "G"
                for index in (9, 10, 19, 20)
            )
        )
        self.assertTrue(
            all(
                templates_18[2]["4409"]["green"][index] == "g"
                for index in (8, 18)
            )
        )
        for phase in templates_18.values():
            self.assertTrue(
                all(
                    phase["4409"]["green"][index] == "g"
                    for index in (6, 11, 16, 21)
                )
            )
            self.assertTrue(
                all(
                    phase["4409"]["green"][index] == "r"
                    for index in (0, 7, 12, 17)
                )
            )

        demo_19 = self.load().intersections["demo_19"]
        definitions_19 = (
            (0, 13, "southeast", "-46538", "r"),
            (1, 12, "southeast", "-46538", "s"),
            (2, 11, "southeast", "-46538", "l"),
            (3, 10, "southwest", "-57395", "r"),
            (4, 9, "southwest", "-57395", "s"),
            (5, 8, "southwest", "-57395", "l"),
            (6, 7, "southwest", "-57395", "t"),
            (7, 6, "northwest", "-52215", "r"),
            (8, 5, "northwest", "-52215", "s"),
            (9, 4, "northwest", "-52215", "l"),
            (10, 3, "northeast", "-55837", "r"),
            (11, 2, "northeast", "-55837", "s"),
            (12, 1, "northeast", "-55837", "l"),
            (13, 0, "northeast", "-55837", "t"),
        )
        foes_19 = {
            0: "10001000010000",
            1: "01111000110000",
            2: "01101111110000",
            3: "01000100000000",
            4: "11001100000111",
            5: "00111100000110",
            6: "00100010000100",
            7: "00100001000100",
            8: "01100000111100",
            9: "11100000110111",
            10: "00000000100010",
            11: "00001111100110",
            12: "00001100011110",
            13: "00001000010001",
        }
        templates_19 = build(demo_19, "demo_19", "891", definitions_19, foes_19)
        self.assertTrue(
            all(
                templates_19[1]["891"]["green"][index] == "G"
                for index in (2, 9)
            )
        )
        self.assertTrue(
            all(
                templates_19[1]["891"]["green"][index] == "g"
                for index in (1, 8)
            )
        )
        self.assertTrue(
            all(
                templates_19[2]["891"]["green"][index] == "G"
                for index in (5, 12)
            )
        )
        self.assertTrue(
            all(
                templates_19[2]["891"]["green"][index] == "g"
                for index in (4, 11)
            )
        )
        for phase in templates_19.values():
            self.assertTrue(
                all(
                    phase["891"]["green"][index] == "g"
                    for index in (3, 6, 10, 13)
                )
            )
            self.assertTrue(
                all(
                    phase["891"]["green"][index] == "r"
                    for index in (0, 7)
                )
            )

    def test_demo_10_and_13_templates_follow_real_foe_matrices(self):
        demo_10 = self.load().intersections["demo_10"]
        definitions = (
            (0, "south", "-50758", "r"),
            (1, "south", "-50758", "l"),
            (2, "west", "-57445", "r"),
            (3, "west", "-57445", "s"),
            (4, "east", "-50726", "s"),
            (5, "east", "-50726", "l"),
        )
        connections = [
            ControlledConnection(
                intersection_id="demo_10",
                junction_id="4162",
                tls_id="4162",
                link_index=request_index,
                approach=approach,
                movement=demo_10.topology.movement_for_direction(
                    from_edge, direction
                ),
                from_edge=from_edge,
                from_lane=0,
                to_edge=f"out_{request_index}",
                to_lane=0,
                direction=direction,
                via=f":4162_{request_index}_0",
                request_index=request_index,
            )
            for request_index, approach, from_edge, direction in definitions
        ]
        foes = {
            0: "001000",
            1: "111000",
            2: "100000",
            3: "100011",
            4: "000010",
            5: "001110",
        }
        templates = _build_templates(
            demo_10, connections, {"4162": 6}, {"4162": foes}
        )
        self.assertTrue(
            all(templates[1]["4162"]["green"][index] == "G" for index in (3, 4))
        )
        self.assertTrue(
            all(templates[2]["4162"]["green"][index] == "g" for index in (1, 5))
        )
        for phase in templates.values():
            self.assertTrue(
                all(phase["4162"]["green"][index] == "g" for index in (0, 2))
            )

        demo_13 = self.load().intersections["demo_13"]
        definitions = (
            (0, "north", "-46884", "r"),
            (1, "north", "-46884", "t"),
            (2, "east", "-56457", "r"),
            (3, "east", "-56457", "s"),
        )
        connections = [
            ControlledConnection(
                intersection_id="demo_13",
                junction_id="1204",
                tls_id="1204",
                link_index=request_index,
                approach=approach,
                movement=demo_13.topology.movement_for_direction(
                    from_edge, direction
                ),
                from_edge=from_edge,
                from_lane=0,
                to_edge=f"out_{request_index}",
                to_lane=0,
                direction=direction,
                via=f":1204_{request_index}_0",
                request_index=request_index,
            )
            for request_index, approach, from_edge, direction in definitions
        ]
        foes = {
            0: "111000",
            1: "000100",
            2: "000010",
            3: "000001",
            4: "000001",
            5: "000001",
        }
        morning = _build_templates(
            demo_13,
            connections,
            {"1204": 6},
            {"1204": foes},
            demo_13.topology.phases_for("demo_13_morning_peak"),
        )
        off_peak = _build_templates(
            demo_13,
            connections,
            {"1204": 6},
            {"1204": foes},
            demo_13.topology.phases_for("demo_13_off_peak"),
        )
        self.assertEqual(morning[1]["1204"]["green"][3], "G")
        self.assertEqual(morning[2]["1204"]["green"][1], "G")
        self.assertEqual(morning[3]["1204"]["green"][2], "G")
        self.assertEqual(morning[4]["1204"]["green"][3], "G")
        self.assertEqual(set(off_peak), {1, 2, 3})
        for phase in morning.values():
            self.assertEqual(phase["1204"]["green"][0], "g")

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

    def test_demo_4_program_templates_support_groups_and_block_uturns(self):
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
                    movement="blocked",
                    from_edge=config.topology.approaches[approach][0],
                    from_lane=0,
                    to_edge=f"out_{approach}_blocked_uturn",
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
        self.assertTrue(
            all(
                phase["3935"]["green"][index] == "r"
                for phase in morning.values()
                for index in (north_uturn, south_uturn)
            )
        )

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
        self.assertTrue(
            all(
                phase["3935"]["green"][index] == "r"
                for phase in off_peak.values()
                for index in (north_uturn, south_uturn)
            )
        )

    def test_demo_1_templates_follow_corrected_directions_and_real_foes(self):
        config = self.load().intersections["demo_1"]
        definitions = (
            (0, "south", "-56384", "r"),
            (1, "south", "-56384", "s"),
            (2, "south", "-56384", "s"),
            (3, "south", "-56384", "l"),
            (5, "west", "-manual_demo1_missing_arm", "r"),
            (6, "west", "-manual_demo1_missing_arm", "s"),
            (7, "west", "-manual_demo1_missing_arm", "l"),
            (8, "north", "-57217", "r"),
            (9, "north", "-57217", "s"),
            (10, "north", "-57217", "s"),
            (11, "north", "-57217", "l"),
            (13, "east", "-56907", "r"),
            (14, "east", "-56907", "s"),
            (15, "east", "-56907", "l"),
        )
        connections = [
            ControlledConnection(
                intersection_id="demo_1",
                junction_id="4427",
                tls_id="4427",
                link_index=link_index,
                approach=approach,
                movement=config.topology.movement_for_direction(from_edge, direction),
                from_edge=from_edge,
                from_lane=0,
                to_edge=f"out_{request_index}",
                to_lane=0,
                direction=direction,
                via=f":4427_{request_index}_0",
                request_index=request_index,
            )
            for link_index, (request_index, approach, from_edge, direction) in enumerate(
                definitions
            )
        ]
        foes = {
            0: "10000100001000000",
            1: "01111100011000000",
            2: "01111100011000000",
            3: "01100011111000000",
            4: "01000011000000000",
            5: "00000011000000000",
            6: "11000111000001111",
            7: "00101111000001110",
            8: "00100000000001000",
            9: "01100000011111000",
            10: "01100000011111000",
            11: "11100000011000111",
            12: "00000000010000110",
            13: "00000000000000110",
            14: "00000111110001110",
            15: "00000111001011110",
            16: "00000100001000001",
        }
        templates = _build_templates(
            config,
            connections,
            {"4427": len(connections)},
            {"4427": foes},
        )
        self.assertEqual(set(templates), {1, 2, 3, 4})
        for phase_templates in templates.values():
            state = phase_templates["4427"]["green"]
            right_indices = [
                item.link_index for item in connections if item.movement == "right"
            ]
            self.assertTrue(all(state[value] == "g" for value in right_indices))
        for phase_number, approaches in (
            (1, {"east", "west"}),
            (2, {"east", "west"}),
            (3, {"north", "south"}),
            (4, {"north", "south"}),
        ):
            expected_movement = "through" if phase_number in {1, 3} else "left"
            protected = [
                item.link_index
                for item in connections
                if item.approach in approaches
                and item.movement == expected_movement
            ]
            self.assertTrue(
                all(
                    templates[phase_number]["4427"]["green"][value] == "G"
                    for value in protected
                )
            )
        self.assertTrue(all(item.direction != "t" for item in connections))

    def test_demo_2_templates_cover_only_official_movements(self):
        config = self.load().intersections["demo_2"]
        definitions = (
            ("south", "s", "through"),
            ("south", "s", "through"),
            ("south", "l", "left"),
            ("west", "r", "right"),
            ("west", "l", "left"),
            ("north", "r", "right"),
            ("north", "s", "through"),
            ("north", "s", "through"),
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
        south_left = 2
        west_left = 4
        self.assertEqual(phase_one["green"][south_left], "g")
        self.assertEqual(phase_one["yellow"][south_left], "y")
        self.assertEqual(phase_two["green"][west_left], "G")
        self.assertEqual(phase_two["yellow"][west_left], "y")
        self.assertEqual(phase_one["green"], "GGggrgGG")
        self.assertEqual(phase_one["yellow"], "yyygrgyy")
        self.assertEqual(phase_two["green"], "rrrgGgrr")
        self.assertEqual(phase_two["yellow"], "rrrgygrr")
        self.assertTrue(all(item.direction != "t" for item in connections))

    def test_demo_3_templates_protect_through_and_yield_left_turns(self):
        config = self.load().intersections["demo_3"]
        definitions = (
            ("south", "r", "right"),
            ("south", "s", "through"),
            ("south", "l", "left"),
            ("west", "r", "right"),
            ("west", "s", "through"),
            ("west", "l", "left"),
            ("north", "r", "right"),
            ("north", "s", "through"),
            ("north", "l", "left"),
            ("east", "r", "right"),
            ("east", "s", "through"),
            ("east", "l", "left"),
        )
        connections = [
            ControlledConnection(
                intersection_id="demo_3",
                junction_id="citypulse_demo_3",
                tls_id="citypulse_demo_3",
                link_index=index,
                approach=approach,
                movement=movement,
                from_edge=config.topology.approaches[approach][0],
                from_lane=0,
                to_edge=f"out_{approach}_{movement}",
                to_lane=0,
                direction=direction,
                via=f":citypulse_demo_3_{index}_0",
                request_index=index,
            )
            for index, (approach, direction, movement) in enumerate(definitions)
        ]
        foes = {
            0: "000100010000",
            1: "111100110000",
            2: "110011110000",
            3: "100010000000",
            4: "100110000111",
            5: "111110000110",
            6: "010000000100",
            7: "110000111100",
            8: "110000110011",
            9: "000000100010",
            10: "000111100110",
            11: "000110111110",
        }
        templates = _build_templates(
            config,
            connections,
            {"citypulse_demo_3": len(connections)},
            {"citypulse_demo_3": foes},
        )
        phase_one = templates[1]["citypulse_demo_3"]["green"]
        phase_two = templates[2]["citypulse_demo_3"]["green"]
        phase_one_yellow = templates[1]["citypulse_demo_3"]["yellow"]
        phase_two_yellow = templates[2]["citypulse_demo_3"]["yellow"]
        self.assertTrue(all(phase_one[index] == "G" for index in (4, 10)))
        self.assertTrue(all(phase_one[index] == "g" for index in (5, 11)))
        self.assertTrue(all(phase_two[index] == "G" for index in (1, 7)))
        self.assertTrue(all(phase_two[index] == "g" for index in (2, 8)))
        for state in (phase_one, phase_two):
            self.assertTrue(all(state[index] == "g" for index in (0, 3, 6, 9)))
        self.assertEqual(phase_one, "grrgGggrrgGg")
        self.assertEqual(phase_two, "gGggrrgGggrr")
        self.assertEqual(phase_one_yellow, "grrgyygrrgyy")
        self.assertEqual(phase_two_yellow, "gyygrrgyygrr")
        self.assertTrue(all(item.direction != "t" for item in connections))

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
        blocked_turnarounds = (
            ("-56734", "-57229"),
            ("-57228", "-56736"),
        )

        def fake_run(command, check):
            self.assertTrue(check)
            tls_index = command.index("--tls.set") + 1
            self.assertEqual(command[tls_index], "317")
            input_index = command.index("--sumo-net-file") + 1
            sanitized_source = Path(command[input_index])
            self.assertNotIn('value=""', sanitized_source.read_text(encoding="utf-8"))
            connection_index = command.index("--connection-files") + 1
            connection_file = Path(command[connection_index])
            deletions = {
                (item.get("from"), item.get("to"))
                for item in ET.parse(connection_file).getroot().findall("delete")
            }
            self.assertEqual(deletions, set(blocked_turnarounds))
            shutil.copy2(sanitized_source, target)

        with patch(
            "simulation.sumo.build_tls.subprocess.run", side_effect=fake_run
        ) as run:
            applied, removed = _run_netconvert(
                "netconvert",
                source,
                target,
                ["317", "3935"],
                blocked_turnarounds,
            )
        self.assertEqual(run.call_count, 1)
        self.assertTrue(applied)
        self.assertEqual(removed, 1)
        self.assertTrue(target.is_file())
        self.assertEqual(
            list(target.parent.glob("*.netconvert-input.net.xml")),
            [],
        )
        self.assertEqual(list(target.parent.glob("*.con.xml")), [])

    def test_only_blocked_turnarounds_are_deleted(self):
        source = Path(self.temp_directory.name) / "connections.net.xml"
        source.write_text(
            """<?xml version="1.0" encoding="UTF-8"?>
<net>
  <connection from="-56384" to="-57218" dir="t"/>
  <connection from="-56732" to="-57230" dir="t"/>
  <connection from="-56734" to="-56736" dir="s"/>
  <connection from="-56734" to="-57229" dir="t"/>
  <connection from="-56907" to="-56915" dir="t"/>
  <connection from="-57217" to="-56371" dir="t"/>
  <connection from="-57228" to="-56736" dir="t"/>
  <connection from="-57229" to="-56733" dir="t"/>
</net>
""",
            encoding="utf-8",
        )
        configuration = self.load()
        self.assertEqual(
            _blocked_turnaround_deletions(
                source,
                [
                    configuration.intersections["demo_1"],
                    configuration.intersections["demo_2"],
                    configuration.intersections["demo_4"],
                ],
            ),
            (
                ("-56384", "-57218"),
                ("-56732", "-57230"),
                ("-56734", "-57229"),
                ("-56907", "-56915"),
                ("-57217", "-56371"),
                ("-57228", "-56736"),
                ("-57229", "-56733"),
            ),
        )


if __name__ == "__main__":
    unittest.main()

