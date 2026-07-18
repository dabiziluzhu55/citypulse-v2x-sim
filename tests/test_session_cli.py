import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from simulation.sumo.events import AccidentEvent, LaneClosureEvent, SpeedLimitEvent
from simulation.sumo.run import _load_events, _parse_origins, parse_args


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

    def test_repeated_origins_are_grouped_by_intersection(self):
        self.assertEqual(
            _parse_origins(["demo_2:west", "demo_2:north", "demo_3:south"]),
            {"demo_2": ("west", "north"), "demo_3": ("south",)},
        )
        with self.assertRaisesRegex(ValueError, "intersection:approach"):
            _parse_origins(["west"])

    def test_event_file_loads_all_supported_types(self):
        payload = {
            "events": [
                {"event_type": "lane_closure", "event_id": "a", "start_seconds": 1, "end_seconds": 2, "lane_ids": ["edge_0"]},
                {"event_type": "speed_limit", "event_id": "b", "start_seconds": 2, "end_seconds": 3, "lane_ids": ["edge_0"], "max_speed": 5},
                {"event_type": "accident", "event_id": "c", "start_seconds": 3, "end_seconds": 4, "lane_id": "edge_0", "position_ratio": 0.5},
            ]
        }
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "events.json"
            path.write_text(json.dumps(payload), encoding="utf-8")
            events = _load_events(path)
        self.assertIsInstance(events[0], LaneClosureEvent)
        self.assertIsInstance(events[1], SpeedLimitEvent)
        self.assertIsInstance(events[2], AccidentEvent)


if __name__ == "__main__":
    unittest.main()
