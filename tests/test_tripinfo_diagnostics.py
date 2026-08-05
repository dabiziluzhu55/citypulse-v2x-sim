"""Tests for the canonical TripInfo diagnostics shared by all evaluators."""

from __future__ import annotations

import pytest

from simulation.sumo.tripinfo import (
    parse_tripinfo_diagnostics,
    residual_mismatch,
)


def _write(tmp_path, records: list[dict]) -> str:
    lines = ['<?xml version="1.0"?>', "<tripinfos>"]
    for record in records:
        attrs = " ".join(f'{k}="{v}"' for k, v in record.items())
        lines.append(f"  <tripinfo {attrs}/>")
    lines.append("</tripinfos>")
    path = tmp_path / "tripinfo.xml"
    path.write_text("\n".join(lines), encoding="utf-8")
    return str(path)


def test_splits_completed_vs_unfinished_with_vaporized_end(tmp_path):
    """真实场景：截断车辆以 arrival=-1 + vaporized=end 写入，计入残余车辆。"""
    path = _write(
        tmp_path,
        [
            {"id": "done-1", "arrival": "20", "duration": "10",
             "waitingTime": "2", "timeLoss": "3", "vaporized": ""},
            {"id": "done-2", "arrival": "30", "duration": "20",
             "waitingTime": "4", "timeLoss": "7", "vaporized": ""},
            {"id": "open-1", "arrival": "-1", "duration": "300",
             "waitingTime": "6", "timeLoss": "5", "vaporized": "end"},
            {"id": "open-2", "arrival": "-1", "duration": "300",
             "waitingTime": "8", "timeLoss": "9", "vaporized": "end"},
        ],
    )
    metrics = parse_tripinfo_diagnostics(path)
    assert metrics["trip_records"] == 4
    assert metrics["completed_trips"] == 2
    assert metrics["unfinished_trips"] == 2
    assert metrics["completion_rate"] == pytest.approx(0.5)
    assert metrics["completed_waiting_mean_s"] == pytest.approx(3.0)
    assert metrics["unfinished_waiting_total_s"] == pytest.approx(14.0)
    assert metrics["all_waiting_total_s"] == pytest.approx(20.0)
    assert metrics["end_waiting_total_s"] == metrics["all_waiting_total_s"]
    assert metrics["end_waiting_mean_s"] == pytest.approx(5.0)
    assert metrics["all_time_loss_total_s"] == pytest.approx(24.0)


def test_vaporized_true_with_positive_arrival_is_not_completed(tmp_path):
    """vaporized=true（非 end 原因蒸发）即使 arrival>=0 也不计为完成。"""
    path = _write(
        tmp_path,
        [
            {"id": "gone", "arrival": "40", "duration": "30",
             "waitingTime": "1", "timeLoss": "2", "vaporized": "true"},
            {"id": "done", "arrival": "50", "duration": "40",
             "waitingTime": "3", "timeLoss": "4", "vaporized": ""},
        ],
    )
    metrics = parse_tripinfo_diagnostics(path)
    assert metrics["completed_trips"] == 1
    assert metrics["unfinished_trips"] == 1
    assert metrics["completed_waiting_mean_s"] == pytest.approx(3.0)
    assert metrics["end_waiting_total_s"] == pytest.approx(4.0)


def test_empty_tripinfo_returns_zero_defaults(tmp_path):
    path = _write(tmp_path, [])
    metrics = parse_tripinfo_diagnostics(path)
    assert metrics["trip_records"] == 0
    assert metrics["completed_trips"] == 0
    assert metrics["unfinished_trips"] == 0
    assert metrics["completion_rate"] == 0.0
    assert metrics["end_waiting_total_s"] == 0.0
    assert metrics["end_waiting_mean_s"] == 0.0


def test_completed_duration_percentiles_present(tmp_path):
    path = _write(
        tmp_path,
        [
            {"id": f"d{i}", "arrival": str(10 + i), "duration": str(i + 1),
             "waitingTime": "0", "timeLoss": "0", "vaporized": ""}
            for i in range(10)
        ],
    )
    metrics = parse_tripinfo_diagnostics(path)
    assert metrics["completed_duration_median_s"] == pytest.approx(5.5)
    assert metrics["completed_duration_p95_s"] == pytest.approx(9.55)
    assert metrics["completed_duration_mean_s"] == pytest.approx(5.5)


def test_residual_mismatch_helper():
    assert residual_mismatch(764, 764) == 0
    assert residual_mismatch(772, 768) == 4
    assert residual_mismatch(0, 5) == -5
