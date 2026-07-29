import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from simulation.sumo.events import (
    AccidentEvent,
    CollisionBlockageEvent,
    LaneClosureEvent,
    QueueSpillbackEvent,
    SpeedLimitEvent,
    StoppedVehicleEvent,
)
from simulation.sumo.run import _load_events, _parse_origins, parse_args
from simulation.sumo.export_snapshots import build_parser as build_snapshot_parser


class SessionCliTests(unittest.TestCase):
    def test_local_transport_and_ai_observer_arguments(self):
        with patch(
            "sys.argv",
            [
                "run.py",
                "--mode",
                "algorithm",
                "--algorithm-transport",
                "local",
                "--algorithm-module",
                "algorithms.local_policy_example",
                "--ai-observer-module",
                "algorithms.ai_observer_example",
                "--ai-frame-interval",
                "0.05",
            ],
        ):
            args = parse_args()
        self.assertEqual(args.algorithm_transport, "local")
        self.assertEqual(args.algorithm_module, "algorithms.local_policy_example")
        self.assertEqual(args.ai_observer_module, "algorithms.ai_observer_example")
        self.assertEqual(args.ai_frame_interval, 0.05)
        self.assertIsNone(args.intersection)

    def test_repeated_origins_are_grouped_by_intersection(self):
        self.assertEqual(
            _parse_origins(["demo_2:west", "demo_2:north", "demo_3:south"]),
            {"demo_2": ("west", "north"), "demo_3": ("south",)},
        )
        with self.assertRaisesRegex(ValueError, "intersection:approach"):
            _parse_origins(["west"])

    def test_snapshot_export_can_use_isolated_artifact_paths(self):
        args = build_snapshot_parser().parse_args(
            [
                "--generated-dir",
                "runs/generated",
                "--session-root",
                "runs/sessions",
            ]
        )
        self.assertEqual(args.generated_dir, Path("runs/generated"))
        self.assertEqual(args.session_root, Path("runs/sessions"))

    def test_event_file_loads_all_supported_types(self):
        payload = {
            "events": [
                {
                    "event_type": "lane_closure",
                    "event_id": "a",
                    "start_seconds": 1,
                    "end_seconds": 2,
                    "lane_ids": ["edge_0"],
                    "max_speed": 0.1,
                },
                {"event_type": "speed_limit", "event_id": "b", "start_seconds": 2, "end_seconds": 3, "lane_ids": ["edge_0"], "max_speed": 5},
                {"event_type": "speed_restriction", "event_id": "c", "start_seconds": 3, "end_seconds": 4, "lane_ids": ["edge_0"], "max_speed": 2},
                {"event_type": "accident", "event_id": "d", "start_seconds": 4, "end_seconds": 5, "lane_id": "edge_0", "position_ratio": 0.5, "max_speed": 0.1},
                {"event_type": "stopped_vehicle", "event_id": "e", "start_seconds": 5, "end_seconds": 6, "lane_id": "edge_0", "position_ratio": 0.25},
                {"event_type": "collision_blockage", "event_id": "f", "start_seconds": 6, "end_seconds": 7, "lane_ids": ["edge_0", "edge_1"], "position_ratio": 0.5},
                {"event_type": "queue_spillback", "event_id": "g", "start_seconds": 7, "end_seconds": 8, "lane_ids": ["edge_0"], "blocked_lane_ids": ["edge_1"], "max_speed": 0.1, "position_ratio": 0.8},
                {"event_type": "queue_blockage", "event_id": "h", "start_seconds": 8, "end_seconds": 9, "lane_ids": ["edge_0"], "blocked_lane_ids": ["edge_1"]},
            ]
        }
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "events.json"
            path.write_text(json.dumps(payload), encoding="utf-8")
            events = _load_events(path)
        self.assertIsInstance(events[0], LaneClosureEvent)
        self.assertIsInstance(events[1], SpeedLimitEvent)
        self.assertIsInstance(events[2], SpeedLimitEvent)
        self.assertIsInstance(events[3], AccidentEvent)
        self.assertIsInstance(events[4], StoppedVehicleEvent)
        self.assertIsInstance(events[5], CollisionBlockageEvent)
        self.assertIsInstance(events[6], QueueSpillbackEvent)
        self.assertIsInstance(events[7], QueueSpillbackEvent)
        self.assertEqual(events[0].max_speed, 0.1)
        self.assertEqual(events[3].max_speed, 0.1)
        self.assertEqual(events[6].max_speed, 0.1)
        self.assertIsNone(events[7].max_speed)


if __name__ == "__main__":
    unittest.main()
