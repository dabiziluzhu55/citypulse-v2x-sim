import csv

from algorithms.prediction.archive.legacy.filter_active_lanes import build_active_lane_dataset


def _write_rows(path, rows):
    with path.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=("elapsed_seconds", "lane_id", "vehicle_count"))
        writer.writeheader()
        writer.writerows(rows)


def test_active_lane_selection_uses_training_input_only(tmp_path):
    train = tmp_path / "train.csv"
    test = tmp_path / "test.csv"
    _write_rows(
        train,
        [
            {"elapsed_seconds": "0", "lane_id": "active", "vehicle_count": "1"},
            {"elapsed_seconds": "0", "lane_id": "held_out_only", "vehicle_count": "0"},
            {"elapsed_seconds": "5", "lane_id": "active", "vehicle_count": "0"},
            {"elapsed_seconds": "5", "lane_id": "held_out_only", "vehicle_count": "0"},
        ],
    )
    _write_rows(
        test,
        [
            {"elapsed_seconds": "0", "lane_id": "active", "vehicle_count": "0"},
            {"elapsed_seconds": "0", "lane_id": "held_out_only", "vehicle_count": "5"},
        ],
    )

    metadata = build_active_lane_dataset(
        train_inputs=[train],
        inputs=[train, test],
        output_dir=tmp_path / "filtered",
        min_active_samples=1,
        min_active_ratio=0.0,
    )

    assert metadata["active_lane_ids"] == ["active"]
    with (tmp_path / "filtered" / "test.csv").open(newline="", encoding="utf-8") as handle:
        rows = list(csv.DictReader(handle))
    assert rows == [{"elapsed_seconds": "0", "lane_id": "active", "vehicle_count": "0"}]
