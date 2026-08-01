import unittest

try:
    from simulation.sumo.distributed.celery_app import app
    import simulation.sumo.distributed.tasks  # noqa: F401
except ImportError:  # pragma: no cover - optional until runtime deps are installed
    app = None


@unittest.skipIf(app is None, "celery is not installed")
class CeleryConfigurationTests(unittest.TestCase):
    def test_worker_uses_json_and_single_task_prefetch(self):
        self.assertEqual(app.conf.task_serializer, "json")
        self.assertEqual(app.conf.result_serializer, "json")
        self.assertNotIn("pickle", app.conf.accept_content)
        self.assertEqual(app.conf.worker_prefetch_multiplier, 1)
        self.assertIn("citypulse.sumo.run_session", app.tasks)


if __name__ == "__main__":
    unittest.main()
