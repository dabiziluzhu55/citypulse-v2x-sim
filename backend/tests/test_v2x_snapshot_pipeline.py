"""V2X algorithm response and distributed snapshot regression tests."""

from simulation.sumo.algorithm.policy_transport import validate_step_response
from simulation.sumo.engine.distributed.codec import dumps_snapshot, loads_snapshot
from simulation.sumo.engine.session import SimulationSnapshot


def _event() -> dict[str, object]:
    return {
        "schema": "cov2x.v2x.event",
        "schema_version": "1.0",
        "sequence": 1,
        "event": "SEND",
        "message_type": "VehicleStateV1",
        "message_id": "ep-1:0:vehicle:veh-1",
        "episode_id": "ep-1",
        "snapshot_id": "ep-1:0",
        "source_role": "vehicle",
        "source_id": "veh-1",
        "destination_role": "cloud",
        "destination_id": "cloud",
        "logical_phase": "state",
        "event_time_s": 0.0,
        "sent_time_s": 0.0,
        "message_age_s": 0.0,
        "ttl_s": 5.0,
        "expires_at_s": 5.0,
        "causal_parent_ids": [],
        "payload_fields": ["vehicle_id"],
        "drop_reason": None,
    }


def test_step_response_preserves_inline_v2x_events() -> None:
    event = _event()
    decision = validate_step_response(
        {
            "protocol_version": "2.0",
            "episode_id": "ep-1",
            "step_id": 0,
            "actions": {"signals": {}, "vehicles": {}},
            "v2x": {
                "schema": "cov2x.v2x.event_batch",
                "schema_version": "1.0",
                "event_count": 1,
                "events": [event],
            },
        },
        episode_id="ep-1",
        step_id=0,
        source="test algorithm",
    )

    assert decision.v2x_events == (event,)


def test_distributed_snapshot_roundtrip_preserves_v2x_events() -> None:
    snapshot = SimulationSnapshot(
        session_id="ep-1",
        state="RUNNING",
        sequence=1,
        elapsed_seconds=1.0,
        duration_seconds=60.0,
        progress=1 / 60,
        official_time="07:00:01",
        v2x_events=(_event(),),
    )

    restored = loads_snapshot(dumps_snapshot(snapshot))

    assert restored.v2x_events == snapshot.v2x_events
