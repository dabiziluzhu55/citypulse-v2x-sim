import json
import math
import tempfile
import unittest
from pathlib import Path

from simulation.sumo.build_traffic import (
    CandidatePool,
    CandidateRoute,
    CompiledGlobalDemand,
    CountLocation,
    NetworkRouteMetadata,
    SampledFlow,
    _attempt_policy_score,
    _calibrate_vehicle_shares,
    _filter_profile_candidates,
    _route_distance_m,
    _route_distribution,
    _route_freeflow_seconds,
    _route_policy_report,
)
from simulation.sumo.traffic import DemandPeriod
from simulation.sumo.traffic_policy import (
    TrafficGenerationPolicyError,
    load_traffic_generation_policy,
)
from simulation.sumo.vehicle_profiles import load_vehicle_profiles


ROOT = Path(__file__).resolve().parents[1]
POLICY_PATH = ROOT / "data" / "maps" / "sumo" / "traffic_generation_policy.json"
PROFILES_PATH = ROOT / "data" / "maps" / "sumo" / "vehicle_profiles.json"


def _metadata(lengths, speeds=None):
    return NetworkRouteMetadata(
        edge_lengths=lengths,
        edge_speeds=speeds or {},
        u_turn_pairs=frozenset(),
        upstream_extensions={},
        downstream_extensions={},
        configured_extensions={},
    )


class TrafficGenerationPolicyTests(unittest.TestCase):
    def setUp(self):
        self.policy = load_traffic_generation_policy(POLICY_PATH)
        self.profiles = load_vehicle_profiles(PROFILES_PATH)

    def test_route_distance_and_freeflow_use_half_endpoint_edges(self):
        metadata = _metadata(
            {"first": 100.0, "middle": 200.0, "last": 300.0},
            {"first": 10.0, "middle": 20.0, "last": 5.0},
        )
        edges = ("first", "middle", "last")
        self.assertEqual(_route_distance_m(edges, metadata), 400.0)
        expected_seconds = 50.0 / 10.0 + 200.0 / 13.9 + 150.0 / 5.0
        self.assertTrue(
            math.isclose(
                _route_freeflow_seconds(
                    edges, self.profiles["passenger"], metadata
                ),
                expected_seconds,
            )
        )

    def test_distance_class_boundaries_are_exact(self):
        metadata = _metadata(
            {
                "short_a": 4999.0,
                "short_b": 4999.0,
                "medium_a": 5000.0,
                "medium_b": 5000.0,
                "long_a": 10000.0,
                "long_b": 10000.0,
            }
        )
        flows = (
            SampledFlow("short", "official_passenger", 0, 1, 1, ("short_a", "short_b")),
            SampledFlow("medium", "official_passenger", 0, 1, 1, ("medium_a", "medium_b")),
            SampledFlow("long", "official_passenger", 0, 1, 1, ("long_a", "long_b")),
        )
        distribution = _route_distribution(
            flows,
            {"official_passenger": self.profiles["passenger"]},
            metadata,
            {},
            self.policy,
        )
        self.assertEqual(distribution["short_route_count"], 1)
        self.assertEqual(distribution["medium_route_count"], 1)
        self.assertEqual(distribution["long_route_count"], 1)
        self.assertEqual(distribution["distance_m"]["median"], 5000.0)

    def test_vehicle_specific_candidates_and_fallback_order(self):
        routes = (
            CandidateRoute("local_a_short", "local", ("a", "x"), 4000.0),
            CandidateRoute("local_b_medium", "local", ("b", "y"), 6000.0),
            CandidateRoute("pair_near", "pair", ("a", "x", "b", "y"), 8000.0),
            CandidateRoute("pair_long", "pair", ("a", "x", "b", "y", "c"), 12000.0),
            CandidateRoute("local_c", "local", ("c", "z"), 3000.0),
        )
        a = CountLocation("a", "east", "through", ("a", "x"))
        b = CountLocation("b", "east", "through", ("b", "y"))
        c = CountLocation("c", "east", "through", ("c", "z"))

        ebike, ebike_fallbacks = _filter_profile_candidates(
            routes, "electric_bicycle", (a, b), self.policy
        )
        self.assertEqual(
            {route.route_id for route in ebike},
            {"local_a_short", "local_c", "local_b_medium"},
        )
        self.assertIn(("b", "y"), ebike_fallbacks)

        passenger, _ = _filter_profile_candidates(
            routes, "passenger", (a, b), self.policy
        )
        self.assertEqual(
            {route.route_id for route in passenger},
            {"local_a_short", "local_b_medium", "local_c", "pair_near", "pair_long"},
        )

        bus, bus_fallbacks = _filter_profile_candidates(
            routes, "bus", (a, b, c), self.policy
        )
        self.assertIn("pair_long", {route.route_id for route in bus})
        self.assertIn("local_c", {route.route_id for route in bus})
        self.assertIn(("c", "z"), bus_fallbacks)

        pair_only = CandidateRoute(
            "pair_d_short", "pair", ("d", "w"), 2000.0
        )
        d = CountLocation("d", "east", "through", ("d", "w"))
        ebike_last_resort, ebike_last_resort_fallbacks = (
            _filter_profile_candidates(
                (pair_only,), "electric_bicycle", (d,), self.policy
            )
        )
        self.assertEqual(ebike_last_resort, (pair_only,))
        self.assertIn(("d", "w"), ebike_last_resort_fallbacks)
        bus_short_pair, bus_short_pair_fallbacks = _filter_profile_candidates(
            (pair_only,), "bus", (d,), self.policy
        )
        self.assertEqual(bus_short_pair, (pair_only,))
        self.assertIn(("d", "w"), bus_short_pair_fallbacks)

    def test_share_calibration_is_deterministic_and_guards_zero_actual(self):
        desired = {"passenger": 0.8, "bus": 0.1, "electric_bicycle": 0.05, "truck": 0.05}
        actual = {"passenger": 0.9, "bus": 0.1, "electric_bicycle": 0.0, "truck": 0.0}
        first = _calibrate_vehicle_shares(desired, desired, actual)
        second = _calibrate_vehicle_shares(desired, desired, actual)
        self.assertEqual(first, second)
        self.assertTrue(math.isclose(sum(first.values()), 1.0))
        self.assertGreater(first["electric_bicycle"], 0.0)
        self.assertGreater(first["truck"], 0.0)

    def test_attempt_score_uses_policy_order_before_seed(self):
        def attempt(violations, deviation, seconds, vehicles, seed, allocation_round):
            return {
                "seed": seed,
                "report": {
                    "sampled_vehicle_count": vehicles,
                    "allocation_round": allocation_round,
                    "route_policy": {
                        "violation_count": violations,
                        "normalized_total_violation": deviation,
                        "load": {"estimated_freeflow_vehicle_seconds": seconds},
                    },
                },
            }

        better = attempt(1, 0.2, 100, 10, 46, 1)
        worse = attempt(2, 0.0, 1, 1, 42, 0)
        self.assertLess(_attempt_policy_score(better), _attempt_policy_score(worse))

    def test_policy_report_contains_fleet_distance_load_and_fallback_details(self):
        route_lengths = {
            "p0": 4000.0,
            "p1": 4000.0,
            "e0": 1000.0,
            "e1": 1000.0,
            "b0": 10000.0,
            "b1": 10000.0,
            "t0": 12000.0,
            "t1": 12000.0,
        }
        metadata = _metadata(route_lengths)
        flows = (
            SampledFlow("p", "official_passenger", 0, 7200, 80, ("p0", "p1")),
            SampledFlow("e", "official_electric_bicycle", 0, 7200, 5, ("e0", "e1")),
            SampledFlow("b", "official_bus", 0, 7200, 10, ("b0", "b1")),
            SampledFlow("t", "official_truck", 0, 7200, 5, ("t0", "t1")),
        )
        locations = {
            flow.edges: CountLocation(flow.flow_id, "east", "through", flow.edges)
            for flow in flows
        }
        period = DemandPeriod(
            "morning_peak", "morning", "program", 0, 7200, (), {}, {}, {}
        )
        compiled = CompiledGlobalDemand(
            periods={"morning_peak": period},
            program_ids={},
            locations=locations,
            targets={},
            cell_targets={},
            cell_paths={},
        )
        pools = {
            profile_id: CandidatePool(
                Path(f"{profile_id}.xml"),
                {("b0", "b1"): ("required_count_path:test",)}
                if profile_id == "bus"
                else {},
                1,
            )
            for profile_id in self.profiles
        }
        desired = {"passenger": 0.8, "electric_bicycle": 0.05, "bus": 0.1, "truck": 0.05}
        report = _route_policy_report(
            "morning_peak",
            period,
            flows,
            self.profiles,
            desired,
            compiled,
            metadata,
            self.policy,
            pools,
        )
        self.assertTrue(report["passed"])
        self.assertEqual(report["overall"]["long_route_share"], 0.15)
        self.assertEqual(report["profiles"]["bus"]["long_route_share"], 1.0)
        self.assertEqual(report["profiles"]["electric_bicycle"]["long_route_share"], 0.0)
        self.assertEqual(report["profiles"]["bus"]["fallback_vehicle_count"], 10)
        self.assertEqual(
            report["candidate_fallbacks"]["bus"][0]["selected_vehicle_count"],
            10,
        )
        self.assertGreater(
            report["load"]["estimated_freeflow_vehicle_seconds"], 0.0
        )
        self.assertEqual(report["violation_count"], 0)

    def test_policy_parser_rejects_missing_mode_distance_and_bad_boundaries(self):
        raw = json.loads(POLICY_PATH.read_text(encoding="utf-8"))
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "policy.json"
            missing = json.loads(json.dumps(raw))
            del missing["profiles"]["bus"]["minimum_pair_distance_km"]
            path.write_text(json.dumps(missing), encoding="utf-8")
            with self.assertRaisesRegex(
                TrafficGenerationPolicyError, "minimum_pair_distance_km"
            ):
                load_traffic_generation_policy(path)

            bad_boundaries = json.loads(json.dumps(raw))
            bad_boundaries["distance_classes"]["short_max_km"] = 10
            path.write_text(json.dumps(bad_boundaries), encoding="utf-8")
            with self.assertRaisesRegex(
                TrafficGenerationPolicyError, "smaller than long_min_km"
            ):
                load_traffic_generation_policy(path)


if __name__ == "__main__":
    unittest.main()
