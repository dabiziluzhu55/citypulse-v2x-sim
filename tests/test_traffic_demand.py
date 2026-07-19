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


def demo_1_manifest():
    routes = (
        ("east", "left", "-56907", "-57218"),
        ("east", "through", "-56907", "manual_demo1_missing_arm"),
        ("east", "right", "-56907", "-56371"),
        ("west", "left", "-manual_demo1_missing_arm", "-56371"),
        ("west", "through", "-manual_demo1_missing_arm", "-56915"),
        ("west", "right", "-manual_demo1_missing_arm", "-57218"),
        ("north", "left", "-57217", "-56915"),
        ("north", "through", "-57217", "-57218"),
        ("north", "right", "-57217", "manual_demo1_missing_arm"),
        ("south", "left", "-56384", "manual_demo1_missing_arm"),
        ("south", "through", "-56384", "-56371"),
        ("south", "right", "-56384", "-56915"),
    )
    return {
        "intersections": {
            "demo_1": {
                "program_ids": [
                    "demo_1_morning_peak",
                    "demo_1_off_peak",
                    "demo_1_evening_peak",
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


def demo_2_manifest():
    connections = []
    for approach, movement, from_edge, to_edge, direction in (
        ("west", "left", "west_in", "south_out", "l"),
        ("west", "right", "west_in", "north_out", "r"),
        ("north", "through", "north_in", "north_out", "s"),
        ("north", "right", "north_in", "west_out", "r"),
        ("south", "left", "south_in", "west_out", "l"),
        ("south", "through", "south_in", "south_out", "s"),
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


def demo_3_manifest():
    routes = (
        ("east", "left", "-57582", "-46791.1195"),
        ("east", "through", "-57582", "-57582.1757"),
        ("east", "right", "-57582", "46791"),
        ("west", "left", "-50816", "46791"),
        ("west", "through", "-50816", "57582"),
        ("west", "right", "-50816", "-46791.1195"),
        ("north", "left", "-46791", "57582"),
        ("north", "through", "-46791", "-46791.1195"),
        ("north", "right", "-46791", "-57582.1757"),
        ("south", "left", "-52565", "-57582.1757"),
        ("south", "through", "-52565", "46791"),
        ("south", "right", "-52565", "57582"),
    )
    return {
        "intersections": {
            "demo_3": {
                "program_ids": [
                    "demo_3_morning_peak",
                    "demo_3_off_peak",
                    "demo_3_evening_peak",
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
        demo_1 = load_traffic_demands(DEMANDS).intersections["demo_1"]
        self.assertEqual(
            {name: period.totals["all"] for name, period in demo_1.periods.items()},
            {"morning_peak": 3998, "off_peak": 2525, "evening_peak": 4592},
        )
        self.assertEqual(
            demo_1.periods["morning_peak"].totals,
            {"east": 1127, "west": 894, "north": 923, "south": 1054, "all": 3998},
        )
        self.assertEqual(
            demo_1.periods["off_peak"].totals,
            {"east": 549, "west": 693, "north": 645, "south": 638, "all": 2525},
        )
        self.assertEqual(
            demo_1.periods["evening_peak"].totals,
            {"east": 1019, "west": 1354, "north": 1278, "south": 941, "all": 4592},
        )
        self.assertTrue(
            all(len(period.intervals) == 8 for period in demo_1.periods.values())
        )
        self.assertTrue(
            all(
                set(approach.movements) == {"left", "through", "right"}
                for approach in demo_1.approaches.values()
            )
        )
        expected_movement_totals = {
            "morning_peak": {
                "east": {"left": 428, "through": 372, "right": 327},
                "west": {"left": 359, "through": 231, "right": 304},
                "north": {"left": 222, "through": 468, "right": 233},
                "south": {"left": 453, "through": 316, "right": 285},
            },
            "off_peak": {
                "east": {"left": 230, "through": 182, "right": 137},
                "west": {"left": 325, "through": 201, "right": 167},
                "north": {"left": 90, "through": 414, "right": 141},
                "south": {"left": 338, "through": 134, "right": 166},
            },
            "evening_peak": {
                "east": {"left": 275, "through": 397, "right": 347},
                "west": {"left": 510, "through": 599, "right": 245},
                "north": {"left": 333, "through": 664, "right": 281},
                "south": {"left": 348, "through": 329, "right": 264},
            },
        }
        actual_movement_totals = {
            period_id: {
                approach_name: {
                    movement: sum(
                        interval.volumes[approach_name][movement]
                        for interval in period.intervals
                    )
                    for movement in approach.movements
                }
                for approach_name, approach in demo_1.approaches.items()
            }
            for period_id, period in demo_1.periods.items()
        }
        self.assertEqual(actual_movement_totals, expected_movement_totals)

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
            "right",
        )
        self.assertEqual(
            demand.approaches["south"].movements["left"],
            "left",
        )

        demo_3 = load_traffic_demands(DEMANDS).intersections["demo_3"]
        self.assertEqual(
            {name: period.totals["all"] for name, period in demo_3.periods.items()},
            {"morning_peak": 3134, "off_peak": 1257, "evening_peak": 2824},
        )
        self.assertEqual(
            demo_3.periods["morning_peak"].totals,
            {"east": 818, "west": 907, "north": 718, "south": 691, "all": 3134},
        )
        self.assertEqual(
            demo_3.periods["off_peak"].totals,
            {"east": 326, "west": 360, "north": 296, "south": 275, "all": 1257},
        )
        self.assertEqual(
            demo_3.periods["evening_peak"].totals,
            {"east": 731, "west": 815, "north": 651, "south": 627, "all": 2824},
        )
        self.assertTrue(
            all(len(period.intervals) == 8 for period in demo_3.periods.values())
        )
        self.assertTrue(
            all(
                set(approach.movements) == {"left", "through", "right"}
                for approach in demo_3.approaches.values()
            )
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

        demo_7 = load_traffic_demands(DEMANDS).intersections["demo_7"]
        self.assertEqual(
            {name: period.totals["all"] for name, period in demo_7.periods.items()},
            {"morning_peak": 2950, "off_peak": 1990, "evening_peak": 4140},
        )
        self.assertEqual(
            demo_7.periods["morning_peak"].totals,
            {"east": 958, "west": 956, "south": 1036, "all": 2950},
        )
        self.assertEqual(
            demo_7.periods["off_peak"].totals,
            {"east": 708, "west": 656, "south": 626, "all": 1990},
        )
        self.assertEqual(
            demo_7.periods["evening_peak"].totals,
            {"east": 1358, "west": 1256, "south": 1526, "all": 4140},
        )
        self.assertEqual(set(demo_7.approaches), {"east", "west", "south"})
        self.assertTrue(
            all(len(period.intervals) == 8 for period in demo_7.periods.values())
        )

        demo_8 = load_traffic_demands(DEMANDS).intersections["demo_8"]
        self.assertEqual(
            {name: period.totals["all"] for name, period in demo_8.periods.items()},
            {"morning_peak": 5090, "off_peak": 3636, "evening_peak": 5454},
        )
        self.assertEqual(
            demo_8.periods["morning_peak"].totals,
            {"east": 1229, "west": 1296, "north": 1330, "south": 1235, "all": 5090},
        )
        self.assertEqual(
            demo_8.periods["off_peak"].totals,
            {"east": 878, "west": 926, "north": 950, "south": 882, "all": 3636},
        )
        self.assertEqual(
            demo_8.periods["evening_peak"].totals,
            {"east": 1317, "west": 1389, "north": 1425, "south": 1323, "all": 5454},
        )
        self.assertTrue(
            all(len(period.intervals) == 8 for period in demo_8.periods.values())
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

        demo_11 = load_traffic_demands(DEMANDS).intersections["demo_11"]
        self.assertEqual(
            {name: period.totals["all"] for name, period in demo_11.periods.items()},
            {"morning_peak": 5962, "off_peak": 5175, "evening_peak": 6881},
        )
        self.assertEqual(
            demo_11.periods["morning_peak"].totals,
            {"east": 1587, "west": 1689, "north": 1215, "south": 1471, "all": 5962},
        )
        self.assertEqual(
            demo_11.periods["off_peak"].totals,
            {"east": 1459, "west": 1464, "north": 1126, "south": 1126, "all": 5175},
        )
        self.assertEqual(
            demo_11.periods["evening_peak"].totals,
            {"east": 1920, "west": 2021, "north": 1804, "south": 1136, "all": 6881},
        )
        self.assertTrue(
            all(len(period.intervals) == 8 for period in demo_11.periods.values())
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
            "demo_16": {
                "morning_peak": 4992,
                "off_peak": 4664,
                "evening_peak": 4792,
            },
            "demo_17": {
                "morning_peak": 2939,
                "off_peak": 2012,
                "evening_peak": 3912,
            },
            "demo_18": {
                "morning_peak": 4953,
                "off_peak": 5474,
                "evening_peak": 6598,
            },
            "demo_19": {
                "morning_peak": 3313,
                "off_peak": 2278,
                "evening_peak": 4873,
            },
            "demo_20": {
                "morning_peak": 2633,
                "off_peak": 1041,
                "evening_peak": 2361,
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
        demo_16 = configuration.intersections["demo_16"]
        self.assertEqual(
            demo_16.periods["morning_peak"].totals,
            {"east": 1539, "west": 1302, "north": 1154, "south": 997, "all": 4992},
        )
        self.assertEqual(
            demo_16.periods["off_peak"].totals,
            {"east": 1168, "west": 1102, "north": 924, "south": 1470, "all": 4664},
        )
        self.assertEqual(
            demo_16.periods["evening_peak"].totals,
            {"east": 1398, "west": 1224, "north": 955, "south": 1215, "all": 4792},
        )
        self.assertEqual(
            demo_16.periods["off_peak"].intervals[-1].volumes["south"]["left"],
            551,
        )
        demo_17 = configuration.intersections["demo_17"]
        self.assertEqual(
            demo_17.periods["morning_peak"].totals,
            {"east": 1044, "north": 893, "south": 1002, "all": 2939},
        )
        self.assertEqual(
            demo_17.periods["off_peak"].totals,
            {"east": 632, "north": 736, "south": 644, "all": 2012},
        )
        self.assertEqual(
            demo_17.periods["evening_peak"].totals,
            {"east": 1358, "north": 1194, "south": 1360, "all": 3912},
        )
        demo_18 = configuration.intersections["demo_18"]
        self.assertEqual(
            demo_18.periods["morning_peak"].totals,
            {"southwest": 1566, "southeast": 988, "northwest": 1024, "northeast": 1375, "all": 4953},
        )
        self.assertEqual(
            demo_18.periods["off_peak"].totals,
            {"southwest": 1382, "southeast": 1254, "northwest": 1170, "northeast": 1668, "all": 5474},
        )
        self.assertEqual(
            demo_18.periods["evening_peak"].totals,
            {"southwest": 1825, "southeast": 1573, "northwest": 1185, "northeast": 2015, "all": 6598},
        )
        demo_19 = configuration.intersections["demo_19"]
        self.assertEqual(
            demo_19.periods["morning_peak"].totals,
            {"southwest": 854, "southeast": 762, "northwest": 631, "northeast": 1066, "all": 3313},
        )
        self.assertEqual(
            demo_19.periods["off_peak"].totals,
            {"southwest": 410, "southeast": 566, "northwest": 384, "northeast": 918, "all": 2278},
        )
        self.assertEqual(
            demo_19.periods["evening_peak"].totals,
            {"southwest": 1098, "southeast": 1125, "northwest": 1253, "northeast": 1397, "all": 4873},
        )
        northeast_evening = demo_19.periods["evening_peak"]
        self.assertEqual(
            sum(
                interval.volumes["northeast"][movement]
                for interval in northeast_evening.intervals
                for movement in ("left", "through", "right")
            ),
            1397,
        )
        demo_20 = configuration.intersections["demo_20"]
        self.assertEqual(
            demo_20.periods["morning_peak"].totals,
            {"east": 479, "west": 353, "north": 705, "south": 1096, "all": 2633},
        )
        self.assertEqual(
            demo_20.periods["off_peak"].totals,
            {"east": 186, "west": 140, "north": 277, "south": 438, "all": 1041},
        )
        self.assertEqual(
            demo_20.periods["evening_peak"].totals,
            {"east": 434, "west": 308, "north": 628, "south": 991, "all": 2361},
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

    def test_demo_3_official_movements_select_expected_routes_without_uturns(self):
        demand = load_traffic_demands(DEMANDS).intersections["demo_3"]
        manifest = demo_3_manifest()["intersections"]["demo_3"]
        expected = {
            (item["approach"], item["movement"]):
                (item["from_edge"], item["to_edge"])
            for item in manifest["connections"]
        }
        actual = {
            (approach_name, movement): _movement_route(
                "demo_3", manifest, approach, movement
            )
            for approach_name, approach in demand.approaches.items()
            for movement in approach.movements
        }
        self.assertEqual(actual, expected)
        self.assertNotIn("uturn", {movement for _, movement in actual})

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

    def test_demo_7_official_movements_select_the_expected_routes(self):
        demand = load_traffic_demands(DEMANDS).intersections["demo_7"]
        expected = {
            ("east", "left"): ("-51953", "-46216"),
            ("east", "right"): ("-51953", "-51872"),
            ("west", "left"): ("-46217", "-46293"),
            ("west", "through"): ("-46217", "-46216"),
            ("south", "through"): ("-51871", "-51872"),
            ("south", "right"): ("-51871", "-46293"),
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
                "demo_7", manifest, approach, movement
            )
            for approach_name, approach in demand.approaches.items()
            for movement in approach.movements
        }
        self.assertEqual(actual, expected)

    def test_demo_8_and_11_official_movements_select_expected_routes(self):
        expected_by_intersection = {
            "demo_8": {
                ("east", "left"): ("-54807", "-57113"),
                ("east", "through"): ("-54807", "-54810"),
                ("east", "right"): ("-54807", "-57125"),
                ("west", "left"): ("-57234", "-57125"),
                ("west", "through"): ("-57234", "-57236"),
                ("west", "right"): ("-57234", "-57113"),
                ("north", "left"): ("-57112", "-57236"),
                ("north", "through"): ("-57112", "-57113"),
                ("north", "right"): ("-57112", "-54810"),
                ("south", "left"): ("-57109", "-54810"),
                ("south", "through"): ("-57109", "-57125"),
                ("south", "right"): ("-57109", "-57236"),
            },
            "demo_11": {
                ("east", "left"): ("-57303", "-51252"),
                ("east", "through"): ("-57303", "-57304"),
                ("east", "right"): ("-57303", "-57056"),
                ("west", "left"): ("-51264", "-57056"),
                ("west", "through"): ("-51264", "-51265"),
                ("west", "right"): ("-51264", "-51252"),
                ("north", "left"): ("-57053", "-51265"),
                ("north", "through"): ("-57053", "-51252"),
                ("north", "right"): ("-57053", "-57304"),
                ("south", "left"): ("-56346", "-57304"),
                ("south", "through"): ("-56346", "-57056"),
                ("south", "right"): ("-56346", "-51265"),
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
                ("east", "left"): ("-46785", "-52217"),
                ("east", "right"): ("-46785", "-46538"),
                ("south", "through"): ("-46539", "-46538"),
                ("south", "right"): ("-46539", "-52559"),
                ("north", "left"): ("-52216", "-52559"),
                ("north", "through"): ("-52216", "-52217"),
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

    def test_demo_16_and_17_official_movements_select_expected_routes(self):
        expected_by_intersection = {
            "demo_16": {
                ("east", "left"): ("-55547", "-57803"),
                ("east", "through"): ("-55547", "-55548"),
                ("east", "right"): ("-55547", "-50942"),
                ("west", "left"): ("-50930", "-50942"),
                ("west", "through"): ("-50930", "-49533"),
                ("west", "right"): ("-50930", "-57803"),
                ("north", "left"): ("-57802", "-49533"),
                ("north", "through"): ("-57802", "-57803"),
                ("north", "right"): ("-57802", "-55548"),
                ("south", "left"): ("-50943", "-55548"),
                ("south", "through"): ("-50943", "-50942"),
                ("south", "right"): ("-50943", "-49533"),
            },
            "demo_17": {
                ("east", "left"): ("-56184", "-57321"),
                ("east", "right"): ("-56184", "-57330"),
                ("north", "left"): ("-57320", "-50050"),
                ("north", "through"): ("-57320", "-57321"),
                ("south", "through"): ("-57329", "-57330"),
                ("south", "right"): ("-57329", "-50050"),
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

    def test_demo_18_19_and_20_official_movements_select_expected_routes(self):
        expected_by_intersection = {
            "demo_18": {
                ("northeast", "left"): ("-56830", "-56009"),
                ("northeast", "through"): ("-56830", "-56831"),
                ("northeast", "right"): ("-56830", "-57005"),
                ("southwest", "left"): ("-57077", "-57005"),
                ("southwest", "through"): ("-57077", "-57058"),
                ("southwest", "right"): ("-57077", "-56009"),
                ("northwest", "left"): ("-56004", "-57058"),
                ("northwest", "through"): ("-56004", "-56009"),
                ("northwest", "right"): ("-56004", "-56831"),
                ("southeast", "left"): ("-57004", "-56831"),
                ("southeast", "through"): ("-57004", "-57005"),
                ("southeast", "right"): ("-57004", "-57058"),
            },
            "demo_19": {
                ("northeast", "left"): ("-55837", "-52216"),
                ("northeast", "through"): ("-55837", "-55838"),
                ("northeast", "right"): ("-55837", "-46537"),
                ("southwest", "left"): ("-57395", "-46537"),
                ("southwest", "through"): ("-57395", "-57396"),
                ("southwest", "right"): ("-57395", "-52216"),
                ("northwest", "left"): ("-52215", "-57396"),
                ("northwest", "through"): ("-52215", "-52216"),
                ("northwest", "right"): ("-52215", "-55838"),
                ("southeast", "left"): ("-46538", "-55838"),
                ("southeast", "through"): ("-46538", "-46537"),
                ("southeast", "right"): ("-46538", "-57396"),
            },
            "demo_20": {
                ("east", "left"): ("-56836", "-57314"),
                ("east", "through"): ("-56836", "-56837"),
                ("east", "right"): ("-56836", "-56093"),
                ("west", "left"): ("-57067", "-56093"),
                ("west", "through"): ("-57067", "-57073"),
                ("west", "right"): ("-57067", "-57314"),
                ("north", "left"): ("-49964", "-57073"),
                ("north", "through"): ("-49964", "-57314"),
                ("north", "right"): ("-49964", "-56837"),
                ("south", "left"): ("-57333", "-56837"),
                ("south", "through"): ("-57333", "-56093"),
                ("south", "right"): ("-57333", "-57073"),
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

    def test_demo_1_generated_flows_have_exact_counts_and_routes(self):
        with tempfile.TemporaryDirectory() as directory:
            output = Path(directory)
            layout = GeneratedArtifactLayout(output)
            layout.create_base_directories()
            layout.network_file.write_text("<net/>", encoding="utf-8")
            layout.signal_programs_file.write_text(
                """<additional>
  <tlLogic id="4427" programID="demo_1_morning_peak"/>
  <tlLogic id="4427" programID="demo_1_off_peak"/>
  <tlLogic id="4427" programID="demo_1_evening_peak"/>
</additional>
""",
                encoding="utf-8",
            )
            result = build_traffic_scenarios(
                demo_1_manifest(),
                demand_path=DEMANDS,
                output_dir=output,
                intersection_ids=["demo_1"],
            )
            self.assertEqual(
                {
                    name: item["total_pcu"]
                    for name, item in result["scenarios"].items()
                },
                {
                    "demo_1_morning_peak": 3998,
                    "demo_1_off_peak": 2525,
                    "demo_1_evening_peak": 4592,
                },
            )
            morning = result["scenarios"]["demo_1_morning_peak"]
            self.assertEqual(len(morning["flows"]), 96)
            self.assertEqual(set(morning["origins"]), {"east", "west", "north", "south"})
            self.assertNotIn(
                "uturn", {item["official_movement"] for item in morning["flows"]}
            )
            first_interval_routes = {
                (item["official_approach"], item["official_movement"]): (
                    item["from_edge"],
                    item["to_edge"],
                )
                for item in morning["flows"]
                if item["begin"] == 0
            }
            self.assertEqual(
                first_interval_routes,
                {
                    (item["approach"], item["movement"]): (
                        item["from_edge"], item["to_edge"]
                    )
                    for item in demo_1_manifest()["intersections"]["demo_1"]["connections"]
                },
            )
            route_path = (
                layout.traffic_scenario_dir("demo_1", "morning_peak")
                / "routes.rou.xml"
            )
            flows = ET.parse(route_path).getroot().findall("flow")
            self.assertEqual(len(flows), 96)
            self.assertEqual(sum(int(flow.get("number")) for flow in flows), 3998)

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
                    flow.find("route").get("edges") == "north_in west_out"
                    for flow in north_right
                )
            )
            self.assertTrue(
                all(
                    flow.find("route").get("edges") == "south_in west_out"
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

    def test_demo_3_generated_flows_match_every_official_cell_without_uturns(self):
        with tempfile.TemporaryDirectory() as directory:
            output = Path(directory)
            layout = GeneratedArtifactLayout(output)
            layout.create_base_directories()
            layout.network_file.write_text("<net/>", encoding="utf-8")
            layout.signal_programs_file.write_text(
                """<additional>
  <tlLogic id="citypulse_demo_3" programID="demo_3_morning_peak"/>
  <tlLogic id="citypulse_demo_3" programID="demo_3_off_peak"/>
  <tlLogic id="citypulse_demo_3" programID="demo_3_evening_peak"/>
</additional>
""",
                encoding="utf-8",
            )
            result = build_traffic_scenarios(
                demo_3_manifest(),
                demand_path=DEMANDS,
                output_dir=output,
                intersection_ids=["demo_3"],
            )
            self.assertEqual(
                {
                    name: scenario["total_pcu"]
                    for name, scenario in result["scenarios"].items()
                },
                {
                    "demo_3_morning_peak": 3134,
                    "demo_3_off_peak": 1257,
                    "demo_3_evening_peak": 2824,
                },
            )
            for scenario in result["scenarios"].values():
                self.assertEqual(scenario["flow_count"], 96)
                self.assertNotIn(
                    "uturn",
                    {flow["official_movement"] for flow in scenario["flows"]},
                )
            route_path = (
                layout.traffic_scenario_dir("demo_3", "morning_peak")
                / "routes.rou.xml"
            )
            flows = ET.parse(route_path).getroot().findall("flow")
            self.assertEqual(len(flows), 96)
            self.assertEqual(sum(int(flow.get("number")) for flow in flows), 3134)

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
