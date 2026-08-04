import queue
import unittest

from simulation.sumo.distributed.commands import RedisCommandQueue
from simulation.sumo.events import LaneClosureEvent


class FakeCommandStore:
    def __init__(self, values):
        self.values = list(values)
        self.claimed = set()
        self.acks = []

    def pop_command(self, session_id, timeout=0.0):
        return None if not self.values else self.values.pop(0)

    def claim_command(self, session_id, command_id):
        if command_id in self.claimed:
            return False
        self.claimed.add(command_id)
        return True

    def acknowledge(self, session_id, command_id, error):
        self.acks.append((session_id, command_id, error))


class DistributedCommandTests(unittest.TestCase):
    def test_decodes_event_and_acknowledges_completion(self):
        store = FakeCommandStore(
            [
                {
                    "command_id": "command-1",
                    "name": "add_event",
                    "payload": {
                        "event_type": "lane_closure",
                        "event_id": "closure",
                        "start_seconds": 1,
                        "end_seconds": 2,
                        "lane_ids": ["in_0"],
                    },
                }
            ]
        )
        command = RedisCommandQueue(store, "session-1").get_nowait()
        self.assertEqual(
            command.payload,
            LaneClosureEvent("closure", 1, 2, ("in_0",)),
        )
        command.completed.set()
        self.assertEqual(store.acks, [("session-1", "command-1", None)])

    def test_duplicate_command_is_not_executed_twice(self):
        value = {"command_id": "same", "name": "pause", "payload": None}
        store = FakeCommandStore([value, value])
        commands = RedisCommandQueue(store, "session-1")
        first = commands.get_nowait()
        first.completed.set()
        with self.assertRaises(queue.Empty):
            commands.get_nowait()
        self.assertEqual(len(store.acks), 2)


if __name__ == "__main__":
    unittest.main()
