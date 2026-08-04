# algorithms/v2x/tests/test_logger.py
import json
from pathlib import Path
from algorithms.v2x.logger import (
    LogRecord, JSONLSink, episode_start_record, message_record,
    delivery_record, episode_end_record,
)

REC = LogRecord("message", {"message_type": "BSM"})


def test_logrecord_data():
    assert REC.record_type == "message"
    assert REC.data["message_type"] == "BSM"


def test_episode_start_record_fields():
    rec = episode_start_record(run_id="r", episode_id="e", scenario={"period": "off_peak"},
                               v2x_config={"drop_rate": 0.0}, capability_seed=0,
                               capability_config={"connected_classes": ["passenger"]},
                               map_versions={"a": 1})
    assert rec.record_type == "episode_start"
    assert rec.data["episode_id"] == "e"
    assert rec.data["capability_seed"] == 0


def test_message_and_delivery_records():
    m = message_record(message={"message_type": "BSM"}, sent_at=10.0,
                       scheduled_delivery_at=10.02)
    assert m.data["sent_at"] == 10.0
    d = delivery_record(message_id="m1", status="delivered", delivered_at=10.02,
                        processed_at=15.0, actual_latency_ms=20.0)
    assert d.data["processed_at"] == 15.0
    drop = delivery_record(message_id="m1", status="dropped", dropped_at=60.0,
                           processed_at=60.0, drop_reason="episode_ended")
    assert drop.data["drop_reason"] == "episode_ended"


def test_jsonsink_writes_flush_close(tmp_path: Path):
    path = tmp_path / "log.jsonl"
    sink = JSONLSink(str(path))
    sink.write(REC)
    sink.flush()
    sink.close()
    lines = path.read_text(encoding="utf-8").strip().splitlines()
    assert len(lines) == 1
    assert json.loads(lines[0])["record_type"] == "message"
