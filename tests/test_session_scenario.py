import json
import tempfile
import unittest
import xml.etree.ElementTree as ET
from pathlib import Path

from simulation.sumo.artifacts import GeneratedArtifactLayout
from simulation.sumo.scenario import (
    ScenarioCompilationError,
    compile_session_scenario,
    load_compiled_scenario,
)


def write_fixture(root: Path):
    generated = root / "generated"
    layout = GeneratedArtifactLayout(generated)
    layout.create_base_directories()
    layout.network_file.write_text("<net/>", encoding="utf-8")
    scenario_dir = layout.global_traffic_scenario_dir("morning_peak")
    scenario_dir.mkdir(parents=True)
    route_file = scenario_dir / "routes.rou.xml"
    additional_file = scenario_dir / "signals.add.xml"
    route_file.write_text(
        """<routes>
  <vType id="demo_car" vClass="passenger"/>
  <flow id="flow_west_0" type="demo_car" begin="0" end="900" number="10" departPos="100" arrivalPos="150"><route edges="far_in in out far_out"/></flow>
  <flow id="flow_north_0" type="demo_car" begin="0" end="900" number="20" departPos="100" arrivalPos="150"><route edges="far_in2 in2 out2 far_out2"/></flow>
  <flow id="flow_west_1" type="demo_car" begin="900" end="1800" number="10" departPos="100" arrivalPos="150"><route edges="far_in in out far_out"/></flow>
  <flow id="flow_north_1" type="demo_car" begin="900" end="1800" number="20" departPos="100" arrivalPos="150"><route edges="far_in2 in2 out2 far_out2"/></flow>
</routes>""",
        encoding="utf-8",
    )
    additional_file.write_text(
        (
            '<additional>'
            '<tlLogic id="4427" programID="demo_1_morning_peak"/>'
            '<tlLogic id="317" programID="demo_2_morning_peak"/>'
            '<tlLogic id="318" programID="demo_3_morning_peak"/>'
            '</additional>'
        ),
        encoding="utf-8",
    )
    layout.tls_manifest.write_text(
        json.dumps(
            {
                "schema_version": 2,
                "intersections": {
                    "demo_2": {
                        "tls_ids": ["317"],
                    }
                },
            }
        ),
        encoding="utf-8",
    )
    profile_root = json.loads(
        (
            Path(__file__).resolve().parents[1]
            / "data"
            / "maps"
            / "sumo"
            / "vehicle_profiles.json"
        ).read_text(encoding="utf-8")
    )
    manifest = {
        "schema_version": 3,
        "vehicle_profile_schema_version": 2,
        "vehicle_profiles": {"passenger": profile_root["profiles"]["passenger"]},
        "vehicle_type_profiles": {"demo_car": "passenger"},
        "intersections": {
            "demo_2": {
                "periods": ["morning_peak"],
                "origins": {
                    "west": {
                        "label": "西进口",
                        "sumo_approach": "west",
                        "lane_ids": ["in_0"],
                    },
                    "north": {
                        "label": "北进口",
                        "sumo_approach": "north",
                        "lane_ids": ["in2_0"],
                    },
                },
            },
        },
        "scenarios": {
            "global_morning_peak": {
                "intersection_ids": ["demo_2"],
                "period_id": "morning_peak",
                "official_time_range": {"start": "07:00:00", "end": "07:30:00"},
                "demand_duration": 1800,
                "route_file": layout.relative(route_file),
                "additional_file": layout.relative(additional_file),
            }
        },
    }
    layout.traffic_manifest.write_text(json.dumps(manifest), encoding="utf-8")
    return generated


class SessionScenarioTests(unittest.TestCase):
    def test_loads_an_existing_compiled_scenario(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            generated = write_fixture(root)
            compiled = compile_session_scenario(
                "restored-session",
                ["demo_2"],
                "morning_peak",
                duration_seconds=60,
                generated_dir=generated,
                session_root=root / "sessions",
            )
            restored = load_compiled_scenario(
                compiled.session_id,
                generated_dir=generated,
                session_root=root / "sessions",
            )
            self.assertEqual(restored, compiled)
    def test_writes_tripinfo_for_completed_and_unfinished_vehicles(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            compiled = compile_session_scenario(
                "tripinfo-session",
                ["demo_2"],
                "morning_peak",
                generated_dir=write_fixture(root),
                session_root=root / "sessions",
            )

            config = ET.parse(compiled.sumocfg).getroot()
            output = config.find("output")
            self.assertIsNotNone(output)
            self.assertEqual(
                output.find("tripinfo-output").get("value"),
                str((compiled.directory / "tripinfo.xml").resolve()),
            )
            self.assertEqual(
                output.find("tripinfo-output.write-unfinished").get("value"),
                "true",
            )

    def test_intersection_selection_does_not_filter_or_duplicate_global_traffic(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            generated = write_fixture(root)
            layout = GeneratedArtifactLayout(generated)
            manifest = json.loads(
                layout.traffic_manifest.read_text(encoding="utf-8")
            )
            manifest["intersections"]["demo_3"] = {
                "periods": ["morning_peak"],
                "origins": {},
            }
            manifest["scenarios"]["global_morning_peak"]["intersection_ids"].append(
                "demo_3"
            )
            layout.traffic_manifest.write_text(
                json.dumps(manifest), encoding="utf-8"
            )
            tls_manifest = json.loads(
                layout.tls_manifest.read_text(encoding="utf-8")
            )
            tls_manifest["intersections"]["demo_3"] = {"tls_ids": ["318"]}
            layout.tls_manifest.write_text(
                json.dumps(tls_manifest), encoding="utf-8"
            )
            one = compile_session_scenario(
                "one-intersection",
                ["demo_2"],
                "morning_peak",
                generated_dir=generated,
                session_root=root / "sessions",
            )
            multiple = compile_session_scenario(
                "multiple-intersections",
                ["demo_2", "demo_3"],
                "morning_peak",
                generated_dir=generated,
                session_root=root / "sessions",
            )

            one_flows = [
                ET.tostring(flow, encoding="unicode")
                for flow in ET.parse(one.route_file).getroot().findall("flow")
            ]
            multiple_flows = [
                ET.tostring(flow, encoding="unicode")
                for flow in ET.parse(multiple.route_file).getroot().findall("flow")
            ]
            self.assertEqual(one_flows, multiple_flows)
            self.assertEqual(one.planned_vehicle_count, multiple.planned_vehicle_count)

    def test_clips_global_window_and_scales_deterministically(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            generated = write_fixture(root)
            compiled = compile_session_scenario(
                "session-test",
                ["demo_2"],
                "morning_peak",
                window_start_seconds=450,
                duration_seconds=900,
                flow_multiplier=1.5,
                generated_dir=generated,
                session_root=root / "sessions",
            )
            route_root = ET.parse(compiled.route_file).getroot()
            self.assertEqual([item.tag for item in route_root][:2], ["vType", "vType"])
            activity_type = route_root.find("vType[@id='citypulse_event_passenger']")
            self.assertIsNotNone(activity_type)
            self.assertEqual(activity_type.get("color"), "255,128,0")
            flows = route_root.findall("flow")
            self.assertEqual(
                [flow.get("id") for flow in flows],
                ["flow_north_0", "flow_west_0", "flow_north_1", "flow_west_1"],
            )
            self.assertEqual(sum(int(flow.get("number")) for flow in flows), 45)
            self.assertEqual(
                [(flow.get("begin"), flow.get("end")) for flow in flows],
                [("0", "450"), ("0", "450"), ("450", "900"), ("450", "900")],
            )
            self.assertTrue(all(flow.get("departPos") == "100" for flow in flows))
            self.assertTrue(all(flow.get("arrivalPos") == "150" for flow in flows))
            self.assertEqual(compiled.planned_vehicle_count, 45)
            self.assertEqual(compiled.official_start_seconds, 7 * 3600)
            self.assertEqual(compiled.vehicle_type_profiles, {"demo_car": "passenger"})
            self.assertEqual(compiled.selected_origins, {})
            config = ET.parse(compiled.sumocfg).getroot()
            self.assertEqual(config.find("time/end").get("value"), "900")
            signal_logics = ET.parse(compiled.additional_file).getroot().findall(
                "tlLogic"
            )
            self.assertEqual(
                [(logic.get("id"), logic.get("programID")) for logic in signal_logics],
                [("317", "demo_2_morning_peak")],
            )

    def test_rejects_origin_filtering_and_invalid_multiplier(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            generated = write_fixture(root)
            with self.assertRaisesRegex(ScenarioCompilationError, "Origin filtering"):
                compile_session_scenario(
                    "origin-filter",
                    ["demo_2"],
                    "morning_peak",
                    origins={"demo_2": ["west"]},
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
