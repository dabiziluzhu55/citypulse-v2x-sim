# algorithms/v2x/hub.py
"""V2XHub：生命周期、两阶段 API、延迟投递、调度/序号、override、统计收集。"""
from __future__ import annotations

import heapq
from dataclasses import dataclass, field
from typing import Any, Callable, Mapping, Optional

from .config import V2XConfig, DOWNSTREAM_TYPES
from .logger import (
    JSONLSink, LogRecord, MessageSink, episode_start_record,
    episode_end_record, message_record, delivery_record,
)
from .messages import V2XMessage, MessageDraft, make_message_id, stable_hash01, validate_draft
from .protocol import build_rsi_draft
from .stats import build_summary  # type: ignore[attr-defined]  # 见 Task 7/11


@dataclass(frozen=True, slots=True)
class FrameContext:
    episode_id: str
    frame_id: str
    sim_time: float
    input_message_ids: tuple[str, ...] = ()


class _Pending:
    __slots__ = ("due", "order", "message", "network_dropped")

    def __init__(self, due: float, order: int, message: V2XMessage,
                 network_dropped: bool) -> None:
        self.due = due
        self.order = order
        self.message = message
        self.network_dropped = network_dropped

    def __lt__(self, other: "_Pending") -> bool:
        return (self.due, self.order) < (other.due, other.order)


def _latest_frame_id(episode_id: str, frame_index: int) -> str:
    return f"{episode_id}:step:{frame_index:06d}"


class V2XHub:
    def __init__(self, config: Optional[V2XConfig] = None,
                 sink: Optional[MessageSink] = None) -> None:
        self.config = config or V2XConfig()
        self._sink = sink
        self._state = "CREATED"
        self._run_id: Optional[str] = None
        self._episode_id: Optional[str] = None
        self._scenario: Optional[Mapping[str, Any]] = None
        self._initial_sim_time = 0.0
        self._frame_index = 0
        self._sim_time = 0.0
        self._sequences: dict[tuple, int] = {}
        self._schedulers: dict[tuple, float] = {}
        self._subscribers: dict[str, list[Callable[[V2XMessage], None]]] = {}
        self._pending: list[_Pending] = []
        self._insertion = 0
        self._last_step_frame: Optional[FrameContext] = None
        self._consumed_frames: set[str] = set()
        self._last_signal_action: dict[str, Optional[int]] = {}
        self._coverage_config: Optional[Any] = None
        # 统计收集
        self.sent_records: list[dict] = []
        self.delivery_records: list[dict] = []
        self._sent_seq: dict[tuple, list[int]] = {}
        self._delivered_seq: dict[tuple, set[int]] = {}
        self._map_versions: dict[str, int] = {}
        self._entity_info: dict[str, Any] = {}
        # 接入方（coslight adapter）登记/感知统计
        self._motor_ids: set[str] = set()
        self._connected_motor_ids: set[str] = set()
        self._rsm_eligible: set[str] = set()
        self._rsm_observed: set[str] = set()
        self._signal_control_count = 0
        self._funnel_requested = 0
        self._funnel_existing = 0
        self._funnel_enabled = 0
        self._funnel_sent = 0
        self._funnel_delivered = 0
        self._funnel_reasons: dict[str, int] = {}

    # ---------- 生命周期 ----------
    def ingest_initialize(
        self, payload: Mapping[str, Any], *,
        run_id: str, episode_id: str,
        scenario: Optional[Mapping[str, Any]] = None,
        initial_sim_time: float = 0.0,
        coverage_config: Optional[Any] = None,
    ) -> FrameContext:
        if self._state not in ("CREATED", "FINISHED"):
            raise ValueError(
                f"ingest_initialize only from CREATED/FINISHED, state={self._state}")
        if not run_id or not episode_id:
            raise ValueError("run_id and episode_id required")
        if self._state == "FINISHED" and episode_id == self._episode_id:
            raise ValueError(
                f"new episode_id must differ from finished episode: {episode_id}")
        # 新 episode（或首次）重置 episode 级状态
        self._run_id = run_id
        self._episode_id = episode_id
        self._scenario = scenario
        self._initial_sim_time = initial_sim_time
        self._sim_time = initial_sim_time
        self._frame_index = 0
        self._sequences = {}
        self._schedulers = {}
        self._pending = []
        self._insertion = 0
        self._last_step_frame = None
        self._consumed_frames = set()
        self._last_signal_action = {}
        self._coverage_config = coverage_config
        self.sent_records = []
        self.delivery_records = []
        self._sent_seq = {}
        self._delivered_seq = {}
        self._map_versions = {}
        self._entity_info = {}
        self._motor_ids = set()
        self._connected_motor_ids = set()
        self._rsm_eligible = set()
        self._rsm_observed = set()
        self._signal_control_count = 0
        self._funnel_requested = 0
        self._funnel_existing = 0
        self._funnel_enabled = 0
        self._funnel_sent = 0
        self._funnel_delivered = 0
        self._funnel_reasons = {}
        frame = FrameContext(episode_id, f"{episode_id}:init", initial_sim_time, ())
        # MAP：每个注册 RSU 一条（无间隔门控）
        intersections = payload.get("intersections") or {}
        for inter_id in intersections:
            self._publish(MessageDraft(
                "MAP", inter_id, "cloud", initial_sim_time,
                {"intersection_id": inter_id,
                 "phases": intersections[inter_id].get("phases") or {},
                 "lanes": intersections[inter_id].get("lanes") or {},
                 "connections": intersections[inter_id].get("connections") or [],
                 "direct_neighbors": intersections[inter_id].get("direct_neighbors") or [],
                 "phase_order": [int(v) for v in (intersections[inter_id].get("phase_order") or [])]},
            ), frame_id=frame.frame_id)
            self._map_versions[inter_id] = 1
        self._state = "ACTIVE"
        if self._sink is not None:
            self._sink.write(episode_start_record(
                run_id=run_id, episode_id=episode_id, scenario=scenario,
                v2x_config=self.config.to_dict(),
                capability_seed=self.config.capability_seed,
                capability_config={
                    "connected_classes": sorted(self.config.connected_classes),
                    "penetration_rate": self.config.penetration_rate,
                },
                map_versions=dict(self._map_versions)))
        return frame

    def ingest_step(self, payload: Mapping[str, Any],
                    intent_overrides: Optional[Mapping[str, MessageDraft]] = None) -> FrameContext:
        if self._state != "ACTIVE":
            raise ValueError(f"ingest_step only from ACTIVE, state={self._state}")
        sim_time = float(payload.get("simulation_time", 0.0))
        if sim_time < self._initial_sim_time:
            raise ValueError(f"step sim_time {sim_time} < initial {self._initial_sim_time}")
        self._advance(sim_time)
        self._sim_time = sim_time
        self._frame_index += 1
        frame = FrameContext(
            self._episode_id or "", _latest_frame_id(self._episode_id or "", self._frame_index),
            sim_time, (),
        )
        self._last_step_frame = frame
        # 上行草稿（由 protocol 适配器生成；本任务先用空占位，Task 9/12 接 protocol）
        drafts: list[MessageDraft] = []
        if intent_overrides:
            drafts = list(intent_overrides.values())
        published = []
        for draft in drafts:
            if draft.message_type == "INTENT" and self._published_intent_this_frame(draft.source_id):
                continue
            self._publish(draft, frame_id=frame.frame_id)
            published.append(draft)
        frame = FrameContext(frame.episode_id, frame.frame_id, frame.sim_time,
                             tuple(published))
        return frame

    def ingest_actions(self, actions: Mapping[str, Any],
                       frame: Optional[FrameContext] = None) -> None:
        if self._state != "ACTIVE":
            raise ValueError("ingest_actions only from ACTIVE")
        if frame is None:
            frame = self._last_step_frame
        if frame is None or frame.frame_id != _latest_frame_id(
                self._episode_id or "", self._frame_index):
            raise ValueError("frame must be the latest step frame of active episode")
        if frame.frame_id in self._consumed_frames:
            raise ValueError(f"frame {frame.frame_id} already consumed")
        # --- 下行影子记录：SignalControlEvent（只记录，不改变原始 actions）---
        signals = actions.get("signals") or {}
        for inter_id, spec in signals.items():
            action = spec.get("target_phase") if isinstance(spec, dict) else spec
            prev = self._last_signal_action.get(inter_id)
            changed = prev is None or action != prev
            self._last_signal_action[inter_id] = action
            self._signal_control_count += 1
            self._publish(MessageDraft(
                "SIGNAL_CONTROL", "cloud", inter_id, frame.sim_time,
                {"intersection_id": inter_id, "action": action,
                 "requested_effective_time": frame.sim_time,
                 "changed": changed, "previous_action": prev, "reason": None},
            ), frame_id=frame.frame_id, correlation_id=frame.frame_id)
        # --- 下行影子记录：RSI（只发给网联车；原始 actions 原样下发）---
        vehicles = actions.get("vehicles") or {}
        for vid, spec in vehicles.items():
            self._funnel_requested += 1
            if vid not in self._entity_info:
                self._funnel_reasons["vehicle_not_found"] = \
                    self._funnel_reasons.get("vehicle_not_found", 0) + 1
                continue
            self._funnel_existing += 1
            if vid not in self._connected_motor_ids:
                self._funnel_reasons["not_v2x_enabled"] = \
                    self._funnel_reasons.get("not_v2x_enabled", 0) + 1
                continue
            if not isinstance(spec, dict) or (
                    "target_speed_mps" not in spec and "target_lane_index" not in spec):
                self._funnel_reasons["invalid_action"] = \
                    self._funnel_reasons.get("invalid_action", 0) + 1
                continue
            self._funnel_enabled += 1
            self._funnel_sent += 1
            self._publish(build_rsi_draft(
                str(vid), spec, sim_time=frame.sim_time),
                frame_id=frame.frame_id, correlation_id=frame.frame_id)
        self._consumed_frames.add(frame.frame_id)

    def finish_episode(self, final_sim_time: float, drain_pending: bool = False) -> dict:
        if self._state != "ACTIVE":
            raise ValueError(f"finish_episode only from ACTIVE, state={self._state}")
        if drain_pending:
            due = max((p.due for p in self._pending), default=final_sim_time)
            self._advance(due)
        else:
            self._advance(final_sim_time)
        # 剩余 pending → episode_ended
        for p in list(self._pending):
            heapq.heappop(self._pending)
            self._record_delivery(p.message, status="dropped", dropped_at=final_sim_time,
                                  processed_at=final_sim_time, drop_reason="episode_ended")
        summary = build_summary(self)
        if self._sink is not None:
            self._sink.write(episode_end_record(summary=summary))
            self._sink.flush()
        self._state = "FINISHED"
        return summary

    def close(self) -> None:
        if self._state == "ACTIVE":
            raise ValueError("close requires no active episode; call finish_episode first")
        if self._sink is not None:
            self._sink.flush()
            self._sink.close()

    # ---------- 发布/订阅/投递 ----------
    def subscribe(self, message_type: str,
                  handler: Callable[[V2XMessage], None]) -> None:
        self._subscribers.setdefault(message_type, []).append(handler)

    def publish(self, draft: MessageDraft, *, frame_id: str,
                correlation_id: Optional[str] = None) -> V2XMessage:
        return self._publish(draft, frame_id=frame_id, correlation_id=correlation_id)

    def _publish(self, draft: MessageDraft, *, frame_id: str,
                 correlation_id: Optional[str] = None) -> V2XMessage:
        validate_draft(draft)
        if self._episode_id is None:
            raise ValueError("no active episode")
        key = (self._run_id, self._episode_id, draft.source_id, draft.message_type)
        seq = self._sequences.get(key, 0) + 1
        self._sequences[key] = seq
        message = V2XMessage(
            message_type=draft.message_type,
            message_id=make_message_id(self._run_id or "", self._episode_id,
                                       draft.source_id, draft.message_type, seq),
            schema_version=self.config.schema_version,
            run_id=self._run_id or "", episode_id=self._episode_id,
            frame_id=frame_id, sequence_no=seq, sim_time=draft.sim_time,
            source_id=draft.source_id, destination=draft.destination,
            correlation_id=correlation_id, payload=dict(draft.payload),
        )
        latency_ms = self.config.latency_ms_for(draft.message_type)
        jitter_score = stable_hash01(
            f"{self.config.network_seed}|jitter|{draft.message_type}|{draft.source_id}|{seq}")
        jitter_ms = (2.0 * jitter_score - 1.0) * self.config.latency_jitter_ms
        latency_ms = max(0.0, latency_ms + jitter_ms)
        scheduled = draft.sim_time + latency_ms / 1000.0
        drop_score = stable_hash01(
            f"{self.config.network_seed}|drop|{draft.message_type}|{draft.source_id}|{seq}")
        network_dropped = drop_score < self.config.drop_rate
        self.sent_records.append({
            "message_id": message.message_id, "message_type": message.message_type,
            "source_id": message.source_id, "destination": message.destination,
            "sim_time": message.sim_time, "frame_id": frame_id,
            "sequence_no": seq, "sent_at": message.sim_time,
            "scheduled_delivery_at": scheduled,
        })
        self._sent_seq.setdefault(key, []).append(seq)
        if self._sink is not None:
            self._sink.write(message_record(
                message=message.to_dict(), sent_at=message.sim_time,
                scheduled_delivery_at=scheduled))
        heapq.heappush(self._pending, _Pending(scheduled, self._insertion,
                                               message, network_dropped))
        self._insertion += 1
        return message

    def advance(self, sim_time: float) -> None:
        self._advance(sim_time)
        self._sim_time = sim_time

    def _advance(self, sim_time: float) -> None:
        eps = self.config.scheduling_epsilon_s
        if sim_time < self._sim_time - eps:
            raise ValueError(f"time regression: {sim_time} < {self._sim_time}")
        while self._pending and self._pending[0].due <= sim_time + eps:
            p = heapq.heappop(self._pending)
            if p.network_dropped:
                self._record_delivery(p.message, status="dropped",
                                      dropped_at=p.due, processed_at=sim_time,
                                      drop_reason="network_drop")
                continue
            for handler in self._subscribers.get(p.message.message_type, []):
                handler(p.message)
            latency_ms = round((p.due - p.message.sim_time) * 1000.0, 6)
            self._record_delivery(p.message, status="delivered", delivered_at=p.due,
                                  processed_at=sim_time, actual_latency_ms=latency_ms)

    def _record_delivery(self, message: V2XMessage, *, status: str,
                         delivered_at: Optional[float] = None,
                         dropped_at: Optional[float] = None,
                         processed_at: Optional[float] = None,
                         actual_latency_ms: Optional[float] = None,
                         drop_reason: Optional[str] = None) -> None:
        self.delivery_records.append({
            "message_id": message.message_id, "status": status,
            "delivered_at": delivered_at, "dropped_at": dropped_at,
            "processed_at": processed_at, "actual_latency_ms": actual_latency_ms,
            "drop_reason": drop_reason,
        })
        key = (self._run_id, self._episode_id, message.source_id, message.message_type)
        if status == "delivered":
            self._delivered_seq.setdefault(key, set()).add(message.sequence_no)
        if message.message_type == "RSI":
            if status == "delivered":
                self._funnel_delivered += 1
            elif drop_reason == "network_drop":
                self._funnel_reasons["message_dropped"] = \
                    self._funnel_reasons.get("message_dropped", 0) + 1
        if self._sink is not None:
            self._sink.write(delivery_record(
                message_id=message.message_id, status=status,
                delivered_at=delivered_at, dropped_at=dropped_at,
                processed_at=processed_at, actual_latency_ms=actual_latency_ms,
                drop_reason=drop_reason))

    # ---------- 调度/序号辅助 ----------
    def sequence_no(self, episode_id: str, source_id: str, message_type: str) -> int:
        key = (self._run_id, episode_id, source_id, message_type)
        return self._sequences.get(key, 0)

    def should_send(self, episode_id: str, source_id: str,
                    message_type: str, sim_time: float) -> bool:
        interval = self.config.interval_for(message_type)
        if interval <= 0.0:
            return False
        key = (self._run_id, episode_id, source_id, message_type)
        next_due = self._schedulers.get(key)
        eps = self.config.scheduling_epsilon_s
        return next_due is None or sim_time + eps >= next_due

    def mark_sent(self, episode_id: str, source_id: str,
                  message_type: str, sim_time: float) -> None:
        interval = self.config.interval_for(message_type)
        key = (self._run_id, episode_id, source_id, message_type)
        if interval > 0.0:
            self._schedulers[key] = sim_time + interval

    def _published_intent_this_frame(self, vehicle_id: str) -> bool:
        # 简化：本实现由 protocol 层保证每车每帧一条 INTENT；此处仅作防御
        return False
