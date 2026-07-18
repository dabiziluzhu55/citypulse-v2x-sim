import threading
import time
import unittest
from types import SimpleNamespace

from simulation.sumo.ai_observer import LocalAIObserver, SimulationTimeFrameClock
from simulation.sumo.external_policy import HttpAlgorithmClient
from simulation.sumo.local_policy import LocalAlgorithmClient
from simulation.sumo.policy import (
    AIFrameObservation,
    IntersectionMetadata,
    LaneMetadata,
    PhaseMetadata,
    PROTOCOL_VERSION,
    RoadConnectionMetadata,
    SimulationMetadata,
    SimulationObservation,
    TrafficObservation,
)
from simulation.sumo.run import _estimate_queue_length, _lane_signal_details
from simulation.sumo.policy_transport import to_protocol_payload


def metadata():
    return SimulationMetadata(
        protocol_version=PROTOCOL_VERSION,
        episode_id="episode-local",
        period="morning_peak",
        seed=42,
        decision_interval=5.0,
        minimum_green=5.0,
        intersections={},
    )


def observation(step_id=7):
    return SimulationObservation(
        protocol_version=PROTOCOL_VERSION,
        episode_id="episode-local",
        step_id=step_id,
        simulation_time=35.0,
        intersections={},
        traffic=TrafficObservation(0, 0, 0, 0),
    )


def frame(frame_id):
    return AIFrameObservation(
        protocol_version=PROTOCOL_VERSION,
        episode_id="episode-local",
        frame_id=frame_id,
        simulation_time=frame_id / 10,
        intersections={},
        traffic=TrafficObservation(0, 0, 0, 0),
    )


class StubHttpClient(HttpAlgorithmClient):
    def __init__(self, responses):
        super().__init__("http://example.invalid")
        self.responses = responses

    def _post(self, path, payload):
        return self.responses[path]


class LocalTransportTests(unittest.TestCase):
    def test_http_and_local_use_the_same_payload_and_validation(self):
        captured = []

        def initialize(payload):
            captured.append(("initialize", payload))
            return {
                "protocol_version": PROTOCOL_VERSION,
                "episode_id": payload["episode_id"],
                "ready": True,
            }

        def step(payload):
            captured.append(("step", payload))
            return {
                "protocol_version": PROTOCOL_VERSION,
                "episode_id": payload["episode_id"],
                "step_id": payload["step_id"],
                "actions": {
                    "signals": {"demo_2": {"target_phase": 1}},
                    "vehicles": {},
                },
            }

        module = SimpleNamespace(initialize=initialize, step=step, finish=lambda body: None)
        local = LocalAlgorithmClient("test_local", module=module)
        local.initialize(metadata())
        local_decision = local.decide(observation())

        http = StubHttpClient(
            {
                "/initialize": initialize(to_protocol_payload(metadata())),
                "/step": step(to_protocol_payload(observation())),
            }
        )
        http.initialize(metadata())
        http_decision = http.decide(observation())
        self.assertEqual(local_decision, http_decision)
        self.assertEqual(captured[0][1], to_protocol_payload(metadata()))
        self.assertEqual(captured[1][1], to_protocol_payload(observation()))

        for client in (
            LocalAlgorithmClient(
                "bad_local",
                module=SimpleNamespace(
                    initialize=lambda body: {
                        "protocol_version": "1.0",
                        "episode_id": body["episode_id"],
                        "ready": True,
                    },
                    step=step,
                    finish=lambda body: None,
                ),
            ),
            StubHttpClient(
                {"/initialize": {
                    "protocol_version": "1.0",
                    "episode_id": "episode-local",
                    "ready": True,
                }}
            ),
        ):
            with self.assertRaisesRegex(ValueError, "protocol_version 2.0"):
                client.initialize(metadata())

    def test_local_module_contract_and_exceptions_are_explicit(self):
        with self.assertRaisesRegex(TypeError, "callable step"):
            LocalAlgorithmClient(
                "missing_step",
                module=SimpleNamespace(initialize=lambda body: None, finish=lambda body: None),
            )
        client = LocalAlgorithmClient(
            "raising",
            module=SimpleNamespace(
                initialize=lambda body: (_ for _ in ()).throw(ValueError("boom")),
                step=lambda body: None,
                finish=lambda body: None,
            ),
        )
        with self.assertRaisesRegex(RuntimeError, "raising.initialize failed: boom"):
            client.initialize(metadata())

    def test_local_payload_has_json_array_semantics_without_json_encoding(self):
        lane = LaneMetadata(
            lane_id="in_0",
            edge_id="in",
            lane_index=0,
            role="incoming",
            length=100.0,
            max_speed=13.9,
            movements=("through", "left"),
            downstream_lane_ids=("out_0", "turn_0"),
        )
        payload = to_protocol_payload(lane)
        self.assertEqual(payload["movements"], ["through", "left"])
        self.assertIsInstance(payload["movements"], list)
        self.assertEqual(payload["downstream_lane_ids"], ["out_0", "turn_0"])
        phases = to_protocol_payload(
            {
                1: PhaseMetadata(
                    phase_id=1,
                    name="through",
                    movement="through",
                    approaches=("west",),
                    green_seconds=30.0,
                    yellow_seconds=3.0,
                    clearance_seconds=0.0,
                    connection_priorities={},
                )
            }
        )
        self.assertEqual(list(phases), ["1"])


class QueueLengthTests(unittest.TestCase):
    def setUp(self):
        self.lane = LaneMetadata(
            lane_id="in_0",
            edge_id="in",
            lane_index=0,
            role="incoming",
            length=100.0,
            max_speed=13.9,
            length_m=100.0,
            speed_limit_mps=13.9,
        )

    def test_queue_estimate_uses_spatial_count_and_occupancy_bounds(self):
        tracker = SimpleNamespace(
            lane_vehicle_samples=lambda lane_id: (
                (95.0, 0.0, 5.0, 2.5),
                (80.0, 0.1, 5.0, 2.5),
                (50.0, 3.0, 5.0, 2.5),
            ),
            default_vehicle_space=lambda: 7.5,
        )
        value = _estimate_queue_length(
            self.lane,
            halting_count=2,
            occupancy=20.0,
            vehicle_tracker=tracker,
        )
        self.assertEqual(value, 25.0)
        self.assertEqual(
            _estimate_queue_length(
                self.lane,
                halting_count=100,
                occupancy=200.0,
                vehicle_tracker=tracker,
            ),
            100.0,
        )
        self.assertEqual(
            _estimate_queue_length(
                self.lane,
                halting_count=0,
                occupancy=80.0,
                vehicle_tracker=tracker,
            ),
            0.0,
        )


class LaneSignalTests(unittest.TestCase):
    def test_raw_signal_characters_and_green_summary_are_preserved(self):
        connection = RoadConnectionMetadata(
            connection_id="connection_0",
            approach="west",
            movement="through",
            from_lane="in_0",
            to_lane="out_0",
            direction="s",
            tls_id="tls",
            link_index=0,
        )
        intersection = IntersectionMetadata(
            intersection_id="demo_2",
            phase_order=(),
            phases={},
            lanes={},
            incoming_lanes=("in_0",),
            outgoing_lanes=("out_0",),
            connections=(connection,),
            direct_neighbors=(),
        )
        state_holder = {"state": "r"}
        traci = SimpleNamespace(
            trafficlight=SimpleNamespace(
                getRedYellowGreenState=lambda tls_id: state_holder["state"]
            )
        )
        for raw_state, has_green in (
            ("G", True),
            ("g", True),
            ("r", False),
            ("y", False),
        ):
            state_holder["state"] = raw_state
            actual_green, summary, details = _lane_signal_details(
                traci, intersection, "in_0"
            )
            self.assertEqual(actual_green, has_green)
            self.assertEqual(summary, raw_state)
            self.assertEqual(details[0].signal_state, raw_state)

        self.assertEqual(
            _lane_signal_details(traci, intersection, "out_0"),
            (None, None, ()),
        )


class AIObserverTests(unittest.TestCase):
    def test_module_contract_and_finish_exception_are_reported(self):
        with self.assertRaisesRegex(TypeError, "callable on_frame"):
            LocalAIObserver(
                "missing_frame",
                module=SimpleNamespace(
                    initialize=lambda body: None,
                    finish=lambda body: None,
                ),
            )

        observer = LocalAIObserver(
            "bad_finish",
            module=SimpleNamespace(
                initialize=lambda body: None,
                on_frame=lambda body: None,
                finish=lambda body: (_ for _ in ()).throw(
                    ValueError("finish failed")
                ),
            ),
        )
        observer.initialize(metadata())
        with self.assertRaisesRegex(RuntimeError, "bad_finish.finish failed"):
            observer.close({"reason": "completed"}, timeout=1.0)

    def test_frame_clock_uses_simulation_time_and_freezes_at_same_time(self):
        clock = SimulationTimeFrameClock(0.1)
        self.assertIsNone(clock.poll(0.05))
        self.assertEqual(clock.poll(0.1), 0)
        self.assertIsNone(clock.poll(0.1))
        self.assertIsNone(clock.poll(0.15))
        self.assertEqual(clock.poll(0.2), 1)

        step_aligned = SimulationTimeFrameClock(0.05)
        self.assertEqual(
            [step_aligned.poll(value) for value in (0.05, 0.1, 0.15)],
            [0, 1, 2],
        )

    def test_latest_frame_overwrites_pending_frames_and_reports_gap(self):
        entered = threading.Event()
        release = threading.Event()
        consumed = []
        summaries = []

        def on_frame(payload):
            consumed.append(payload["frame_id"])
            if payload["frame_id"] == 0:
                entered.set()
                release.wait(2.0)

        module = SimpleNamespace(
            initialize=lambda body: None,
            on_frame=on_frame,
            finish=summaries.append,
        )
        observer = LocalAIObserver("observer", module=module)
        observer.initialize(metadata())
        observer.publish(frame(0))
        self.assertTrue(entered.wait(1.0))
        observer.publish(frame(1))
        observer.publish(frame(2))
        release.set()
        observer.close({"reason": "completed"}, timeout=2.0)

        self.assertEqual(consumed, [0, 2])
        self.assertEqual(
            summaries[0]["observer_frames"],
            {"generated": 3, "consumed": 2, "dropped": 1},
        )

    def test_background_exception_is_propagated(self):
        called = threading.Event()

        def fail(payload):
            called.set()
            raise ValueError("inference failed")

        observer = LocalAIObserver(
            "bad_observer",
            module=SimpleNamespace(
                initialize=lambda body: None,
                on_frame=fail,
                finish=lambda body: None,
            ),
        )
        observer.initialize(metadata())
        observer.publish(frame(0))
        self.assertTrue(called.wait(1.0))
        time.sleep(0.01)
        with self.assertRaisesRegex(RuntimeError, "inference failed"):
            observer.check_error()
        with self.assertRaisesRegex(RuntimeError, "inference failed"):
            observer.close({"reason": "error"}, timeout=1.0)

    def test_shutdown_timeout_is_reported(self):
        entered = threading.Event()
        release = threading.Event()

        def block(payload):
            entered.set()
            release.wait(2.0)

        observer = LocalAIObserver(
            "slow_observer",
            module=SimpleNamespace(
                initialize=lambda body: None,
                on_frame=block,
                finish=lambda body: None,
            ),
        )
        observer.initialize(metadata())
        observer.publish(frame(0))
        self.assertTrue(entered.wait(1.0))
        with self.assertRaisesRegex(TimeoutError, "did not stop"):
            observer.close({"reason": "completed"}, timeout=0.01)
        release.set()
        observer._thread.join(1.0)


if __name__ == "__main__":
    unittest.main()
