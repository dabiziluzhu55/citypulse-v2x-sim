"""Contract tests for deployment-owned CoV2X V2X communication."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from traffic_control.cov2x.communication import (
    IdealPhasedTransport,
    TypedEnvelope,
    V2X_EVENT_BATCH_SCHEMA,
    V2X_EVENT_SCHEMA,
    V2X_EVENT_SCHEMA_VERSION,
    V2XEventDrain,
)


def _vehicle_state(*, ttl_s: float = 5.0) -> TypedEnvelope:
    return TypedEnvelope(
        "VehicleStateV1",
        "episode-a:0:vehicle:veh-1",
        "episode-a:0",
        "veh-1",
        "cloud",
        10.0,
        ttl_s,
        "state",
        {
            "vehicle_id": "veh-1",
            "motion": {"speed_mps": 3.0},
            "location": {"lane_id": "lane-1"},
            "next_signal": {"intersection_id": "demo_1"},
        },
    )


def test_event_batch_is_versioned_json_and_drain_is_cursor_safe() -> None:
    transport = IdealPhasedTransport()
    message = _vehicle_state()

    transport.send(message)
    assert transport.deliver(message.snapshot_id, "state", 10.25) == (message,)
    assert transport.consume(message, sim_time=10.5)["vehicle_id"] == "veh-1"

    batch = transport.event_batch(snapshot_id=message.snapshot_id)
    assert batch["schema"] == V2X_EVENT_BATCH_SCHEMA
    assert batch["schema_version"] == V2X_EVENT_SCHEMA_VERSION
    assert batch["event_count"] == 3
    assert [row["event"] for row in batch["events"]] == [
        "SEND",
        "DELIVER",
        "CONSUME",
    ]
    record = batch["events"][-1]
    assert record["schema"] == V2X_EVENT_SCHEMA
    assert record["source_role"] == "vehicle"
    assert record["destination_role"] == "cloud"
    assert record["message_age_s"] == pytest.approx(0.5)
    assert record["payload_fields"] == [
        "location",
        "motion",
        "next_signal",
        "vehicle_id",
    ]
    assert record["drop_reason"] is None
    json.dumps(batch, allow_nan=False)

    assert transport.drain()["event_count"] == 3
    assert transport.drain()["event_count"] == 0


def test_ttl_expiry_has_drop_reason_and_never_delivers() -> None:
    transport = IdealPhasedTransport()
    message = _vehicle_state(ttl_s=1.0)
    transport.send(message)

    assert transport.deliver(message.snapshot_id, "state", 11.01) == ()
    record = transport.event_batch()["events"][-1]
    assert record["event"] == "TTL_EXPIRED"
    assert record["drop_reason"] == "ttl_expired"
    assert record["message_age_s"] == pytest.approx(1.01)


def test_bounded_sink_and_sink_failure_do_not_change_transport() -> None:
    drain = V2XEventDrain(max_events=2)
    transport = IdealPhasedTransport(event_sink=drain)
    message = _vehicle_state()
    transport.send(message)
    transport.deliver(message.snapshot_id, "state", 10.0)
    transport.consume(message, sim_time=10.0)

    assert drain.dropped_events == 1
    assert [row["event"] for row in drain.drain()] == [
        "DELIVER",
        "CONSUME",
    ]

    def broken_sink(_event):
        raise RuntimeError("backend unavailable")

    isolated = IdealPhasedTransport(event_sink=broken_sink)
    isolated.send(_vehicle_state())
    assert isolated.sink_error_count == 1
    assert len(isolated.trace()) == 1


def test_causal_parent_must_be_known_and_same_snapshot() -> None:
    transport = IdealPhasedTransport()
    with pytest.raises(ValueError, match="unknown causal parent"):
        transport.send(
            TypedEnvelope(
                "RegionalPriorityV1",
                "episode-a:0:priority",
                "episode-a:0",
                "cloud",
                "demo_1",
                10.0,
                5.0,
                "cloud",
                {"intersection_id": "demo_1", "priority": 0.5},
                causal_parents=("missing",),
            )
        )


def test_json_schema_matches_emitted_batch_shape() -> None:
    schema_path = (
        Path(__file__).parent
        / "communication"
        / "v2x_event_batch_v1.schema.json"
    )
    schema = json.loads(schema_path.read_text(encoding="utf-8"))
    transport = IdealPhasedTransport()
    message = _vehicle_state()
    transport.send(message)
    batch = transport.event_batch()

    assert schema["properties"]["schema"]["const"] == batch["schema"]
    assert schema["properties"]["schema_version"]["const"] == (
        batch["schema_version"]
    )
    assert set(schema["required"]) == set(batch)
    event_schema = schema["$defs"]["event"]
    assert set(event_schema["required"]) == set(batch["events"][0])
