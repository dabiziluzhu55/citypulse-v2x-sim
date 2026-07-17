import json
import tempfile
import unittest
import xml.etree.ElementTree as ET
from pathlib import Path

from simulation.sumo.artifacts import GeneratedArtifactLayout
from simulation.sumo.build_traffic import (
    _allocate_route_counts,
    _movement_route,
    build_traffic_scenarios,
)
from simulation.sumo.scenario import compile_session_scenario
from simulation.sumo.traffic import TrafficDemandError, load_traffic_demands


ROOT = Path(__file__).resolve().parents[1]
DEMANDS = ROOT / "data" / "maps" / "sumo" / "official_traffic_demands.json"


def demo_2_manifest():
    connections = []
    for approach, movement, from_edge, to_edge, direction in (
        ("southeast_branch", "left", "branch_in", "northeast_out", "l"),
        ("southeast_branch", "right", "branch_in", "southwest_out", "r"),
        ("northeast_main", "through", "northeast_in", "southwest_out", "s"),
        ("northeast_main", "left", "northeast_in", "branch_out", "l"),
        ("southwest_main", "right", "southwest_in", "branch_out", "r"),
        ("southwest_main", "through", "southwest_in", "northeast_out", "s"),
    ):
        connections.append(
            {
                "approach": approach,
                "movement": movement,
                "from_edge": from_edge,
                "from_lane": 0,
                "to_edge": to_edge,
                "to_lane": 0,
                "direction": direction,
            }
        )
    return {
        "intersections": {
            "demo_2": {
                "program_ids": [
                    "demo_2_morning_peak",
                    "demo_2_off_peak",
                    "demo_2_evening_peak",
                ],
                "connections": connections,
            }
        }
    }


def demo_9_manifest():
    routes = (
        ("east", "left", "-56619", "-56715"),
        ("east", "through", "-56619", "-56620"),
        ("east", "right", "-56619", "-56496"),
        ("east", "right", "-56619", "-56370"),
        ("west", "left", "-50339", "-56370"),
        ("west", "through", "-50339", "-50338"),
        ("west", "right", "-50339", "-56715"),
        ("north", "left", "-57214", "-50338"),
        ("north", "left", "-57214", "-56496"),
        ("north", "through", "-57214", "-56715"),
        ("north", "right", "-57214", "-56620"),
        ("south", "left", "-56369", "-56620"),
        ("south", "through", "-56369", "-56370"),
        ("south", "right", "-56369", "-50338"),
        ("northeast", "left", "-50241", "-50338"),
        ("northeast", "right", "-50241", "-56370"),
    )
    return {
        "intersections": {
            "demo_9": {
                "program_ids": [
                    "demo_9_morning_peak",
                    "demo_9_off_peak",
                    "demo_9_evening_peak",
                ],
                "connections": [
                    {
                        "approach": approach,
                        "movement": movement,
                        "from_edge": from_edge,
                        "from_lane": 0,
                        "to_edge": to_edge,
                        "to_lane": 0,
                    }
                    for approach, movement, from_edge, to_edge in routes
                ],
            }
        }
    }


class TrafficDemandTests(unittest.TestCase):
    def test_official_intervals_and_totals(self):
        demand = load_traffic_demands(DEMANDS).intersections["demo_2"]
        self.assertEqual(len(demand.periods), 3)
        self.assertEqual(
            {name: period.totals["all"] for name, period in demand.periods.items()},
            {"morning_peak": 2761, "off_peak": 1502, "evening_peak": 2299},
        )
        self.assertTrue(
            all(len(period.intervals) == 8 for period in demand.periods.values())
        )
        self.assertEqual(
            demand.approaches["north"].movements["right"],
            "left",
        )
        self.assertEqual(
            demand.approaches["south"].movements["left"],
            "right",
        )

        demo_4 = load_traffic_demands(DEMANDS).intersections["demo_4"]
        self.assertEqual(
            {name: period.totals["all"] for name, period in demo_4.periods.items()},
            {"morning_peak": 3413, "off_peak": 1999, "evening_peak": 4073},
        )
        self.assertEqual(
            demo_4.periods["morning_peak"].totals,
            {"east": 894, "west": 741, "north": 865, "south": 913, "all": 3413},
        )
        self.assertEqual(
            demo_4.periods["off_peak"].totals,
            {"east": 474, "west": 512, "north": 490, "south": 523, "all": 1999},
        )
        self.assertEqual(
            demo_4.periods["evening_peak"].totals,
            {"east": 1054, "west": 916, "north": 983, "south": 1120, "all": 4073},
        )
        self.assertTrue(
            all(len(period.intervals) == 8 for period in demo_4.periods.values())
        )
        self.assertTrue(
            all(
                mapping.movements
                == {"left": "left", "through": "through", "right": "right"}
                for mapping in demo_4.approaches.values()
            )
        )

        demo_5 = load_traffic_demands(DEMANDS).intersections["demo_5"]
        self.assertEqual(
            {name: period.totals["all"] for name, period in demo_5.periods.items()},
            {"morning_peak": 2438, "off_peak": 1988, "evening_peak": 4038},
        )
        self.assertEqual(
            demo_5.periods["morning_peak"].totals,
            {"east": 736, "west": 890, "south": 812, "all": 2438},
        )
        self.assertEqual(
            demo_5.periods["off_peak"].totals,
            {"east": 636, "west": 690, "south": 662, "all": 1988},
        )
        self.assertEqual(
            demo_5.periods["evening_peak"].totals,
            {"east": 1136, "west": 1490, "south": 1412, "all": 4038},
        )
        self.assertEqual(set(demo_5.approaches), {"east", "west", "south"})
        self.assertTrue(
            all(len(period.intervals) == 8 for period in demo_5.periods.values())
        )

        demo_6 = load_traffic_demands(DEMANDS).intersections["demo_6"]
        self.assertEqual(
            {name: period.totals["all"] for name, period in demo_6.periods.items()},
            {"morning_peak": 4455, "off_peak": 3210, "evening_peak": 4909},
        )
        self.assertEqual(
            demo_6.periods["morning_peak"].totals,
            {"east": 1588, "west": 875, "south": 964, "north": 1028, "all": 4455},
        )
        self.assertEqual(
            demo_6.periods["off_peak"].totals,
            {"east": 870, "west": 924, "south": 674, "north": 742, "all": 3210},
        )
        self.assertEqual(
            demo_6.periods["evening_peak"].totals,
            {"east": 1543, "west": 1265, "south": 1139, "north": 962, "all": 4909},
        )
        self.assertEqual(set(demo_6.approaches), {"east", "west", "south", "north"})
        self.assertTrue(
            all(len(period.intervals) == 8 for period in demo_6.periods.values())
        )

        demo_9 = load_traffic_demands(DEMANDS).intersections["demo_9"]
        self.assertEqual(
            {name: period.totals["all"] for name, period in demo_9.periods.items()},
            {"morning_peak": 3725, "off_peak": 1495, "evening_peak": 3347},
        )
        self.assertEqual(
            demo_9.periods["morning_peak"].totals,
            {
                "east": 683,
                "west": 640,
                "north": 898,
                "south": 919,
                "northeast": 585,
                "all": 3725,
            },
        )
        self.assertEqual(
            demo_9.periods["off_peak"].totals,
            {
                "east": 274,
                "west": 258,
                "north": 359,
                "south": 369,
                "northeast": 235,
                "all": 1495,
            },
        )
        self.assertEqual(
            demo_9.periods["evening_peak"].totals,
            {
                "east": 613,
                "west": 574,
                "north": 804,
                "south": 831,
                "northeast": 525,
                "all": 3347,
            },
        )
        self.assertTrue(
            all(len(period.intervals) == 8 for period in demo_9.periods.values())
        )

        expected = {
            "demo_12": {
                "morning_peak": 5013,
                "off_peak": 1906,
                "evening_peak": 4412,
            },
            "demo_14": {
                "morning_peak": 3406,
                "off_peak": 2792,
                "evening_peak": 3644,
            },
            "demo_15": {
                "morning_peak": 5878,
                "off_peak": 5279,
                "evening_peak": 6405,
            },
            "demo_10": {
                "morning_peak": 3609,
                "off_peak": 1884,
                "evening_peak": 3712,
            },
            "demo_13": {
                "morning_peak": 3087,
                "off_peak": 2750,
                "evening_peak": 3786,
            },
        }
        configuration = load_traffic_demands(DEMANDS)
        for intersection_id, totals in expected.items():
            demand = configuration.intersections[intersection_id]
            self.assertEqual(
                {name: period.totals["all"] for name, period in demand.periods.items()},
                totals,
            )
            self.assertTrue(
                all(len(period.intervals) == 8 for period in demand.periods.values())
            )
        self.assertEqual(
            configuration.intersections["demo_15"].periods["off_peak"].totals,
            {
                "east": 1083,
                "west": 942,
                "south": 1670,
                "north": 1584,
                "all": 5279,
            },
        )
        self.assertEqual(
            configuration.intersections["demo_10"].periods["evening_peak"].totals,
            {"east": 1007, "west": 1418, "south": 1287, "all": 3712},
        )

    def test_shared_sumo_approaches_require_explicit_opt_in(self):
        raw = json.loads(DEMANDS.read_text(encoding="utf-8"))
        del raw["intersections"]["demo_13"]["allow_shared_sumo_approaches"]
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "shared-without-opt-in.json"
            path.write_text(json.dumps(raw), encoding="utf-8")
            with self.assertRaisesRegex(TrafficDemandError, "must be unique"):
                load_traffic_demands(path)

    def test_largest_remainder_route_allocation_is_exact_and_deterministic(self):
        routes = ((("in", "a"), 2), (("in", "b"), 4))
        self.assertEqual(_allocate_route_counts(5, routes), (2, 3))
        tied_routes = ((("in", "a"), 1), (("in", "b"), 1))
        self.assertEqual(_allocate_route_counts(1, tied_routes), (1, 0))
        for count in range(100):
            self.assertEqual(sum(_allocate_route_counts(count, routes)), count)

    def test_declared_total_mismatch_is_rejected(self):
        raw = json.loads(DEMANDS.read_text(encoding="utf-8"))
        raw["intersections"]["demo_2"]["periods"][0]["expected_totals"]["all"] = 1
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "bad.json"
            path.write_text(json.dumps(raw), encoding="utf-8")
            with self.assertRaisesRegex(TrafficDemandError, "do not match"):
                load_traffic_demands(path)

    def test_demo_4_left_demand_does_not_select_the_uturn_connection(self):
        demand = load_traffic_demands(DEMANDS).intersections["demo_4"]
        manifest = {
            "connections": [
                {
                    "approach": "north",
                    "movement": "left",
                    "from_edge": "-57229",
                    "to_edge": "-50675",
                },
                {
                    "approach": "north",
                    "movement": "uturn",
                    "from_edge": "-57229",
                    "to_edge": "-56733",
                },
            ]
        }
        self.assertEqual(
            _movement_route(
                "demo_4",
                manifest,
                demand.approaches["north"],
                "left",
            ),
            ("-57229", "-50675"),
        )

    def test_demo_5_official_movements_select_the_expected_t_junction_routes(self):
        demand = load_traffic_demands(DEMANDS).intersections["demo_5"]
        manifest = {
            "connections": [
                {"approach": "east", "movement": "left", "from_edge": "-50182", "to_edge": "-50820"},
                {"approach": "east", "movement": "through", "from_edge": "-50182", "to_edge": "-50172"},
                {"approach": "west", "movement": "through", "from_edge": "-56392", "to_edge": "-56402"},
                {"approach": "west", "movement": "right", "from_edge": "-56392", "to_edge": "-50820"},
                {"approach": "south", "movement": "left", "from_edge": "-57586", "to_edge": "-50172"},
                {"approach": "south", "movement": "right", "from_edge": "-57586", "to_edge": "-56402"},
            ]
        }
        expected = {
            ("east", "left"): ("-50182", "-50820"),
            ("east", "through"): ("-50182", "-50172"),
            ("west", "through"): ("-56392", "-56402"),
            ("west", "right"): ("-56392", "-50820"),
            ("south", "left"): ("-57586", "-50172"),
            ("south", "right"): ("-57586", "-56402"),
        }
        actual = {
            (approach_name, movement): _movement_route(
                "demo_5", manifest, approach, movement
            )
            for approach_name, approach in demand.approaches.items()
            for movement in approach.movements
        }
        self.assertEqual(actual, expected)

    def test_demo_6_official_movements_select_the_expected_routes(self):
        demand = load_traffic_demands(DEMANDS).intersections["demo_6"]
        expected = {
            ("east", "left"): ("-56623", "-50818"),
            ("east", "through"): ("-56623", "-56614"),
            ("east", "right"): ("-56623", "-57585"),
            ("west", "left"): ("-50334", "-57585"),
            ("west", "through"): ("-50334", "-50342"),
            ("west", "right"): ("-50334", "-50818"),
            ("north", "left"): ("-50819", "-50342"),
            ("north", "through"): ("-50819", "-50818"),
            ("north", "right"): ("-50819", "-56614"),
            ("south", "left"): ("-57584", "-56614"),
            ("south", "through"): ("-57584", "-57585"),
            ("south", "right"): ("-57584", "-50342"),
        }
        manifest = {
            "connections": [
                {
                    "approach": approach,
                    "movement": movement,
                    "from_edge": route[0],
                    "to_edge": route[1],
                }
                for (approach, movement), route in expected.items()
            ]
        }
        actual = {
            (approach_name, movement): _movement_route(
                "demo_6", manifest, approach, movement
            )
            for approach_name, approach in demand.approaches.items()
            for movement in approach.movements
        }
        self.assertEqual(actual, expected)

    def test_demo_12_14_15_official_movements_select_expected_routes(self):
        expected_by_intersection = {
            "demo_12": {
                ("east", "left"): ("-50293", "-51257"),
                ("east", "through"): ("-50293", "-45669"),
                ("east", "right"): ("-50293", "-56346"),
                ("west", "left"): ("-51273", "-56346"),
                ("west", "through"): ("-51273", "-56564"),
                ("west", "right"): ("-51273", "-51257"),
                ("north", "left"): ("-51253", "-56564"),
                ("north", "through"): ("-51253", "-51257"),
                ("north", "right"): ("-51253", "-45669"),
                ("south", "left"): ("-56345", "-45669"),
                ("south", "through"): ("-56345", "-56346"),
                ("south", "right"): ("-56345", "-56564"),
            },
            "demo_14": {
                ("east", "left"): ("-46786", "-52203"),
                ("east", "right"): ("-46786", "-46528"),
                ("south", "through"): ("-46529", "-46528"),
                ("south", "right"): ("-46529", "-52560"),
                ("north", "left"): ("-52202", "-52560"),
                ("north", "through"): ("-52202", "-52203"),
            },
            "demo_15": {
                ("east", "left"): ("-46787", "-56027"),
                ("east", "through"): ("-46787", "-46786"),
                ("east", "right"): ("-46787", "-52228"),
                ("west", "left"): ("-52560", "-52228"),
                ("west", "through"): ("-52560", "-52561"),
                ("west", "right"): ("-52560", "-56027"),
                ("north", "left"): ("-56026", "-52561"),
                ("north", "through"): ("-56026", "-56027"),
                ("north", "right"): ("-56026", "-46786"),
                ("south", "left"): ("-52227", "-46786"),
                ("south", "through"): ("-52227", "-52228"),
                ("south", "right"): ("-52227", "-52561"),
            },
        }
        configuration = load_traffic_demands(DEMANDS)
        for intersection_id, expected in expected_by_intersection.items():
            demand = configuration.intersections[intersection_id]
            manifest = {
                "connections": [
                    {
                        "approach": approach,
                        "movement": movement,
                        "from_edge": route[0],
                        "to_edge": route[1],
                    }
                    for (approach, movement), route in expected.items()
                ]
            }
            actual = {
                (approach_name, movement): _movement_route(
                    intersection_id, manifest, approach, movement
                )
                for approach_name, approach in demand.approaches.items()
                for movement in approach.movements
            }
            self.assertEqual(actual, expected)

    def test_demo_10_and_13_official_movements_select_expected_routes(self):
        expected_by_intersection = {
            "demo_10": {
                ("east", "left"): ("-50726", "-57487"),
                ("east", "through"): ("-50726", "-50725"),
                ("west", "through"): ("-57445", "-57446"),
                ("west", "right"): ("-57445", "-57487"),
                ("south", "left"): ("-50758", "-50725"),
                ("south", "right"): ("-50758", "-57446"),
            },
            "demo_13": {
                ("east", "through"): ("-56457", "-56458"),
                ("east", "right"): ("-56457", "-53030"),
                ("west", "left"): ("-56457", "-53030"),
                ("west", "through"): ("-56457", "-56458"),
                ("north", "left"): ("-46884", "-53030"),
                ("north", "right"): ("-46884", "-56458"),
            },
        }
        configuration = load_traffic_demands(DEMANDS)
        for intersection_id, expected in expected_by_intersection.items():
            demand = configuration.intersections[intersection_id]
            manifest = {
                "connections": [
                    {
                        "approach": approach.sumo_approach,
                        "movement": approach.movements[movement],
                        "from_edge": route[0],
                        "to_edge": route[1],
                    }
                    for (official_approach, movement), route in expected.items()
                    for approach in (demand.approaches[official_approach],)
                ]
            }
            actual = {
                (official_approach, movement): _movement_route(
                    intersection_id, manifest, approach, movement
                )
                for official_approach, approach in demand.approaches.items()
                for movement in approach.movements
            }
            self.assertEqual(actual, expected)

    def test_generated_flows_have_exact_counts_and_routes(self):
        with tempfile.TemporaryDirectory() as directory:
            output = Path(directory)
            layout = GeneratedArtifactLayout(output)
            layout.create_base_directories()
            layout.network_file.write_text("<net/>", encoding="utf-8")
            layout.signal_programs_file.write_text(
                """<additional>
  <tlLogic id="317" programID="demo_2_morning_peak"/>
  <tlLogic id="317" programID="demo_2_off_peak"/>
  <tlLogic id="317" programID="demo_2_evening_peak"/>
</additional>
""",
                encoding="utf-8",
            )
            result = build_traffic_scenarios(
                demo_2_manifest(),
                demand_path=DEMANDS,
                output_dir=output,
                intersection_ids=["demo_2"],
            )
            self.assertEqual(len(result["scenarios"]), 3)
            self.assertEqual(result["schema_version"], 2)
            self.assertEqual(
                {
                    name: item["total_pcu"]
                    for name, item in result["scenarios"].items()
                },
                {
                    "demo_2_morning_peak": 2761,
                    "demo_2_off_peak": 1502,
                    "demo_2_evening_peak": 2299,
                },
            )

            route_path = (
                layout.traffic_scenario_dir("demo_2", "morning_peak")
                / "routes.rou.xml"
            )
            morning = result["scenarios"]["demo_2_morning_peak"]
            self.assertEqual(len(morning["flows"]), 48)
            self.assertEqual(set(morning["origins"]), {"west", "north", "south"})
            root = ET.parse(route_path).getroot()
            vehicle_type = root.find("vType")
            self.assertEqual(vehicle_type.get("emissionClass"), "HBEFA3/PC_G_EU4")
            self.assertEqual(vehicle_type.get("width"), "1.8")
            self.assertEqual(
                morning["vehicle_profile_id"], "passenger"
            )
            flows = root.findall("flow")
            self.assertEqual(len(flows), 48)
            self.assertEqual(sum(int(flow.get("number")) for flow in flows), 2761)
            self.assertEqual(min(int(flow.get("begin")) for flow in flows), 0)
            self.assertEqual(max(int(flow.get("end")) for flow in flows), 7200)
            north_right = [
                flow
                for flow in flows
                if flow.get("id", "").endswith("north_right")
            ]
            south_left = [
                flow
                for flow in flows
                if flow.get("id", "").endswith("south_left")
            ]
            self.assertEqual(len(north_right), 8)
            self.assertEqual(len(south_left), 8)
            self.assertTrue(
                all(
                    flow.find("route").get("edges") == "northeast_in branch_out"
                    for flow in north_right
                )
            )
            self.assertTrue(
                all(
                    flow.find("route").get("edges") == "southwest_in branch_out"
                    for flow in south_left
                )
            )
            config = ET.parse(
                layout.traffic_scenario_dir("demo_2", "morning_peak")
                / "simulation.sumocfg"
            ).getroot()
            self.assertEqual(config.find("time/end").get("value"), "7500")
            self.assertEqual(
                config.find("input/route-files").get("value"),
                route_path.name,
            )
            additional_name = config.find("input/additional-files").get("value")
            self.assertEqual(additional_name, "signals.add.xml")
            tls_programs = ET.parse(route_path.parent / additional_name).getroot().findall(
                "tlLogic"
            )
            self.assertEqual(len(tls_programs), 1)
            self.assertEqual(tls_programs[0].get("programID"), "demo_2_morning_peak")
            self.assertEqual(
                config.find("input/net-file").get("value"),
                "../../../network/TotalMap_20.signals.net.xml",
            )
            self.assertTrue(layout.traffic_manifest.is_file())
            self.assertEqual(list(output.glob("*.xml")), [])

            compiled = compile_session_scenario(
                "profile-session",
                ["demo_2"],
                "morning_peak",
                duration_seconds=60,
                generated_dir=output,
                session_root=output / "sessions",
            )
            self.assertEqual(
                compiled.vehicle_type_profiles,
                {"demo_2_official_passenger": "passenger"},
            )
            self.assertEqual(
                compiled.vehicle_profiles["passenger"].emission_class,
                "HBEFA3/PC_G_EU4",
            )

    def test_demo_9_generated_flows_preserve_cells_and_split_routes(self):
        with tempfile.TemporaryDirectory() as directory:
            output = Path(directory)
            layout = GeneratedArtifactLayout(output)
            layout.create_base_directories()
            layout.network_file.write_text("<net/>", encoding="utf-8")
            layout.signal_programs_file.write_text(
                """<additional>
  <tlLogic id="3864" programID="demo_9_morning_peak"/>
  <tlLogic id="3864" programID="demo_9_off_peak"/>
  <tlLogic id="3864" programID="demo_9_evening_peak"/>
</additional>
""",
                encoding="utf-8",
            )
            result = build_traffic_scenarios(
                demo_9_manifest(),
                demand_path=DEMANDS,
                output_dir=output,
                intersection_ids=["demo_9"],
            )
            demand = load_traffic_demands(DEMANDS).intersections["demo_9"]
            for period_id, period in demand.periods.items():
                scenario = result["scenarios"][f"demo_9_{period_id}"]
                self.assertEqual(scenario["total_pcu"], period.totals["all"])
                self.assertEqual(scenario["flow_count"], 128)
                self.assertEqual(
                    sum(flow["number"] for flow in scenario["flows"]),
                    period.totals["all"],
                )
                for interval_index, interval in enumerate(period.intervals):
                    begin = interval.start - period.start
                    for approach, movements in interval.volumes.items():
                        for movement, expected in movements.items():
                            actual = sum(
                                flow["number"]
                                for flow in scenario["flows"]
                                if flow["begin"] == begin
                                and flow["official_approach"] == approach
                                and flow["official_movement"] == movement
                            )
                            self.assertEqual(
                                actual,
                                expected,
                                f"{period_id}/{interval_index}/{approach}/{movement}",
                            )

            morning = result["scenarios"]["demo_9_morning_peak"]
            east_right = [
                flow
                for flow in morning["flows"]
                if flow["begin"] == 0
                and flow["official_approach"] == "east"
                and flow["official_movement"] == "right"
            ]
            north_left = [
                flow
                for flow in morning["flows"]
                if flow["begin"] == 0
                and flow["official_approach"] == "north"
                and flow["official_movement"] == "left"
            ]
            self.assertEqual(
                {flow["to_edge"]: flow["number"] for flow in east_right},
                {"-56370": 37, "-56496": 19},
            )
            self.assertEqual(
                {flow["to_edge"]: flow["number"] for flow in north_left},
                {"-50338": 24, "-56496": 18},
            )

    def test_demo_13_shared_approach_generates_distinct_official_flows(self):
        with tempfile.TemporaryDirectory() as directory:
            output = Path(directory)
            layout = GeneratedArtifactLayout(output)
            layout.create_base_directories()
            layout.network_file.write_text("<net/>", encoding="utf-8")
            layout.signal_programs_file.write_text(
                """<additional>
  <tlLogic id="1204" programID="demo_13_morning_peak"/>
  <tlLogic id="1204" programID="demo_13_off_peak"/>
  <tlLogic id="1204" programID="demo_13_evening_peak"/>
</additional>
""",
                encoding="utf-8",
            )
            manifest = {
                "intersections": {
                    "demo_13": {
                        "program_ids": [
                            "demo_13_morning_peak",
                            "demo_13_off_peak",
                            "demo_13_evening_peak",
                        ],
                        "connections": [
                            {
                                "approach": "east",
                                "movement": "through",
                                "from_edge": "-56457",
                                "from_lane": 0,
                                "to_edge": "-56458",
                                "to_lane": 0,
                            },
                            {
                                "approach": "east",
                                "movement": "left",
                                "from_edge": "-56457",
                                "from_lane": 0,
                                "to_edge": "-53030",
                                "to_lane": 0,
                            },
                            {
                                "approach": "north",
                                "movement": "uturn",
                                "from_edge": "-46884",
                                "from_lane": 0,
                                "to_edge": "-53030",
                                "to_lane": 0,
                            },
                            {
                                "approach": "north",
                                "movement": "right",
                                "from_edge": "-46884",
                                "from_lane": 0,
                                "to_edge": "-56458",
                                "to_lane": 0,
                            },
                        ],
                    }
                }
            }
            result = build_traffic_scenarios(
                manifest,
                demand_path=DEMANDS,
                output_dir=output,
                intersection_ids=["demo_13"],
            )
            morning = result["scenarios"]["demo_13_morning_peak"]
            self.assertEqual(morning["total_pcu"], 3087)
            self.assertEqual(morning["flow_count"], 48)
            self.assertEqual(
                morning["origins"]["east"]["lane_ids"], ["-56457_0"]
            )
            self.assertEqual(
                morning["origins"]["west"]["lane_ids"], ["-56457_0"]
            )
            east_right = [
                flow
                for flow in morning["flows"]
                if flow["official_approach"] == "east"
                and flow["official_movement"] == "right"
            ]
            west_left = [
                flow
                for flow in morning["flows"]
                if flow["official_approach"] == "west"
                and flow["official_movement"] == "left"
            ]
            self.assertEqual(len(east_right), 8)
            self.assertEqual(len(west_left), 8)
            self.assertTrue(
                all(flow["to_edge"] == "-53030" for flow in east_right + west_left)
            )


if __name__ == "__main__":
    unittest.main()
