import io
import json
import sys
import tempfile
import unittest
from contextlib import redirect_stdout
from pathlib import Path
from unittest.mock import patch

from simulation.sumo.build_tls import (
    _build_templates,
    _inspect_generated_network,
    build,
    main,
    validate,
)
from simulation.sumo.config import SignalConfigurationError, load_signal_configuration
from simulation.sumo.traffic import TrafficDemandError


ROOT = Path(__file__).resolve().parents[1]
SUMO_DIR = ROOT / "data" / "maps" / "sumo"
MAPPING = SUMO_DIR / "TotalMap_20.intersections.json"
PLANS = SUMO_DIR / "official_tls_plans.json"
TOPOLOGY = SUMO_DIR / "official_tls_topology.json"
DEMANDS = SUMO_DIR / "official_traffic_demands.json"
NETWORK = SUMO_DIR / "TotalMap_20.net.xml"
ALL_INTERSECTIONS = tuple(f"demo_{index}" for index in range(1, 21))


class RealNetworkCompatibilityTests(unittest.TestCase):
    def test_all_official_intersections_match_the_current_source_network(self):
        report = validate(ALL_INTERSECTIONS)
        self.assertEqual(report["intersections"], 20)
        self.assertEqual(report["periods"], 60)
        self.assertEqual(report["nonzero_movements"], 591)
        self.assertEqual(report["override_routes"], 12)

    def test_demo_8_real_tls_controls_only_central_junction_movements(self):
        configuration = load_signal_configuration(MAPPING, PLANS, TOPOLOGY)
        demo_8 = configuration.intersections["demo_8"]
        connections, state_lengths, foes = _inspect_generated_network(
            NETWORK, (demo_8,)
        )
        self.assertEqual(len(connections), 29)
        self.assertEqual({item.tls_id for item in connections}, {"J1"})
        self.assertFalse(
            any(
                item.approach in {"east", "south"} and item.movement == "right"
                for item in connections
            )
        )
        controlled_right_turns = [
            item
            for item in connections
            if item.approach in {"west", "north"} and item.movement == "right"
        ]
        self.assertTrue(controlled_right_turns)

        for program in demo_8.programs.values():
            templates = _build_templates(
                demo_8,
                connections,
                state_lengths,
                foes,
                demo_8.topology.phases_for(program.program_id),
            )
            self.assertEqual(len(templates), 4)
            for phase in templates.values():
                for states in phase.values():
                    for connection in controlled_right_turns:
                        self.assertEqual(states["green"][connection.link_index], "g")
                        self.assertEqual(states["yellow"][connection.link_index], "g")
                        self.assertEqual(
                            states["clearance"][connection.link_index], "g"
                        )

    def test_missing_demo_8_bypasses_report_all_six_period_movements(self):
        raw = json.loads(DEMANDS.read_text(encoding="utf-8"))
        for period in raw["intersections"]["demo_8"]["periods"]:
            period.pop("route_overrides")

        with tempfile.TemporaryDirectory() as directory:
            demand_path = Path(directory) / "demands.json"
            demand_path.write_text(
                json.dumps(raw, ensure_ascii=False), encoding="utf-8"
            )
            with patch("simulation.sumo.build_tls._binary") as binary, patch(
                "simulation.sumo.build_tls.GeneratedArtifactLayout.reset"
            ) as reset:
                with self.assertRaises(TrafficDemandError) as caught:
                    build(["demo_8"], demand_path=demand_path)

        message = str(caught.exception)
        self.assertIn("failed with 6 issue(s)", message)
        for period_id in ("morning_peak", "off_peak", "evening_peak"):
            self.assertIn(f"demo_8/{period_id}/east/right", message)
            self.assertIn(f"demo_8/{period_id}/south/right", message)
        binary.assert_not_called()
        reset.assert_not_called()

    def test_validate_only_does_not_require_sumo_or_touch_generated_output(self):
        with tempfile.TemporaryDirectory() as directory:
            output = Path(directory) / "generated"
            argv = [
                "build_tls",
                "--validate-only",
                "--intersections",
                "demo_8",
                "--output-dir",
                str(output),
            ]
            with patch.object(sys, "argv", argv), patch(
                "simulation.sumo.build_tls._binary"
            ) as binary, patch(
                "simulation.sumo.build_tls.GeneratedArtifactLayout.reset"
            ) as reset, redirect_stdout(io.StringIO()) as stdout:
                main()

            self.assertIn(
                "Validated source network compatibility for 1 intersections",
                stdout.getvalue(),
            )
            self.assertFalse(output.exists())
            binary.assert_not_called()
            reset.assert_not_called()


class SyntheticNetworkCompatibilityTests(unittest.TestCase):
    def setUp(self):
        self.temp_directory = tempfile.TemporaryDirectory()
        self.root = Path(self.temp_directory.name)
        self.mapping = self.root / "mapping.json"
        self.plans = self.root / "plans.json"
        self.topology = self.root / "topology.json"
        self.demands = self.root / "demands.json"
        self.network = self.root / "network.net.xml"
        self.mapping.write_text(
            json.dumps({"demo_x": {"junction_id": "J"}}), encoding="utf-8"
        )
        self.plans.write_text(
            json.dumps(
                {
                    "schema_version": 2,
                    "intersections": {
                        "demo_x": {
                            "programs": [
                                {
                                    "program_id": "demo_x_period",
                                    "period_type": "test",
                                    "time_range": {
                                        "start": "00:00:00",
                                        "end": "00:15:00",
                                    },
                                    "cycle_duration": 10,
                                    "phases": [
                                        {
                                            "official_phase_no": 1,
                                            "official_phase_name": "through",
                                            "green": 10,
                                            "yellow": 0,
                                            "all_red": 0,
                                            "total": 10,
                                        }
                                    ],
                                }
                            ]
                        }
                    },
                }
            ),
            encoding="utf-8",
        )
        self.write_topology()
        self.write_demands()
        self.write_network([("in", "out")])

    def tearDown(self):
        self.temp_directory.cleanup()

    def write_topology(self, incoming_edge="in"):
        self.topology.write_text(
            json.dumps(
                {
                    "schema_version": 1,
                    "intersections": {
                        "demo_x": {
                            "right_turn_policy": "permissive_always",
                            "u_turn_policy": "blocked",
                            "approaches": {
                                "east": {"incoming_edges": [incoming_edge]}
                            },
                            "direction_mapping": {
                                "s": "through",
                                "l": "left",
                                "r": "right",
                                "t": "blocked",
                            },
                            "phases": [
                                {
                                    "official_phase_no": 1,
                                    "movement": "through",
                                    "approaches": ["east"],
                                }
                            ],
                        }
                    },
                }
            ),
            encoding="utf-8",
        )

    def write_demands(self, route_overrides=None, route_splits=None):
        period = {
            "period_id": "period",
            "label": "test",
            "program_id": "demo_x_period",
            "time_range": {"start": "00:00:00", "end": "00:15:00"},
            "expected_totals": {"east": 1, "all": 1},
            "intervals": [
                {
                    "start": "00:00:00",
                    "end": "00:15:00",
                    "volumes": {"east": {"through": 1}},
                }
            ],
        }
        if route_overrides is not None:
            period["route_overrides"] = route_overrides
        if route_splits is not None:
            period["route_splits"] = route_splits
        self.demands.write_text(
            json.dumps(
                {
                    "schema_version": 2,
                    "source": "test",
                    "unit": "pcu",
                    "interval_seconds": 900,
                    "vehicle_mix": {
                        "basis": "vehicle_count",
                        "shares": {"passenger": 1.0},
                    },
                    "intersections": {
                        "demo_x": {
                            "approaches": {
                                "east": {
                                    "label": "east",
                                    "sumo_approach": "east",
                                    "movements": {"through": "through"},
                                }
                            },
                            "periods": [period],
                        }
                    },
                }
            ),
            encoding="utf-8",
        )

    def write_network(self, connections, incoming_edge="in", incoming_target="J"):
        edges = {incoming_edge, "out", "out2", "out3"}
        edges.update(edge for pair in connections for edge in pair)
        edge_xml = []
        for edge_id in sorted(edges):
            target = incoming_target if edge_id == incoming_edge else f"T_{edge_id}"
            edge_xml.append(
                f'<edge id="{edge_id}" from="F_{edge_id}" to="{target}"/>'
            )
        connection_xml = [
            f'<connection from="{first}" to="{second}" dir="s"/>'
            for first, second in connections
        ]
        junctions = ['<junction id="J" type="priority"/>']
        if incoming_target != "J":
            junctions.append(
                f'<junction id="{incoming_target}" type="priority"/>'
            )
        self.network.write_text(
            "<net>"
            + "".join(edge_xml)
            + "".join(junctions)
            + "".join(connection_xml)
            + "</net>",
            encoding="utf-8",
        )

    def validate(self):
        return validate(
            ["demo_x"],
            mapping_path=self.mapping,
            plans_path=self.plans,
            topology_path=self.topology,
            demand_path=self.demands,
            source_net=self.network,
        )

    def inspect_generated_network(self, *, include_tls_logic=True, state="G"):
        logic = (
            '<tlLogic id="J" type="static" programID="default" offset="0">'
            f'<phase duration="10" state="{state}"/>'
            "</tlLogic>"
            if include_tls_logic
            else ""
        )
        self.network.write_text(
            "<net>"
            f"{logic}"
            '<junction id="J" type="traffic_light">'
            '<request index="0" response="0" foes="0" cont="0"/>'
            "</junction>"
            '<connection from="in" to="out" fromLane="0" toLane="0" '
            'via=":J_0_0" tl="J" linkIndex="0" dir="s" state="O"/>'
            "</net>",
            encoding="utf-8",
        )
        configuration = load_signal_configuration(
            self.mapping, self.plans, self.topology
        )
        return _inspect_generated_network(
            self.network, (configuration.intersections["demo_x"],)
        )

    def test_generated_network_requires_an_internal_tls_logic(self):
        with self.assertRaisesRegex(
            SignalConfigurationError,
            "references TLS without an internal tlLogic.*J",
        ):
            self.inspect_generated_network(include_tls_logic=False)

    def test_generated_network_rejects_tls_state_length_mismatch(self):
        with self.assertRaisesRegex(
            SignalConfigurationError,
            "tlLogic state lengths inconsistent with linkIndex",
        ):
            self.inspect_generated_network(state="GG")

    def test_generated_network_accepts_matching_tls_logic(self):
        connections, state_lengths, _ = self.inspect_generated_network()
        self.assertEqual([item.tls_id for item in connections], ["J"])
        self.assertEqual(state_lengths["J"], 1)

    def test_route_override_rejects_a_missing_edge_with_context(self):
        self.write_demands(
            route_overrides={
                "east": {
                    "through": [
                        {"edges": ["missing", "out"], "weight": 1}
                    ]
                }
            }
        )
        with self.assertRaisesRegex(
            TrafficDemandError,
            "demo_x/period/east/through: route override edge 'missing'",
        ):
            self.validate()

    def test_route_override_rejects_disconnected_adjacent_edges(self):
        self.write_demands(
            route_overrides={
                "east": {
                    "through": [{"edges": ["in", "out2"], "weight": 1}]
                }
            }
        )
        with self.assertRaisesRegex(
            TrafficDemandError,
            "demo_x/period/east/through: route override edges 'in' and 'out2'",
        ):
            self.validate()

    def test_route_split_rejects_targets_that_differ_from_network_routes(self):
        self.write_network([("in", "out"), ("in", "out2")])
        self.write_demands(
            route_splits={
                "east": {
                    "through": [
                        {"to_edge": "out", "weight": 1},
                        {"to_edge": "out3", "weight": 1},
                    ]
                }
            }
        )
        with self.assertRaisesRegex(
            TrafficDemandError,
            "configured split targets.*out3.*do not match SUMO routes",
        ):
            self.validate()

    def test_incoming_edge_must_end_at_the_mapped_junction(self):
        self.write_topology(incoming_edge="other")
        self.write_network(
            [("other", "out")], incoming_edge="other", incoming_target="K"
        )
        with self.assertRaisesRegex(
            SignalConfigurationError,
            "incoming edge 'other' ends at junction 'K'",
        ):
            self.validate()


if __name__ == "__main__":
    unittest.main()
