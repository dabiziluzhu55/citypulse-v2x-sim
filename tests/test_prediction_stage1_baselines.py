import csv
import json

import numpy as np

from algorithms.prediction.evaluate_stage1_baselines import _metrics, evaluate


def _write_split(path, x, y):
    np.savez(path, x=np.asarray(x, dtype=np.float32), y=np.asarray(y, dtype=np.float32))


def test_stage1_baselines_are_training_only_and_use_the_history_window(tmp_path):
    dataset_dir = tmp_path / "tensors"
    dataset_dir.mkdir()
    (dataset_dir / "metadata.json").write_text(
        json.dumps(
            {
                "normalization": {"target_mean": 0.0, "target_std": 1.0},
                "target_feature_index": 0,
                "interval_seconds": 5.0,
            }
        ),
        encoding="utf-8",
    )
    # Two training windows produce a training-only historical mean of 4.0.
    train_labels = np.zeros((2, 12, 1), dtype=np.float32)
    _write_split(dataset_dir / "train.npz", [[[[1], [3]]], [[[5], [7]]]], train_labels)
    for split in ("validation", "test_in_distribution", "test_extrapolation"):
        labels = np.full((1, 12, 1), 6.0, dtype=np.float32)
        _write_split(dataset_dir / f"{split}.npz", [[[[2], [4]]]], labels)

    output = tmp_path / "baseline_metrics.csv"
    rows = evaluate(dataset_dir, output)
    final_horizon = [
        row for row in rows
        if row["split"] == "test_in_distribution" and row["horizon_steps"] == 1
    ]

    assert {row["model"] for row in final_horizon} == {
        "persistence", "moving_average", "historical_average",
    }
    by_model = {row["model"]: row for row in final_horizon}
    assert by_model["persistence"]["mae"] == 2.0
    assert by_model["moving_average"]["mae"] == 3.0
    assert by_model["historical_average"]["mae"] == 2.0
    assert by_model["persistence"]["smape"] == 0.4
    with output.open(encoding="utf-8", newline="") as handle:
        assert {row["model"] for row in csv.DictReader(handle)} == {
            "persistence", "moving_average", "historical_average",
        }


def test_metrics_do_not_treat_zero_count_roundoff_as_mape_denominator():
    metrics = _metrics(
        np.asarray([2.0], dtype=np.float32),
        np.asarray([1e-7], dtype=np.float32),
    )

    assert metrics["mape"] == 0.0
    assert metrics["smape"] > 1.9
