# algorithms/v2x/tests/test_replay.py
import json
from pathlib import Path
from algorithms.v2x.replay import summarize_log, format_summary

def _write_log(path: Path):
    lines = [
        {"record_type": "episode_start", "episode_id": "e1", "run_id": "r1"},
        {"record_type": "message", "message": {"message_type": "BSM"},
         "sent_at": 0.0, "scheduled_delivery_at": 0.02},
        {"record_type": "delivery", "message_id": "m1", "status": "delivered",
         "delivered_at": 0.02, "processed_at": 5.0, "actual_latency_ms": 20.0},
        {"record_type": "episode_end", "summary": {"delivery_rate": 1.0}},
    ]
    path.write_text("\n".join(json.dumps(x) for x in lines), encoding="utf-8")

def test_summarize_log(tmp_path: Path):
    p = tmp_path / "log.jsonl"
    _write_log(p)
    s = summarize_log(str(p))
    assert s["episodes"] == ["e1"]
    assert s["counts"]["BSM"] == 1
    assert s["delivered"] == 1
    assert s["dropped"] == 0

def test_format_summary_contains_counts():
    text = format_summary({"episodes": ["e1"], "counts": {"BSM": 1},
                           "delivered": 1, "dropped": 0})
    assert "BSM" in text and "delivered" in text
