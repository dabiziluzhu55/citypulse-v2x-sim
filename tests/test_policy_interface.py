import unittest

from simulation.sumo.controller import SafePhaseController
from simulation.sumo.external_policy import HttpAlgorithmClient
from simulation.sumo.policy import (
    PROTOCOL_VERSION,
    SimulationMetadata,
    SimulationObservation,
    TrafficObservation,
)
from simulation.sumo.run import _validate_actions


class StubHttpClient(HttpAlgorithmClient):
    def __init__(self, responses):
        super().__init__("http://algorithm.test")
        self.responses = responses
        self.requests = []

    def _post(self, path, payload):
        self.requests.append((path, payload))
        return self.responses[path]


def observation(step_id=3):
    return SimulationObservation(
        protocol_version=PROTOCOL_VERSION,
        episode_id="episode-test",
        step_id=step_id,
        simulation_time=15.0,
        intersections={},
        traffic=TrafficObservation(
            active_vehicles=10,
            departed_vehicles=2,
            arrived_vehicles=1,
            min_expected_vehicles=20,
        ),
    )


class PolicyInterfaceTests(unittest.TestCase):
    def setUp(self):
        self.controllers = {
            "demo_2": SafePhaseController(
                (1, 2),
                {1: (3.0, 0.0), 2: (3.0, 0.0)},
            )
        }

    def test_initialize_requires_explicit_ready(self):
        metadata = SimulationMetadata(
            protocol_version=PROTOCOL_VERSION,
            episode_id="episode-test",
            period="morning_peak",
            seed=42,
            decision_interval=5.0,
            minimum_green=5.0,
            intersections={},
        )
        client = StubHttpClient({"/initialize": {"ready": True}})
        client.initialize(metadata)
        self.assertEqual(client.requests[0][0], "/initialize")
        self.assertEqual(client.requests[0][1]["protocol_version"], "1.0")

        client = StubHttpClient({"/initialize": {}})
        with self.assertRaisesRegex(RuntimeError, "ready"):
            client.initialize(metadata)

    def test_step_returns_phase_actions_and_must_echo_step_id(self):
        client = StubHttpClient(
            {"/step": {"step_id": 3, "actions": {"demo_2": 2}}}
        )
        actions = client.decide(observation())
        self.assertEqual(actions, {"demo_2": 2})
        self.assertEqual(client.requests[0][0], "/step")

        stale = StubHttpClient(
            {"/step": {"step_id": 2, "actions": {"demo_2": 2}}}
        )
        with self.assertRaisesRegex(ValueError, "echo"):
            stale.decide(observation())

    def test_actions_are_fully_validated(self):
        self.assertEqual(
            _validate_actions({"demo_2": 2}, self.controllers),
            {"demo_2": 2},
        )
        self.assertEqual(
            _validate_actions({"demo_2": None}, self.controllers),
            {"demo_2": None},
        )
        with self.assertRaisesRegex(ValueError, "unknown intersections"):
            _validate_actions({"demo_99": 1}, self.controllers)
        with self.assertRaisesRegex(ValueError, "must be one of"):
            _validate_actions({"demo_2": 9}, self.controllers)
        with self.assertRaisesRegex(TypeError, "integer phase"):
            _validate_actions({"demo_2": True}, self.controllers)

    def test_finish_forwards_summary(self):
        client = StubHttpClient({"/finish": {"ok": True}})
        client.finish({"episode_id": "episode-test", "reason": "completed"})
        self.assertEqual(client.requests, [
            ("/finish", {"episode_id": "episode-test", "reason": "completed"})
        ])


if __name__ == "__main__":
    unittest.main()
