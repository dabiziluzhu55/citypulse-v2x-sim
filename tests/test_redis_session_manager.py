import tempfile
import unittest
from pathlib import Path

from simulation.sumo.distributed.manager import RedisSimulationManager
from simulation.sumo.session import SessionError, SimulationConfig
from simulation.sumo.scenario import ScenarioCompilationError
from test_session_manager import complete_generated_fixture


class FakeStore:
    def __init__(self):
        self.snapshots = {}
        self.configs = {}

    def create(self, session_id, config, snapshot):
        self.configs[session_id] = config
        self.snapshots[session_id] = snapshot

    def snapshot(self, session_id):
        return self.snapshots.get(session_id)

    def exists(self, session_id):
        return session_id in self.snapshots

    def publish(self, snapshot):
        self.snapshots[snapshot.session_id] = snapshot

    def compare_and_publish(self, expected, snapshot):
        current = self.snapshots.get(snapshot.session_id)
        if current is None or current.state != expected:
            return False
        self.publish(snapshot)
        return True

    def heartbeat_alive(self, session_id):
        return False

    def updated_at(self, session_id):
        return None


class FakeResult:
    state = "PENDING"


class FakeControl:
    def __init__(self):
        self.revoked = []

    def revoke(self, task_id, terminate=False):
        self.revoked.append((task_id, terminate))


class FakeCelery:
    def __init__(self):
        self.sent = []
        self.control = FakeControl()

    def send_task(self, name, *, args, task_id, queue):
        self.sent.append((name, args, task_id, queue))

    def AsyncResult(self, task_id):
        return FakeResult()


class RedisSessionManagerTests(unittest.TestCase):
    def test_multiple_sessions_queue_and_queued_stop(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            store = FakeStore()
            celery = FakeCelery()
            manager = RedisSimulationManager(
                generated_dir=complete_generated_fixture(root),
                session_root=root / "sessions",
                store=store,
                celery_app=celery,
            )
            config = SimulationConfig(
                intersection_ids=("demo_2",), duration_seconds=60
            )
            session_ids = [manager.start(config) for _ in range(5)]
            first, second = session_ids[:2]

            self.assertNotEqual(first, second)
            self.assertEqual(manager.snapshot(first).state, "QUEUED")
            self.assertEqual(manager.snapshot(second).state, "QUEUED")
            self.assertEqual(len(celery.sent), 5)
            self.assertTrue(
                all(manager.snapshot(item).state == "QUEUED" for item in session_ids)
            )
            self.assertTrue((root / "sessions" / first / "session.sumocfg").is_file())
            self.assertTrue((root / "sessions" / second / "session.sumocfg").is_file())

            with self.assertRaisesRegex(SessionError, "queued"):
                manager.pause(first)
            manager.stop(first)
            self.assertEqual(manager.snapshot(first).state, "STOPPED")
            self.assertEqual(celery.control.revoked, [(first, False)])

    def test_distributed_manager_rejects_gui(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            manager = RedisSimulationManager(
                generated_dir=complete_generated_fixture(root),
                session_root=root / "sessions",
                store=FakeStore(),
                celery_app=FakeCelery(),
            )
            with self.assertRaisesRegex(ScenarioCompilationError, "headless"):
                manager.start(
                    SimulationConfig(
                        intersection_ids=("demo_2",),
                        duration_seconds=60,
                        gui=True,
                    )
                )


if __name__ == "__main__":
    unittest.main()
