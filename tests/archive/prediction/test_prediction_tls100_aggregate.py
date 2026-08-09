import csv

import pytest

from algorithms.prediction.archive.aggregation.aggregate_tls100_junction_snapshots import aggregate


def _write_manifest(path):
    path.write_text(
        """{
  "nodes": ["A", "B"],
  "node_order_sha256": "not-used-by-test",
  "junctions": {
    "A": {"incoming_lanes": ["in_a_0"]},
    "B": {"incoming_lanes": ["in_b_0"]}
  }
}""",
        encoding="utf-8",
    )
    # Keep the fixture self-contained while using the production hash check.
    import json
    import hashlib

    payload = json.loads(path.read_text(encoding="utf-8"))
    encoded = json.dumps(payload["nodes"], separators=(",", ":")).encode("utf-8")
    payload["node_order_sha256"] = hashlib.sha256(encoded).hexdigest()
    path.write_text(json.dumps(payload), encoding="utf-8")


def _write_snapshots(path):
    rows = [
        ("0", "in_a_0", "2", "1", "10", "0.2"),
        ("0", "in_b_0", "1", "0", "20", "0.4"),
        ("0", "extra_0", "99", "0", "1", "0.9"),
        ("5", "in_a_0", "2", "1", "10", "0.4"),
        ("5", "in_b_0", "1", "1", "20", "0.6"),
        ("5", "extra_0", "99", "0", "1", "0.9"),
        ("10", "in_a_0", "1", "0", "10", "0.6"),
        ("10", "in_b_0", "3", "1", "30", "0.8"),
        ("10", "extra_0", "99", "0", "1", "0.9"),
    ]
    with path.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(
            handle,
            fieldnames=(
                "elapsed_seconds",
                "lane_id",
                "vehicle_count",
                "halting_count",
                "mean_speed",
                "occupancy",
            ),
        )
        writer.writeheader()
        for values in rows:
            writer.writerow(dict(zip(writer.fieldnames, values)))


def test_aggregate_keeps_junction_nodes_and_applies_documented_rules(tmp_path):
    manifest = tmp_path / "manifest.json"
    source = tmp_path / "episode.csv"
    output = tmp_path / "junctions.csv"
    _write_manifest(manifest)
    _write_snapshots(source)

    summary = aggregate(
        input_path=source,
        manifest_path=manifest,
        output_path=output,
        expected_nodes=2,
        expected_snapshots=3,
        interval_seconds=5,
    )

    assert summary["node_count"] == 2
    assert summary["snapshot_count"] == 3
    assert summary["output_rows"] == 6
    with output.open(newline="", encoding="utf-8") as handle:
        rows = list(csv.DictReader(handle))
    assert [row["lane_id"] for row in rows[:2]] == ["A", "B"]
    assert rows[0]["vehicle_count"] == "2"
    assert rows[1]["vehicle_count"] == "1"
    assert rows[4]["vehicle_count"] == "1"
    assert rows[5]["vehicle_count"] == "3"
    assert rows[5]["mean_speed"] == "30.000000"


def test_aggregate_rejects_a_missing_target_lane(tmp_path):
    manifest = tmp_path / "manifest.json"
    source = tmp_path / "episode.csv"
    _write_manifest(manifest)
    _write_snapshots(source)
    text = source.read_text(encoding="utf-8")
    source.write_text(text.replace("5,in_b_0,1,1,20,0.6\n", ""), encoding="utf-8")

    with pytest.raises(ValueError, match="missing"):
        aggregate(
            input_path=source,
            manifest_path=manifest,
            output_path=None,
            expected_nodes=2,
            expected_snapshots=3,
            interval_seconds=5,
        )
