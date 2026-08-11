from __future__ import annotations

import math

import pytest

from algorithms.evaluation import summarize_dev_four_line as summary


def _run(seed, wait, departed, *, status_marker=True):
    row = {
        "seed": seed,
        "official_metrics": {
            "all_waiting_total_s": wait,
            "departed_count": departed,
        },
        "all_waiting_total_s": 999999.0,
    }
    if status_marker:
        row["status"] = "complete"
    return row


def test_strict_v8_adapter_delegates_only_explicitly(monkeypatch):
    rows = [_run(66501, 10.0, 1.0), _run(66502, 100.0, 100.0)]
    seen = []

    def fake_protocol_pooled_m(received):
        seen.append(received)
        return 123.0

    monkeypatch.setattr(summary, "protocol_pooled_m", fake_protocol_pooled_m)
    assert summary.strict_protocol_pooled_m({"runs": rows}) == 123.0
    assert seen == [rows]


def test_legacy_summary_preserves_historical_input_and_zero_departure_semantics():
    raw = {
        "runs": [
            _run(66501, 10.0, 1.0, status_marker=False),
            _run(66502, 100.0, 100.0, status_marker=False),
        ]
    }
    assert summary.per_seed_m(raw) == {66501: 10.0, 66502: 1.0}
    assert summary.pooled_m(raw) == pytest.approx(110.0 / 101.0)
    assert math.isnan(
        summary.pooled_m(
            {"runs": [_run(66501, 1.0, 0.0, status_marker=False)]}
        )
    )
