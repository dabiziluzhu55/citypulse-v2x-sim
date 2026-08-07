import os
import sys
import unittest
from unittest.mock import patch

from simulation.sumo.runtime import (
    SumoRuntime,
    SumoRuntimeBusyError,
    SumoRuntimeError,
    load_sumo_runtime,
)


class FakeSumolib:
    def __init__(self):
        self.requested_binaries = []

    def checkBinary(self, name):
        self.requested_binaries.append(name)
        return f"/sumo/bin/{name}"


class FakeApi:
    def __init__(self, *, start_error=None, close_error=None):
        self.start_error = start_error
        self.close_error = close_error
        self.commands = []
        self.close_calls = 0
        self.simulation = object()

    def start(self, command):
        self.commands.append(command)
        if self.start_error is not None:
            raise self.start_error

    def close(self):
        self.close_calls += 1
        if self.close_error is not None:
            raise self.close_error


class SumoRuntimeSelectionTests(unittest.TestCase):
    def test_headless_loads_strict_libsumo_without_importing_traci(self):
        sumolib = FakeSumolib()
        libsumo = FakeApi()
        imported = []

        def load(name):
            imported.append(name)
            return {"sumolib": sumolib, "libsumo": libsumo}[name]

        with patch("simulation.sumo.runtime.importlib.import_module", side_effect=load):
            runtime = load_sumo_runtime(gui=False)

        self.assertEqual(imported, ["sumolib", "libsumo"])
        self.assertEqual(runtime.backend, "libsumo")
        self.assertIs(runtime.simulation, libsumo.simulation)
        self.assertEqual(runtime.command(["-c", "session.sumocfg"])[0], "/sumo/bin/sumo")

    def test_gui_loads_traci_and_sumo_gui(self):
        sumolib = FakeSumolib()
        traci = FakeApi()

        def load(name):
            return {"sumolib": sumolib, "traci": traci}[name]

        with patch("simulation.sumo.runtime.importlib.import_module", side_effect=load):
            runtime = load_sumo_runtime(gui=True)

        self.assertEqual(runtime.backend, "traci-gui")
        self.assertEqual(runtime.command([]), ["/sumo/bin/sumo-gui"])

    def test_headless_imports_libsumo_without_sumo_tools_on_sys_path(self):
        sumolib = FakeSumolib()
        libsumo = FakeApi()
        tools_path = "/usr/share/sumo/tools"
        saved_path = sys.path[:]
        sys.path.insert(0, tools_path)
        try:

            def load(name):
                if name == "sumolib":
                    return sumolib
                if name == "libsumo":
                    blocked = any(
                        os.path.normpath(entry) == os.path.normpath(tools_path)
                        for entry in sys.path
                    )
                    self.assertFalse(
                        blocked,
                        "libsumo import should ignore apt SUMO tools stubs on sys.path",
                    )
                    return libsumo
                raise ImportError(name)

            with (
                patch.dict(os.environ, {"SUMO_HOME": "/usr/share/sumo"}, clear=False),
                patch("simulation.sumo.runtime.importlib.import_module", side_effect=load),
            ):
                runtime = load_sumo_runtime(gui=False)

            self.assertEqual(runtime.backend, "libsumo")
        finally:
            sys.path[:] = saved_path

    def test_missing_libsumo_does_not_fall_back_to_traci(self):
        imported = []

        def load(name):
            imported.append(name)
            if name == "sumolib":
                return FakeSumolib()
            raise ImportError(name)

        with (
            patch("simulation.sumo.runtime.importlib.import_module", side_effect=load),
            self.assertRaisesRegex(SumoRuntimeError, "do not fall back to TraCI"),
        ):
            load_sumo_runtime(gui=False)

        self.assertEqual(imported, ["sumolib", "libsumo"])


class SumoRuntimeLifecycleTests(unittest.TestCase):
    def make_runtime(self, api):
        return SumoRuntime(
            FakeSumolib(),
            api,
            backend="libsumo",
            binary_name="sumo",
        )

    def test_process_lock_rejects_a_second_runtime_until_close(self):
        first = self.make_runtime(FakeApi())
        second = self.make_runtime(FakeApi())
        self.assertFalse(first.started)
        first.start(["sumo"])
        self.assertTrue(first.started)
        try:
            with self.assertRaisesRegex(SumoRuntimeBusyError, "already active"):
                second.start(["sumo"])
        finally:
            first.close()
        self.assertFalse(first.started)

        second.start(["sumo"])
        second.close()

    def test_start_failure_closes_partial_runtime_and_releases_lock(self):
        failed_api = FakeApi(start_error=RuntimeError("start failed"))
        failed = self.make_runtime(failed_api)
        with self.assertRaisesRegex(RuntimeError, "start failed"):
            failed.start(["sumo"])
        self.assertEqual(failed_api.close_calls, 1)
        self.assertFalse(failed.started)

        following = self.make_runtime(FakeApi())
        following.start(["sumo"])
        following.close()

    def test_close_failure_still_releases_lock(self):
        failed = self.make_runtime(FakeApi(close_error=RuntimeError("close failed")))
        failed.start(["sumo"])
        with self.assertRaisesRegex(RuntimeError, "close failed"):
            failed.close()
        self.assertFalse(failed.started)

        following = self.make_runtime(FakeApi())
        following.start(["sumo"])
        following.close()


if __name__ == "__main__":
    unittest.main()
