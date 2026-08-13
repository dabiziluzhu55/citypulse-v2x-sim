import unittest

from algorithms.event_detection.cards import build_event_cards
from algorithms.event_detection.congestion import classify_congestion
from algorithms.event_detection.rules import RuleConfig, detect_rows


class EventDetectionCongestionTests(unittest.TestCase):
    def test_shared_classifier_matches_backend_levels(self):
        self.assertEqual(
            classify_congestion(
                vehicle_count=5,
                halting_count=1,
                mean_speed=7.0,
                occupancy=10.0,
            )[0],
            "slow",
        )
        self.assertEqual(
            classify_congestion(
                vehicle_count=5,
                halting_count=2,
                mean_speed=3.0,
                occupancy=20.0,
            )[0],
            "congested",
        )
        self.assertEqual(
            classify_congestion(
                vehicle_count=5,
                halting_count=2,
                mean_speed=1.0,
                occupancy=35.0,
            )[0],
            "severe",
        )
        self.assertEqual(
            classify_congestion(
                vehicle_count=0,
                halting_count=0,
                mean_speed=0.0,
                occupancy=100.0,
            )[0],
            "free",
        )

    def test_yellow_route_becomes_blockage_event_even_without_green(self):
        rows = [
            self.row(
                elapsed=index * 5,
                vehicle_count=5,
                halting_count=1,
                mean_speed=7.0,
                occupancy=10.0,
                lane_has_green="false",
                stage="RED",
            )
            for index in range(3)
        ]

        detections = detect_rows(
            rows,
            resolver=_NoGreenResolver(),
            config=RuleConfig(use_cusum=False, consecutive_points=3),
        )
        active = detections[-1]

        self.assertEqual(active.event_type, "lane_blocked")
        self.assertEqual(active.traffic_state, "localized_blockage")
        self.assertEqual(active.reason, "traffic_style_slow")

        cards = build_event_cards(detections)
        self.assertEqual(len(cards), 1)
        self.assertEqual(cards[0].traffic_state, "localized_blockage")
        self.assertIn("路线拥堵等级为黄色（slow）", cards[0].evidence)

    def test_yellow_route_is_confirmed_with_cusum_enabled(self):
        rows = [
            self.row(
                elapsed=index * 5,
                vehicle_count=5,
                halting_count=1,
                mean_speed=7.0,
                occupancy=10.0,
                lane_has_green="false",
                stage="RED",
            )
            for index in range(3)
        ]

        detections = detect_rows(
            rows,
            resolver=_NoGreenResolver(),
            config=RuleConfig(use_cusum=True, consecutive_points=3),
        )

        self.assertEqual(detections[-1].event_type, "lane_blocked")
        self.assertEqual(detections[-1].traffic_state, "localized_blockage")

    def test_free_route_does_not_create_congestion_event(self):
        rows = [
            self.row(
                elapsed=index * 5,
                vehicle_count=5,
                halting_count=1,
                mean_speed=10.0,
                occupancy=5.0,
                lane_has_green="false",
                stage="RED",
            )
            for index in range(3)
        ]

        detections = detect_rows(
            rows,
            resolver=_NoGreenResolver(),
            config=RuleConfig(use_cusum=False, consecutive_points=3),
        )

        self.assertNotIn("lane_blocked", {row.event_type for row in detections})

    @staticmethod
    def row(
        *,
        elapsed: int,
        vehicle_count: int,
        halting_count: int,
        mean_speed: float,
        occupancy: float,
        lane_has_green: str,
        stage: str,
    ) -> dict[str, str]:
        return {
            "session_id": "s1",
            "sequence": str(elapsed // 5),
            "elapsed_seconds": str(elapsed),
            "official_time": "",
            "intersection_id": "demo",
            "current_phase": "0",
            "pending_phase": "",
            "stage": stage,
            "stage_elapsed": "20",
            "lane_id": "edge_0",
            "edge_id": "edge",
            "lane_has_green": lane_has_green,
            "vehicle_count": str(vehicle_count),
            "halting_count": str(halting_count),
            "mean_speed": str(mean_speed),
            "waiting_time": "0",
            "occupancy": str(occupancy),
        }


class _NoGreenResolver:
    def lane_has_green(self, row, current_phase, stage):
        return False


if __name__ == "__main__":
    unittest.main()
