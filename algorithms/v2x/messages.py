# algorithms/v2x/messages.py
"""消息草稿/正式消息、必填字段表、稳定哈希与 message_id。"""
from __future__ import annotations

import hashlib
import uuid
from dataclasses import dataclass, field
from typing import Any, Mapping, Optional

MESSAGE_NAMESPACE = uuid.UUID("3f2b8a1e-9c44-4f2d-8e1a-6b7c0d9e5f21")

REQUIRED_FIELDS: Mapping[str, frozenset] = {
    "BSM": frozenset({
        "vehicle_id", "type_id", "position", "motion", "location",
        "route_edges", "next_signal", "front_gap_m", "rear_gap_m", "gap_source",
    }),
    "INTENT": frozenset({
        "vehicle_id", "turn_intent", "lane_change_intent", "estimated_arrival_s",
        "turn_confidence", "lane_change_confidence", "arrival_confidence",
        "intent_origin",
    }),
    "SPaT": frozenset({
        "intersection_id", "current_phase", "stage", "stage_elapsed",
        "connection_signal_states", "remaining_time_s", "next_stage",
        "next_stage_start_time", "schedule_status",
    }),
    "MAP": frozenset({"intersection_id", "phases", "lanes", "connections", "direct_neighbors"}),
    "RSM": frozenset({"rsu_id", "objects"}),
    "RSI": frozenset({"vehicle_id", "target_speed_mps", "target_lane_index", "guidance_type"}),
    "SIGNAL_CONTROL": frozenset({
        "intersection_id", "action", "requested_effective_time",
        "changed", "previous_action", "reason",
    }),
}


def stable_hash01(value: str) -> float:
    digest = hashlib.sha256(value.encode("utf-8")).digest()
    integer = int.from_bytes(digest[:8], "big")
    return integer / 2**64


def make_message_id(
    run_id: str, episode_id: str, source_id: str,
    message_type: str, sequence_no: int,
) -> str:
    return str(uuid.uuid5(
        MESSAGE_NAMESPACE,
        f"{run_id}|{episode_id}|{source_id}|{message_type}|{sequence_no}",
    ))


@dataclass(frozen=True, slots=True)
class MessageDraft:
    message_type: str
    source_id: str
    destination: str
    sim_time: float
    payload: Mapping[str, Any] = field(default_factory=dict)


@dataclass(frozen=True, slots=True)
class V2XMessage:
    message_type: str
    message_id: str
    schema_version: str
    run_id: str
    episode_id: str
    frame_id: str
    sequence_no: int
    sim_time: float
    source_id: str
    destination: str
    correlation_id: Optional[str]
    payload: Mapping[str, Any]

    def to_dict(self) -> dict:
        return {
            "message_type": self.message_type,
            "message_id": self.message_id,
            "schema_version": self.schema_version,
            "run_id": self.run_id,
            "episode_id": self.episode_id,
            "frame_id": self.frame_id,
            "sequence_no": self.sequence_no,
            "sim_time": self.sim_time,
            "source_id": self.source_id,
            "destination": self.destination,
            "correlation_id": self.correlation_id,
            "payload": dict(self.payload),
        }


def validate_draft(draft: MessageDraft) -> None:
    if draft.message_type not in REQUIRED_FIELDS:
        raise ValueError(f"unknown message type: {draft.message_type}")
    missing = REQUIRED_FIELDS[draft.message_type] - frozenset(draft.payload.keys())
    if missing:
        raise ValueError(
            f"{draft.message_type} missing required fields: {sorted(missing)}"
        )
