"""Validate actual six-direction event exports against the published schema."""
import json
from pathlib import Path
import pytest
from jsonschema import Draft202012Validator, ValidationError
from .communication import MessageBus, CVJointV1EventBridge


def test_six_direction_batches_obey_strict_schema():
    schema = json.loads((Path(__file__).parent / "communication/v2x_event_batch_v1.schema.json").read_text())
    validator = Draft202012Validator(schema)
    Draft202012Validator.check_schema(schema)
    bus = MessageBus("schema-test")
    bridge = CVJointV1EventBridge("schema-test")
    for source, dest, kind in [("vehicle", "road", "vehicle_state"), ("vehicle", "cloud", "vehicle_state"), ("road", "vehicle", "road_state"), ("road", "cloud", "road_feedback"), ("cloud", "road", "coordination_context"), ("cloud", "vehicle", "speed_permission")]:
        payload = {"movement": "m", "permit": True}
        msg = bus.send(kind, source, dest, 0, 0., 15., payload)
        bridge.observe_message(msg.message_id, payload)
        bus.consume(msg, dest, 0.)
    bridge.sync(bus.events)
    batch = bridge.event_batch(snapshot_id="schema-test:0")
    validator.validate(batch)
    assert len({(e["source_role"], e["destination_role"]) for e in batch["events"]}) == 6
    assert batch["event_count"] == 18
    batch["events"][0]["undeclared"] = True
    with pytest.raises(ValidationError):
        validator.validate(batch)


def test_empty_and_expired_batch_obey_schema():
    schema = json.loads((Path(__file__).parent / "communication/v2x_event_batch_v1.schema.json").read_text())
    validator = Draft202012Validator(schema)
    bridge = CVJointV1EventBridge("expired")
    validator.validate(bridge.event_batch())
    bridge.sync([dict(event="EXPIRE", message_id="expired:1", episode_id="expired", kind="speed_permission", source="cloud", destination="vehicle", step_id=3, time=15., generated_at=0., valid_until=15., payload={}, parents=())])
    validator.validate(bridge.event_batch())
