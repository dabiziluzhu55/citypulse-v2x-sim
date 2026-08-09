# intelligence_runtime：拥堵口径、无答案泄露、长事件ID稳定、预测降级

from __future__ import annotations

import unittest
from dataclasses import dataclass, field
from pathlib import Path
from typing import Mapping

from backend.app.services.intelligence_runtime import (
    IntelligenceHub,
    instant_congestion_level,
    occupancy_to_pct,
)
from backend.app.services.prediction_runtime import PredictionRuntime


@dataclass
class Lane:
    vehicle_count: int
    halting_count: int
    mean_speed: float
    waiting_time: float
    occupancy: float
    edge_id: str = "E1"
    approach_id: str | None = "east"
    downstream_lane_ids: tuple[str, ...] = ("E2_0",)
    lane_has_green: bool | None = True
    signal_state: str | None = "G"
    current_allowed_speed_mps: float | None = 13.0


@dataclass
class Intersection:
    current_phase: int = 0
    pending_phase: int | None = None
    stage: str = "G"
    stage_elapsed: float = 20.0
    lanes: Mapping[str, Lane] = field(default_factory=dict)


@dataclass
class Snapshot:
    session_id: str
    sequence: int
    elapsed_seconds: float
    official_time: str
    intersections: Mapping[str, Intersection]


def _hub() -> IntelligenceHub:
    return IntelligenceHub(
        tls_manifest_path=Path("/nonexistent/tls_manifest.json"),
        sample_seconds=5.0,
        history_frames=12,
        horizon_seconds=60.0,
        lane_lonlat=lambda _lane: (116.0, 39.0),
        intersection_lonlat=lambda _node: (116.0, 39.0),
        prediction_runtime=PredictionRuntime(None),
    )


class OccupancyAndCongestionTests(unittest.TestCase):
    def test_occupancy_pct_keeps_traci_percent_scale(self):
        self.assertAlmostEqual(occupancy_to_pct(0.42), 0.42)
        self.assertAlmostEqual(occupancy_to_pct(25.0), 25.0)
        self.assertAlmostEqual(occupancy_to_pct(120.0), 100.0)

    def test_light_traffic_stays_free(self):
        level, _ = instant_congestion_level(
            vehicle_count=2,
            halting_count=2,
            mean_speed=0.5,
            occupancy_pct=40.0,
        )
        self.assertEqual(level, "free")
        level, _ = instant_congestion_level(
            vehicle_count=5,
            halting_count=1,
            mean_speed=0.2,
            occupancy_pct=4.0,
        )
        self.assertEqual(level, "free")

    def test_severe_requires_enough_halting(self):
        level, _ = instant_congestion_level(
            vehicle_count=10,
            halting_count=6,
            mean_speed=0.5,
            occupancy_pct=40.0,
        )
        self.assertEqual(level, "severe")

    def test_hysteresis_needs_two_samples_to_upgrade(self):
        hub = _hub()
        light = Snapshot(
            session_id="s1",
            sequence=0,
            elapsed_seconds=0.0,
            official_time="t0",
            intersections={
                "demo_1": Intersection(
                    lanes={
                        "E1_0": Lane(1, 0, 12.0, 0.0, 1.0, edge_id="E1"),
                    }
                )
            },
        )
        jammed = Snapshot(
            session_id="s1",
            sequence=1,
            elapsed_seconds=5.0,
            official_time="t1",
            intersections={
                "demo_1": Intersection(
                    lanes={
                        "E1_0": Lane(10, 8, 0.4, 120.0, 40.0, edge_id="E1"),
                        "E2_0": Lane(4, 4, 0.2, 100.0, 30.0, edge_id="E2", lane_has_green=False, signal_state="r", downstream_lane_ids=()),
                    }
                )
            },
        )
        first = hub.observe(light)
        self.assertEqual(first["traffic_style"]["edges"]["E1"]["level"], "free")
        mid = hub.observe(jammed)
        # 首次升到更严重等级仍保持旧等级
        self.assertEqual(mid["traffic_style"]["edges"]["E1"]["level"], "free")
        jammed.sequence = 2
        jammed.elapsed_seconds = 10.0
        second = hub.observe(jammed)
        self.assertIn(second["traffic_style"]["edges"]["E1"]["level"], {"slow", "congested", "severe"})
        self.assertIn("occupancy_pct", second["traffic_style"]["edges"]["E1"])


class AnswerLeakageTests(unittest.TestCase):
    def test_allowed_speed_alone_does_not_create_detection(self):
        hub = _hub()
        for index in range(20):
            snap = Snapshot(
                session_id="leak",
                sequence=index,
                elapsed_seconds=float(index * 5),
                official_time=f"t{index}",
                intersections={
                    "demo_1": Intersection(
                        lanes={
                            "E1_0": Lane(
                                vehicle_count=2,
                                halting_count=0,
                                mean_speed=10.0,
                                waiting_time=0.0,
                                occupancy=1.0,
                                current_allowed_speed_mps=0.5,
                                downstream_lane_ids=(),
                            )
                        }
                    )
                },
            )
            payload = hub.observe(snap)
        active = [
            card
            for card in payload["event_detection"]["cards"]
            if card["status"] == "active"
        ]
        self.assertEqual(active, [])


class LongEventStabilityTests(unittest.TestCase):
    def test_event_id_and_start_stable_over_300_seconds(self):
        hub = _hub()
        first_id = None
        first_start = None
        last_duration = None
        for index in range(70):  # 350s
            elapsed = float(index * 5)
            wait = 80.0 + elapsed
            snap = Snapshot(
                session_id="long",
                sequence=index,
                elapsed_seconds=elapsed,
                official_time=f"t{index}",
                intersections={
                    "demo_15": Intersection(
                        stage_elapsed=25.0,
                        lanes={
                            "L_up_0": Lane(
                                vehicle_count=10,
                                halting_count=8,
                                mean_speed=0.2,
                                waiting_time=wait,
                                occupancy=35.0,
                                edge_id="L_up",
                                downstream_lane_ids=("L_dn_0",),
                            ),
                            "L_dn_0": Lane(
                                vehicle_count=5,
                                halting_count=5,
                                mean_speed=0.1,
                                waiting_time=200.0,
                                occupancy=30.0,
                                edge_id="L_dn",
                                lane_has_green=False,
                                signal_state="r",
                                downstream_lane_ids=(),
                            ),
                        },
                    )
                },
            )
            payload = hub.observe(snap)
            active = [
                card
                for card in payload["event_detection"]["cards"]
                if card["status"] == "active"
            ]
            if not active:
                continue
            card = active[0]
            if first_id is None:
                first_id = card["event_id"]
                first_start = card["start_seconds"]
            else:
                self.assertEqual(card["event_id"], first_id)
                self.assertEqual(card["start_seconds"], first_start)
                self.assertGreaterEqual(card["duration_seconds"], last_duration or 0.0)
            last_duration = card["duration_seconds"]
        self.assertIsNotNone(first_id)
        self.assertGreaterEqual(last_duration or 0.0, 300.0)


class DisplayLabelTests(unittest.TestCase):
    def test_localized_blockage_display_label(self):
        from backend.app.services.intelligence_runtime import TRAFFIC_STATE_DISPLAY
        self.assertEqual(
            TRAFFIC_STATE_DISPLAY["localized_blockage"][1],
            "疑似局部阻塞",
        )


class PredictionFallbackTests(unittest.TestCase):
    def test_prediction_marks_moving_average_fallback_without_model(self):
        hub = _hub()
        for index in range(4):
            snap = Snapshot(
                session_id="pred",
                sequence=index,
                elapsed_seconds=float(index * 5),
                official_time=f"t{index}",
                intersections={
                    "demo_1": Intersection(
                        lanes={"E1_0": Lane(3, 0, 10.0, 0.0, 2.0, downstream_lane_ids=())}
                    )
                },
            )
            payload = hub.observe(snap)
        prediction = payload["prediction"]
        self.assertEqual(prediction["model"], "moving_average")
        self.assertTrue(prediction["fallback"])
        self.assertTrue(prediction["fallback_reason"])
        self.assertTrue(prediction["ready"])


if __name__ == "__main__":
    unittest.main()
