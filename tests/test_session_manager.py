import json
import tempfile
import time
import unittest
from dataclasses import replace
from pathlib import Path

from simulation.sumo.artifacts import GeneratedArtifactLayout
from simulation.sumo.session import (
    SessionBusyError,
    SimulationConfig,
    SimulationManager,
    load_catalog,
)
from test_session_scenario import write_fixture


def complete_generated_fixture(root: Path):
    generated = write_fixture(root)
    layout = GeneratedArtifactLayout(generated)
    layout.network_file.write_text(
        """<net><edge id="in"><lane id="in_0" length="100" speed="13.9"/></edge>
<edge id="out"><lane id="out_0" length="120" speed="12"/></edge></net>""",
        encoding="utf-8",
    )
    tls = {
        "schema_version": 2,
        "intersections": {
            "demo_2": {
                "connections": [
                    {
                        "approach": "west",
                        "from_edge": "in",
                        "from_lane": 0,
                        "to_edge": "out",
                        "to_lane": 0,
                    }
                ]
            }
        }
    }
    layout.tls_manifest.write_text(json.dumps(tls), encoding="utf-8")
    mapping = {"demo_2": {"lon": 116.1, "lat": 39.0}}
    (generated.parent / "TotalMap_20.intersections.json").write_text(
        json.dumps(mapping), encoding="utf-8"
    )
    return generated


class FakeSimulationManager(SimulationManager):
    def _run_worker(self, record):
        self._publish(record, replace(record.snapshot, state="RUNNING"))
        command = record.commands.get(timeout=5)
        if command.name == "stop":
            command.completed.set()
            self._publish(record, replace(record.snapshot, state="STOPPED"))
        else:
            command.error = RuntimeError("unsupported fake command")
            command.completed.set()
        with self._lock:
            self._active_session_id = None


class SessionManagerTests(unittest.TestCase):
    def test_catalog_exposes_origins_and_event_lanes(self):
        with tempfile.TemporaryDirectory() as directory:
            generated = complete_generated_fixture(Path(directory))
            catalog = load_catalog(generated)
            intersection = catalog.intersections["demo_2"]
            self.assertEqual(intersection.periods, ("morning_peak",))
            self.assertEqual(intersection.origins[0].origin_id, "north")
            west = next(item for item in intersection.origins if item.origin_id == "west")
            self.assertEqual(west.label, "西进口")
            incoming = next(item for item in intersection.lanes if item.role == "incoming")
            self.assertEqual(incoming.length, 100.0)
            self.assertEqual(incoming.approach_label, "西进口")

    def test_manager_enforces_single_active_session_and_publishes_terminal_state(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            generated = complete_generated_fixture(root)
            manager = FakeSimulationManager(
                generated_dir=generated,
                session_root=root / "sessions",
            )
            config = SimulationConfig(
                intersection_ids=("demo_2",),
                duration_seconds=60,
            )
            session_id = manager.start(config)
            deadline = time.time() + 2
            while manager.snapshot(session_id).state != "RUNNING" and time.time() < deadline:
                time.sleep(0.01)
            with self.assertRaises(SessionBusyError):
                manager.start(config)
            subscription = manager.subscribe(session_id)
            manager.stop(session_id)
            final = manager.wait(session_id, timeout=2)
            self.assertEqual(final.state, "STOPPED")
            self.assertEqual(subscription.get(timeout=1).state, "STOPPED")
            subscription.close()


if __name__ == "__main__":
    unittest.main()
