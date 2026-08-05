from algorithms.v2x.collab.proposals import (
    DecisionMode, DecisionSource, GuidanceDecisionStatus,
    GuidanceEmissionMode, SignalDecisionStatus, SignalProposal,
)
from algorithms.v2x.collab.records import (
    InMemoryRecordCollector, arbitration_record, cloud_proposal_record,
    collab_episode_end_record, collab_tick_stats_record, edge_snapshot_record,
)
from algorithms.v2x.collab.snapshot import EdgeSnapshot


def _snapshot():
    return EdgeSnapshot(
        intersection_id="i1", sim_time=5.0, phase=1, stage="GREEN",
        stage_elapsed_s=3.0, remaining_time_s=20.0,
        approaches={}, connected_vehicles={},
        last_delivery_at={"SPaT": 5.0, "MAP": 0.0},
        source_message_ids=("map-i1", "spat-i1"),
        source_frame_ids=("ep1:init", "ep1:step:000001"),
    )


def _signal_proposal():
    return SignalProposal(
        intersection_id="i1", status=SignalDecisionStatus.PROPOSED,
        candidate_action=2, proposed_action=2, current_action=1,
        action_scores={1: 1.0, 2: 3.0}, reason="queue_demand",
        confidence=0.8, valid_from=5.0, valid_until=10.0,
        needs_transition=True, decision_frame_id="ep1:step:000001",
        source_message_ids=("spat-i1",), source_frame_ids=("ep1:step:000001",),
    )


def test_collector_reset_and_episode_records():
    collector = InMemoryRecordCollector()
    collector.write(edge_snapshot_record(run_id="run1", episode_id="ep1",
                                         frame_id="ep1:step:000001",
                                         snapshot=_snapshot()))
    assert len(collector.episode_records) == 1
    collector.reset_episode()
    assert collector.episode_records == []


def test_record_schemas_contain_required_keys():
    collector = InMemoryRecordCollector()
    collector.write(edge_snapshot_record(run_id="run1", episode_id="ep1",
                                         frame_id="ep1:step:000001",
                                         snapshot=_snapshot()))
    proposal = _signal_proposal()
    collector.write(cloud_proposal_record(
        run_id="run1", episode_id="ep1", frame_id="ep1:step:000001",
        sim_time=5.0, proposal=proposal, proposal_type="signal"))
    collector.write(arbitration_record(
        run_id="run1", episode_id="ep1", frame_id="ep1:step:000001",
        intersection_id="i1", sim_time=5.0, baseline_action=1,
        candidate_action=2, proposed_action=2, selected_action=1,
        proposal_status="proposed", validation_status="passed",
        validation_failure_reason=None, decision_source="baseline",
        selection_status="selected_baseline_shadow", confidence=0.8,
        reason="queue_demand"))
    collector.write(collab_tick_stats_record(
        run_id="run1", episode_id="ep1", frame_id="ep1:step:000001",
        sim_time=5.0, baseline_slots=1, decision_records=1,
        status_counts={"proposed": 1}, validation_counts={"passed": 1},
        proposal_without_baseline=0, guidance_funnel={"published": 1},
        filter_reason_counts={}))
    collector.write(collab_episode_end_record(
        summary={"collab": {"schema_version": "1.0"}}))
    by_type = {r.record_type: r.data for r in collector.episode_records}
    assert "edge_snapshot" in by_type
    assert by_type["cloud_proposal"]["proposal_type"] == "signal"
    assert by_type["cloud_proposal"]["intersection_id"] == "i1"
    arb = by_type["arbitration"]
    assert arb["signal_event_ref"] == ("run1", "ep1:step:000001", "i1")
    assert arb["selection_status"] == "selected_baseline_shadow"
    tick = by_type["collab_tick_stats"]
    assert tick["signal"]["baseline_slots"] == 1
    assert tick["guidance"]["published"] == 1
    assert by_type["collab_episode_end"]["summary"]["collab"]["schema_version"] == "1.0"


def test_guidance_proposal_record_optional_fields():
    from algorithms.v2x.collab.proposals import VehicleGuidanceProposal
    from algorithms.v2x.collab.records import cloud_proposal_record

    proposal = VehicleGuidanceProposal(
        vehicle_id="car1", status=GuidanceDecisionStatus.PROPOSED,
        speed_status=GuidanceDecisionStatus.PROPOSED,
        lane_status=GuidanceDecisionStatus.NO_ACTION_NEEDED,
        current_speed_mps=8.0, target_speed_mps=10.25,
        current_lane_id="A_0", target_lane_id=None, target_lane_index=None,
        guidance_type="speed", reason="speed_catchup", confidence=None,
        valid_from=5.0, valid_until=15.0,
        source_message_ids=("bsm-car1-5",), source_frame_ids=("ep1:step:000001",),
    )
    record = cloud_proposal_record(
        run_id="run1", episode_id="ep1", frame_id="ep1:step:000001",
        sim_time=5.0, proposal=proposal, proposal_type="vehicle_guidance",
        emitted_message_id="rsi-1", next_signal_intersection_id="i1")
    assert record.data["emitted_message_id"] == "rsi-1"
    assert record.data["next_signal_intersection_id"] == "i1"
