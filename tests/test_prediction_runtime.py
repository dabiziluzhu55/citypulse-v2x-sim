# PredictionRuntime：模型包缺失时不可用，不在无包时强行推理

from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

import numpy as np

from backend.app.services.prediction_runtime import PredictionRuntime, _calc_chebynet_gso


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
        # 即使available=False，也应给出明确降级原因且不抛异常
        values, meta = runtime.predict_vehicle_counts([{}] * 3)
        self.assertIsNone(values)
        self.assertTrue(meta["fallback"])

    def test_chebynet_gso_shape(self):
        adjacency = np.eye(20, dtype=np.float32)
        adjacency[0, 1] = adjacency[1, 0] = 1.0
        gso = _calc_chebynet_gso(adjacency)
        self.assertEqual(gso.shape, (20, 20))

    def test_from_settings_empty_string(self):
        runtime = PredictionRuntime.from_settings(model_dir="", stgcn_root="")
        self.assertFalse(runtime.status.available)


class NormalizationContractSmokeTests(unittest.TestCase):
    def test_manifest_json_roundtrip_shape(self):
        # 仅校验模型包契约字段形状，不加载权重
        payload = {
            "nodes": [f"demo_{i}" for i in [1, *range(10, 21), *range(2, 10)]],
            "features": ["vehicle_count", "halting_count", "mean_speed", "occupancy"],
            "n_his": 12,
            "normalization": {
                "feature_mean": [1.0, 1.0, 1.0, 1.0],
                "feature_std": [1.0, 1.0, 1.0, 1.0],
                "target_mean": 1.0,
                "target_std": 1.0,
            },
        }
        self.assertEqual(len(payload["nodes"]), 20)
        self.assertEqual(len(payload["features"]), 4)
        text = json.dumps(payload)
        loaded = json.loads(text)
        self.assertEqual(loaded["n_his"], 12)


if __name__ == "__main__":
    unittest.main()
