# PredictionRuntime：NarrowNet-TDP交付包缺失时不可用，不在无包时强行推理

from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

from backend.app.services.prediction_runtime import PredictionRuntime


BUNDLE_DIR = (
    Path(__file__).resolve().parents[1]
    / "backend"
    / "models"
    / "prediction"
    / "narrow_net_tdp"
)


class PredictionRuntimeTests(unittest.TestCase):
    def test_missing_model_dir_is_unavailable(self):
        runtime = PredictionRuntime(None)
        self.assertFalse(runtime.status.available)
        values, meta = runtime.predict_vehicle_counts([])
        self.assertIsNone(values)
        self.assertTrue(meta["fallback"])
        self.assertEqual(meta["fallback_reason"], "model_dir_unset")

    def test_incomplete_model_pack_is_unavailable(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / "model_manifest.json").write_text("{}", encoding="utf-8")
            runtime = PredictionRuntime(root)
            self.assertFalse(runtime.status.available)
            self.assertIn("missing_files", runtime.status.reason)

    def test_history_insufficient_returns_fallback_meta(self):
        runtime = PredictionRuntime(None)
        values, meta = runtime.predict_vehicle_counts([{}] * 3)
        self.assertIsNone(values)
        self.assertTrue(meta["fallback"])

    def test_from_settings_empty_string(self):
        runtime = PredictionRuntime.from_settings(model_dir="", stgcn_root="")
        self.assertFalse(runtime.status.available)

    def test_bundle_load_and_smoke_inference(self):
        if not BUNDLE_DIR.is_dir():
            self.skipTest("NarrowNet-TDP交付包不在仓库中")
        try:
            import torch  # noqa: F401
        except Exception:
            self.skipTest("torch不可用")

        runtime = PredictionRuntime(BUNDLE_DIR, device="cpu")
        self.assertTrue(
            runtime.status.available,
            msg=runtime.status.reason,
        )
        self.assertEqual(len(runtime.nodes), 206)
        self.assertEqual(runtime.status.model, "NarrowNet-TDP")

        empty_frame = {
            lane: {
                "vehicle_count": 0.0,
                "halting_count": 0.0,
                "mean_speed": 0.0,
                "occupancy": 0.0,
            }
            for lane in runtime.nodes
        }
        values, meta = runtime.predict_vehicle_counts([empty_frame] * runtime.n_his)
        self.assertFalse(meta["fallback"], msg=meta.get("fallback_reason"))
        self.assertIsNotNone(values)
        assert values is not None
        self.assertEqual(len(values), 206)
        self.assertTrue(all(value >= 0.0 for value in values.values()))


class NormalizationContractSmokeTests(unittest.TestCase):
    def test_manifest_json_roundtrip_shape(self):
        payload = {
            "node_order": [f"lane_{i}" for i in range(206)],
            "features": ["vehicle_count", "halting_count", "mean_speed", "occupancy"],
            "n_his": 12,
            "normalization": {
                "feature_mean": [1.0, 1.0, 1.0, 1.0],
                "feature_std": [1.0, 1.0, 1.0, 1.0],
                "target_mean": 1.0,
                "target_std": 1.0,
            },
        }
        self.assertEqual(len(payload["node_order"]), 206)
        self.assertEqual(len(payload["features"]), 4)
        loaded = json.loads(json.dumps(payload))
        self.assertEqual(loaded["n_his"], 12)


if __name__ == "__main__":
    unittest.main()
