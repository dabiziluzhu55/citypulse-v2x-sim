import json
import re
import subprocess
import tempfile
import unittest
import xml.etree.ElementTree as ET
from pathlib import Path

from simulation.sumo.artifacts import GeneratedArtifactLayout
from simulation.sumo.build_traffic import (
    _allocate_vehicle_mix,
    _validate_route_sampler,
    build_traffic_scenarios,
)
from simulation.sumo.traffic import load_traffic_demands
from simulation.sumo.traffic import TrafficDemandError
from simulation.sumo.vehicle_profiles import VehicleProfileError, load_vehicle_profiles


ROOT = Path(__file__).resolve().parents[1]
PROFILES = ROOT / "data" / "maps" / "sumo" / "vehicle_profiles.json"


def _arg(command, name):
    return Path(command[command.index(name) + 1])


class FakeSumoToolchain:
    def __init__(self):
        self.commands = []
        self.count_relations = []

    def __call__(self, command, **kwargs):
        self.commands.append(command)
        if command[0] == "fake-duarouter":
            source = ET.parse(_arg(command, "--route-files")).getroot()
            root = ET.Element("routes")
            for trip in source.findall("trip"):
                edges = [trip.get("from")]
                edges.extend(str(trip.get("via", "")).split())
                edges.append(trip.get("to"))
                vehicle = ET.SubElement(
                    root,
                    "vehicle",
                    {"id": trip.get("id"), "depart": trip.get("depart")},
                )
                ET.SubElement(vehicle, "route", {"edges": " ".join(edges)})
            ET.ElementTree(root).write(
                _arg(command, "--output-file"), encoding="utf-8", xml_declaration=True
            )
        elif len(command) > 1 and command[1] == "fake-routeSampler.py":
            counts_root = ET.parse(_arg(command, "--turn-files")).getroot()
            attributes = command[command.index("--attributes") + 1]
            type_id = re.search(r'type="([^"]+)"', attributes).group(1)
            prefix = command[command.index("--prefix") + 1]
            output = ET.Element("routes")
            flow_index = 0
            for interval in counts_root.findall("interval"):
                relations = []
                for relation in interval.findall("edgeRelation"):
                    edges = [relation.get("from")]
                    edges.extend(str(relation.get("via", "")).split())
                    edges.append(relation.get("to"))
                    relations.append([edges, int(relation.get("count"))])
                    self.count_relations.append(dict(relation.attrib))
                positive = [item for item in relations if item[1] > 0]
                if len(positive) >= 2:
                    shared = min(positive[0][1], positive[1][1])
                    combined = positive[0][0] + positive[1][0]
                    flow = ET.SubElement(
                        output,
                        "flow",
                        {
                            "id": f"{prefix}shared_{flow_index}",
                            "type": type_id,
                            "begin": interval.get("begin"),
                            "end": interval.get("end"),
                            "number": str(shared),
                        },
                    )
                    ET.SubElement(flow, "route", {"edges": " ".join(combined)})
                    positive[0][1] -= shared
                    positive[1][1] -= shared
                    flow_index += 1
                for edges, count in relations:
                    if count <= 0:
                        continue
                    flow = ET.SubElement(
                        output,
                        "flow",
                        {
                            "id": f"{prefix}local_{flow_index}",
                            "type": type_id,
                            "begin": interval.get("begin"),
                            "end": interval.get("end"),
                            "number": str(count),
                        },
                    )
                    ET.SubElement(flow, "route", {"edges": " ".join(edges)})
                    flow_index += 1
            ET.ElementTree(output).write(
                _arg(command, "--output-file"), encoding="utf-8", xml_declaration=True
            )
            if "--mismatch-output" in command:
                _arg(command, "--mismatch-output").write_text(
                    "<data/>", encoding="utf-8"
                )
        return subprocess.CompletedProcess(command, 0, stdout="ok")


class RetrySumoToolchain(FakeSumoToolchain):
    def __call__(self, command, **kwargs):
        result = super().__call__(command, **kwargs)
        if len(command) > 1 and command[1] == "fake-routeSampler.py":
            seed = int(command[command.index("--seed") + 1])
            if seed == 42:
                output_path = _arg(command, "--output-file")
                root = ET.parse(output_path).getroot()
                first = root.find("flow")
                if first is not None:
                    first.set("number", str(int(first.get("number")) + 10))
                    ET.ElementTree(root).write(
                        output_path, encoding="utf-8", xml_declaration=True
                    )
        return result


class LegacyFlowWindowSumoToolchain(FakeSumoToolchain):
    def __call__(self, command, **kwargs):
        result = super().__call__(command, **kwargs)
        if len(command) > 1 and command[1] == "fake-routeSampler.py":
            output_path = _arg(command, "--output-file")
            root = ET.parse(output_path).getroot()
            for flow in root.findall("flow"):
                flow.set("end", str(float(flow.get("begin")) + 1))
            ET.ElementTree(root).write(
                output_path, encoding="utf-8", xml_declaration=True
            )
        return result


class ZeroOverflowSumoToolchain(FakeSumoToolchain):
    def __call__(self, command, **kwargs):
        result = super().__call__(command, **kwargs)
        if len(command) > 1 and command[1] == "fake-routeSampler.py":
            counts_root = ET.parse(_arg(command, "--turn-files")).getroot()
            zero_relation = next(
                relation
                for relation in counts_root.findall("interval/edgeRelation")
                if relation.get("count") == "0" and relation.get("from", "").startswith("zero_")
            )
            interval = next(
                item
                for item in counts_root.findall("interval")
                if zero_relation in list(item)
            )
            attributes = command[command.index("--attributes") + 1]
            type_id = re.search(r'type="([^"]+)"', attributes).group(1)
            output_path = _arg(command, "--output-file")
            output = ET.parse(output_path).getroot()
            flow = ET.SubElement(
                output,
                "flow",
                {
                    "id": f"zero_overflow_{type_id}",
                    "type": type_id,
                    "begin": interval.get("begin"),
                    "end": interval.get("end"),
                    "number": "1",
                },
            )
            edges = [zero_relation.get("from")]
            edges.extend(str(zero_relation.get("via", "")).split())
            edges.append(zero_relation.get("to"))
            ET.SubElement(flow, "route", {"edges": " ".join(edges)})
            ET.ElementTree(output).write(
                output_path, encoding="utf-8", xml_declaration=True
            )
        return result


def _write_demand(
    path: Path,
    *,
    intersection_ids=("demo_a", "demo_b"),
    include_zero_movement=False,
    via_override=False,
):
    intersections = {}
    for index, intersection_id in enumerate(intersection_ids):
        movements = {"through": "through"}
        volumes = {"through": 10}
        if include_zero_movement:
            movements["right"] = "right"
            volumes["right"] = 0
        period = {
            "period_id": "morning_peak",
            "label": "morning",
            "program_id": f"{intersection_id}_morning_peak",
            "time_range": {"start": "07:00:00", "end": "07:15:00"},
            "expected_totals": {"east": 10, "all": 10},
            "intervals": [
                {
                    "start": "07:00:00",
                    "end": "07:15:00",
                    "volumes": {"east": volumes},
                }
            ],
        }
        if via_override:
            period["route_overrides"] = {
                "east": {
                    "through": [
                        {
                            "edges": [f"in_{index}", f"via_{index}", f"out_{index}"],
                            "weight": 1,
                        }
                    ]
                }
            }
        intersections[intersection_id] = {
            "approaches": {
                "east": {
                    "label": "east",
                    "sumo_approach": "east",
                    "movements": movements,
                }
            },
            "periods": [period],
        }
    path.write_text(
        json.dumps(
            {
                "schema_version": 2,
                "source": "test",
                "unit": "pcu",
                "interval_seconds": 900,
                "vehicle_mix": {
                    "basis": "vehicle_count",
                    "shares": {"passenger": 0.85, "bus": 0.10, "truck": 0.05},
                },
                "intersections": intersections,
            }
        ),
        encoding="utf-8",
    )


def _write_tls_fixture(root: Path, intersection_ids, *, include_zero_movement=False):
    generated = root / "generated"
    layout = GeneratedArtifactLayout(generated)
    layout.create_base_directories()
    network = ET.Element("net")
    for index, _intersection_id in enumerate(intersection_ids):
        edge_ids = (f"in_{index}", f"via_{index}", f"out_{index}")
        if include_zero_movement:
            edge_ids += (
                f"zero_in_{index}",
                f"zero_out_{index}",
                f"zero_in_alt_{index}",
                f"zero_out_alt_{index}",
            )
        for edge_id in edge_ids:
            edge = ET.SubElement(network, "edge", {"id": edge_id})
            ET.SubElement(
                edge,
                "lane",
                {"id": f"{edge_id}_0", "index": "0", "length": "100"},
            )
    ET.ElementTree(network).write(
        layout.network_file, encoding="utf-8", xml_declaration=True
    )
    programs = ET.Element("additional")
    manifest_intersections = {}
    for index, intersection_id in enumerate(intersection_ids):
        program_id = f"{intersection_id}_morning_peak"
        ET.SubElement(
            programs,
            "tlLogic",
            {"id": str(index), "programID": program_id, "type": "static"},
        )
        connections = [
            {
                "approach": "east",
                "movement": "through",
                "from_edge": f"in_{index}",
                "from_lane": 0,
                "to_edge": f"out_{index}",
                "to_lane": 0,
            }
        ]
        if include_zero_movement:
            connections.extend(
                [
                    {
                        "approach": "east",
                        "movement": "right",
                        "from_edge": f"zero_in_{index}",
                        "from_lane": 0,
                        "to_edge": f"zero_out_{index}",
                        "to_lane": 0,
                    },
                    {
                        "approach": "east",
                        "movement": "right",
                        "from_edge": f"zero_in_alt_{index}",
                        "from_lane": 0,
                        "to_edge": f"zero_out_alt_{index}",
                        "to_lane": 0,
                    },
                ]
            )
        manifest_intersections[intersection_id] = {
            "program_ids": [program_id],
            "connections": connections,
        }
    ET.ElementTree(programs).write(
        layout.signal_programs_file, encoding="utf-8", xml_declaration=True
    )
    return generated, manifest_intersections


class GlobalTrafficTests(unittest.TestCase):
    def test_legacy_route_sampler_without_no_sampling_is_supported(self):
        with tempfile.TemporaryDirectory() as directory:
            script = Path(directory) / "routeSampler.py"
            script.write_text(
                "\n".join(
                    (
                        "--interval",
                        "--write-flows",
                        "--optimize",
                        "--minimize-vehicles",
                        "--mismatch-output",
                        "--seed",
                        "--attributes",
                        "output for edge relations with more than 2 edges not supported",
                    )
                ),
                encoding="utf-8",
            )
            capabilities = _validate_route_sampler(str(script))
            self.assertFalse(capabilities.no_sampling)
            self.assertFalse(capabilities.via_mismatch)

    def test_legacy_route_sampler_command_omits_no_sampling(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            generated, manifest_intersections = _write_tls_fixture(
                root, ("demo_a", "demo_b")
            )
            demand_path = root / "demands.json"
            _write_demand(demand_path)
            fake = LegacyFlowWindowSumoToolchain()
            build_traffic_scenarios(
                {"intersections": manifest_intersections},
                demand_path=demand_path,
                vehicle_profile_path=PROFILES,
                output_dir=generated,
                command_runner=fake,
                tool_paths={
                    "duarouter": "fake-duarouter",
                    "sumo": "fake-sumo",
                    "routeSampler": "fake-routeSampler.py",
                    "routeSamplerSupportsNoSampling": "false",
                },
            )

            sampler_commands = [
                command
                for command in fake.commands
                if len(command) > 1 and command[1] == "fake-routeSampler.py"
            ]
            self.assertTrue(sampler_commands)
            self.assertTrue(
                all("--no-sampling" not in command for command in sampler_commands)
            )
            self.assertTrue(
                all(
                    command[command.index("--optimize") + 1] == "full"
                    for command in sampler_commands
                )
            )

    def test_vehicle_mix_must_sum_to_one(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "demands.json"
            _write_demand(path)
            raw = json.loads(path.read_text(encoding="utf-8"))
            raw["vehicle_mix"]["shares"]["passenger"] = 0.5
            path.write_text(json.dumps(raw), encoding="utf-8")
            with self.assertRaisesRegex(TrafficDemandError, "sum to 1.0"):
                load_traffic_demands(path)

    def test_profile_pcu_must_use_half_unit_steps(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "profiles.json"
            raw = json.loads(PROFILES.read_text(encoding="utf-8"))
            raw["profiles"]["bus"]["pcu_factor"] = 1.25
            path.write_text(json.dumps(raw), encoding="utf-8")
            with self.assertRaisesRegex(VehicleProfileError, "0.5 PCU"):
                load_vehicle_profiles(path)

    def test_build_rejects_vehicle_mix_with_unknown_profile(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            demand_path = root / "demands.json"
            _write_demand(demand_path, intersection_ids=("demo_a",))
            raw = json.loads(demand_path.read_text(encoding="utf-8"))
            raw["vehicle_mix"]["shares"] = {"unknown_profile": 1.0}
            demand_path.write_text(json.dumps(raw), encoding="utf-8")
            with self.assertRaisesRegex(VehicleProfileError, "unknown vehicle profiles"):
                build_traffic_scenarios(
                    {"intersections": {}},
                    demand_path=demand_path,
                    vehicle_profile_path=PROFILES,
                    output_dir=root / "generated",
                    tool_paths={
                        "duarouter": "fake-duarouter",
                        "sumo": "fake-sumo",
                        "routeSampler": "fake-routeSampler.py",
                    },
                )

    def test_half_pcu_vehicle_mix_is_exact_and_deterministic(self):
        demand = load_traffic_demands(
            ROOT / "data" / "maps" / "sumo" / "official_traffic_demands.json"
        )
        profiles = load_vehicle_profiles(PROFILES)
        self.assertEqual(
            _allocate_vehicle_mix(100, demand.vehicle_mix.shares, profiles),
            {"bus": 9, "passenger": 72, "truck": 4},
        )
        self.assertEqual(
            9 * 2.0 + 72 * 1.0 + 4 * 2.5,
            100,
        )

    def test_global_build_uses_one_route_to_satisfy_two_intersections(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            generated, manifest_intersections = _write_tls_fixture(
                root, ("demo_a", "demo_b")
            )
            demand_path = root / "demands.json"
            _write_demand(demand_path)
            fake = FakeSumoToolchain()
            result = build_traffic_scenarios(
                {"intersections": manifest_intersections},
                demand_path=demand_path,
                vehicle_profile_path=PROFILES,
                output_dir=generated,
                command_runner=fake,
                tool_paths={
                    "duarouter": "fake-duarouter",
                    "sumo": "fake-sumo",
                    "routeSampler": "fake-routeSampler.py",
                },
            )

            self.assertEqual(result["schema_version"], 3)
            self.assertEqual(set(result["scenarios"]), {"global_morning_peak"})
            scenario = result["scenarios"]["global_morning_peak"]
            self.assertEqual(scenario["target_observation_pcu"], 20)
            self.assertGreater(scenario["multi_intersection_vehicle_count"], 0)
            self.assertEqual(scenario["selected_seed"], 42)
            self.assertEqual(
                scenario["arrival_position"],
                {"strategy": "final_edge_midpoint", "fraction": 0.5},
            )
            route_root = ET.parse(generated / scenario["route_file"]).getroot()
            self.assertEqual(len(route_root.findall("vType")), 3)
            self.assertTrue(route_root.findall("flow"))
            self.assertTrue(
                all(
                    flow.get("arrivalPos") == "50"
                    for flow in route_root.findall("flow")
                )
            )
            self.assertTrue(
                any(
                    flow.find("route").get("edges") == "in_0 out_0 in_1 out_1"
                    for flow in route_root.findall("flow")
                )
            )
            report = json.loads(
                (generated / scenario["quality_report"]).read_text(encoding="utf-8")
            )
            self.assertTrue(report["passed"])
            self.assertEqual(report["total_absolute_error_pcu"], 0)
            self.assertTrue(any(item["count"] == "0" for item in fake.count_relations))
            self.assertTrue(
                any(command[0] == "fake-sumo" for command in fake.commands)
            )

    def test_route_sampler_retries_after_seed_42_quality_failure(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            generated, manifest_intersections = _write_tls_fixture(
                root, ("demo_a", "demo_b")
            )
            demand_path = root / "demands.json"
            _write_demand(demand_path)
            fake = RetrySumoToolchain()
            result = build_traffic_scenarios(
                {"intersections": manifest_intersections},
                demand_path=demand_path,
                vehicle_profile_path=PROFILES,
                output_dir=generated,
                command_runner=fake,
                tool_paths={
                    "duarouter": "fake-duarouter",
                    "sumo": "fake-sumo",
                    "routeSampler": "fake-routeSampler.py",
                },
            )

            scenario = result["scenarios"]["global_morning_peak"]
            self.assertEqual(scenario["selected_seed"], 43)
            sampler_seeds = [
                int(command[command.index("--seed") + 1])
                for command in fake.commands
                if len(command) > 1 and command[1] == "fake-routeSampler.py"
            ]
            self.assertEqual(sorted(set(sampler_seeds)), [42, 43, 44, 45, 46])

    def test_official_zero_movement_overflow_writes_failure_report(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            generated, manifest_intersections = _write_tls_fixture(
                root,
                ("demo_a", "demo_b"),
                include_zero_movement=True,
            )
            demand_path = root / "demands.json"
            _write_demand(demand_path, include_zero_movement=True)
            fake = ZeroOverflowSumoToolchain()
            with self.assertRaisesRegex(TrafficDemandError, "quality thresholds failed"):
                build_traffic_scenarios(
                    {"intersections": manifest_intersections},
                    demand_path=demand_path,
                    vehicle_profile_path=PROFILES,
                    output_dir=generated,
                    command_runner=fake,
                    tool_paths={
                        "duarouter": "fake-duarouter",
                        "sumo": "fake-sumo",
                        "routeSampler": "fake-routeSampler.py",
                    },
                )

            failure_path = (
                generated / "reports" / "traffic_quality_morning_peak_failed.json"
            )
            failure = json.loads(failure_path.read_text(encoding="utf-8"))
            self.assertEqual([item["seed"] for item in failure["attempts"]], [42, 43, 44, 45, 46])
            zero_rows = [
                row
                for row in failure["attempts"][0]["cells"]
                if row["target_pcu"] == 0
            ]
            self.assertTrue(zero_rows)
            self.assertTrue(any(not row["passed"] for row in zero_rows))
            zero_relations = {
                item["from"]
                for item in fake.count_relations
                if item["count"] == "0" and item["from"].startswith("zero_")
            }
            self.assertEqual(
                zero_relations,
                {"zero_in_0", "zero_in_alt_0", "zero_in_1", "zero_in_alt_1"},
            )

    def test_via_count_path_is_written_and_recounted(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            generated, manifest_intersections = _write_tls_fixture(
                root, ("demo_a",)
            )
            demand_path = root / "demands.json"
            _write_demand(
                demand_path,
                intersection_ids=("demo_a",),
                via_override=True,
            )
            fake = FakeSumoToolchain()
            result = build_traffic_scenarios(
                {"intersections": manifest_intersections},
                demand_path=demand_path,
                vehicle_profile_path=PROFILES,
                output_dir=generated,
                command_runner=fake,
                tool_paths={
                    "duarouter": "fake-duarouter",
                    "sumo": "fake-sumo",
                    "routeSampler": "fake-routeSampler.py",
                    "routeSamplerSupportsViaMismatch": "false",
                },
            )

            self.assertTrue(
                any(item.get("via") == "via_0" for item in fake.count_relations)
            )
            scenario = result["scenarios"]["global_morning_peak"]
            report = json.loads(
                (generated / scenario["quality_report"]).read_text(encoding="utf-8")
            )
            self.assertTrue(report["passed"])
            self.assertEqual(report["actual_observation_pcu"], 10)
            self.assertEqual(
                report["vehicle_target_allocations"][0]["edges"],
                ["in_0", "via_0", "out_0"],
            )
            sampler_commands = [
                command
                for command in fake.commands
                if len(command) > 1 and command[1] == "fake-routeSampler.py"
            ]
            self.assertTrue(
                all("--mismatch-output" not in command for command in sampler_commands)
            )
            mismatch_path = generated / scenario["route_sampler_mismatch_files"][
                "passenger"
            ]
            mismatch_root = ET.parse(mismatch_path).getroot()
            self.assertEqual(mismatch_root.get("native"), "false")
            self.assertEqual(
                mismatch_root.get("reason"),
                "legacy-routeSampler-via-mismatch-unsupported",
            )


if __name__ == "__main__":
    unittest.main()
