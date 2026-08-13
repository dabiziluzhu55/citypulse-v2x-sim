import json
import tempfile
import unittest
import xml.etree.ElementTree as ET
from pathlib import Path

from simulation.sumo.building.artifacts import GeneratedArtifactLayout
from simulation.sumo.building.build_traffic import DENSE_TRAFFIC_SCOPES
from simulation.sumo.engine.scenario import (
    ScenarioCompilationError,
    compile_session_scenario,
)
from simulation.sumo.engine.session import load_catalog


PASSENGER_PROFILE = {
    "profile_id": "passenger",
    "pcu_factor": 1.0,
    "v_class": "passenger",
    "powertrain": "gasoline",
    "emission_class": "HBEFA3/PC_G_EU4",
    "accel_mps2": 2.6,
    "decel_mps2": 4.5,
    "length_m": 5.0,
    "width_m": 1.8,
    "min_gap_m": 2.5,
    "max_speed_mps": 13.9,
    "sigma": 0.5,
    "fuel_density_mg_per_ml": 745.0,
    "hard_braking_threshold_mps2": -3.0,
}


def _write_json(path: Path, value) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def _write_xml(path: Path, root: ET.Element) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    ET.indent(root, space="  ")
    ET.ElementTree(root).write(path, encoding="utf-8", xml_declaration=True)


class DenseTrafficScopeTests(unittest.TestCase):
    def test_dense_scope_constants_and_layout(self):
        self.assertEqual(
            DENSE_TRAFFIC_SCOPES["east_dense"],
            ("demo_3", "demo_5", "demo_6", "demo_9"),
        )
        self.assertEqual(
            DENSE_TRAFFIC_SCOPES["west_dense"],
            ("demo_14", "demo_15", "demo_19"),
        )
        layout = GeneratedArtifactLayout(Path("generated"))
        self.assertEqual(
            layout.traffic_scenario_scope_dir("east_dense", "morning_peak"),
            Path("generated") / "traffic" / "east_dense" / "morning_peak",
        )

    def test_compile_session_uses_dense_scope_and_rejects_other_intersections(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            generated = root / "generated"
            sessions = root / "sessions"
            _write_minimal_generated_artifacts(generated)

            compiled = compile_session_scenario(
                "session-ok",
                ("demo_3",),
                "morning_peak",
                scenario_scope="east_dense",
                generated_dir=generated,
                session_root=sessions,
            )

            self.assertEqual(compiled.scenario_scope, "east_dense")
            self.assertEqual(compiled.planned_vehicle_count, 10)
            session_manifest = json.loads(
                (compiled.directory / "session_manifest.json").read_text(encoding="utf-8")
            )
            self.assertEqual(session_manifest["scenario_scope"], "east_dense")

            with self.assertRaisesRegex(
                ScenarioCompilationError,
                "does not include intersections",
            ):
                compile_session_scenario(
                    "session-bad",
                    ("demo_14",),
                    "morning_peak",
                    scenario_scope="east_dense",
                    generated_dir=generated,
                    session_root=sessions,
                )

    def test_catalog_exposes_available_scenario_scopes(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            generated = root / "generated"
            _write_minimal_generated_artifacts(generated)
            _write_minimal_tls_catalog_artifacts(generated)

            catalog = load_catalog(generated)

            self.assertIn("east_dense", catalog.scenario_scopes)
            east = catalog.scenario_scopes["east_dense"]
            self.assertEqual(east.periods, ("morning_peak",))
            self.assertEqual(east.intersection_ids, ("demo_3",))


def _write_minimal_generated_artifacts(generated: Path) -> None:
    layout = GeneratedArtifactLayout(generated)
    _write_json(
        layout.traffic_manifest,
        {
            "schema_version": 3,
            "vehicle_profile_schema_version": 2,
            "vehicle_profiles": {"passenger": PASSENGER_PROFILE},
            "vehicle_type_profiles": {"official_passenger": "passenger"},
            "available_scopes": {
                "east_dense": {
                    "scope_id": "east_dense",
                    "label": "East dense area",
                    "periods": ["morning_peak"],
                    "intersection_ids": ["demo_3"],
                }
            },
            "intersections": {
                "demo_3": {
                    "periods": ["morning_peak"],
                    "origins": {
                        "east": {
                            "label": "east",
                            "sumo_approach": "east",
                            "lane_ids": ["E_0"],
                        }
                    },
                }
            },
            "scenarios": {
                "east_dense_morning_peak": {
                    "scenario_id": "east_dense_morning_peak",
                    "scope_id": "east_dense",
                    "period_id": "morning_peak",
                    "intersection_ids": ["demo_3"],
                    "program_ids": {"demo_3": "demo_3_morning_peak"},
                    "official_time_range": {"start": "07:00:00", "end": "09:00:00"},
                    "route_file": "traffic/east_dense/morning_peak/routes.rou.xml",
                    "additional_file": "traffic/east_dense/morning_peak/signals.add.xml",
                    "sumocfg": "traffic/east_dense/morning_peak/simulation.sumocfg",
                    "demand_duration": 7200,
                    "sampled_vehicle_count": 10,
                    "target_observation_pcu": 10,
                }
            },
        },
    )
    network = ET.Element("net")
    edge = ET.SubElement(network, "edge", {"id": "E"})
    ET.SubElement(edge, "lane", {"id": "E_0", "index": "0", "length": "100", "speed": "13.9"})
    out_edge = ET.SubElement(network, "edge", {"id": "O"})
    ET.SubElement(out_edge, "lane", {"id": "O_0", "index": "0", "length": "100", "speed": "13.9"})
    _write_xml(layout.network_file, network)

    routes = ET.Element("routes")
    ET.SubElement(routes, "vType", {"id": "official_passenger", "vClass": "passenger"})
    ET.SubElement(
        routes,
        "flow",
        {
            "id": "flow_1",
            "type": "official_passenger",
            "begin": "0",
            "end": "900",
            "number": "10",
            "from": "E",
            "to": "O",
        },
    )
    _write_xml(generated / "traffic" / "east_dense" / "morning_peak" / "routes.rou.xml", routes)

    additional = ET.Element("additional")
    tl_logic = ET.SubElement(
        additional,
        "tlLogic",
        {"id": "tls_demo_3", "type": "static", "programID": "demo_3_morning_peak"},
    )
    ET.SubElement(tl_logic, "phase", {"duration": "30", "state": "G"})
    _write_xml(
        generated / "traffic" / "east_dense" / "morning_peak" / "signals.add.xml",
        additional,
    )


def _write_minimal_tls_catalog_artifacts(generated: Path) -> None:
    _write_json(
        generated / "manifests" / "tls_manifest.json",
        {
            "schema_version": 2,
            "intersections": {
                "demo_3": {
                    "connections": [
                        {
                            "from_edge": "E",
                            "from_lane": 0,
                            "to_edge": "O",
                            "to_lane": 0,
                            "approach": "east",
                        }
                    ]
                }
            },
        },
    )
    _write_json(
        generated.parent / "official" / "map" / "TotalMap_20.intersections.json",
        {"demo_3": {"lon": 116.070252, "lat": 38.976818}},
    )


if __name__ == "__main__":
    unittest.main()
