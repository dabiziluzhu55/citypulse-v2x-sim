import json
import tempfile
import unittest
from pathlib import Path

from algorithms.prediction.build_official20_results_table import build_rows, write_results


class Official20ResultsTableTests(unittest.TestCase):
    def test_builds_all_methods_for_each_comparable_split(self):
        with tempfile.TemporaryDirectory() as temporary_directory:
            root = Path(temporary_directory)
            metrics = {"mae": 1.0, "rmse": 2.0, "mape": 3.0, "wmape": 0.4}
            baselines = [
                {"split": split, "model": model, "horizon_seconds": 60.0, **metrics}
                for split in ("validation", "test_in_distribution", "test_extrapolation")
                for model in ("persistence", "moving_average", "historical_average")
            ]
            baseline_path = root / "baselines.json"
            baseline_path.write_text(json.dumps(baselines), encoding="utf-8")
            xgb_path = root / "xgb.json"
            stgcn_path = root / "stgcn.json"
            xgb_path.write_text(json.dumps({split: metrics for split in ("validation", "test_in_distribution", "test_extrapolation")}), encoding="utf-8")
            stgcn_path.write_text(json.dumps({split: metrics for split in ("validation", "test_in_distribution", "test_extrapolation")}), encoding="utf-8")

            rows = build_rows(baseline_path=baseline_path, xgb_path=xgb_path, stgcn_path=stgcn_path)
            csv_path = root / "summary.csv"
            markdown_path = root / "summary.md"
            write_results(rows, csv_path=csv_path, markdown_path=markdown_path)

            self.assertEqual(len(rows), 15)
            self.assertEqual({row["model"] for row in rows}, {"persistence", "moving_average", "historical_average", "XGBoost", "STGCN"})
            self.assertIn("Test (OOD)", markdown_path.read_text(encoding="utf-8"))
            self.assertTrue(csv_path.is_file())


if __name__ == "__main__":
    unittest.main()
