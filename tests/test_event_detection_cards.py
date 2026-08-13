import unittest

from algorithms.event_detection.cards import VisualCauseEvidence, build_event_cards


class EventDetectionCardTests(unittest.TestCase):
    def test_consecutive_detections_are_merged_into_cards(self):
        rows = [
            self.row(0, "normal"),
            self.row(10, "lane_blocked", confidence="0.6", reason="mean_speed_low"),
            self.row(15, "lane_blocked", confidence="0.8", reason="halting_count_high"),
            self.row(20, "normal"),
            self.row(30, "speed_restriction", confidence="0.7", reason="speed_restriction_low_speed_with_flow"),
            self.row(35, "speed_restriction", confidence="0.9", reason="speed_restriction_low_speed_with_flow"),
        ]

        cards = build_event_cards(rows, max_gap_seconds=10)

        self.assertEqual(len(cards), 2)
        self.assertEqual(cards[0].event_type, "lane_blocked")
        self.assertEqual(cards[0].traffic_state, "localized_blockage")
        self.assertEqual(cards[0].cause, "unknown")
        self.assertEqual(cards[0].cause_confidence, 0.0)
        self.assertEqual(cards[0].status, "ended")
        self.assertEqual(cards[0].start_seconds, 10.0)
        self.assertEqual(cards[0].end_seconds, 25.0)
        self.assertEqual(cards[0].duration_seconds, 15.0)
        self.assertEqual(cards[0].confidence, 0.7)
        self.assertIn("平均速度偏低", cards[0].evidence)
        self.assertIn("停车车辆数偏高", cards[0].evidence)
        self.assertEqual(cards[1].event_type, "speed_restriction")
        self.assertEqual(cards[1].traffic_state, "capacity_drop")
        self.assertEqual(cards[1].cause, "capacity_restriction")
        self.assertEqual(cards[1].status, "active")
        self.assertIsNone(cards[1].end_seconds)
        self.assertEqual(cards[1].duration_seconds, 5.0)

    def test_default_gap_bridges_signal_cycle_gaps(self):
        rows = [
            self.row(300, "speed_restriction", confidence="0.8", reason="speed_restriction_cusum_threshold"),
            self.row(360, "normal"),
            self.row(420, "normal"),
            self.row(455, "speed_restriction", confidence="0.9", reason="speed_restriction_low_speed_with_flow"),
        ]

        cards = build_event_cards(rows)

        self.assertEqual(len(cards), 1)
        self.assertEqual(cards[0].event_type, "speed_restriction")
        self.assertEqual(cards[0].start_seconds, 300.0)
        self.assertEqual(cards[0].duration_seconds, 155.0)
        self.assertIn("限速异常分数持续累积达到报警阈值", cards[0].evidence)
        self.assertIn("车辆仍在通行，但速度长期偏低", cards[0].evidence)

    def test_signal_state_reasons_are_not_card_evidence(self):
        rows = [
            self.row(300, "lane_blocked", confidence="0.8", reason="closure_cusum_threshold"),
            self.row(305, "lane_blocked", confidence="0.7", reason="no_lane_green"),
            self.row(310, "lane_blocked", confidence="0.7", reason="green_startup_loss"),
        ]

        cards = build_event_cards(rows)

        self.assertEqual(len(cards), 1)
        self.assertIn("异常分数持续累积达到报警阈值", cards[0].evidence)
        self.assertNotIn("no_lane_green", cards[0].evidence)
        self.assertNotIn("green_startup_loss", cards[0].evidence)

    def test_spillback_rows_on_same_edge_are_merged(self):
        rows = [
            self.row(10, "spillback", lane_id="lane_0", edge_id="edge_up"),
            self.row(10, "spillback", lane_id="lane_1", edge_id="edge_up"),
            self.row(15, "spillback", lane_id="lane_0", edge_id="edge_up"),
            self.row(15, "spillback", lane_id="lane_1", edge_id="edge_up"),
        ]

        cards = build_event_cards(rows)

        self.assertEqual(len(cards), 1)
        self.assertEqual(cards[0].event_type, "spillback")
        self.assertEqual(cards[0].traffic_state, "spillback")
        self.assertEqual(cards[0].cause, "downstream_congestion")
        self.assertEqual(cards[0].edge_id, "edge_up")
        self.assertEqual(cards[0].lane_ids, ("lane_0", "lane_1"))

    def test_localized_blockage_rows_on_same_edge_are_merged(self):
        rows = [
            self.row(10, "lane_blocked", lane_id="lane_0", edge_id="edge_up"),
            self.row(10, "lane_blocked", lane_id="lane_1", edge_id="edge_up"),
            self.row(15, "lane_blocked", lane_id="lane_0", edge_id="edge_up"),
            self.row(15, "lane_blocked", lane_id="lane_1", edge_id="edge_up"),
        ]

        cards = build_event_cards(rows)

        self.assertEqual(len(cards), 1)
        self.assertEqual(cards[0].traffic_state, "localized_blockage")
        self.assertEqual(cards[0].edge_id, "edge_up")
        self.assertEqual(cards[0].lane_ids, ("lane_0", "lane_1"))

    def test_visual_evidence_updates_card_cause(self):
        rows = [
            self.row(10, "lane_blocked", confidence="0.8", reason="closure_cusum_threshold"),
        ]
        evidence = [
            VisualCauseEvidence(
                intersection_id="demo",
                lane_ids=("lane_0",),
                elapsed_seconds=10,
                cause="construction_or_lane_closure",
                confidence=0.93,
                source="carla_stub",
            )
        ]

        cards = build_event_cards(rows, visual_evidence=evidence)

        self.assertEqual(cards[0].cause, "construction_or_lane_closure")
        self.assertEqual(cards[0].cause_confidence, 0.93)
        self.assertGreaterEqual(cards[0].confidence, 0.93)

    def row(
        self,
        elapsed,
        event_type,
        confidence="0",
        reason="normal",
        lane_id="lane_0",
        edge_id="",
    ):
        return {
            "session_id": "s1",
            "elapsed_seconds": str(elapsed),
            "intersection_id": "demo",
            "lane_id": lane_id,
            "edge_id": edge_id,
            "event_type": event_type,
            "confidence": confidence,
            "reason": reason,
        }


if __name__ == "__main__":
    unittest.main()
