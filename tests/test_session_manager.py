import json
import tempfile
import threading
import time
import unittest
from dataclasses import replace
from pathlib import Path

from simulation.sumo.events import EventValidationError, MajorEventOpeningEvent
from simulation.sumo.artifacts import GeneratedArtifactLayout
from simulation.sumo.session import (
    SessionBusyError,
    SimulationConfig,
    SimulationManager,
    _playback_delay_seconds,
    load_catalog,
)
from simulation.sumo.scenario import ScenarioCompilationError
from simulation.sumo.runtime import SumoRuntime
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
        class FakeScheduler:
            def schedule(self, event, current_time=0):
                pass

            def cancel(self, event_id):
                pass

            def snapshots(self):
                return ()

        self._publish(
            record,
            replace(
                record.snapshot,
                state="PAUSED" if record.paused else "RUNNING",
                playback_speed=record.playback_speed,
            ),
        )
        sequence = 0
        stop = False
        while not stop:
            stop, sequence = self._process_commands(
                record,
                FakeScheduler(),
                0.0,
                sequence,
                wait_timeout=1.0,
            )
        self._publish(record, replace(record.snapshot, state="STOPPED"))
        with self._lock:
            self._active_session_id = None


class _RuntimeApi:
    def start(self, command):
        pass

    def close(self):
        pass


class RuntimeOwningSimulationManager(SimulationManager):
    """Minimal worker used to exercise ownership across manager instances."""

    def __init__(self, *, release: threading.Event, **kwargs):
        super().__init__(**kwargs)
        self.release = release
        self.ready = threading.Event()

    def _run_worker(self, record):
        runtime = SumoRuntime(
            None,
            _RuntimeApi(),
            backend="libsumo",
            binary_name="sumo",
        )
        try:
            runtime.start(["sumo"])
            self._publish(record, replace(record.snapshot, state="RUNNING"))
            self.ready.set()
            self.release.wait(timeout=5)
            self._publish(record, replace(record.snapshot, state="COMPLETED"))
        except BaseException as exc:
            self._publish(
                record,
                replace(record.snapshot, state="FAILED", error=str(exc)),
            )
        finally:
            runtime.close()
            self.ready.set()
            with self._lock:
                self._active_session_id = None


class SessionManagerTests(unittest.TestCase):
    def test_algorithm_transport_and_ai_observer_config_combinations(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            manager = SimulationManager(
                generated_dir=complete_generated_fixture(root),
                session_root=root / "sessions",
            )
            base = SimulationConfig(
                intersection_ids=("demo_2",),
                duration_seconds=60,
            )
            valid = (
                base,
                replace(base, ai_observer_module="algorithms.ai_observer_example"),
                replace(
                    base,
                    initial_events=(
                        MajorEventOpeningEvent(
                            "opening",
                            1,
                            2,
                            "out_0",
                            5,
                            source_lane_ids=("in_0",),
                        ),
                    ),
                ),
                replace(
                    base,
                    control_mode="algorithm",
                    algorithm_transport="http",
                    algorithm_endpoint="http://127.0.0.1:8001",
                ),
                replace(
                    base,
                    control_mode="algorithm",
                    algorithm_transport="local",
                    algorithm_module="algorithms.local_policy_example",
                    ai_observer_module="algorithms.ai_observer_example",
                ),
            )
            for config in valid:
                manager._validate_config(config)

            invalid = (
                replace(base, algorithm_transport="pipe"),
                replace(base, control_mode="algorithm"),
                replace(
                    base,
                    control_mode="algorithm",
                    algorithm_transport="local",
                ),
                replace(base, ai_frame_interval_seconds=0.01),
                replace(base, ai_observer_shutdown_timeout=0.0),
            )
            for config in invalid:
                with self.assertRaises(ScenarioCompilationError):
                    manager._validate_config(config)
            with self.assertRaises(EventValidationError):
                manager._validate_config(
                    replace(
                        base,
                        initial_events=(
                            MajorEventOpeningEvent("unknown", 1, 2, "missing_0", 5),
                        ),
                    )
                )
            with self.assertRaises(EventValidationError):
                manager._validate_config(
                    replace(
                        base,
                        initial_events=(
                            MajorEventOpeningEvent("bad_count", 1, 2, "out_0", 0),
                        ),
                    )
                )

    def test_playback_speed_scales_wall_clock_delay(self):
        self.assertAlmostEqual(_playback_delay_seconds(0.05, 1.0, 0.01), 0.04)
        self.assertAlmostEqual(_playback_delay_seconds(0.05, 2.0, 0.01), 0.015)
        self.assertEqual(_playback_delay_seconds(0.05, 5.0, 0.02), 0.0)
        self.assertEqual(_playback_delay_seconds(0.05, None, 0.0), 0.0)

    def test_catalog_exposes_origins_and_event_lanes(self):
        with tempfile.TemporaryDirectory() as directory:
            generated = complete_generated_fixture(Path(directory))
            catalog = load_catalog(generated)
            intersection = catalog.intersections["demo_2"]
            self.assertEqual(
                catalog.playback_speeds,
                (1.0, 1.25, 1.5, 2.0, 3.0, 5.0),
            )
            self.assertIn("major_event_opening", catalog.event_types)
            self.assertIn("major_event_closing", catalog.event_types)
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
            self.assertIsNone(manager.snapshot(session_id).playback_speed)
            with self.assertRaises(SessionBusyError):
                manager.start(config)
            subscription = manager.subscribe(session_id)
            manager.stop(session_id)
            final = manager.wait(session_id, timeout=2)
            self.assertEqual(final.state, "STOPPED")
            self.assertEqual(subscription.get(timeout=1).state, "STOPPED")
            subscription.close()

    def test_two_managers_cannot_own_libsumo_in_the_same_process(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            generated = complete_generated_fixture(root)
            release = threading.Event()
            first = RuntimeOwningSimulationManager(
                generated_dir=generated,
                session_root=root / "sessions-one",
                release=release,
            )
            second = RuntimeOwningSimulationManager(
                generated_dir=generated,
                session_root=root / "sessions-two",
                release=threading.Event(),
            )
            config = SimulationConfig(
                intersection_ids=("demo_2",),
                duration_seconds=60,
            )

            first_id = first.start(config)
            self.assertTrue(first.ready.wait(timeout=2))
            self.assertEqual(first.snapshot(first_id).state, "RUNNING")
            try:
                second_id = second.start(config)
                second_final = second.wait(second_id, timeout=2)
                self.assertEqual(second_final.state, "FAILED")
                self.assertIn("already active", second_final.error)
                self.assertEqual(first.snapshot(first_id).state, "RUNNING")
            finally:
                release.set()
                first.wait(first_id, timeout=2)

            self.assertEqual(first.snapshot(first_id).state, "COMPLETED")

    def test_pause_resume_and_runtime_speed_control(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            manager = FakeSimulationManager(
                generated_dir=complete_generated_fixture(root),
                session_root=root / "sessions",
            )
            session_id = manager.start(
                SimulationConfig(
                    intersection_ids=("demo_2",),
                    duration_seconds=60,
                    start_paused=True,
                    playback_speed=1.0,
                )
            )

            deadline = time.time() + 2
            while manager.snapshot(session_id).state == "STARTING" and time.time() < deadline:
                time.sleep(0.01)
            initial = manager.snapshot(session_id)
            self.assertEqual(initial.state, "PAUSED")
            self.assertEqual(initial.playback_speed, 1.0)

            manager.set_playback_speed(session_id, 2.0)
            faster = manager.snapshot(session_id)
            self.assertEqual(faster.state, "PAUSED")
            self.assertEqual(faster.playback_speed, 2.0)
            self.assertGreater(faster.sequence, initial.sequence)

            manager.set_playing(session_id, True)
            self.assertEqual(manager.snapshot(session_id).state, "RUNNING")
            manager.set_playing(session_id, False)
            self.assertEqual(manager.snapshot(session_id).state, "PAUSED")
            manager.pause(session_id)
            self.assertEqual(manager.snapshot(session_id).state, "PAUSED")

            with self.assertRaisesRegex(ValueError, "playback speed"):
                manager.set_playback_speed(session_id, 4.0)
            with self.assertRaisesRegex(ValueError, "boolean"):
                manager.set_playback_speed(session_id, True)
            with self.assertRaisesRegex(ValueError, "playing"):
                manager.set_playing(session_id, "true")

            manager.stop(session_id)
            self.assertEqual(manager.wait(session_id, timeout=2).state, "STOPPED")


if __name__ == "__main__":
    unittest.main()
