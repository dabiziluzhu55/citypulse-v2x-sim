import json
import tempfile
import unittest
import xml.etree.ElementTree as ET
from pathlib import Path

from simulation.sumo.artifacts import GeneratedArtifactLayout
from simulation.sumo.scenario import ScenarioCompilationError, compile_session_scenario


def write_fixture(root: Path):
    generated = root / "generated"
    layout = GeneratedArtifactLayout(generated)
    layout.create_base_directories()
    layout.network_file.write_text("<net/>", encoding="utf-8")
    scenario_dir = layout.traffic_scenario_dir("morning_peak")
    scenario_dir.mkdir(parents=True)
    route_file = scenario_dir / "routes.rou.xml"
    additional_file = scenario_dir / "signals.add.xml"
    route_file.write_text(
        """<routes>
  <vType id="demo_car" vClass="passenger"/>
  <flow id="flow_west_0" type="demo_car" begin="0" end="900" number="10"><route edges="in out"/></flow>
  <flow id="flow_north_0" type="demo_car" begin="0" end="900" number="20"><route edges="in2 out2"/></flow>
  <flow id="flow_west_1" type="demo_car" begin="900" end="1800" number="10"><route edges="in out"/></flow>
  <flow id="flow_north_1" type="demo_car" begin="900" end="1800" number="20"><route edges="in2 out2"/></flow>
</routes>""",
        encoding="utf-8",
    )
    additional_file.write_text(
        '<additional><tlLogic id="317" programID="demo_2_morning_peak"/></additional>',
        encoding="utf-8",
    )
    flows = [
        {"flow_id": "flow_west_0", "source_intersection_id": "demo_2", "source_official_approach": "west", "begin": 0, "end": 900, "number": 10},
        {"flow_id": "flow_north_0", "source_intersection_id": "demo_2", "source_official_approach": "north", "begin": 0, "end": 900, "number": 20},
        {"flow_id": "flow_west_1", "source_intersection_id": "demo_2", "source_official_approach": "west", "begin": 900, "end": 1800, "number": 10},
        {"flow_id": "flow_north_1", "source_intersection_id": "demo_2", "source_official_approach": "north", "begin": 900, "end": 1800, "number": 20},
    ]
    manifest = {
        "schema_version": 3,
        "vehicle_profile_schema_version": 1,
        "vehicle_profiles": {
            "passenger": json.loads(
                (
                    Path(__file__).resolve().parents[1]
                    / "data"
                    / "maps"
                    / "sumo"
                    / "vehicle_profiles.json"
                ).read_text(encoding="utf-8")
            )["profiles"]["passenger"]
        },
        "intersection_ids": ["demo_2"],
        "origins": {
            "demo_2": {
                "west": {"label": "西进口", "sumo_approach": "west", "lane_ids": ["in_0"]},
                "north": {"label": "北进口", "sumo_approach": "north", "lane_ids": ["in2_0"]},
            }
        },
        "scenarios": {
            "global_morning_peak": {
                "intersection_ids": ["demo_2"],
                "period_id": "morning_peak",
                "official_time_range": {"start": "07:00:00", "end": "07:30:00"},
                "demand_duration": 1800,
                "route_file": layout.relative(route_file),
                "additional_file": layout.relative(additional_file),
                "vehicle_profile_id": "passenger",
                "sumo_vehicle_type_id": "demo_car",
                "flows": flows,
                "origins": {
                    "west": {"label": "西进口", "sumo_approach": "west", "lane_ids": ["in_0"]},
                    "north": {"label": "北进口", "sumo_approach": "north", "lane_ids": ["in2_0"]},
                },
            }
        },
    }
    layout.traffic_manifest.write_text(
        json.dumps(manifest), encoding="utf-8"
    )
    return generated


class SessionScenarioTests(unittest.TestCase):
    def test_filters_origin_clips_window_and_scales_deterministically(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            generated = write_fixture(root)
            compiled = compile_session_scenario(
                "session-test",
                ["demo_2"],
                "morning_peak",
                origins={"demo_2": ["west"]},
                window_start_seconds=450,
                duration_seconds=900,
                flow_multiplier=1.5,
                generated_dir=generated,
                session_root=root / "sessions",
            )
            route_root = ET.parse(compiled.route_file).getroot()
            self.assertEqual([item.tag for item in route_root][:2], ["vType", "vType"])
            flows = route_root.findall("flow")
            self.assertEqual([flow.get("id") for flow in flows], ["flow_west_0", "flow_west_1"])
            self.assertEqual(sum(int(flow.get("number")) for flow in flows), 15)
            self.assertEqual([(flow.get("begin"), flow.get("end")) for flow in flows], [("0", "450"), ("450", "900")])
            self.assertEqual(compiled.planned_vehicle_count, 15)
            self.assertFalse(compiled.official_complete_demand)
            self.assertEqual(compiled.official_start_seconds, 7 * 3600)
            self.assertEqual(compiled.vehicle_type_profiles, {"demo_car": "passenger"})
            config = ET.parse(compiled.sumocfg).getroot()
            self.assertEqual(config.find("time/end").get("value"), "900")

    def test_rejects_unknown_origin_and_invalid_multiplier(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            generated = write_fixture(root)
            with self.assertRaisesRegex(ScenarioCompilationError, "unknown origins"):
                compile_session_scenario(
                    "unknown-origin",
                    ["demo_2"],
                    "morning_peak",
                    origins={"demo_2": ["east"]},
                    generated_dir=generated,
                    session_root=root / "sessions",
                )
            with self.assertRaisesRegex(ScenarioCompilationError, "flow_multiplier"):
                compile_session_scenario(
                    "bad-scale",
                    ["demo_2"],
                    "morning_peak",
                    flow_multiplier=6.0,
                    generated_dir=generated,
                    session_root=root / "sessions",
                )

    def test_control_subset_keeps_other_global_sources(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            generated = write_fixture(root)
            layout = GeneratedArtifactLayout(generated)
            route_path = layout.traffic_scenario_dir("morning_peak") / "routes.rou.xml"
            route_root = ET.parse(route_path).getroot()
            extra = ET.SubElement(
                route_root,
                "flow",
                {
                    "id": "flow_other_intersection",
                    "type": "demo_car",
                    "begin": "0",
                    "end": "900",
                    "number": "5",
                },
            )
            ET.SubElement(extra, "route", {"edges": "other_in other_out"})
            ET.ElementTree(route_root).write(route_path, encoding="utf-8")
            manifest = json.loads(layout.traffic_manifest.read_text(encoding="utf-8"))
            scenario = manifest["scenarios"]["global_morning_peak"]
            scenario["intersection_ids"].append("demo_3")
            scenario["flows"].append(
                {
                    "flow_id": "flow_other_intersection",
                    "source_intersection_id": "demo_3",
                    "source_official_approach": "south",
                    "begin": 0,
                    "end": 900,
                    "number": 5,
                }
            )
            layout.traffic_manifest.write_text(json.dumps(manifest), encoding="utf-8")

            compiled = compile_session_scenario(
                "control-subset",
                ["demo_2"],
                "morning_peak",
                origins={"demo_2": ["west"]},
                generated_dir=generated,
                session_root=root / "sessions",
            )
            flow_ids = {
                flow.get("id") for flow in ET.parse(compiled.route_file).getroot().findall("flow")
            }
            self.assertIn("flow_other_intersection", flow_ids)
            self.assertNotIn("flow_north_0", flow_ids)

    def test_full_unfiltered_period_is_marked_official(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            generated = write_fixture(root)
            compiled = compile_session_scenario(
                "official-full",
                ["demo_2"],
                "morning_peak",
                generated_dir=generated,
                session_root=root / "sessions",
            )
            self.assertTrue(compiled.official_complete_demand)
            manifest = json.loads(
                (compiled.directory / "session_manifest.json").read_text(encoding="utf-8")
            )
            self.assertTrue(manifest["official_complete_demand"])


if __name__ == "__main__":
    unittest.main()
