import json
import tempfile
import unittest
import xml.etree.ElementTree as ET
from pathlib import Path

from simulation.sumo.scenario import ScenarioCompilationError, compile_session_scenario


def write_fixture(root: Path):
    generated = root / "generated"
    generated.mkdir()
    (generated / "TotalMap_20.signals.net.xml").write_text("<net/>", encoding="utf-8")
    (generated / "demo.rou.xml").write_text(
        """<routes>
  <vType id="demo_car" vClass="passenger"/>
  <flow id="flow_west_0" type="demo_car" begin="0" end="900" number="10"><route edges="in out"/></flow>
  <flow id="flow_north_0" type="demo_car" begin="0" end="900" number="20"><route edges="in2 out2"/></flow>
  <flow id="flow_west_1" type="demo_car" begin="900" end="1800" number="10"><route edges="in out"/></flow>
  <flow id="flow_north_1" type="demo_car" begin="900" end="1800" number="20"><route edges="in2 out2"/></flow>
</routes>""",
        encoding="utf-8",
    )
    (generated / "demo.add.xml").write_text(
        '<additional><tlLogic id="317" programID="demo_2_morning_peak"/></additional>',
        encoding="utf-8",
    )
    flows = [
        {"flow_id": "flow_west_0", "official_approach": "west", "official_movement": "left", "begin": 0, "end": 900, "number": 10},
        {"flow_id": "flow_north_0", "official_approach": "north", "official_movement": "through", "begin": 0, "end": 900, "number": 20},
        {"flow_id": "flow_west_1", "official_approach": "west", "official_movement": "left", "begin": 900, "end": 1800, "number": 10},
        {"flow_id": "flow_north_1", "official_approach": "north", "official_movement": "through", "begin": 900, "end": 1800, "number": 20},
    ]
    manifest = {
        "schema_version": 2,
        "scenarios": {
            "demo_2_morning_peak": {
                "intersection_id": "demo_2",
                "period_id": "morning_peak",
                "official_time_range": {"start": "07:00:00", "end": "07:30:00"},
                "demand_duration": 1800,
                "route_file": "demo.rou.xml",
                "additional_file": "demo.add.xml",
                "flows": flows,
                "origins": {
                    "west": {"label": "西进口", "sumo_approach": "west", "lane_ids": ["in_0"]},
                    "north": {"label": "北进口", "sumo_approach": "north", "lane_ids": ["in2_0"]},
                },
            }
        },
    }
    (generated / "traffic_manifest.json").write_text(
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
            self.assertEqual(compiled.official_start_seconds, 7 * 3600)
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


if __name__ == "__main__":
    unittest.main()
