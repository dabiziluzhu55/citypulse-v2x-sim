"""Backend orchestration for event-scoped Traffic-Qwen signal control.

The orchestrator is fed by the existing per-session snapshot watcher.  It
pauses simulation time while building Observation V2 and asking Traffic-Qwen
for a plan, then sends only the validated JSON plan to the SUMO manager.
The worker remains the final safety boundary.
"""

from __future__ import annotations

import json
import logging
import threading
from dataclasses import dataclass
from typing import Any, Mapping, Sequence

from simulation.sumo.engine.ai_control import (
    AIControlPlan,
)
from simulation.sumo.engine.session import SimulationSnapshot

from algorithms.traffic_llm.dataset.feature_builder import resolve_controlled_region
from algorithms.traffic_llm.dataset.sft_builder import SYSTEM_PROMPT
from algorithms.traffic_llm.deployment.schema import PLAN_JSON_SCHEMA
from algorithms.traffic_llm.evaluation.policy import POLICY_INSTRUCTION

from ..copilot.llm import LLMProvider
from .ai_control_validation import active_ai_control_events
from .history import HistoryRepository
from .traffic_qwen_observation import build_live_observation_v2, neighbors_from_topology

logger = logging.getLogger(__name__)

CONTROL_PLAN_MAX_ATTEMPTS = 2
CONTROL_CONTEXT_MAX_CHARS = 6_000


class TakeoverPlanningError(RuntimeError):
    """A planning failure that must result in a baseline fallback."""

    def __init__(
        self,
        message: str,
        *,
        rag_status: str | None = "not_required",
        event_id: str | None = None,
    ) -> None:
        super().__init__(message)
        self.rag_status = rag_status
        self.event_id = event_id


def _clip_plan_to_scope(
    plan: AIControlPlan,
    allowed_scope: Sequence[str],
    *,
    config: Any,
) -> AIControlPlan:
    """Drop extra intersections instead of failing the whole takeover."""

    allowed = {str(item) for item in allowed_scope}
    extras = [
        intersection_id
        for intersection_id in plan.controlled_intersections
        if intersection_id not in allowed
    ]
    if not extras:
        return plan
    kept = [
        intersection_id
        for intersection_id in plan.controlled_intersections
        if intersection_id in allowed
    ]
    if not kept:
        raise ValueError(
            "Traffic-Qwen selected an intersection outside the allowed scope."
        )
    payload = plan.to_dict()
    payload["controlled_intersections"] = kept
    payload["signal_plan"] = {
        key: value
        for key, value in (payload.get("signal_plan") or {}).items()
        if key in set(kept)
    }
    logger.warning(
        "Clipped Traffic-Qwen plan intersections outside allowed scope: %s",
        extras,
    )
    return AIControlPlan.from_mapping(payload, config=config)


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
    last_prediction_token: tuple[Any, ...] | None = None
    replan_requested: bool = False
    config_error: bool = False


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
        self._topology: Any = None
        self._states: dict[str, _PlanningState] = {}
        self._lock = threading.RLock()

    def configure(self, *, provider=None, retriever=None, topology=None) -> None:
        self._provider = provider
        self._topology = topology

    def observe(
        self,
        snapshot: SimulationSnapshot,
        *,
        intelligence: Mapping[str, Any] | None = None,
        preset_id: str | None = None,
        baseline_controller: str | None = None,
        period: str | None = None,
        seed: int | None = None,
    ) -> None:
        """Consume one watcher snapshot and plan only when a plan is due."""

        try:
            event = _unique_active_ai_event(snapshot.events)
        except TakeoverPlanningError as exc:
            logger.error(
                "AI takeover configuration error: session=%s reason=%s",
                snapshot.session_id,
                exc,
            )
            with self._lock:
                state = self._states.setdefault(snapshot.session_id, _PlanningState())
                already_blocked = state.config_error
                state.config_error = True
            if already_blocked:
                return
            self._handle_failure(
                snapshot,
                str(getattr(exc, "event_id", "") or "multiple_ai_events"),
                exc,
                state,
            )
            return
        if event is None:
            with self._lock:
                self._states.pop(snapshot.session_id, None)
            return
        if snapshot.state != "RUNNING":
            return

        status = snapshot.ai_takeover
        intelligence_payload = intelligence or {}
        allowed_scope = self.allowed_scope(snapshot, event)
        with self._lock:
            state = self._states.setdefault(snapshot.session_id, _PlanningState())
            if state.event_id != event.event_id:
                state.event_id = event.event_id
                state.failures = 0
                state.next_retry_seconds = 0.0
                state.last_replan_signature = None
                state.last_prediction_token = None
                state.replan_requested = False
                state.config_error = False
            replan_signature, prediction_token, prediction_changed = _replan_signature(
                event,
                intelligence_payload,
                allowed_scope=allowed_scope,
                last_prediction_token=state.last_prediction_token,
            )
            state.last_prediction_token = prediction_token
            if state.last_replan_signature is None:
                state.last_replan_signature = replan_signature
            elif replan_signature != state.last_replan_signature or prediction_changed:
                # Coarse event-risk or Narrow-TDP bucket changes can replan
                # before the current 30-second window expires.  Continuous
                # numeric jitter is ignored; unusable prediction is frozen.
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
                period=period,
                seed=seed,
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
        period: str | None,
        seed: int | None,
        state: _PlanningState,
    ) -> None:
        if self._provider is None:
            raise TakeoverPlanningError("Traffic-Qwen provider is unavailable.")

        # Pause before reading any runtime-dependent context.  The watcher
        # snapshot may be a little older than the worker, while the completed
        # pause command gives us one consistent SUMO time for Observation V2
        # and the Traffic-Qwen request.
        paused = False
        self._manager.pause(snapshot.session_id)
        paused = True
        try:
            live = self._manager.snapshot(snapshot.session_id)
            try:
                live_event = _unique_active_ai_event(live.events)
            except TakeoverPlanningError:
                raise
            if live_event is None or live_event.event_id != event.event_id:
                raise TakeoverPlanningError(
                    "AI event is no longer active after pausing the simulation."
                )
            allowed_scope = self.allowed_scope(live, live_event)
            if not allowed_scope:
                raise TakeoverPlanningError(
                    "AI event has no intersection in the active session."
                )
            phase_orders = self._phase_orders_for_scope(allowed_scope)
            primary = _event_intersections(live, live_event, self._topology)
            primary_intersection = next(iter(sorted(primary)), allowed_scope[0])
            observation = build_live_observation_v2(
                live,
                live_event,
                primary_intersection=str(primary_intersection),
                topology=self._topology,
                allowed_phases=phase_orders,
                scope_hops=int(self._settings.ai_control_config.scope_hops),
                preset_id=preset_id,
                period=period,
                seed=seed,
            )
            allowed_scope, phase_orders = self._align_scope_with_observation(
                live,
                observation,
                allowed_scope,
            )
            plan = self._request_valid_plan(
                observation,
                allowed_scope=allowed_scope,
                phase_orders=phase_orders,
            )
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
                "rag_status": "not_required",
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

    def _request_valid_plan(
        self,
        observation: Mapping[str, Any],
        *,
        allowed_scope: Sequence[str],
        phase_orders: Mapping[str, Sequence[int]],
    ) -> AIControlPlan:
        """Request a plan and validate it before returning it to the worker.

        Structured decoding is a generation constraint only.  Backend still
        runs AIControlPlan.from_mapping and runtime phase/region checks.
        """

        if self._provider is None:
            raise TakeoverPlanningError("Traffic-Qwen provider is unavailable.")

        user_payload = {
            "instruction": POLICY_INSTRUCTION,
            "observation": dict(observation),
        }
        context_message = json.dumps(
            user_payload,
            ensure_ascii=False,
            separators=(",", ":"),
        )
        logger.debug(
            "AI control Observation V2 prepared: session_t=%s region=%s",
            observation.get("scene", {}),
            observation.get("controlled_region"),
        )
        max_tokens = max(
            512,
            int(
                getattr(self._settings, "resolved_ai_control_max_tokens", None)
                or getattr(self._settings, "citypulse_ai_control_max_tokens", None)
                or getattr(self._settings, "citypulse_qwen_max_tokens", 512)
            ),
        )
        last_error: ValueError | None = None

        for attempt in range(1, CONTROL_PLAN_MAX_ATTEMPTS + 1):
            system_prompt = SYSTEM_PROMPT
            if attempt > 1:
                system_prompt += _CONTROL_RETRY_PROMPT
            completion = self._provider.complete(
                [
                    {
                        "role": "system",
                        "content": system_prompt,
                    },
                    {
                        "role": "user",
                        "content": context_message,
                    },
                ],
                tools=(),
                tool_choice=None,
                temperature=0.0,
                max_tokens=max_tokens,
                response_format={
                    "type": "json_schema",
                    "json_schema": {
                        "name": "AIControlPlan",
                        "schema": PLAN_JSON_SCHEMA,
                        "strict": True,
                    },
                },
                extra_body={"guided_json": PLAN_JSON_SCHEMA},
            )
            raw_content = completion.message.content
            try:
                if completion.message.tool_calls:
                    raise ValueError(
                        "control planner must return JSON content, not tool calls"
                    )
                payload = _decode_control_plan_json(raw_content)
                plan = AIControlPlan.from_mapping(
                    payload,
                    config=self._settings.ai_control_config,
                )
                plan = _clip_plan_to_scope(
                    plan,
                    allowed_scope,
                    config=self._settings.ai_control_config,
                )
                if phase_orders:
                    plan.validate_runtime(
                        allowed_scope=allowed_scope,
                        phase_orders=phase_orders,
                    )
                return plan
            except (TypeError, ValueError) as exc:
                last_error = exc
                preview = (
                    raw_content[:300]
                    if isinstance(raw_content, str)
                    else repr(raw_content)
                )
                logger.warning(
                    "Rejected Traffic-Qwen AI control plan attempt %s/%s: "
                    "finish_reason=%s error=%s content_prefix=%r",
                    attempt,
                    CONTROL_PLAN_MAX_ATTEMPTS,
                    completion.finish_reason,
                    exc,
                    preview,
                )

        raise TakeoverPlanningError(
            "Traffic-Qwen returned an invalid control plan after "
            f"{CONTROL_PLAN_MAX_ATTEMPTS} attempts: {last_error}"
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
            rag_status = error.rag_status or "not_required"
        else:
            rag_status = "not_required"

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
            try:
                latest_event = _unique_active_ai_event(latest.events)
            except TakeoverPlanningError:
                latest_event = None
            if latest.state in {"STOPPED", "COMPLETED", "FAILED"} or (
                latest.ai_takeover.state == "RECOVERY"
            ):
                logger.info(
                    "Skip late AI fallback: session=%s event=%s state=%s",
                    snapshot.session_id,
                    event_id,
                    latest.state,
                )
                return
            if (
                latest_event is not None
                and event_id not in {"multiple_ai_events", ""}
                and latest_event.event_id != event_id
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
        targets = _event_intersections(snapshot, event, self._topology)
        if not targets:
            return ()
        return resolve_controlled_region(
            tuple(str(item) for item in snapshot.intersections),
            tuple(sorted(targets)),
            neighbors_from_topology(self._topology),
            int(self._settings.ai_control_config.scope_hops),
        )

    def _align_scope_with_observation(
        self,
        snapshot: SimulationSnapshot,
        observation: Mapping[str, Any],
        allowed_scope: Sequence[str],
    ) -> tuple[tuple[str, ...], dict[str, tuple[int, ...]]]:
        """Keep plan validation on the same region Observation V2 showed the model."""

        session_intersections = {str(item) for item in snapshot.intersections}
        region = [
            str(item)
            for item in observation.get("controlled_region") or ()
            if str(item) in session_intersections
        ]
        if region:
            allowed_scope = tuple(sorted(set(region)))
        return tuple(allowed_scope), self._phase_orders_for_scope(allowed_scope)

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


def _prediction_trend_token(
    intelligence: Mapping[str, Any],
    allowed_scope: Sequence[str],
) -> tuple[Any, ...] | None:
    """Return coarse Narrow-TDP buckets, or None when prediction is not usable."""

    prediction = intelligence.get("prediction", {})
    if not isinstance(prediction, Mapping):
        return None
    if prediction.get("ready") is not True or prediction.get("fallback") is True:
        return None
    rows = prediction.get("intersections", {})
    if not isinstance(rows, Mapping):
        return None
    scope = {str(item) for item in allowed_scope}
    tokens: list[tuple[str, str]] = []
    for intersection_id, row in sorted(rows.items(), key=lambda item: str(item[0])):
        if scope and str(intersection_id) not in scope:
            continue
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
        tokens.append((str(intersection_id), bucket))
    return tuple(tokens)


def _replan_signature(
    event: Any,
    intelligence: Mapping[str, Any],
    *,
    allowed_scope: Sequence[str] = (),
    last_prediction_token: tuple[Any, ...] | None = None,
) -> tuple[tuple[Any, ...], tuple[Any, ...] | None, bool]:
    """Return coarse changes that justify an early replan.

    Prediction buckets are only used when Narrow-TDP is ready and not in
    fallback.  Unusable prediction keeps the last token so fallback/unavailable
    does not itself trigger a prediction-enhanced replan.  The first usable
    prediction is recorded without counting as a bucket change.
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

    usable_prediction = _prediction_trend_token(intelligence, allowed_scope)
    prediction_changed = (
        usable_prediction is not None
        and last_prediction_token is not None
        and usable_prediction != last_prediction_token
    )
    prediction_token = (
        usable_prediction if usable_prediction is not None else last_prediction_token
    )
    signature = (
        str(event.event_id),
        str(event.event_type),
        tuple(sorted(card_signature)),
    )
    return signature, prediction_token, prediction_changed


def _unique_active_ai_event(events: Sequence[Any]):
    matches = active_ai_control_events(events)
    if len(matches) > 1:
        raise TakeoverPlanningError(
            "configuration error: multiple ACTIVE AI-control events; "
            "refusing concurrent Traffic-Qwen plans",
            rag_status="not_required",
            event_id="multiple_ai_events",
        )
    return matches[0] if matches else None


def _active_ai_event(events: Sequence[Any]):
    """Compatibility wrapper. Raises if more than one ACTIVE AI event exists."""

    return _unique_active_ai_event(events)


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


_CONTROL_RETRY_PROMPT = """

这是最后一次输出机会。上一轮结果没有通过后端的 JSON 或安全校验。
请不要输出任何思考过程、前后缀说明、Markdown 代码块或工具调用；只输出一个可被
JSON.parse 直接解析的 JSON 对象。若无法安全规划，严格返回合法的回退 JSON 对象，
不要用自然语言代替字段。
"""


def _decode_control_plan_json(raw_content: object) -> Mapping[str, Any]:
    """Decode the model's JSON while removing only known transport wrappers.

    The control contract remains strict.  We intentionally do not search for
    an arbitrary ``{...}`` substring in prose, because that could silently
    accept an incomplete or unrelated object.  Only a complete Markdown JSON
    fence and a leading, complete ``<think>...</think>`` block are normalized.
    """

    if not isinstance(raw_content, str) or not raw_content.strip():
        raise ValueError("Qwen returned an empty control plan.")

    text = raw_content.strip()
    if text.startswith("<think>"):
        closing = text.find("</think>")
        if closing < 0:
            raise ValueError("Qwen returned an unterminated think block.")
        text = text[closing + len("</think>") :].strip()

    if text.startswith("```"):
        lines = text.splitlines()
        language = lines[0].strip().lower()
        if len(lines) < 3 or language not in {"```", "```json"}:
            raise ValueError("Qwen returned an invalid JSON code fence.")
        if lines[-1].strip() != "```":
            raise ValueError("Qwen returned an unterminated JSON code fence.")
        text = "\n".join(lines[1:-1]).strip()

    try:
        payload = json.loads(text)
    except json.JSONDecodeError as exc:
        raise ValueError("Qwen response content is not valid JSON.") from exc
    if not isinstance(payload, Mapping):
        raise ValueError("Qwen control plan must be a JSON object.")
    return payload


def _control_context_max_chars(settings: Any) -> int:
    try:
        configured = int(
            getattr(settings, "citypulse_qwen_control_context_max_chars", CONTROL_CONTEXT_MAX_CHARS)
        )
    except (TypeError, ValueError):
        configured = CONTROL_CONTEXT_MAX_CHARS
    # Keep enough room for the system prompt and generation marker.  The
    # Keep the default compatible with both the 4096-token smoke-service
    # default and the current 8192-token school-server configuration.
    return max(6_000, min(configured, 20_000))


def _compact_control_context(
    context: Mapping[str, Any],
    *,
    max_chars: int = CONTROL_CONTEXT_MAX_CHARS,
) -> dict[str, Any]:
    """Build a bounded, valid JSON context for the control planner.

    The Qwen smoke service uses tokenizer truncation.  Sending an oversized
    JSON user message can truncate the final chat-template generation marker;
    the model then continues the cut-off context instead of starting a new
    JSON answer.  Keep runtime-critical fields and progressively reduce only
    optional evidence until the complete user message fits the budget.
    """

    source = dict(context)
    event = source.get("event", {})
    if isinstance(event, Mapping):
        event_payload = dict(event)
        if "details" in event_payload:
            event_payload["details"] = _bounded(
                event_payload.get("details", {}),
                max_chars=800,
            )
    else:
        event_payload = {}

    payload: dict[str, Any] = {
        "session_id": source.get("session_id"),
        "simulation_time": source.get("simulation_time"),
        "scenario_preset_id": source.get("scenario_preset_id"),
        "baseline_controller": source.get("baseline_controller"),
        "event": event_payload,
        "allowed_scope": list(source.get("allowed_scope", ())),
        "intersections": _compact_control_intersections(
            source.get("intersections", {})
        ),
        "event_detection": _bounded(
            source.get("event_detection", {}),
            max_chars=1_600,
        ),
        "prediction": _bounded(
            source.get("prediction", {}),
            max_chars=1_600,
        ),
        "history": _bounded(
            source.get("history", {}),
            max_chars=2_200,
        ),
        "knowledge": _compact_control_knowledge(
            source.get("knowledge", ()),
            max_items=3,
            max_text_chars=700,
        ),
    }

    if _json_chars(payload) <= max_chars:
        return payload

    # Preserve current phase, allowed phases and current lane metrics before
    # reducing historical/knowledge evidence.
    payload["history"] = {
        "status": "omitted",
        "reason": "control_context_budget",
    }
    payload["knowledge"] = _compact_control_knowledge(
        source.get("knowledge", ()),
        max_items=2,
        max_text_chars=500,
    )
    payload["prediction"] = _bounded(
        source.get("prediction", {}),
        max_chars=1_000,
    )
    payload["event_detection"] = _bounded(
        source.get("event_detection", {}),
        max_chars=1_000,
    )
    if _json_chars(payload) <= max_chars:
        return payload

    payload["intersections"] = _compact_control_intersections(
        source.get("intersections", {}),
        lane_limit=4,
    )
    if _json_chars(payload) <= max_chars:
        return payload

    payload["intersections"] = _compact_control_intersections(
        source.get("intersections", {}),
        lane_limit=2,
    )
    if _json_chars(payload) <= max_chars:
        return payload

    # This final form still includes all information needed to choose a valid
    # phase and explicitly marks the optional evidence as unavailable.
    payload["event"] = {
        key: value
        for key, value in event_payload.items()
        if key != "details"
    }
    payload["event_detection"] = {"status": "omitted", "reason": "context_budget"}
    payload["prediction"] = {"status": "omitted", "reason": "context_budget"}
    payload["history"] = {"status": "omitted", "reason": "context_budget"}
    payload["knowledge"] = []
    payload["intersections"] = _compact_control_intersections(
        source.get("intersections", {}),
        lane_limit=0,
    )
    return payload


def _compact_control_intersections(
    value: Any,
    *,
    lane_limit: int | None = None,
) -> dict[str, Any]:
    if not isinstance(value, Mapping):
        return {}
    result: dict[str, Any] = {}
    for raw_intersection_id, raw_intersection in value.items():
        if not isinstance(raw_intersection, Mapping):
            continue
        row: dict[str, Any] = {}
        for key in (
            "current_phase",
            "pending_phase",
            "stage",
            "stage_elapsed",
            "allowed_phase_ids",
        ):
            if key in raw_intersection:
                row[key] = raw_intersection[key]
        raw_lanes = raw_intersection.get("lanes", {})
        lanes: dict[str, Any] = {}
        if isinstance(raw_lanes, Mapping):
            lane_items = sorted(raw_lanes.items(), key=lambda item: str(item[0]))
            if lane_limit is not None:
                lane_items = lane_items[: max(0, lane_limit)]
            for raw_lane_id, raw_lane in lane_items:
                if not isinstance(raw_lane, Mapping):
                    continue
                lanes[str(raw_lane_id)] = {
                    key: raw_lane[key]
                    for key in (
                        "vehicle_count",
                        "halting_count",
                        "mean_speed",
                        "waiting_time",
                        "occupancy",
                        "signal_state",
                    )
                    if key in raw_lane
                }
        row["lanes"] = lanes
        result[str(raw_intersection_id)] = row
    return result


def _compact_control_knowledge(
    value: Any,
    *,
    max_items: int,
    max_text_chars: int,
) -> list[dict[str, Any]]:
    if not isinstance(value, Sequence) or isinstance(value, (str, bytes)):
        return []
    result: list[dict[str, Any]] = []
    for raw_item in value:
        if not isinstance(raw_item, Mapping):
            continue
        metadata = raw_item.get("metadata", {})
        metadata_payload = {}
        if isinstance(metadata, Mapping):
            metadata_payload = {
                key: metadata[key]
                for key in (
                    "source_path",
                    "section",
                    "information_type",
                    "status",
                    "priority",
                    "knowledge_version",
                )
                if key in metadata
            }
        result.append(
            {
                "chunk_id": raw_item.get("chunk_id"),
                "text": str(raw_item.get("text", ""))[:max_text_chars],
                "metadata": metadata_payload,
                "distance": raw_item.get("distance"),
            }
        )
        if len(result) >= max_items:
            break
    return result


def _json_chars(value: Any) -> int:
    return len(json.dumps(value, ensure_ascii=False, separators=(",", ":"), default=str))


__all__ = ["TakeoverOrchestrator", "TakeoverPlanningError"]
