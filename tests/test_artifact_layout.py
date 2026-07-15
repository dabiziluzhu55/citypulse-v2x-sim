import tempfile
import unittest
from pathlib import Path

from simulation.sumo.artifacts import GeneratedArtifactLayout


class GeneratedArtifactLayoutTests(unittest.TestCase):
    def test_paths_group_shared_and_per_scenario_artifacts(self):
        layout = GeneratedArtifactLayout(Path("generated"))

        self.assertEqual(
            layout.relative(layout.network_file),
            "network/TotalMap_20.signals.net.xml",
        )
        self.assertEqual(
            layout.relative(layout.traffic_manifest),
            "manifests/traffic_manifest.json",
        )
        self.assertEqual(
            layout.relative(
                layout.traffic_scenario_dir("demo_20", "evening_peak")
                / "simulation.sumocfg"
            ),
            "traffic/demo_20/evening_peak/simulation.sumocfg",
        )

    def test_reset_removes_legacy_and_stale_output(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory) / "generated"
            root.mkdir()
            (root / "official_tls_validation.rou.xml").write_text(
                "legacy", encoding="utf-8"
            )
            stale = root / "traffic" / "removed_intersection"
            stale.mkdir(parents=True)
            (stale / "routes.rou.xml").write_text("stale", encoding="utf-8")

            layout = GeneratedArtifactLayout(root)
            layout.reset()

            self.assertFalse((root / "official_tls_validation.rou.xml").exists())
            self.assertFalse(stale.exists())
            self.assertTrue(layout.network_file.parent.is_dir())
            self.assertTrue(layout.traffic_manifest.parent.is_dir())


if __name__ == "__main__":
    unittest.main()
