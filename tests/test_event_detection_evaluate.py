import unittest

from algorithms.event_detection.evaluate import EventLabel, evaluate, normalize_event_type


class EventDetectionEvaluateTests(unittest.TestCase):
    def test_normalize_event_type_maps_injection_labels_to_coarse_labels(self):
        self.assertEqual(normalize_event_type("lane_closure"), "lane_blocked")
        self.assertEqual(normalize_event_type("stopped_vehicle"), "lane_blocked")
        self.assertEqual(normalize_event_type("collision_blockage"), "lane_blocked")
        self.assertEqual(normalize_event_type("queue_blockage"), "spillback")
        self.assertEqual(normalize_event_type("speed_limit"), "speed_restriction")

    def test_evaluate_counts_detection_and_type_accuracy(self):
        events = [
            EventLabel(
                event_id="e1",
                raw_event_type="lane_closure",
                event_type="lane_blocked",
                start_seconds=10,
                end_seconds=30,
                lane_ids=("lane_0",),
            ),
            EventLabel(
                event_id="e2",
                raw_event_type="speed_restriction",
                event_type="speed_restriction",
                start_seconds=40,
                end_seconds=60,
                lane_ids=("lane_1",),
            ),
        ]
        detections = [
            self.row(10, "lane_0", "lane_blocked"),
            self.row(20, "lane_0", "lane_blocked"),
            self.row(40, "lane_1", "lane_blocked", traffic_state="capacity_drop"),
            self.row(50, "lane_1", "speed_restriction"),
            self.row(70, "lane_2", "normal"),
        ]
        cards = [
            self.card(10, 30, "lane_0", "lane_blocked"),
            self.card(40, 60, "lane_1", "lane_blocked", traffic_state="capacity_drop"),
        ]

        metrics = evaluate(detections, events, cards)

        self.assertEqual(metrics.true_positive, 4)
        self.assertEqual(metrics.type_correct_samples, 3)
        self.assertEqual(metrics.type_incorrect_samples, 1)
        self.assertEqual(metrics.lane_blocked_tp, 2)
        self.assertEqual(metrics.speed_restriction_tp, 1)
        self.assertEqual(metrics.detected_event_count, 2)
        self.assertEqual(metrics.event_type_correct_count, 1)
        self.assertEqual(metrics.traffic_state_correct_samples, 4)
        self.assertEqual(metrics.event_traffic_state_correct_count, 2)
        self.assertEqual(metrics.card_detected_event_count, 2)
        self.assertEqual(metrics.card_type_correct_event_count, 1)
        self.assertEqual(metrics.card_traffic_state_correct_event_count, 2)

    def row(self, elapsed, lane_id, event_type, traffic_state=""):
        return {
            "elapsed_seconds": str(elapsed),
            "lane_id": lane_id,
            "event_type": event_type,
            "traffic_state": traffic_state,
        }

    def card(self, start, end, lane_id, event_type, traffic_state=""):
        return {
            "start_seconds": start,
            "end_seconds": end,
            "intersection_id": "demo",
            "lane_ids": [lane_id],
            "event_type": event_type,
            "traffic_state": traffic_state,
        }


if __name__ == "__main__":
    unittest.main()
