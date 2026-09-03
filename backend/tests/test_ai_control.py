"""AI 控制契约、编排和 SUMO 安全执行器测试。"""

from __future__ import annotations

from dataclasses import replace
from types import SimpleNamespace

import pytest

from backend.app.copilot.llm import AssistantMessage, LLMCompletion
from backend.app.copilot.rag import (
    KnowledgeQuery,
    KnowledgeResult,
    KnowledgeSearchResponse,
)
from backend.app.copilot.traffic_tools import RoadTopology
from backend.app.core.exceptions import AppError
from backend.app.services.history import InMemoryHistoryRepository
from backend.app.services.takeover_orchestrator import (
    TakeoverOrchestrator,
    _parse_control_plan_content,
)
from backend.app.schemas.events import AccidentRequest
from backend.app.services.simulation_service import SimulationService
from simulation.sumo.engine.ai_control import (
    AIControlConfig,
    AIControlPlan,
    AIControlStatus,
    AIControlValidationError,
)
from simulation.sumo.engine.ai_executor import AIPlanExecutor
from simulation.sumo.engine.events import EventSnapshot
from simulation.sumo.engine.session import (
    IntersectionRuntimeSnapshot,
    LaneRuntimeSnapshot,
    SessionMetrics,
    SimulationConfig,
    SimulationSnapshot,
)
from simulation.sumo.engine.signal import SafePhaseController, SignalStage


def _plan(intersection_id: str = "j1") -> dict:
    return {
        "controlled_intersections": [intersection_id],
        "valid_seconds": 30,
        "signal_plan": {intersection_id: [1, 0, 1, 0, 1, 0]},
        "objective": "protect the blocked approach",
        "reason": "keep the affected junction safe while reducing queue growth",
        "fallback_to_baseline": False,
    }


@pytest.mark.parametrize(
    "content",
    (
        lambda value: f"```json\n{value}\n```",
        lambda value: f"控制方案如下：\n{value}\n请按安全约束执行。",
    ),
)
def test_parse_control_plan_accepts_model_wrapping(content) -> None:
    encoded = __import__("json").dumps(_plan(), ensure_ascii=False)

    assert _parse_control_plan_content(content(encoded)) == _plan()


def _active_event(event_id: str = "event-1") -> EventSnapshot:
    return EventSnapshot(
        event_id=event_id,
        event_type="accident",
        state="ACTIVE",
        start_seconds=0.0,
        end_seconds=40.0,
        error=None,
        details={
            "lane_id": "edge_a_0",
            "ai_control_enabled": True,
        },
    )


def _snapshot(
    *,
    session_id: str = "session-1",
    elapsed: float = 10.0,
    state: str = "RUNNING",
    events: tuple[EventSnapshot, ...] = (),
    ai_takeover: AIControlStatus | None = None,
) -> SimulationSnapshot:
    lane = LaneRuntimeSnapshot(
        vehicle_count=8,
        halting_count=4,
        mean_speed=2.0,
        waiting_time=12.0,
        occupancy=35.0,
        edge_id="edge_a",
        lane_index=0,
        role="incoming",
        approach_id="west",
        downstream_lane_ids=(),
        lane_has_green=False,
        signal_state="r",
        current_allowed_speed_mps=13.9,
    )
    intersection = IntersectionRuntimeSnapshot(
        current_phase=0,
        pending_phase=None,
        stage="GREEN",
        stage_elapsed=10.0,
        lanes={"edge_a_0": lane},
    )
    return SimulationSnapshot(
        session_id=session_id,
        state=state,
        sequence=1,
        elapsed_seconds=elapsed,
        duration_seconds=120.0,
        progress=elapsed / 120.0,
        official_time="08:00:10",
        intersections={"j1": intersection},
        events=events,
        metrics=SessionMetrics(active_vehicles=8),
        ai_takeover=ai_takeover or AIControlStatus(),
    )


def test_control_plan_is_strict_and_has_six_slots() -> None:
    parsed = AIControlPlan.from_mapping(_plan())
    assert parsed.signal_plan["j1"] == (1, 0, 1, 0, 1, 0)
    assert parsed.to_dict()["controlled_intersections"] == ["j1"]

    invalid = dict(_plan())
    invalid["unexpected"] = True
    with pytest.raises(AIControlValidationError):
        AIControlPlan.from_mapping(invalid)

    invalid = _plan()
    invalid["signal_plan"] = {"j1": [0, 1]}
    with pytest.raises(AIControlValidationError):
        AIControlPlan.from_mapping(invalid)


def test_ai_control_status_normalizes_serializable_sequences() -> None:
    status = AIControlStatus(
        state="ACTIVE",
        ai_enabled=True,
        allowed_scope=["j1"],  # type: ignore[arg-type]
        controlled_intersections=["j1"],  # type: ignore[arg-type]
        plan_sequence="2",  # type: ignore[arg-type]
    )
    assert status.allowed_scope == ("j1",)
    assert status.controlled_intersections == ("j1",)
    assert status.plan_sequence == 2


def test_executor_enforces_safe_transition_and_delayed_recovery(monkeypatch) -> None:
    controller = SafePhaseController(
        [0, 1],
        {0: (1.0, 1.0), 1: (1.0, 1.0)},
        minimum_green=5.0,
        initial_phase=0,
    )
    executor = AIPlanExecutor(
        traci=None,
        selected_manifest={"j1": {}},
        controllers={"j1": controller},
        baseline_mode="algorithm",
    )
    applied: list[tuple[str, SignalStage]] = []
    monkeypatch.setattr(
        executor,
        "_apply_controller_state",
        lambda intersection_id, current: applied.append(
            (intersection_id, current.stage)
        ),
    )

    event = _active_event()
    assert executor.observe_events((event,), 0.0)
    executor.install_from_payload(
        {
            "event_id": event.event_id,
            "plan": _plan(),
            "allowed_scope": ["j1"],
            "plan_id": "session-1:event-1:1",
            "plan_started_at": 0.0,
        },
        current_time=0.0,
        events=(event,),
    )
    executor.apply_slot(0.0)
    assert controller.stage == SignalStage.GREEN
    assert controller.pending_phase == 1

    executor.advance(5.0)
    assert controller.stage == SignalStage.YELLOW
    executor.advance(7.0)
    assert controller.stage == SignalStage.GREEN
    assert controller.current_phase == 1
    assert applied

    assert executor.observe_events((), 8.0)
    assert executor.status.state == "RECOVERY"
    executor.advance(13.0)
    assert executor.status.state == "RECOVERY"
    executor.advance(23.0)
    assert executor.status.state == "FINISHED"
    assert not executor.override_intersections


def test_algorithm_fallback_immediately_releases_scope_to_baseline(monkeypatch) -> None:
    controller = SafePhaseController(
        [0, 1],
        {0: (1.0, 1.0), 1: (1.0, 1.0)},
        minimum_green=0.0,
    )
    executor = AIPlanExecutor(
        traci=None,
        selected_manifest={"j1": {}},
        controllers={"j1": controller},
        baseline_mode="algorithm",
    )
    monkeypatch.setattr(executor, "_apply_controller_state", lambda *_: None)
    event = _active_event()
    executor.observe_events((event,), 0.0)
    executor.install_from_payload(
        {
            "event_id": event.event_id,
            "plan": _plan(),
            "allowed_scope": ["j1"],
            "plan_id": "plan-1",
            "plan_started_at": 0.0,
        },
        current_time=0.0,
        events=(event,),
    )
    executor.mark_fallback(
        event_id=event.event_id,
        reason="qwen_timeout",
        current_time=1.0,
    )
    assert executor.status.state == "FALLBACK"
    assert executor.override_intersections == frozenset()


def test_fixed_fallback_restores_program_after_safe_transition(monkeypatch) -> None:
    controller = SafePhaseController(
        [0, 1],
        {0: (1.0, 1.0), 1: (1.0, 1.0)},
        minimum_green=0.0,
    )
    restored: list[str] = []
    executor = AIPlanExecutor(
        traci=None,
        selected_manifest={"j1": {}},
        controllers={"j1": controller},
        baseline_mode="fixed",
        fixed_state_provider=lambda _intersection_id, _time: (0, None, "GREEN", 0.0),
        baseline_restore=lambda intersection_id: restored.append(intersection_id),
    )
    monkeypatch.setattr(executor, "_apply_controller_state", lambda *_: None)
    event = _active_event()
    executor.observe_events((event,), 0.0)
    executor.install_from_payload(
        {
            "event_id": event.event_id,
            "plan": _plan(),
            "allowed_scope": ["j1"],
            "plan_id": "fixed-plan-1",
            "plan_started_at": 0.0,
        },
        current_time=0.0,
        events=(event,),
    )
    executor.apply_slot(0.0)
    executor.mark_fallback(
        event_id=event.event_id,
        reason="invalid_model_output",
        current_time=0.0,
    )
    assert executor.override_intersections == frozenset({"j1"})
    executor.advance(0.0)
    executor.advance(2.0)
    assert restored == ["j1"]
    assert executor.override_intersections == frozenset()


def test_fixed_replan_keeps_live_ai_controller_instead_of_stale_tracker(monkeypatch) -> None:
    controller = SafePhaseController(
        [0, 1],
        {0: (1.0, 1.0), 1: (1.0, 1.0)},
        minimum_green=0.0,
    )
    executor = AIPlanExecutor(
        traci=None,
        selected_manifest={"j1": {}},
        controllers={"j1": controller},
        baseline_mode="fixed",
        fixed_state_provider=lambda _intersection_id, _time: (
            0,
            None,
            "GREEN",
            0.0,
        ),
    )
    monkeypatch.setattr(executor, "_apply_controller_state", lambda *_: None)
    event = _active_event()
    assert executor.observe_events((event,), 0.0)
    executor.install_from_payload(
        {
            "event_id": event.event_id,
            "plan": _plan(),
            "allowed_scope": ["j1"],
            "plan_id": "fixed-plan-1",
            "plan_started_at": 0.0,
        },
        current_time=0.0,
        events=(event,),
    )
    executor.apply_slot(0.0)
    executor.advance(2.0)
    assert controller.current_phase == 1

    # A second plan is a replan while the fixed tracker is intentionally
    # excluded; the controller must not be reset to the provider's old phase.
    executor.install_from_payload(
        {
            "event_id": event.event_id,
            "plan": _plan(),
            "allowed_scope": ["j1"],
            "plan_id": "fixed-plan-2",
            "plan_started_at": 2.0,
        },
        current_time=2.0,
        events=(event,),
    )
    assert controller.current_phase == 1
    assert executor.plan_expired(32.0)


class _FakeRetriever:
    def __init__(self) -> None:
        self.requests: list[KnowledgeQuery] = []

    def search(self, request: KnowledgeQuery) -> KnowledgeSearchResponse:
        self.requests.append(request)
        return KnowledgeSearchResponse(
            results=(
                KnowledgeResult(
                    chunk_id="chunk-1",
                    text="Protect the upstream approach before changing phases.",
                    metadata={
                        "source_path": "traffic_knowledge/control.md",
                        "section": "Accident response",
                        "information_type": "traffic_expertise",
                    },
                    distance=0.1,
                ),
            ),
            search_mode="vector",
            index_metadata={"knowledge_version": "test"},
        )


class _FakeProvider:
    def __init__(self) -> None:
        self.messages = []

    def complete(self, messages, **kwargs):
        self.messages.append((messages, kwargs))
        return LLMCompletion(
            message=AssistantMessage(content=__import__("json").dumps(_plan()))
        )


class _FakeManager:
    def __init__(self, snapshot: SimulationSnapshot) -> None:
        self.current = snapshot
        self.pause_calls: list[str] = []
        self.resume_calls: list[str] = []
        self.installed: list[tuple[str, dict]] = []
        self.fallbacks: list[tuple[str, dict]] = []

    def pause(self, session_id: str) -> None:
        self.pause_calls.append(session_id)
        self.current = replace(self.current, state="PAUSED")

    def resume(self, session_id: str) -> None:
        self.resume_calls.append(session_id)
        self.current = replace(self.current, state="RUNNING")

    def snapshot(self, session_id: str) -> SimulationSnapshot:
        return self.current

    def install_ai_plan(self, session_id: str, payload) -> None:
        self.installed.append((session_id, dict(payload)))

    def fallback_ai_control(self, session_id: str, payload) -> None:
        self.fallbacks.append((session_id, dict(payload)))


def test_orchestrator_queries_vector_rag_and_installs_plan() -> None:
    event = _active_event()
    snapshot = _snapshot(events=(event,))
    manager = _FakeManager(snapshot)
    settings = SimpleNamespace(
        ai_control_config=AIControlConfig(),
        citypulse_qwen_max_tokens=512,
    )
    retriever = _FakeRetriever()
    provider = _FakeProvider()
    topology = RoadTopology(lane_to_intersection={"edge_a_0": "j1"})
    orchestrator = TakeoverOrchestrator(
        manager=manager,
        settings=settings,
        history_repository=InMemoryHistoryRepository(),
    )
    orchestrator.configure(provider=provider, retriever=retriever, topology=topology)

    orchestrator.observe(snapshot, intelligence={"event_detection": {}})

    assert manager.pause_calls == ["session-1"]
    assert manager.resume_calls == ["session-1"]
    assert len(manager.installed) == 1
    assert retriever.requests[0].profile == "control"
    assert retriever.requests[0].event_type == "accident"
    assert manager.installed[0][1]["allowed_scope"] == ["j1"]


def test_config_and_snapshot_codec_roundtrip_ai_fields() -> None:
    from simulation.sumo.engine.distributed.codec import dumps_config, dumps_snapshot, loads_config, loads_snapshot
    from simulation.sumo.engine.events import LaneClosureEvent

    config = SimulationConfig(
        intersection_ids=("j1",),
        duration_seconds=60.0,
        initial_events=(
            LaneClosureEvent(
                event_id="event-1",
                start_seconds=0.0,
                end_seconds=40.0,
                lane_ids=("edge_a_0",),
                ai_control_enabled=True,
            ),
        ),
        baseline_controller="sotl",
        ai_control=AIControlConfig(),
    )
    restored_config = loads_config(dumps_config(config))
    assert restored_config.ai_control.slot_seconds == 5.0
    assert restored_config.initial_events[0].ai_control_enabled is True
    assert restored_config.baseline_controller == "sotl"

    status = AIControlStatus(
        state="ACTIVE",
        ai_enabled=True,
        active_event_id="event-1",
        allowed_scope=("j1",),
        controlled_intersections=("j1",),
        plan_sequence=1,
        plan_id="plan-1",
    )
    restored_snapshot = loads_snapshot(
        dumps_snapshot(_snapshot(ai_takeover=status))
    )
    assert restored_snapshot.ai_takeover == status


def test_ai_takeover_status_endpoint_returns_snapshot_state(
    client, simulation_service, monkeypatch
) -> None:
    status = AIControlStatus(
        state="ACTIVE",
        ai_enabled=True,
        active_event_id="event-1",
        allowed_scope=("j1",),
        controlled_intersections=("j1",),
        plan_sequence=1,
        baseline_controller="sotl",
    )
    monkeypatch.setattr(
        simulation_service,
        "snapshot",
        lambda _session_id: {"ai_takeover": status.to_dict()},
    )

    response = client.get("/api/v1/simulations/session-1/ai-takeover")

    assert response.status_code == 200
    assert response.json()["state"] == "ACTIVE"
    assert response.json()["baseline_controller"] == "sotl"


def test_runtime_ai_event_is_rejected_until_next_session_start(
    simulation_service: SimulationService,
) -> None:
    request = AccidentRequest(
        event_type="accident",
        event_id="event-1",
        start_seconds=10.0,
        end_seconds=30.0,
        lane_id="edge_a_0",
        position_ratio=0.5,
        ai_control_enabled=True,
    )

    with pytest.raises(AppError) as error:
        simulation_service.add_event("session-1", request)

    assert getattr(error.value, "code", None) == "AI_EVENT_MUST_BE_CONFIGURED_AT_START"
