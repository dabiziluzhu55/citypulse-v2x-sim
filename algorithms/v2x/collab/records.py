# algorithms/v2x/collab/records.py
"""InMemoryRecordCollector + 五类协同记录构造（spec §1.7/§4.4/§5.1）。"""
from __future__ import annotations

from typing import Any, Mapping, Optional, Sequence

from ..logger import LogRecord, MessageSink
from .proposals import SignalProposal, VehicleGuidanceProposal
from .snapshot import EdgeSnapshot


class CompositeSink(MessageSink):
    """required 必有（InMemoryRecordCollector）+ optional（JsonlSink）按序写入（spec §6.2）。"""

    def __init__(self, required: Sequence[MessageSink] = (),
                 optional: Sequence[Optional[MessageSink]] = ()) -> None:
        self._sinks: list[MessageSink] = list(required)
        self._sinks.extend(s for s in optional if s is not None)

    def write(self, record: LogRecord) -> None:
        for sink in self._sinks:
            sink.write(record)

    def flush(self) -> None:
        for sink in self._sinks:
            sink.flush()

    def close(self) -> None:
        for sink in self._sinks:
            sink.close()


class InMemoryRecordCollector(MessageSink):
    """内存记录收集器（spec §6.2：collab 开启时必有，不可关闭）。"""

    def __init__(self) -> None:
        self._records: list[LogRecord] = []

    def write(self, record: LogRecord) -> None:
        self._records.append(record)

    def flush(self) -> None:
        return None

    def close(self) -> None:
        return None

    @property
    def episode_records(self) -> list[LogRecord]:
        return list(self._records)

    def reset_episode(self) -> None:
        self._records.clear()


def edge_snapshot_record(
    *, run_id: str, episode_id: str, frame_id: str,
    snapshot: EdgeSnapshot,
) -> LogRecord:
    return LogRecord("edge_snapshot", {
        "run_id": run_id, "episode_id": episode_id, "frame_id": frame_id,
        "intersection_id": snapshot.intersection_id,
        "sim_time": snapshot.sim_time,
        "phase": snapshot.phase, "stage": snapshot.stage,
        "stage_elapsed_s": snapshot.stage_elapsed_s,
        "remaining_time_s": snapshot.remaining_time_s,
        "approaches": {
            aid: {
                "incoming_lane_ids": list(ap.incoming_lane_ids),
                "lane_states": {
                    lid: {
                        "connected_count": lane.connected_count,
                        "observed_count": lane.observed_count,
                        "stopped_count": lane.stopped_count,
                        "queue_estimate": lane.queue_estimate,
                        "arrivals_since_last_snapshot": lane.arrivals_since_last_snapshot,
                    } for lid, lane in ap.lane_states.items()
                },
                "downstream_vehicle_count": ap.downstream_vehicle_count,
                "downstream_queue_estimate": ap.downstream_queue_estimate,
                "turn_intent_counts": dict(ap.turn_intent_counts),
                "arrival_etas_s": list(ap.arrival_etas_s),
            } for aid, ap in snapshot.approaches.items()
        },
        "connected_vehicle_ids": sorted(snapshot.connected_vehicles),
        "last_delivery_at": dict(snapshot.last_delivery_at),
        "source_message_ids": list(snapshot.source_message_ids),
        "source_frame_ids": list(snapshot.source_frame_ids),
    })


def cloud_proposal_record(
    *, run_id: str, episode_id: str, frame_id: str, sim_time: float,
    proposal: SignalProposal | VehicleGuidanceProposal,
    proposal_type: str,  # "signal" | "vehicle_guidance"
    emitted_message_id: Optional[str] = None,
    next_signal_intersection_id: Optional[str] = None,
) -> LogRecord:
    if proposal_type == "signal":
        assert isinstance(proposal, SignalProposal)
        data: Mapping[str, Any] = {
            "proposal_type": "signal",
            "intersection_id": proposal.intersection_id,
            "status": proposal.status.value,
            "candidate_action": proposal.candidate_action,
            "proposed_action": proposal.proposed_action,
            "current_action": proposal.current_action,
            "action_scores": dict(proposal.action_scores),
            "reason": proposal.reason,
            "confidence": proposal.confidence,
            "valid_from": proposal.valid_from,
            "valid_until": proposal.valid_until,
            "needs_transition": proposal.needs_transition,
            "decision_frame_id": proposal.decision_frame_id,
            "source_message_ids": list(proposal.source_message_ids),
            "source_frame_ids": list(proposal.source_frame_ids),
        }
    else:
        assert isinstance(proposal, VehicleGuidanceProposal)
        data = {
            "emitted_message_id": emitted_message_id,
            "next_signal_intersection_id": next_signal_intersection_id,
            "proposal_type": "vehicle_guidance",
            "vehicle_id": proposal.vehicle_id,
            "status": proposal.status.value,
            "speed_status": proposal.speed_status.value,
            "lane_status": proposal.lane_status.value,
            "current_speed_mps": proposal.current_speed_mps,
            "target_speed_mps": proposal.target_speed_mps,
            "current_lane_id": proposal.current_lane_id,
            "target_lane_id": proposal.target_lane_id,
            "target_lane_index": proposal.target_lane_index,
            "guidance_type": proposal.guidance_type,
            "reason": proposal.reason,
            "confidence": proposal.confidence,
            "valid_from": proposal.valid_from,
            "valid_until": proposal.valid_until,
            "source_message_ids": list(proposal.source_message_ids),
            "source_frame_ids": list(proposal.source_frame_ids),
        }
    return LogRecord("cloud_proposal", {
        "run_id": run_id, "episode_id": episode_id, "frame_id": frame_id,
        "sim_time": sim_time, **data,
    })


def arbitration_record(
    *, run_id: str, episode_id: str, frame_id: str, intersection_id: str,
    sim_time: float, baseline_action: Optional[int],
    candidate_action: Optional[int], proposed_action: Optional[int],
    selected_action: Optional[int], proposal_status: Optional[str],
    validation_status: Optional[str],
    validation_failure_reason: Optional[str],
    decision_source: str, selection_status: str,
    confidence: Optional[float], reason: Optional[str],
) -> LogRecord:
    return LogRecord("arbitration", {
        "run_id": run_id, "episode_id": episode_id, "frame_id": frame_id,
        "intersection_id": intersection_id, "sim_time": sim_time,
        "baseline_action": baseline_action,
        "candidate_action": candidate_action,
        "proposed_action": proposed_action,
        "selected_action": selected_action,
        "proposal_status": proposal_status,
        "validation_status": validation_status,
        "validation_failure_reason": validation_failure_reason,
        "decision_source": decision_source,
        "selection_status": selection_status,
        "confidence": confidence, "reason": reason,
        "signal_event_ref": (run_id, frame_id, intersection_id),
    })


def collab_tick_stats_record(
    *, run_id: str, episode_id: str, frame_id: str, sim_time: float,
    baseline_slots: int, decision_records: int,
    status_counts: Mapping[str, int], validation_counts: Mapping[str, int],
    proposal_without_baseline: int,
    guidance_funnel: Mapping[str, int],
    filter_reason_counts: Mapping[str, int],
) -> LogRecord:
    return LogRecord("collab_tick_stats", {
        "run_id": run_id, "episode_id": episode_id,
        "frame_id": frame_id, "sim_time": sim_time,
        "signal": {
            "baseline_slots": baseline_slots,
            "decision_records": decision_records,
            "status_counts": dict(status_counts),
            "validation_counts": dict(validation_counts),
            "proposal_without_baseline": proposal_without_baseline,
        },
        "guidance": {
            **{k: guidance_funnel.get(k, 0) for k in (
                "connected_seen", "fresh_bsm", "next_signal_known",
                "next_signal_managed", "distance_known",
                "in_horizon_candidates", "raw_proposals", "threshold_passed",
                "dedup_passed", "cooldown_passed", "published")},
            **{k: guidance_funnel[k] for k in guidance_funnel
               if k not in (
                   "connected_seen", "fresh_bsm", "next_signal_known",
                   "next_signal_managed", "distance_known",
                   "in_horizon_candidates", "raw_proposals", "threshold_passed",
                   "dedup_passed", "cooldown_passed", "published")
               and k != "filter_reason_counts"},
            "filter_reason_counts": dict(filter_reason_counts),
        },
    })


def collab_episode_end_record(*, summary: Mapping[str, Any]) -> LogRecord:
    return LogRecord("collab_episode_end", {"summary": dict(summary)})
