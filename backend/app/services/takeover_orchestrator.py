"""Backend orchestration for event-scoped Qwen signal control.

The orchestrator is fed by the existing per-session snapshot watcher.  It
pauses simulation time while building one bounded control context and asking
Qwen for a plan, then sends only the validated JSON plan to the SUMO manager.
The worker remains the final safety boundary.
"""

from __future__ import annotations

import json
import logging
import threading
from dataclasses import dataclass, field
from typing import Any, Mapping, Sequence

from simulation.sumo.engine.ai_control import (
    AIControlPlan,
    AIControlValidationError,
)
from simulation.sumo.engine.session import SimulationSnapshot

from ..copilot.llm import LLMProvider
from ..copilot.rag import (
    KnowledgeQuery,
    KnowledgeRetriever,
    KnowledgeUnavailableError,
)
from .history import HistoryQuery, HistoryRepository, HistoryUnavailableError

logger = logging.getLogger(__name__)


class TakeoverPlanningError(RuntimeError):
    """A planning failure that must result in a baseline fallback."""

    def __init__(self, message: str, *, rag_status: str | None = None) -> None:
        super().__init__(message)
        self.rag_status = rag_status


def _parse_control_plan_content(raw_content: str) -> Mapping[str, Any]:
    """Extract the first JSON object from a Qwen control-plan response.

    Chat models occasionally wrap an otherwise valid object in a Markdown
    fence or a short explanation.  ``json.loads`` rejects that harmless
    decoration with ``Extra data``; ``raw_decode`` lets us accept the object
    while the existing AIControlPlan validation still enforces every safety
    and runtime constraint.
    """

    content = raw_content.strip()
    decoder = json.JSONDecoder()
    for index, character in enumerate(content):
        if character != "{":
            continue
        try:
            payload, _ = decoder.raw_decode(content, index)
        except json.JSONDecodeError:
            continue
        if isinstance(payload, Mapping):
            return payload
    raise json.JSONDecodeError("No JSON control-plan object found", content, 0)


@dataclass
class _PlanningState:
    event_id: str | None = None
    failures: int = 0
    next_retry_seconds: float = 0.0
    planning: bool = False
    last_replan_signature: tuple[Any, ...] | None = None
    replan_requested: bool = False
    rag_cache: dict[tuple[str, str | None, tuple[str, ...]], tuple[dict[str, Any], ...]] = field(
        default_factory=dict
    )


class TakeoverOrchestrator:
    """Generate, validate and install plans for configured disturbance events."""

    def __init__(
        self,
        *,
        manager: Any,
        settings: Any,
        history_repository: HistoryRepository,
    ) -> None:
        self._manager = manager
        self._settings = settings
        self._history_repository = history_repository
        self._provider: LLMProvider | None = None
        self._retriever: KnowledgeRetriever | None = None
        self._topology: Any = None
        self._states: dict[str, _PlanningState] = {}
        self._lock = threading.RLock()

    def configure(self, *, provider=None, retriever=None, topology=None) -> None:
        self._provider = provider
        self._retriever = retriever
        self._topology = topology

    def observe(
        self,
        snapshot: SimulationSnapshot,
        *,
        intelligence: Mapping[str, Any] | None = None,
        preset_id: str | None = None,
        baseline_controller: str | None = None,
    ) -> None:
        """Consume one watcher snapshot and plan only when a plan is due."""

        event = _active_ai_event(snapshot.events)
        if event is None:
            with self._lock:
                self._states.pop(snapshot.session_id, None)
            return
        if snapshot.state != "RUNNING":
            return

        status = snapshot.ai_takeover
        intelligence_payload = intelligence or {}
        replan_signature = _replan_signature(event, intelligence_payload)
        with self._lock:
            state = self._states.setdefault(snapshot.session_id, _PlanningState())
            if state.event_id != event.event_id:
                state.event_id = event.event_id
                state.failures = 0
                state.next_retry_seconds = 0.0
                state.last_replan_signature = None
                state.replan_requested = False
            if state.last_replan_signature is None:
                state.last_replan_signature = replan_signature
            elif replan_signature != state.last_replan_signature:
                # Coarse event-risk/prediction changes are a valid reason to
                # replan before the current 30-second window expires.  The
                # signature deliberately ignores noisy continuous values.
                state.replan_requested = True
            if state.planning:
                return
            if status.state == "ACTIVE":
                if (
                    not state.replan_requested
                    and (
                        status.plan_valid_until is None
                        or snapshot.elapsed_seconds < status.plan_valid_until - 1e-6
                    )
                ):
                    return
            elif status.state == "FALLBACK":
                # Fixed-time recovery keeps the affected intersections under
                # SafePhaseController until the original program is restored.
                # Do not queue a new Qwen plan on top of that transition.
                if status.controlled_intersections:
                    return
                if (
                    state.failures >= self._settings.ai_control_config.max_plan_failures
                    or snapshot.elapsed_seconds < state.next_retry_seconds - 1e-6
                ):
                    return
            elif status.state not in {"INACTIVE", "ARMED", "FINISHED"}:
                return
            state.planning = True

        try:
            self._plan_and_install(
                snapshot,
                event,
                intelligence=intelligence_payload,
                preset_id=preset_id,
                baseline_controller=baseline_controller,
                state=state,
            )
            with self._lock:
                state.last_replan_signature = replan_signature
                state.replan_requested = False
        except Exception as exc:
            self._handle_failure(snapshot, event.event_id, exc, state)
        finally:
            with self._lock:
                state.planning = False

    def _plan_and_install(
        self,
        snapshot: SimulationSnapshot,
        event,
        *,
        intelligence: Mapping[str, Any],
        preset_id: str | None,
        baseline_controller: str | None,
        state: _PlanningState,
    ) -> None:
        if self._provider is None:
            raise TakeoverPlanningError("Qwen provider is unavailable.")

        # Pause before reading any runtime-dependent context.  The watcher
        # snapshot may be a little older than the worker, while the completed
        # pause command gives us one consistent SUMO time for current state,
        # history, RAG context, and the Qwen request.
        paused = False
        self._manager.pause(snapshot.session_id)
        paused = True
        try:
            live = self._manager.snapshot(snapshot.session_id)
            live_event = next(
                (
                    item
                    for item in live.events
                    if item.event_id == event.event_id and item.state == "ACTIVE"
                ),
                None,
            )
            if live_event is None or not bool(
                live_event.details.get("ai_control_enabled", False)
            ):
                raise TakeoverPlanningError(
                    "AI event is no longer active after pausing the simulation."
                )
            allowed_scope = self.allowed_scope(live, live_event)
            if not allowed_scope:
                raise TakeoverPlanningError(
                    "AI event has no intersection in the active session."
                )
            phase_orders = self._phase_orders_for_scope(allowed_scope)
            rag_items = self._knowledge_for_event(
                live_event,
                allowed_scope,
                preset_id=preset_id,
                state=state,
            )
            history_payload = self._history_for_scope(
                live.session_id,
                allowed_scope,
            )
            context = self._build_context(
                live,
                live_event,
                allowed_scope,
                intelligence=intelligence,
                history=history_payload,
                rag=rag_items,
                preset_id=preset_id,
                baseline_controller=baseline_controller,
                phase_orders=phase_orders,
            )
            completion = self._provider.complete(
                [
                    {
                        "role": "system",
                        "content": _CONTROL_SYSTEM_PROMPT,
                    },
                    {
                        "role": "user",
                        "content": json.dumps(
                            context,
                            ensure_ascii=False,
                            separators=(",", ":"),
                        ),
                    },
                ],
                tools=(),
                tool_choice=None,
                temperature=0.0,
                max_tokens=max(700, int(getattr(self._settings, "citypulse_qwen_max_tokens", 512))),
            )
            raw_content = completion.message.content
            if not isinstance(raw_content, str) or not raw_content.strip():
                raise TakeoverPlanningError("Qwen returned an empty control plan.")
            try:
                plan = AIControlPlan.from_mapping(
                    _parse_control_plan_content(raw_content),
                    config=self._settings.ai_control_config,
                )
            except (json.JSONDecodeError, AIControlValidationError) as exc:
                raise TakeoverPlanningError(
                    f"Qwen returned an invalid control plan: {exc}"
                ) from exc
            if not set(plan.controlled_intersections) <= set(allowed_scope):
                raise TakeoverPlanningError(
                    "Qwen selected an intersection outside the allowed scope."
                )
            if phase_orders:
                try:
                    plan.validate_runtime(
                        allowed_scope=allowed_scope,
                        phase_orders=phase_orders,
                    )
                except AIControlValidationError as exc:
                    raise TakeoverPlanningError(
                        f"Qwen returned phases outside the runtime phase order: {exc}"
                    ) from exc
            latest = self._manager.snapshot(snapshot.session_id)
            if latest.state not in {"PAUSED", "RUNNING"}:
                raise TakeoverPlanningError(
                    f"Session is no longer available for AI takeover: {latest.state}."
                )
            plan_started_at = float(latest.elapsed_seconds)
            plan_sequence = int(latest.ai_takeover.plan_sequence) + 1
            payload = {
                "event_id": live_event.event_id,
                "plan": plan.to_dict(),
                "allowed_scope": list(allowed_scope),
                "plan_id": f"{snapshot.session_id}:{live_event.event_id}:{plan_sequence}",
                "plan_started_at": plan_started_at,
                "rag_status": "ready",
            }
            self._manager.install_ai_plan(snapshot.session_id, payload)
            state.failures = 0
            state.next_retry_seconds = (
                plan_started_at + self._settings.ai_control_config.replan_seconds
                if plan.fallback_to_baseline
                else 0.0
            )
        finally:
            if paused:
                try:
                    self._manager.resume(snapshot.session_id)
                except Exception:
                    logger.exception(
                        "Failed to resume session after AI planning: %s",
                        snapshot.session_id,
                    )

    def _handle_failure(
        self,
        snapshot: SimulationSnapshot,
        event_id: str,
        error: Exception,
        state: _PlanningState,
    ) -> None:
        state.failures += 1
        state.next_retry_seconds = (
            snapshot.elapsed_seconds + self._settings.ai_control_config.replan_seconds
        )
        reason = str(error) or error.__class__.__name__
        if isinstance(error, TakeoverPlanningError):
            rag_status = error.rag_status
        elif isinstance(error, KnowledgeUnavailableError):
            rag_status = "unavailable"
        else:
            rag_status = None

        # Planning runs in the metrics watcher while the SUMO worker continues
        # to publish snapshots.  The event may therefore finish (or the
        # worker may enter recovery) after the planning snapshot was captured
        # but before Qwen returns.  Do not overwrite that newer recovery state
        # with a late fallback command.
        try:
            latest = self._manager.snapshot(snapshot.session_id)
        except Exception:
            # A snapshot read failure should not hide the original planning
            # failure; the fallback command below remains the safe default.
            latest = None
        if latest is not None:
            latest_event = _active_ai_event(latest.events)
            if latest.state in {"STOPPED", "COMPLETED", "FAILED"} or (
                latest_event is None
                or latest_event.event_id != event_id
                or latest.ai_takeover.state == "RECOVERY"
            ):
                logger.info(
                    "Skip late AI fallback: session=%s event=%s state=%s",
                    snapshot.session_id,
                    event_id,
                    latest.state,
                )
                return
        try:
            self._manager.fallback_ai_control(
                snapshot.session_id,
                {
                    "event_id": event_id,
                    "reason": reason,
                    "rag_status": rag_status,
                },
            )
        except Exception:
            logger.exception(
                "Failed to install AI fallback for session %s",
                snapshot.session_id,
            )
        logger.warning(
            "AI takeover fallback: session=%s event=%s failure=%s/%s reason=%s",
            snapshot.session_id,
            event_id,
            state.failures,
            self._settings.ai_control_config.max_plan_failures,
            reason,
        )

    def allowed_scope(
        self,
        snapshot: SimulationSnapshot,
        event,
    ) -> tuple[str, ...]:
        session_intersections = set(str(item) for item in snapshot.intersections)
        targets = _event_intersections(snapshot, event, self._topology)
        if not targets:
            return ()
        scope = set(targets)
        frontier = set(targets)
        for _ in range(self._settings.ai_control_config.scope_hops):
            next_frontier: set[str] = set()
            for intersection_id in frontier:
                if self._topology is None:
                    continue
                next_frontier.update(
                    str(item)
                    for item in self._topology.upstream_intersections.get(intersection_id, ())
                )
                next_frontier.update(
                    str(item)
                    for item in self._topology.downstream_intersections.get(intersection_id, ())
                )
            next_frontier -= scope
            scope.update(next_frontier)
            frontier = next_frontier
        return tuple(sorted(scope & session_intersections))

    def _knowledge_for_event(
        self,
        event,
        allowed_scope: Sequence[str],
        *,
        preset_id: str | None,
        state: _PlanningState,
    ) -> tuple[dict[str, Any], ...]:
        key = (str(event.event_type), preset_id, tuple(sorted(allowed_scope)))
        if key in state.rag_cache:
            return state.rag_cache[key]
        if self._retriever is None:
            raise TakeoverPlanningError("Traffic knowledge RAG is unavailable.", rag_status="unavailable")
        query = (
            f"{event.event_type} event signal control for intersections "
            f"{', '.join(sorted(allowed_scope))}; protect upstream and downstream traffic"
        )
        try:
            response = self._retriever.search(
                KnowledgeQuery(
                    query=query,
                    limit=5,
                    profile="control",
                    event_type=str(event.event_type),
                    preset_id=preset_id,
                )
            )
        except KnowledgeUnavailableError as exc:
            raise TakeoverPlanningError(
                "Traffic knowledge RAG is unavailable.", rag_status="unavailable"
            ) from exc
        if response.search_mode != "vector":
            raise TakeoverPlanningError(
                "Traffic knowledge RAG did not return vector results.",
                rag_status="invalid",
            )
        results = tuple(result.as_dict() for result in response.results[:5])
        if not results:
            raise TakeoverPlanningError(
                "Traffic knowledge RAG returned no control guidance.",
                rag_status="empty",
            )
        state.rag_cache[key] = results
        return results

    def _history_for_scope(
        self,
        session_id: str,
        allowed_scope: Sequence[str],
    ) -> Mapping[str, Any]:
        try:
            result = self._history_repository.query(
                HistoryQuery(
                    session_id=session_id,
                    intersection_ids=tuple(allowed_scope),
                    lookback_seconds=300.0,
                    max_points=12,
                )
            )
        except HistoryUnavailableError:
            return {"status": "unavailable", "frames": [], "events": []}
        return {
            "status": "ready",
            "frames": list(result.frames[-12:]),
            "events": list(result.events[-20:]),
            "downsampled": result.downsampled,
        }

    def _phase_orders_for_scope(
        self, allowed_scope: Sequence[str]
    ) -> dict[str, tuple[int, ...]]:
        """Return manifest-backed phase IDs for the model and early validation."""

        raw_phase_orders = getattr(self._topology, "phase_orders", {})
        if not isinstance(raw_phase_orders, Mapping):
            return {}
        result: dict[str, tuple[int, ...]] = {}
        for intersection_id in allowed_scope:
            raw_phases = raw_phase_orders.get(str(intersection_id), ())
            if not isinstance(raw_phases, Sequence) or isinstance(
                raw_phases, (str, bytes)
            ):
                continue
            phases = tuple(int(value) for value in raw_phases)
            if phases:
                result[str(intersection_id)] = phases
        return result

    @staticmethod
    def _build_context(
        snapshot: SimulationSnapshot,
        event,
        allowed_scope: Sequence[str],
        *,
        intelligence: Mapping[str, Any],
        history: Mapping[str, Any],
        rag: Sequence[Mapping[str, Any]],
        preset_id: str | None,
        baseline_controller: str | None,
        phase_orders: Mapping[str, Sequence[int]] | None = None,
    ) -> dict[str, Any]:
        intersections: dict[str, Any] = {}
        for intersection_id in allowed_scope:
            intersection = snapshot.intersections.get(intersection_id)
            if intersection is None:
                continue
            intersection_payload: dict[str, Any] = {
                "current_phase": int(intersection.current_phase),
                "pending_phase": intersection.pending_phase,
                "stage": str(intersection.stage),
                "stage_elapsed": float(intersection.stage_elapsed),
                "lanes": {
                    str(lane_id): {
                        "vehicle_count": int(lane.vehicle_count),
                        "halting_count": int(lane.halting_count),
                        "mean_speed": round(float(lane.mean_speed), 3),
                        "waiting_time": round(float(lane.waiting_time), 3),
                        "occupancy": round(float(lane.occupancy), 3),
                        "signal_state": lane.signal_state,
                    }
                    for lane_id, lane in intersection.lanes.items()
                },
            }
            raw_phase_order = (phase_orders or {}).get(str(intersection_id), ())
            if raw_phase_order:
                intersection_payload["allowed_phase_ids"] = [
                    int(value) for value in raw_phase_order
                ]
            intersections[intersection_id] = intersection_payload
        return {
            "session_id": snapshot.session_id,
            "simulation_time": float(snapshot.elapsed_seconds),
            "scenario_preset_id": preset_id,
            "baseline_controller": baseline_controller,
            "event": {
                "event_id": event.event_id,
                "event_type": event.event_type,
                "start_seconds": event.start_seconds,
                "end_seconds": event.end_seconds,
                "details": dict(event.details),
            },
            "allowed_scope": list(allowed_scope),
            "intersections": intersections,
            "event_detection": _bounded(intelligence.get("event_detection", {})),
            "prediction": _bounded(intelligence.get("prediction", {})),
            "history": _bounded(history),
            "knowledge": list(rag),
        }


def _replan_signature(
    event: Any,
    intelligence: Mapping[str, Any],
) -> tuple[Any, ...]:
    """Return coarse changes that justify an early replan.

    Detection cards and prediction ratios contain noisy continuous values.  A
    signature made from their categorical transitions avoids sending a Qwen
    request on every 0.5-second snapshot while still reacting to a new risk
    level, traffic-state transition, affected scope, or a material prediction
    change.
    """

    detection = intelligence.get("event_detection", {})
    cards = detection.get("cards", ()) if isinstance(detection, Mapping) else ()
    card_signature: list[tuple[Any, ...]] = []
    if isinstance(cards, Sequence) and not isinstance(cards, (str, bytes)):
        for card in cards:
            if not isinstance(card, Mapping):
                continue
            raw_lanes = card.get("lane_ids", ())
            lane_ids = (
                tuple(sorted(str(item) for item in raw_lanes))
                if isinstance(raw_lanes, Sequence) and not isinstance(raw_lanes, (str, bytes))
                else ()
            )
            card_signature.append(
                (
                    str(card.get("event_id", "")),
                    str(card.get("intersection_id", "")),
                    str(card.get("traffic_state", "")),
                    str(card.get("severity", "")),
                    str(card.get("status", "")),
                    str(card.get("edge_id", "")),
                    lane_ids,
                )
            )

    prediction = intelligence.get("prediction", {})
    rows = prediction.get("intersections", {}) if isinstance(prediction, Mapping) else {}
    prediction_signature: list[tuple[str, str]] = []
    if isinstance(rows, Mapping):
        for intersection_id, row in sorted(rows.items(), key=lambda item: str(item[0])):
            if not isinstance(row, Mapping):
                continue
            try:
                ratio = float(row.get("delta_ratio"))
            except (TypeError, ValueError):
                bucket = "unknown"
            else:
                if ratio >= 0.2:
                    bucket = "rising"
                elif ratio <= -0.2:
                    bucket = "falling"
                else:
                    bucket = "stable"
            prediction_signature.append((str(intersection_id), bucket))

    return (
        str(event.event_id),
        str(event.event_type),
        tuple(sorted(card_signature)),
        tuple(prediction_signature),
    )


def _active_ai_event(events: Sequence[Any]):
    for event in events:
        if event.state == "ACTIVE" and bool(event.details.get("ai_control_enabled", False)):
            return event
    return None


def _event_intersections(snapshot: SimulationSnapshot, event, topology) -> set[str]:
    targets: set[str] = set()
    details = event.details if isinstance(event.details, Mapping) else {}
    raw_lanes: list[str] = []
    for key in ("lane_id", "venue_lane_id"):
        value = details.get(key)
        if value:
            raw_lanes.append(str(value))
    for key in ("lane_ids", "source_lane_ids", "destination_lane_ids"):
        value = details.get(key, ())
        if isinstance(value, Sequence) and not isinstance(value, (str, bytes)):
            raw_lanes.extend(str(item) for item in value)
    if topology is not None:
        targets.update(
            str(topology.lane_to_intersection[lane_id])
            for lane_id in raw_lanes
            if lane_id in topology.lane_to_intersection
        )
    for intersection_id, intersection in snapshot.intersections.items():
        if any(lane_id in intersection.lanes for lane_id in raw_lanes):
            targets.add(str(intersection_id))
    return targets


def _bounded(value: Any, *, max_chars: int = 12_000) -> Any:
    try:
        raw = json.dumps(value, ensure_ascii=False, separators=(",", ":"), default=str)
    except (TypeError, ValueError):
        return {"status": "unavailable"}
    if len(raw) <= max_chars:
        return value
    return {
        "status": "truncated",
        "content": raw[:max_chars],
    }


_CONTROL_SYSTEM_PROMPT = """你是 CityPulse 的高层交通信号控制规划器。
只根据用户事件、当前运行时交通状态、历史趋势、预测和交通工程知识生成一个控制计划。
Runtime Traffic Context 的事实优先于知识库；不要编造缺失数据。

你必须只输出一个严格 JSON 对象，不要输出 Markdown、代码块、解释文字或工具调用。
JSON 必须包含且只能包含以下字段：
controlled_intersections (string array), valid_seconds (number=30),
signal_plan (object: 每个受控路口对应 6 个整数目标相位),
objective (string), reason (string), fallback_to_baseline (boolean)。
`signal_plan` 的键必须是路口 ID，值必须是长度为 6 的整数数组；例如格式为
{"controlled_intersections":["demo_3"],"valid_seconds":30,"signal_plan":{"demo_3":[2,1,2,1,2,1]},"objective":"...","reason":"...","fallback_to_baseline":false}。
绝对不要把 `signal_plan` 写成 {"0":2,"1":2,"2":2,"3":2,"4":2,"5":2} 这样的槽位对象。

只能选择 allowed_scope 中的路口；只能返回 target_phase，不得返回黄灯、全红、持续时间、车辆控制、事件修改或基线切换。
每个路口的目标相位整数必须从 `intersections[路口].allowed_phase_ids` 中选择；这些是 SUMO 的真实相位编号，不是从 0 开始的数组下标，不能凭常见习惯输出 0 或其它列表外编号。数组长度 6 表示 6 个连续的 5 秒决策槽位，不表示有 6 个不同相位。比如 allowed_phase_ids 为 [1,2] 时，合法示例是 [2,1,2,1,2,1]，非法示例是 [1,2,3,4,5,6]。
只要 signal_plan 非空，fallback_to_baseline 必须为 false；只要 fallback_to_baseline 为 true，controlled_intersections 必须为 [] 且 signal_plan 必须为 {}。
如果无法安全规划，返回 controlled_intersections=[]、signal_plan={}、fallback_to_baseline=true，并说明原因。
"""


__all__ = ["TakeoverOrchestrator", "TakeoverPlanningError"]
