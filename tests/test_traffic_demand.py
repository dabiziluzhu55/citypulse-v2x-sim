import json
import tempfile
import unittest
import xml.etree.ElementTree as ET
from pathlib import Path

from simulation.sumo.artifacts import GeneratedArtifactLayout
from simulation.sumo.build_traffic import _movement_route, build_traffic_scenarios
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


if __name__ == "__main__":
    unittest.main()
