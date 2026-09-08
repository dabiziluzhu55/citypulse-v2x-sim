"""V2X algorithm response, snapshot serialization, and Redis codec tests."""

from dataclasses import fields
from unittest.mock import MagicMock

from backend.app.services.snapshot_serializer import SnapshotSerializer
from simulation.sumo.algorithm.policy_transport import validate_step_response
from simulation.sumo.engine.distributed.codec import dumps_snapshot, loads_snapshot
from simulation.sumo.engine.session import (
    V2X_EVENT_WINDOW_SIZE,
    SimulationSnapshot,
    _SessionRecord,
)


def _event(
    *,
    sequence: int = 1,
    event: str = "SEND",
    message_type: str = "VehicleStateV1",
    message_id: str = "ep-1:0:vehicle:veh-1",
    source_role: str = "vehicle",
    source_id: str = "veh-1",
    destination_role: str = "cloud",
    destination_id: str = "cloud",
    causal_parent_ids: list[str] | None = None,
    payload_fields: list[str] | None = None,
) -> dict[str, object]:
    return {
        "schema": "cov2x.v2x.event",
        "schema_version": "1.0",
        "sequence": sequence,
        "event": event,
        "message_type": message_type,
        "message_id": message_id,
        "episode_id": "ep-1",
        "snapshot_id": "ep-1:0",
        "source_role": source_role,
        "source_id": source_id,
        "destination_role": destination_role,
        "destination_id": destination_id,
        "logical_phase": "state",
        "event_time_s": 0.0,
        "sent_time_s": 0.0,
        "message_age_s": 0.0,
        "ttl_s": 5.0,
        "expires_at_s": 5.0,
        "causal_parent_ids": list(causal_parent_ids or []),
        "payload_fields": list(payload_fields or ["vehicle_id"]),
        "drop_reason": None,
    }


def _cov2x_routes() -> tuple[dict[str, object], ...]:
    return (
        _event(
            sequence=1,
            message_type="VehicleStateV1",
            message_id="ep-1:0:vehicle:veh-1",
            source_role="vehicle",
            source_id="veh-1",
            destination_role="cloud",
            destination_id="cloud",
        ),
        _event(
            sequence=2,
            message_type="IntersectionSummaryV1",
            message_id="ep-1:0:intersection:demo_1",
            source_role="road",
            source_id="demo_1",
            destination_role="cloud",
            destination_id="cloud",
            payload_fields=["intersection_id", "lanes", "current_phase"],
        ),
        _event(
            sequence=3,
            message_type="RegionalPriorityV1",
            message_id="ep-1:0:cloud-road:demo_1",
            source_role="cloud",
            source_id="cloud",
            destination_role="road",
            destination_id="demo_1",
            causal_parent_ids=["ep-1:0:intersection:demo_1"],
            payload_fields=["intersection_id", "priority"],
        ),
        _event(
            sequence=4,
            message_type="RegionalPriorityV1",
            message_id="ep-1:0:cloud-vehicle:demo_1",
            source_role="cloud",
            source_id="cloud",
            destination_role="vehicle",
            destination_id="vehicle",
            causal_parent_ids=["ep-1:0:intersection:demo_1"],
            payload_fields=["intersection_id", "priority"],
        ),
        _event(
            sequence=5,
            message_type="SPaTV2",
            message_id="ep-1:0:spat:demo_1",
            source_role="road",
            source_id="demo_1",
            destination_role="vehicle",
            destination_id="vehicle",
            causal_parent_ids=["ep-1:0:cloud-road:demo_1"],
            payload_fields=["intersection_id", "current_phase", "stage", "remaining_time_s"],
        ),
        _event(
            sequence=6,
            message_type="MAPV1",
            message_id="ep-1:0:map:demo_1",
            source_role="road",
            source_id="demo_1",
            destination_role="vehicle",
            destination_id="vehicle",
            payload_fields=["intersection_id", "phases", "lanes", "connections", "direct_neighbors"],
        ),
    )


def _step_payload(events: tuple[dict[str, object], ...] | None = None) -> dict[str, object]:
    payload: dict[str, object] = {
        "protocol_version": "2.0",
        "episode_id": "ep-1",
        "step_id": 0,
        "actions": {"signals": {}, "vehicles": {}},
    }
    if events is not None:
        payload["v2x"] = {
            "schema": "cov2x.v2x.event_batch",
            "schema_version": "1.0",
            "event_count": len(events),
            "events": list(events),
        }
    return payload


def test_step_response_preserves_inline_v2x_events() -> None:
    event = _event()
    decision = validate_step_response(
        _step_payload((event,)),
        episode_id="ep-1",
        step_id=0,
        source="test algorithm",
    )

    assert decision.v2x_events == (event,)


def test_step_response_without_v2x_does_not_invent_events() -> None:
    decision = validate_step_response(
        _step_payload(),
        episode_id="ep-1",
        step_id=0,
        source="max_pressure",
    )

    assert decision.v2x_events == ()


def test_step_response_copies_events_without_mutating_source() -> None:
    event = _event()
    decision = validate_step_response(
        _step_payload((event,)),
        episode_id="ep-1",
        step_id=0,
        source="test algorithm",
    )

    copied = decision.v2x_events[0]
    assert copied == event
    assert copied is not event
    copied["source_role"] = "road"
    assert event["source_role"] == "vehicle"


def test_step_response_preserves_cov2x_routes_and_causal_parents() -> None:
    events = _cov2x_routes()
    decision = validate_step_response(
        _step_payload(events),
        episode_id="ep-1",
        step_id=0,
        source="traffic_control.cov2x",
    )

    routes = {
        (
            row["message_type"],
            row["source_role"],
            row["destination_role"],
            row["destination_id"],
        )
        for row in decision.v2x_events
    }
    assert routes == {
        ("VehicleStateV1", "vehicle", "cloud", "cloud"),
        ("IntersectionSummaryV1", "road", "cloud", "cloud"),
        ("RegionalPriorityV1", "cloud", "road", "demo_1"),
        ("RegionalPriorityV1", "cloud", "vehicle", "vehicle"),
        ("SPaTV2", "road", "vehicle", "vehicle"),
        ("MAPV1", "road", "vehicle", "vehicle"),
    }
    assert decision.v2x_events[2]["causal_parent_ids"] == ["ep-1:0:intersection:demo_1"]
    assert "VehicleAdviceV1" not in {row["message_type"] for row in decision.v2x_events}


def test_distributed_snapshot_roundtrip_preserves_v2x_events() -> None:
    events = _cov2x_routes()
    snapshot = SimulationSnapshot(
        session_id="ep-1",
        state="RUNNING",
        sequence=1,
        elapsed_seconds=1.0,
        duration_seconds=60.0,
        progress=1 / 60,
        official_time="07:00:01",
        v2x_events=events,
    )

    restored = loads_snapshot(dumps_snapshot(snapshot))

    assert restored.v2x_events == snapshot.v2x_events


def test_snapshot_serializer_forwards_v2x_events(
    coordinate_converter: MagicMock,
) -> None:
    events = _cov2x_routes()
    serializer = SnapshotSerializer(coordinate_converter)
    payload = serializer.serialize(
        SimulationSnapshot(
            session_id="ep-1",
            state="RUNNING",
            sequence=1,
            elapsed_seconds=1.0,
            duration_seconds=60.0,
            progress=1 / 60,
            official_time="07:00:01",
            v2x_events=events,
        )
    )

    assert payload["v2x_events"] == list(events)


def test_session_v2x_window_is_bounded_and_keeps_latest() -> None:
    field = next(item for item in fields(_SessionRecord) if item.name == "v2x_events")
    window = field.default_factory()
    assert window.maxlen == V2X_EVENT_WINDOW_SIZE
    assert V2X_EVENT_WINDOW_SIZE >= 4_000

    overflow = V2X_EVENT_WINDOW_SIZE + 25
    for index in range(overflow):
        window.append({"sequence": index})
    assert len(window) == V2X_EVENT_WINDOW_SIZE
    assert window[0]["sequence"] == 25
    assert window[-1]["sequence"] == overflow - 1
