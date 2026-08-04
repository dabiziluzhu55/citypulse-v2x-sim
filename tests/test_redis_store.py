import unittest
from dataclasses import replace

try:
    import fakeredis
except ImportError:  # pragma: no cover - optional test dependency
    fakeredis = None

from simulation.sumo.distributed.store import RedisSessionStore
from simulation.sumo.distributed.manager import RedisSnapshotSubscription
from simulation.sumo.session import SimulationConfig, SimulationSnapshot


@unittest.skipIf(fakeredis is None, "fakeredis is not installed")
class RedisSessionStoreTests(unittest.TestCase):
    def setUp(self):
        self.client = fakeredis.FakeRedis()
        self.store = RedisSessionStore(
            "redis://unused/1",
            client=self.client,
            terminal_ttl_seconds=60,
        )
        self.config = SimulationConfig(
            ("demo_2",), duration_seconds=10, start_paused=True
        )
        self.snapshot = SimulationSnapshot(
            "session-1", "QUEUED", 0, 0, 10, 0, "07:00:00", 1.0
        )
        self.store.create("session-1", self.config, self.snapshot)

    def test_config_snapshot_commands_ack_and_heartbeat(self):
        self.assertEqual(self.store.config("session-1"), self.config)
        self.assertEqual(self.store.session_ttl("session-1"), 60)
        self.assertEqual(self.store.snapshot("session-1"), self.snapshot)

        self.store.enqueue_command("session-1", "command-1", "pause", None)
        command = self.store.pop_command("session-1")
        self.assertEqual(command["command_id"], "command-1")
        self.assertTrue(self.store.claim_command("session-1", "command-1"))
        self.assertFalse(self.store.claim_command("session-1", "command-1"))
        self.store.acknowledge("session-1", "command-1", None)
        self.assertTrue(
            self.store.wait_for_ack("session-1", "command-1", 1)["ok"]
        )

        self.store.heartbeat("session-1", 15)
        self.assertTrue(self.store.heartbeat_alive("session-1"))

    def test_compare_and_publish_is_state_guarded(self):
        starting = replace(self.snapshot, state="STARTING", sequence=1)
        self.assertTrue(self.store.compare_and_publish("QUEUED", starting))
        self.assertFalse(
            self.store.compare_and_publish(
                "QUEUED", replace(self.snapshot, state="STOPPED")
            )
        )
        self.assertEqual(self.store.snapshot("session-1"), starting)

    def test_terminal_snapshot_gets_retention_ttl(self):
        self.store.publish(replace(self.snapshot, state="COMPLETED"))
        self.assertGreater(
            self.client.ttl(self.store.key("session-1", "meta")), 0
        )
        self.assertGreater(
            self.client.ttl(self.store.key("session-1", "snapshot")), 0
        )

    def test_subscription_delivers_state_change_with_same_sequence(self):
        class Manager:
            def __init__(own, store):
                own._store = store

            def snapshot(own, session_id):
                return own._store.snapshot(session_id)

        subscription = RedisSnapshotSubscription(Manager(self.store), "session-1")
        try:
            self.assertEqual(subscription.get(timeout=1).state, "QUEUED")
            self.store.publish(replace(self.snapshot, state="COMPLETED"))
            self.assertEqual(subscription.get(timeout=1).state, "COMPLETED")
        finally:
            subscription.close()


if __name__ == "__main__":
    unittest.main()
