import json
import tempfile
import unittest
import xml.etree.ElementTree as ET
from pathlib import Path

from simulation.sumo.build_traffic import build_traffic_scenarios
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

    def test_declared_total_mismatch_is_rejected(self):
        raw = json.loads(DEMANDS.read_text(encoding="utf-8"))
        raw["intersections"]["demo_2"]["periods"][0]["expected_totals"]["all"] = 1
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "bad.json"
            path.write_text(json.dumps(raw), encoding="utf-8")
            with self.assertRaisesRegex(TrafficDemandError, "do not match"):
                load_traffic_demands(path)

    def test_generated_flows_have_exact_counts_and_routes(self):
        with tempfile.TemporaryDirectory() as directory:
            output = Path(directory)
            (output / "official_tls.add.xml").write_text(
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

            route_path = output / "official_traffic_demo_2_morning_peak.rou.xml"
            root = ET.parse(route_path).getroot()
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
                output / "official_traffic_demo_2_morning_peak.sumocfg"
            ).getroot()
            self.assertEqual(config.find("time/end").get("value"), "7500")
            self.assertEqual(
                config.find("input/route-files").get("value"),
                route_path.name,
            )
            additional_name = config.find("input/additional-files").get("value")
            self.assertEqual(additional_name, "official_tls_demo_2_morning_peak.add.xml")
            tls_programs = ET.parse(output / additional_name).getroot().findall("tlLogic")
            self.assertEqual(len(tls_programs), 1)
            self.assertEqual(tls_programs[0].get("programID"), "demo_2_morning_peak")


if __name__ == "__main__":
    unittest.main()
