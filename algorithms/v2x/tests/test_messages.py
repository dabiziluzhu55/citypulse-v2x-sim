# algorithms/v2x/tests/test_messages.py
import pytest
from algorithms.v2x.messages import (
    V2XMessage, MessageDraft, make_message_id, stable_hash01,
    validate_draft, REQUIRED_FIELDS, MESSAGE_NAMESPACE,
)


def test_stable_hash01_deterministic_and_range():
    a = stable_hash01("x|1")
    b = stable_hash01("x|1")
    c = stable_hash01("x|2")
    assert a == b
    assert a != c
    assert 0.0 <= a < 1.0


def test_stable_hash01_sha256_expected_value():
    # 锁死算法：sha256("abc") 前 8 字节大端 / 2**64
    import hashlib
    digest = hashlib.sha256(b"abc").digest()
    expected = int.from_bytes(digest[:8], "big") / 2**64
    assert stable_hash01("abc") == expected


def test_message_id_includes_all_keys():
    a = make_message_id("r1", "ep1", "v1", "BSM", 1)
    b = make_message_id("r1", "ep1", "v1", "INTENT", 1)  # 同源不同型不冲突
    c = make_message_id("r2", "ep1", "v1", "BSM", 1)     # 不同 run 不冲突
    assert a != b
    assert a != c
    assert make_message_id("r1", "ep1", "v1", "BSM", 1) == a


def test_validate_draft_required_fields():
    draft = MessageDraft(
        message_type="BSM", source_id="v1", destination="cloud",
        sim_time=10.0,
        payload={"vehicle_id": "v1", "type_id": "t", "position": (0.0, 0.0),
                 "motion": {"speed_mps": 1.0}, "location": {"lane_id": "L_0"},
                 "route_edges": ["e0", "e1"], "next_signal": None,
                 "front_gap_m": None, "rear_gap_m": None, "gap_source": None},
    )
    validate_draft(draft)  # 不抛
    bad = MessageDraft(message_type="BSM", source_id="v1", destination="cloud",
                       sim_time=10.0, payload={"vehicle_id": "v1"})
    with pytest.raises(ValueError, match="BSM.*missing"):
        validate_draft(bad)
    with pytest.raises(ValueError, match="unknown message type"):
        validate_draft(MessageDraft("NOPE", "s", "d", 0.0, {}))


def test_message_to_dict_has_message_type():
    msg = V2XMessage(
        message_type="BSM", message_id="m1", schema_version="1.0",
        run_id="r", episode_id="e", frame_id="e:step:000001",
        sequence_no=1, sim_time=10.0, source_id="v1", destination="cloud",
        correlation_id=None, payload={"vehicle_id": "v1"},
    )
    data = msg.to_dict()
    assert data["message_type"] == "BSM"
    assert data["message_id"] == "m1"
    assert data["payload"]["vehicle_id"] == "v1"
