import unittest

from algorithms.event_detection.rules import RuleConfig, detect_rows, detect_states
from algorithms.event_detection.state import (
    realtime_json_to_intersection_states,
    rows_to_intersection_states,
)


class FakeGreenResolver:
    def lane_has_green(self, row, current_phase, stage):
        return stage == "GREEN" and row.get("lane_id") == "lane_0"


class FakeTopologyResolver(FakeGreenResolver):
    def downstream_lane_ids(self, intersection_id, lane_id):
        if intersection_id == "demo" and lane_id == "lane_0":
            return ("lane_2",)
        return ()


class EventDetectionStateTests(unittest.TestCase):
    def test_csv_rows_are_grouped_into_intersection_state(self):
        rows = [
            {
                "session_id": "s1",
                "sequence": "10",
                "elapsed_seconds": "5",
                "official_time": "07:00:05",
                "intersection_id": "demo",
                "current_phase": "1",
                "pending_phase": "",
                "stage": "GREEN",
                "stage_elapsed": "12",
                "lane_id": "lane_1",
                "vehicle_count": "2",
                "halting_count": "1",
                "mean_speed": "0.5",
                "waiting_time": "3.0",
                "occupancy": "20",
            },
            {
                "session_id": "s1",
                "sequence": "10",
                "elapsed_seconds": "5",
                "official_time": "07:00:05",
                "intersection_id": "demo",
                "current_phase": "1",
                "pending_phase": "",
                "stage": "GREEN",
                "stage_elapsed": "12",
                "lane_id": "lane_0",
                "vehicle_count": "4",
                "halting_count": "3",
                "mean_speed": "0.2",
                "waiting_time": "6.0",
                "occupancy": "30",
            },
        ]

        states = rows_to_intersection_states(rows, resolver=FakeGreenResolver())

        self.assertEqual(len(states), 1)
        self.assertEqual(states[0].intersection_id, "demo")
        self.assertEqual(states[0].current_phase, 1)
        self.assertEqual(states[0].stage, "GREEN")
        self.assertEqual([lane.lane_id for lane in states[0].lanes], ["lane_0", "lane_1"])
        self.assertTrue(states[0].lanes[0].lane_has_green)
        self.assertFalse(states[0].lanes[1].lane_has_green)
        self.assertIsNone(states[0].lanes[0].queue_length_m)
        self.assertIsNone(states[0].lanes[0].current_allowed_speed_mps)

    def test_csv_rows_can_use_optional_extended_fields(self):
        rows = [
            {
                "session_id": "s1",
                "sequence": "10",
                "elapsed_seconds": "5",
                "official_time": "07:00:05",
                "intersection_id": "demo",
                "current_phase": "1",
                "pending_phase": "",
                "stage": "GREEN",
                "stage_elapsed": "12",
                "lane_id": "lane_0",
                "edge_id": "edge_0",
                "approach_id": "west_in",
                "movement": "straight",
                "lane_has_green": "true",
                "signal_state": "G",
                "vehicle_count": "4",
                "halting_count": "3",
                "mean_speed": "0.2",
                "waiting_time": "6.0",
                "occupancy": "30",
                "queue_length_m": "42.5",
                "current_allowed_speed_mps": "7.0",
                "downstream_lane_ids": "lane_2;lane_3",
            },
        ]

        states = rows_to_intersection_states(rows, resolver=FakeGreenResolver())

        lane = states[0].lanes[0]
        self.assertEqual(lane.edge_id, "edge_0")
        self.assertEqual(lane.approach_id, "west_in")
        self.assertEqual(lane.movement, "straight")
        self.assertEqual(lane.signal_state, "G")
        self.assertEqual(lane.queue_length_m, 42.5)
        self.assertEqual(lane.current_allowed_speed_mps, 7.0)
        self.assertEqual(lane.downstream_lane_ids, ("lane_2", "lane_3"))

    def test_csv_rows_can_fill_downstream_lanes_from_resolver(self):
        rows = [
            {
                "session_id": "s1",
                "sequence": "10",
                "elapsed_seconds": "5",
                "official_time": "07:00:05",
                "intersection_id": "demo",
                "current_phase": "1",
                "pending_phase": "",
                "stage": "GREEN",
                "stage_elapsed": "12",
                "lane_id": "lane_0",
                "vehicle_count": "4",
                "halting_count": "3",
                "mean_speed": "0.2",
                "waiting_time": "6.0",
                "occupancy": "30",
            },
        ]

        states = rows_to_intersection_states(rows, resolver=FakeTopologyResolver())

        self.assertEqual(states[0].lanes[0].downstream_lane_ids, ("lane_2",))

    def test_realtime_json_is_converted_to_intersection_state(self):
        payload = {
            "protocol_version": "2.0",
            "episode_id": "episode-1",
            "step_id": 12,
            "simulation_time": 60.0,
            "intersections": {
                "demo": {
                    "current_phase": 1,
                    "pending_phase": None,
                    "stage": "GREEN",
                    "stage_elapsed": 17.0,
                    "lanes": {
                        "lane_0": {
                            "edge_id": "edge_0",
                            "approach_id": "west_in",
                            "movement": "straight",
                            "lane_has_green": True,
                            "signal_state": "G",
                            "vehicle_count": 8,
                            "halting_count": 5,
                            "mean_speed": 3.2,
                            "waiting_time": 41.5,
                            "occupancy": 27.0,
                            "queue_length_m": 95.0,
                            "current_allowed_speed_mps": 13.9,
                            "downstream_lane_ids": ["lane_2"],
                        }
                    },
                }
            },
        }

        states = realtime_json_to_intersection_states(payload, resolver=FakeGreenResolver())

        self.assertEqual(len(states), 1)
        state = states[0]
        self.assertEqual(state.source, "realtime_json")
        self.assertEqual(state.session_id, "episode-1")
        self.assertEqual(state.sequence, 12)
        self.assertEqual(state.elapsed_seconds, 60.0)
        self.assertEqual(state.intersection_id, "demo")
        self.assertEqual(state.current_phase, 1)
        lane = state.lanes[0]
        self.assertTrue(lane.lane_has_green)
        self.assertEqual(lane.signal_state, "G")
        self.assertEqual(lane.queue_length_m, 95.0)
        self.assertEqual(lane.current_allowed_speed_mps, 13.9)
        self.assertEqual(lane.downstream_lane_ids, ("lane_2",))

    def test_realtime_json_falls_back_to_resolver_when_lane_green_is_missing(self):
        payload = {
            "episode_id": "episode-1",
            "step_id": 1,
            "simulation_time": 5.0,
            "intersections": {
                "demo": {
                    "current_phase": 1,
                    "stage": "GREEN",
                    "stage_elapsed": 5.0,
                    "lanes": {
                        "lane_0": {
                            "vehicle_count": 1,
                            "halting_count": 0,
                            "mean_speed": 5.0,
                            "waiting_time": 0.0,
                            "occupancy": 1.0,
                        },
                        "lane_1": {
                            "vehicle_count": 1,
                            "halting_count": 0,
                            "mean_speed": 5.0,
                            "waiting_time": 0.0,
                            "occupancy": 1.0,
                        },
                    },
                }
            },
        }

        states = realtime_json_to_intersection_states(payload, resolver=FakeGreenResolver())

        self.assertTrue(states[0].lanes[0].lane_has_green)
        self.assertFalse(states[0].lanes[1].lane_has_green)

    def test_detect_rows_uses_same_logic_as_detect_states(self):
        rows = []
        for index in range(8):
            rows.append(
                {
                    "session_id": "s1",
                    "sequence": str(index),
                    "elapsed_seconds": str(index * 5),
                    "official_time": "",
                    "intersection_id": "demo",
                    "current_phase": "1",
                    "pending_phase": "",
                    "stage": "GREEN",
                    "stage_elapsed": "15",
                    "lane_id": "lane_0",
                    "vehicle_count": "4",
                    "halting_count": "3",
                    "mean_speed": "0.2",
                    "waiting_time": str(index),
                    "occupancy": "30",
                }
            )

        resolver = FakeGreenResolver()
        config = RuleConfig(consecutive_points=2)
        states = rows_to_intersection_states(rows, resolver=resolver)

        from_rows = detect_rows(rows, resolver=resolver, config=config)
        from_states = detect_states(states, config=config)

        self.assertEqual(from_rows, from_states)
        self.assertTrue(any(row.event_type != "normal" for row in from_states))
        self.assertIn("lane_blocked", {row.event_type for row in from_states})

    def test_empty_lane_closure_heuristic_is_opt_in(self):
        rows = []
        for index in range(6):
            target_count = "4" if index == 0 else "0"
            rows.extend(
                [
                    {
                        "session_id": "s1",
                        "sequence": str(index),
                        "elapsed_seconds": str(index * 5),
                        "official_time": "",
                        "intersection_id": "demo",
                        "current_phase": "1",
                        "pending_phase": "",
                        "stage": "GREEN",
                        "stage_elapsed": "20",
                        "lane_id": "lane_0",
                        "vehicle_count": target_count,
                        "halting_count": "0",
                        "mean_speed": "0",
                        "waiting_time": "0",
                        "occupancy": "0",
                    },
                    {
                        "session_id": "s1",
                        "sequence": str(index),
                        "elapsed_seconds": str(index * 5),
                        "official_time": "",
                        "intersection_id": "demo",
                        "current_phase": "1",
                        "pending_phase": "",
                        "stage": "GREEN",
                        "stage_elapsed": "20",
                        "lane_id": "lane_1",
                        "vehicle_count": "5",
                        "halting_count": "0",
                        "mean_speed": "13",
                        "waiting_time": "0",
                        "occupancy": "0.1",
                    },
                ]
            )

        resolver = FakeGreenResolver()
        default_rows = detect_rows(
            rows,
            resolver=resolver,
            config=RuleConfig(use_cusum=True, closure_cusum_threshold=1.0),
        )
        opt_in_rows = detect_rows(
            rows,
            resolver=resolver,
            config=RuleConfig(
                use_cusum=True,
                closure_cusum_threshold=1.0,
                enable_empty_lane_closure=True,
            ),
        )

        self.assertNotIn("lane_blocked", {row.event_type for row in default_rows})
        self.assertIn("lane_blocked", {row.event_type for row in opt_in_rows})

    def test_spillback_requires_downstream_blockage(self):
        rows = []
        for index in range(6):
            rows.extend(
                [
                    {
                        "session_id": "s1",
                        "sequence": str(index),
                        "elapsed_seconds": str(index * 5),
                        "official_time": "",
                        "intersection_id": "demo",
                        "current_phase": "1",
                        "pending_phase": "",
                        "stage": "GREEN",
                        "stage_elapsed": "20",
                        "lane_id": "lane_0",
                        "edge_id": "up",
                        "lane_has_green": "true",
                        "vehicle_count": "5",
                        "halting_count": "5",
                        "mean_speed": "0.2",
                        "waiting_time": str(120 + index * 20),
                        "occupancy": "0.3",
                        "downstream_lane_ids": "lane_2;lane_3",
                    },
                    {
                        "session_id": "s1",
                        "sequence": str(index),
                        "elapsed_seconds": str(index * 5),
                        "official_time": "",
                        "intersection_id": "demo",
                        "current_phase": "1",
                        "pending_phase": "",
                        "stage": "GREEN",
                        "stage_elapsed": "20",
                        "lane_id": "lane_2",
                        "edge_id": "down",
                        "lane_has_green": "false",
                        "vehicle_count": "3",
                        "halting_count": "3",
                        "mean_speed": "0.1",
                        "waiting_time": "0",
                        "occupancy": "0.3",
                    },
                    {
                        "session_id": "s1",
                        "sequence": str(index),
                        "elapsed_seconds": str(index * 5),
                        "official_time": "",
                        "intersection_id": "demo",
                        "current_phase": "1",
                        "pending_phase": "",
                        "stage": "GREEN",
                        "stage_elapsed": "20",
                        "lane_id": "lane_3",
                        "edge_id": "down_alt",
                        "lane_has_green": "false",
                        "vehicle_count": "3",
                        "halting_count": "0",
                        "mean_speed": "13",
                        "waiting_time": "0",
                        "occupancy": "0.3",
                    },
                ]
            )

        config = RuleConfig(
            enable_queue_blockage=True,
            use_cusum=True,
            closure_cusum_threshold=1.0,
            queue_blockage_min_vehicle_count=3,
            queue_blockage_min_halting_count=3,
            queue_blockage_max_mean_speed=1.0,
            queue_blockage_min_waiting_delta=5.0,
        )

        blocked_rows = detect_rows(rows, resolver=FakeGreenResolver(), config=config)
        clear_rows = detect_rows(
            [
                {
                    **row,
                    "vehicle_count": "3" if row["lane_id"] == "lane_0" else "3",
                    "halting_count": "0" if row["lane_id"] == "lane_2" else row["halting_count"],
                    "mean_speed": "10" if row["lane_id"] == "lane_2" else row["mean_speed"],
                    "occupancy": "0.02" if row["lane_id"] == "lane_2" else row["occupancy"],
                }
                for row in rows
            ],
            resolver=FakeGreenResolver(),
            config=config,
        )

        self.assertIn("spillback", {row.event_type for row in blocked_rows})
        self.assertNotIn("spillback", {row.event_type for row in clear_rows})

    def test_accident_draft_is_disabled_by_default(self):
        rows = [
            {
                "session_id": "s1",
                "sequence": str(index),
                "elapsed_seconds": str(index * 5),
                "official_time": "",
                "intersection_id": "demo",
                "current_phase": "1",
                "pending_phase": "",
                "stage": "GREEN",
                "stage_elapsed": "20",
                "lane_id": "lane_0",
                "edge_id": "up",
                "lane_has_green": "true",
                "vehicle_count": "7",
                "halting_count": "5",
                "mean_speed": "0.2",
                "waiting_time": str(100 + index * 20),
                "occupancy": "0.1",
                "current_allowed_speed_mps": "0.1",
            }
            for index in range(3)
        ]

        default_rows = detect_rows(
            rows,
            resolver=FakeGreenResolver(),
            config=RuleConfig(consecutive_points=1),
        )
        opt_in_rows = detect_rows(
            rows,
            resolver=FakeGreenResolver(),
            config=RuleConfig(consecutive_points=1, enable_accident=True),
        )

        self.assertNotIn("accident", {row.event_type for row in default_rows})
        self.assertIn("accident", {row.event_type for row in opt_in_rows})

    def test_near_zero_allowed_speed_is_lane_closure_evidence(self):
        rows = [
            {
                "session_id": "s1",
                "sequence": str(index),
                "elapsed_seconds": str(index * 5),
                "official_time": "",
                "intersection_id": "demo",
                "current_phase": "1",
                "pending_phase": "",
                "stage": "GREEN",
                "stage_elapsed": "20",
                "lane_id": "lane_0",
                "edge_id": "up",
                "lane_has_green": "true",
                "vehicle_count": "1",
                "halting_count": "1",
                "mean_speed": "0.1",
                "waiting_time": str(index * 5),
                "occupancy": "0.05",
                "current_allowed_speed_mps": "0.1",
            }
            for index in range(3)
        ]

        detections = detect_rows(
            rows,
            resolver=FakeGreenResolver(),
            config=RuleConfig(),
        )

        self.assertIn("lane_blocked", {row.event_type for row in detections})

    def test_speed_restriction_requires_slow_flow_without_queue(self):
        rows = []
        for index in range(6):
            rows.append(
                {
                    "session_id": "s1",
                    "sequence": str(index),
                    "elapsed_seconds": str(index * 5),
                    "official_time": "",
                    "intersection_id": "demo",
                    "current_phase": "1",
                    "pending_phase": "",
                    "stage": "GREEN",
                    "stage_elapsed": "20",
                    "lane_id": "lane_0",
                    "edge_id": "up",
                    "lane_has_green": "true",
                    "vehicle_count": "4",
                    "halting_count": "1",
                    "mean_speed": "1.5",
                    "waiting_time": str(index * 2),
                    "occupancy": "0.08",
                }
            )

        config = RuleConfig(
            enable_speed_restriction=True,
            use_cusum=True,
            closure_cusum_threshold=1.0,
        )
        slow_flow_rows = detect_rows(rows, resolver=FakeGreenResolver(), config=config)
        queued_rows = detect_rows(
            [
                {
                    **row,
                    "halting_count": "4",
                }
                for row in rows
            ],
            resolver=FakeGreenResolver(),
            config=config,
        )

        self.assertIn("speed_restriction", {row.event_type for row in slow_flow_rows})
        self.assertNotIn("speed_restriction", {row.event_type for row in queued_rows})


if __name__ == "__main__":
    unittest.main()
