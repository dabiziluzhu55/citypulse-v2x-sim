import os
import tempfile
import unittest
from dataclasses import replace
from pathlib import Path
from unittest.mock import patch

try:
    from simulation.sumo.distributed import tasks
except ImportError:  # pragma: no cover - optional until runtime deps are installed
    tasks = None

from simulation.sumo.scenario import compile_session_scenario
from simulation.sumo.session import SimulationConfig, SimulationSnapshot
from test_session_manager import complete_generated_fixture


class WorkerStore:
    def __init__(self, config, snapshot):
        self.own_config = config
        self.own_snapshot = snapshot
        self.states = [snapshot.state]

    def snapshot(self, session_id):
        return self.own_snapshot

    def config(self, session_id):
        return self.own_config

    def session_ttl(self, session_id):
        return 60

    def compare_and_publish(self, expected, snapshot):
        if self.own_snapshot.state != expected:
            return False
        self.publish(snapshot)
        return True

    def publish(self, snapshot):
        self.own_snapshot = snapshot
        self.states.append(snapshot.state)

    def state(self, session_id):
        return self.own_snapshot.state

    def heartbeat(self, session_id, ttl):
        pass

    def fail_pending_commands(self, session_id, message):
        pass


class FakeHeartbeat:
    def __init__(self, *args, **kwargs):
        pass

    def start(self):
        pass

    def close(self):
        pass


class FakeWorkerManager:
    def __init__(self, store, **kwargs):
        self.store = store

    def _run_worker(self, record):
        self.store.publish(replace(record.snapshot, state="RUNNING"))
        self.store.publish(replace(record.snapshot, state="COMPLETED"))


@unittest.skipIf(tasks is None, "celery is not installed")
class DistributedWorkerTests(unittest.TestCase):
    def test_task_claims_compiled_session_and_reaches_terminal_state(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            generated = complete_generated_fixture(root)
            session_root = root / "sessions"
            session_id = "worker-session"
            config = SimulationConfig(
                intersection_ids=("demo_2",), duration_seconds=60
            )
            scenario = compile_session_scenario(
                session_id,
                config.intersection_ids,
                config.period,
                duration_seconds=config.duration_seconds,
                generated_dir=generated,
                session_root=session_root,
            )
            initial = SimulationSnapshot(
                session_id,
                "QUEUED",
                0,
                0,
                scenario.duration_seconds,
                0,
                "07:00:00",
            )
            store = WorkerStore(config, initial)
            environment = {
                "CITYPULSE_SUMO_GENERATED_DIR": str(generated),
                "CITYPULSE_SUMO_SESSION_ROOT": str(session_root),
            }
            with (
                patch.dict(os.environ, environment),
                patch.object(tasks, "RedisSessionStore", return_value=store),
                patch.object(tasks, "_Heartbeat", FakeHeartbeat),
                patch.object(tasks, "_RedisWorkerManager", FakeWorkerManager),
            ):
                result = tasks.run_session.run(session_id)

            self.assertEqual(result["state"], "COMPLETED")
            self.assertEqual(
                store.states, ["QUEUED", "STARTING", "RUNNING", "COMPLETED"]
            )


if __name__ == "__main__":
    unittest.main()
