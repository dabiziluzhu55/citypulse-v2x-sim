import pytest

from algorithms.v2x.collab.proposals import CollabConfig, DecisionMode, GuidanceEmissionMode
from algorithms.v2x.collab.records import (
    arbitration_record, cloud_proposal_record, collab_tick_stats_record,
    InMemoryRecordCollector,
)
from algorithms.v2x.collab.stats import build_collab_summary, pool_collab_summaries
from algorithms.v2x.collab.snapshot import EdgeSnapshot
from config.scenario_presets import ResolvedScenarioScope


SCOPE = ResolvedScenarioScope(source="preset", preset_id="east_dense",
                              managed_ids=("demo_3", "demo_5", "demo_6", "demo_9"))
REGISTERED = tuple(f"demo_{i}" for i in range(1, 21))


class _FakeHub:
    def __init__(self):
        self.sent_records = []
        self.delivery_records = []


def _edge_snapshot():
    return EdgeSnapshot(
        intersection_id="demo_3", sim_time=50.0, phase=1, stage="GREEN",
        stage_elapsed_s=3.0, remaining_time_s=20.0, approaches={},
        connected_vehicles={}, last_delivery_at={"SPaT": 49.9, "MAP": 0.0},
        source_message_ids=("map-demo_3", "spat-demo_3-50"),
        source_frame_ids=("ep1:init", "ep1:step:000010"),
    )


def _tick(funnel=None):
    return collab_tick_stats_record(
        run_id="run1", episode_id="ep1", frame_id="ep1:step:000010",
        sim_time=50.0, baseline_slots=4, decision_records=4,
        status_counts={"proposed": 1, "keep_current": 3},
        validation_counts={"passed": 4},
        proposal_without_baseline=0,
        guidance_funnel=funnel or {
            "connected_seen": 8, "fresh_bsm": 8, "next_signal_known": 8,
            "next_signal_managed": 6, "distance_known": 6,
            "in_horizon_candidates": 4, "raw_proposals": 2,
            "threshold_passed": 2, "dedup_passed": 2,
            "cooldown_passed": 2, "published": 1},
        filter_reason_counts={"next_signal_not_managed": 2})


def _arbitration(proposal_status="proposed", proposed=2, baseline=1,
                 validation="passed", failure=None):
    return arbitration_record(
        run_id="run1", episode_id="ep1", frame_id="ep1:step:000010",
        intersection_id="demo_3", sim_time=50.0, baseline_action=baseline,
        candidate_action=2, proposed_action=proposed, selected_action=baseline,
        proposal_status=proposal_status, validation_status=validation,
        validation_failure_reason=failure, decision_source="baseline",
        selection_status="selected_baseline_shadow", confidence=0.8,
        reason="queue_demand")


def _records(include_guidance=True):
    collector = InMemoryRecordCollector()
    collector.write(_edge_snapshot_record())
    collector.write(_tick())
    collector.write(_arbitration())
    collector.write(cloud_proposal_record(
        run_id="run1", episode_id="ep1", frame_id="ep1:step:000010",
        sim_time=50.0, proposal_type="signal",
        proposal=_signal_proposal()))
    if include_guidance:
        collector.write(cloud_proposal_record(
            run_id="run1", episode_id="ep1", frame_id="ep1:step:000010",
            sim_time=50.0, proposal_type="vehicle_guidance",
            proposal=_guidance_proposal(),
            emitted_message_id="rsi-1",
            next_signal_intersection_id="demo_3"))
    return collector.episode_records


def _edge_snapshot_record():
    from algorithms.v2x.collab.records import edge_snapshot_record
    return edge_snapshot_record(run_id="run1", episode_id="ep1",
                                frame_id="ep1:step:000010",
                                snapshot=_edge_snapshot())


def _signal_proposal():
    from algorithms.v2x.collab.proposals import SignalProposal, SignalDecisionStatus
    return SignalProposal(
        intersection_id="demo_3", status=SignalDecisionStatus.PROPOSED,
        candidate_action=2, proposed_action=2, current_action=1,
        action_scores={1: 1.0, 2: 3.0}, reason="queue_demand",
        confidence=0.8, valid_from=50.0, valid_until=55.0,
        needs_transition=True, decision_frame_id="ep1:step:000010",
        source_message_ids=("spat-demo_3-50",),
        source_frame_ids=("ep1:step:000010",))


def _guidance_proposal():
    from algorithms.v2x.collab.proposals import (
        GuidanceDecisionStatus, VehicleGuidanceProposal,
    )
    return VehicleGuidanceProposal(
        vehicle_id="car1", status=GuidanceDecisionStatus.PROPOSED,
        speed_status=GuidanceDecisionStatus.PROPOSED,
        lane_status=GuidanceDecisionStatus.NO_ACTION_NEEDED,
        current_speed_mps=8.0, target_speed_mps=10.25,
        current_lane_id="A_0", target_lane_id=None, target_lane_index=None,
        guidance_type="speed", reason="speed_catchup", confidence=None,
        valid_from=50.0, valid_until=60.0,
        source_message_ids=("bsm-car1-50", "spat-demo_3-50"),
        source_frame_ids=("ep1:step:000010",))


def test_episode_summary_rates_and_scope():
    hub = _FakeHub()
    hub.sent_records = [
        {"message_id": "m-signal-1", "message_type": "SIGNAL_CONTROL",
         "frame_id": "ep1:step:000010", "source_id": "demo_3"},
        {"message_id": "rsi-1", "message_type": "RSI",
         "frame_id": "ep1:step:000010", "source_id": "cloud"},
    ]
    hub.delivery_records = [
        {"message_id": "rsi-1", "status": "delivered", "delivered_at": 50.05},
    ]
    summary = build_collab_summary(
        records=_records(), config=CollabConfig(), scope=SCOPE,
        registered_ids=REGISTERED, hub=hub,
        run_id="run1", episode_id="ep1")
    collab = summary["collab"]
    assert collab["schema_version"] == "1.0"
    signal = collab["signal"]
    assert signal["baseline_signal_slots"] == 4
    assert signal["decision_record_coverage"] == pytest.approx(1.0)
    assert signal["selectable_output_rate"] == pytest.approx(1 / 4)
    assert signal["suggested_switch_rate"] == pytest.approx(1 / 4)
    assert signal["action_agreement_rate"] == pytest.approx(0.0)  # 1 次 passed 验证、0 次一致 → 0.0
    assert signal["stale_input_rate"] == 0.0
    assert signal["missing_input_rate"] == 0.0
    guidance = collab["guidance"]
    assert guidance["funnel"]["published"] == 1
    assert guidance["rates"]["guidance_generation_rate"] == pytest.approx(0.5)
    assert guidance["rates"]["network_delivery_rate"] == pytest.approx(1.0)
    assert guidance["delivered_count"] == 1
    assert guidance["expired_on_delivery_count"] == 0
    assert collab["arbitration"]["selection_status_counts"] == {
        "selected_baseline_shadow": 1}
    assert collab["validation"]["validation_pass_rate"] == pytest.approx(1.0)
    integrity = collab["integrity"]
    assert integrity["missing_signal_event_refs"] == 0
    assert integrity["orphan_rsi_messages"] == 0
    assert integrity["orphan_rsi_deliveries"] == 0
    assert integrity["duplicate_terminal_delivery_records"] == 0
    scope_block = summary["scope"]
    assert scope_block["source"] == "preset"
    assert scope_block["algorithm_controlled_intersections"] == 4
    assert scope_block["fixed_intersections"] == 16
    assert scope_block["managed_ids"] == list(SCOPE.managed_ids)


def test_pooled_rates_use_summed_denominators():
    summaries = [build_collab_summary(
        records=_records(), config=CollabConfig(), scope=SCOPE,
        registered_ids=REGISTERED, hub=_FakeHub(),
        run_id="run1", episode_id=f"ep{i}") for i in (1, 2)]
    pooled = pool_collab_summaries(summaries)
    assert pooled["pooled_episodes"] == 2
    assert pooled["collab"]["signal"]["baseline_signal_slots"] == 8
    assert pooled["collab"]["guidance"]["funnel"]["published"] == 2
    assert pooled["collab"]["guidance"]["rates"]["guidance_generation_rate"] == \
        pytest.approx(0.5)


def test_zero_denominators_are_null():
    summary = build_collab_summary(
        records=[], config=CollabConfig(), scope=SCOPE,
        registered_ids=REGISTERED, hub=_FakeHub(),
        run_id="run1", episode_id="ep1")
    assert summary["collab"]["signal"]["baseline_signal_slots"] == 0
    assert summary["collab"]["signal"]["decision_record_coverage"] is None
    assert summary["collab"]["guidance"]["rates"]["network_delivery_rate"] is None