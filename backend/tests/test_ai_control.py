"""AI 控制契约、编排和 SUMO 安全执行器测试。"""

from __future__ import annotations

import json
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
    TakeoverPlanningError,
    _clip_plan_to_scope,
    _compact_control_context,
    _decode_control_plan_json,
    _parse_control_plan_content,
    _replan_signature,
    _unique_active_ai_event,
)
from backend.app.services.ai_control_validation import (
    MULTIPLE_AI_TARGETS_MESSAGE,
    ensure_at_most_one_ai_target,
)
from backend.app.schemas.events import AccidentRequest
from backend.app.services.simulation_service import SimulationService
from simulation.sumo.engine.ai_control import (
    AIControlConfig,
    AIControlPlan,
    AIControlPlanSummary,
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


def _plan(*intersection_ids: str) -> dict:
    ids = list(intersection_ids) or ["j1"]
    return {
        "controlled_intersections": ids,
        "valid_seconds": 30,
        "signal_plan": {intersection_id: [1, 0, 1, 0, 1, 0] for intersection_id in ids},
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
    intersection_ids: tuple[str, ...] = ("j1",),
    primary_intersection: str | None = None,
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
    primary = primary_intersection or intersection_ids[0]
    primary_intersection = IntersectionRuntimeSnapshot(
        current_phase=0,
        pending_phase=None,
        stage="GREEN",
        stage_elapsed=10.0,
        lanes={"edge_a_0": lane},
    )
    other_intersection = IntersectionRuntimeSnapshot(
        current_phase=0,
        pending_phase=None,
        stage="GREEN",
        stage_elapsed=10.0,
        lanes={},
    )
    return SimulationSnapshot(
        session_id=session_id,
        state=state,
        sequence=1,
        elapsed_seconds=elapsed,
        duration_seconds=120.0,
        progress=elapsed / 120.0,
        official_time="08:00:10",
        intersections={
            intersection_id: (
                primary_intersection if intersection_id == primary else other_intersection
            )
            for intersection_id in intersection_ids
        },
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
    assert executor.status.last_plan is not None
    assert executor.status.last_plan.target_phase_sequence["j1"] == (
        1,
        0,
        1,
        0,
        1,
        0,
    )
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


class _BrokenRetriever:
    def __init__(self) -> None:
        self.calls = 0

    def search(self, request: KnowledgeQuery) -> KnowledgeSearchResponse:
        self.calls += 1
        raise RuntimeError("RAG unavailable")


def _stub_observation_v2(
    monkeypatch,
    region: tuple[str, ...] = ("j1",),
    scope: str = "xiongan_20",
) -> None:
    monkeypatch.setattr(
        "backend.app.services.takeover_orchestrator.build_live_observation_v2",
        lambda *args, **kwargs: {
            "observation_version": "traffic_observation_v2",
            "scene": {
                "period": "morning_peak",
                "scope": scope,
                "t": 10.0,
                "seed": 0,
            },
            "event": {"event_type": "accident"},
            "controlled_region": list(region),
            "ix": {},
            "net": {"veh": 8, "halt": 4, "speed": 2.0},
            "allowed_phases": {intersection_id: [0, 1] for intersection_id in region},
        },
    )


def _control_settings() -> SimpleNamespace:
    return SimpleNamespace(
        ai_control_config=AIControlConfig(),
        citypulse_qwen_max_tokens=512,
        resolved_ai_control_max_tokens=512,
    )


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
    def __init__(self, plan: dict | None = None) -> None:
        self.messages = []
        self.plan = plan or _plan()

    def complete(self, messages, **kwargs):
        self.messages.append((messages, kwargs))
        return LLMCompletion(
            message=AssistantMessage(content=json.dumps(self.plan))
        )


class _SequenceProvider:
    def __init__(self, contents: list[object]) -> None:
        self.contents = list(contents)
        self.messages = []

    def complete(self, messages, **kwargs):
        self.messages.append((messages, kwargs))
        content = self.contents.pop(0)
        return LLMCompletion(
            message=AssistantMessage(
                content=content if isinstance(content, str) else None
            )
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


def test_orchestrator_installs_plan_without_rag(monkeypatch) -> None:
    _stub_observation_v2(monkeypatch)
    event = _active_event()
    snapshot = _snapshot(events=(event,))
    manager = _FakeManager(snapshot)
    settings = SimpleNamespace(
        ai_control_config=AIControlConfig(),
        citypulse_qwen_max_tokens=512,
        resolved_ai_control_max_tokens=512,
    )
    retriever = _BrokenRetriever()
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
    assert retriever.calls == 0
    user_content = provider.messages[0][0][1]["content"]
    assert "traffic_observation_v2" in user_content
    assert "observation_version" in user_content
    assert manager.installed[0][1]["allowed_scope"] == ["j1"]
    assert manager.installed[0][1]["rag_status"] == "not_required"


def test_control_plan_decoder_accepts_known_transport_wrappers() -> None:
    expected = _plan()

    assert _decode_control_plan_json(json.dumps(expected)) == expected
    assert _decode_control_plan_json(
        "<think>先检查约束</think>\n```json\n"
        + json.dumps(expected)
        + "\n```"
    ) == expected


def test_control_context_is_bounded_without_cutting_the_json_object() -> None:
    context = {
        "session_id": "session-1",
        "simulation_time": 10.0,
        "scenario_preset_id": "west_dense",
        "baseline_controller": "fixed",
        "event": {
            "event_id": "event-1",
            "event_type": "accident",
            "details": {"lane_id": "edge_a_0", "notes": "x" * 5_000},
        },
        "allowed_scope": ["j1"],
        "intersections": {
            "j1": {
                "current_phase": 0,
                "pending_phase": None,
                "stage": "GREEN",
                "stage_elapsed": 3.0,
                "allowed_phase_ids": [0, 1, 2],
                "lanes": {
                    f"lane-{index}": {
                        "vehicle_count": index,
                        "halting_count": index,
                        "mean_speed": 1.0,
                        "waiting_time": 2.0,
                        "occupancy": 30.0,
                        "signal_state": "r",
                        "irrelevant": "x" * 100,
                    }
                    for index in range(20)
                },
            }
        },
        "event_detection": {"cards": [{"text": "x" * 10_000}]},
        "prediction": {"intersections": {"j1": {"text": "x" * 10_000}}},
        "history": {"frames": [{"text": "x" * 10_000}]},
        "knowledge": [
            {
                "chunk_id": f"chunk-{index}",
                "text": "x" * 10_000,
                "metadata": {"source_path": "knowledge.md"},
                "distance": 0.1,
            }
            for index in range(10)
        ],
    }

    compact = _compact_control_context(context, max_chars=6_000)
    encoded = json.dumps(compact, ensure_ascii=False, separators=(",", ":"))

    assert len(encoded) <= 6_000
    assert compact["session_id"] == "session-1"
    assert compact["allowed_scope"] == ["j1"]
    assert compact["intersections"]["j1"]["allowed_phase_ids"] == [0, 1, 2]


def test_orchestrator_retries_invalid_format_once_then_installs_plan(monkeypatch) -> None:
    _stub_observation_v2(monkeypatch)
    event = _active_event()
    snapshot = _snapshot(events=(event,))
    manager = _FakeManager(snapshot)
    settings = SimpleNamespace(
        ai_control_config=AIControlConfig(),
        citypulse_qwen_max_tokens=512,
    )
    provider = _SequenceProvider(
        [
            "我将根据当前情况生成控制计划：",
            "```json\n" + json.dumps(_plan()) + "\n```",
        ]
    )
    orchestrator = TakeoverOrchestrator(
        manager=manager,
        settings=settings,
        history_repository=InMemoryHistoryRepository(),
    )
    orchestrator.configure(
        provider=provider,
        retriever=_FakeRetriever(),
        topology=RoadTopology(lane_to_intersection={"edge_a_0": "j1"}),
    )

    orchestrator.observe(snapshot, intelligence={"event_detection": {}})

    assert len(provider.messages) == 2
    assert "这是最后一次输出机会" in provider.messages[1][0][0]["content"]
    assert len(manager.installed) == 1


def test_orchestrator_falls_back_after_two_invalid_control_plan_outputs(monkeypatch) -> None:
    _stub_observation_v2(monkeypatch)
    event = _active_event()
    snapshot = _snapshot(events=(event,))
    manager = _FakeManager(snapshot)
    settings = SimpleNamespace(
        ai_control_config=AIControlConfig(),
        citypulse_qwen_max_tokens=512,
    )
    provider = _SequenceProvider(["不是 JSON", "仍然不是 JSON"])
    orchestrator = TakeoverOrchestrator(
        manager=manager,
        settings=settings,
        history_repository=InMemoryHistoryRepository(),
    )
    orchestrator.configure(
        provider=provider,
        retriever=_FakeRetriever(),
        topology=RoadTopology(lane_to_intersection={"edge_a_0": "j1"}),
    )

    orchestrator.observe(snapshot, intelligence={"event_detection": {}})

    assert len(provider.messages) == 2
    assert manager.installed == []
    assert len(manager.fallbacks) == 1
    assert "after 2 attempts" in manager.fallbacks[0][1]["reason"]


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
        last_plan=AIControlPlanSummary(
            event_id="event-1",
            plan_id="plan-1",
            sequence=1,
            plan_started_at=0.0,
            plan_valid_until=30.0,
            controlled_intersections=("j1",),
            target_phase_sequence={"j1": (1, 0, 1, 0, 1, 0)},
            objective="protect the blocked approach",
            reason="keep the affected junction safe",
        ),
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
        last_plan=AIControlPlanSummary(
            event_id="event-1",
            plan_id="plan-1",
            sequence=1,
            plan_started_at=0.0,
            plan_valid_until=30.0,
            controlled_intersections=("j1",),
            target_phase_sequence={"j1": (1, 0, 1, 0, 1, 0)},
            objective="protect the blocked approach",
            reason="keep the affected junction safe",
        ),
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
    assert response.json()["last_plan"]["target_phase_sequence"]["j1"] == [
        1,
        0,
        1,
        0,
        1,
        0,
    ]


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


def _prediction_payload(*, ready: bool, fallback: bool, ratio: float) -> dict:
    return {
        "event_detection": {},
        "prediction": {
            "ready": ready,
            "fallback": fallback,
            "intersections": {"j1": {"delta_ratio": ratio}},
        },
    }


def test_zero_or_one_ai_target_is_legal_two_are_rejected() -> None:
    none_enabled = SimpleNamespace(ai_control_enabled=False, details={})
    one_enabled = SimpleNamespace(ai_control_enabled=True, details={})
    ensure_at_most_one_ai_target([])
    ensure_at_most_one_ai_target([none_enabled, none_enabled])
    ensure_at_most_one_ai_target([one_enabled])
    with pytest.raises(AppError) as error:
        ensure_at_most_one_ai_target([one_enabled, one_enabled])
    assert error.value.status_code == 422
    assert error.value.code == "MULTIPLE_AI_CONTROL_TARGETS"
    assert error.value.message == MULTIPLE_AI_TARGETS_MESSAGE


def test_simulation_service_rejects_multiple_ai_targets_at_start() -> None:
    service = SimulationService.__new__(SimulationService)
    service._settings = SimpleNamespace(ai_control_config=AIControlConfig())
    ai_event = SimpleNamespace(
        event_id="event-1",
        ai_control_enabled=True,
        start_seconds=0.0,
        end_seconds=40.0,
    )
    request = SimpleNamespace(
        initial_events=(ai_event, SimpleNamespace(
            event_id="event-2",
            ai_control_enabled=True,
            start_seconds=50.0,
            end_seconds=80.0,
        )),
        duration_seconds=120.0,
    )
    with pytest.raises(AppError) as error:
        service._validate_ai_control_request(request)
    assert error.value.code == "MULTIPLE_AI_CONTROL_TARGETS"

    legal = SimpleNamespace(initial_events=(ai_event,), duration_seconds=120.0)
    service._validate_ai_control_request(legal)
    empty = SimpleNamespace(initial_events=(), duration_seconds=120.0)
    service._validate_ai_control_request(empty)


def test_unique_active_ai_event_refuses_to_pick_the_first() -> None:
    with pytest.raises(TakeoverPlanningError, match="multiple ACTIVE"):
        _unique_active_ai_event((_active_event("event-a"), _active_event("event-b")))
    assert _unique_active_ai_event((_active_event(),)).event_id == "event-1"
    assert _unique_active_ai_event(()) is None


def test_orchestrator_does_not_plan_when_multiple_active_ai_events(monkeypatch) -> None:
    _stub_observation_v2(monkeypatch)
    snapshot = _snapshot(events=(_active_event("event-a"), _active_event("event-b")))
    manager = _FakeManager(snapshot)
    provider = _FakeProvider()
    orchestrator = TakeoverOrchestrator(
        manager=manager,
        settings=_control_settings(),
        history_repository=InMemoryHistoryRepository(),
    )
    orchestrator.configure(
        provider=provider,
        topology=RoadTopology(lane_to_intersection={"edge_a_0": "j1"}),
    )

    orchestrator.observe(snapshot, intelligence={"event_detection": {}})
    orchestrator.observe(snapshot, intelligence={"event_detection": {}})

    assert provider.messages == []
    assert manager.installed == []
    assert len(manager.fallbacks) == 1
    assert "multiple ACTIVE" in manager.fallbacks[0][1]["reason"]
    assert manager.fallbacks[0][1]["rag_status"] == "not_required"


def test_executor_does_not_silently_arm_the_first_of_multiple_ai_events() -> None:
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
    )
    changed = executor.observe_events(
        (_active_event("event-a"), _active_event("event-b")),
        0.0,
    )
    assert changed is False
    assert executor.status.state == "INACTIVE"
    assert executor.status.active_event_id is None


def test_narrow_tdp_bucket_change_requests_early_replan(monkeypatch) -> None:
    _stub_observation_v2(monkeypatch)
    event = _active_event()
    snapshot = _snapshot(events=(event,))
    manager = _FakeManager(snapshot)
    provider = _FakeProvider()
    orchestrator = TakeoverOrchestrator(
        manager=manager,
        settings=_control_settings(),
        history_repository=InMemoryHistoryRepository(),
    )
    orchestrator.configure(
        provider=provider,
        topology=RoadTopology(lane_to_intersection={"edge_a_0": "j1"}),
    )

    orchestrator.observe(snapshot, intelligence=_prediction_payload(ready=True, fallback=False, ratio=0.0))
    assert len(manager.installed) == 1

    active = replace(
        snapshot,
        elapsed_seconds=12.0,
        ai_takeover=AIControlStatus(
            state="ACTIVE",
            ai_enabled=True,
            active_event_id=event.event_id,
            plan_valid_until=40.0,
            plan_sequence=1,
        ),
    )
    manager.current = active
    orchestrator.observe(active, intelligence=_prediction_payload(ready=True, fallback=False, ratio=0.05))
    assert len(manager.installed) == 1

    orchestrator.observe(active, intelligence=_prediction_payload(ready=True, fallback=False, ratio=0.35))
    assert len(manager.installed) == 2

    orchestrator.observe(
        replace(active, elapsed_seconds=14.0, ai_takeover=replace(active.ai_takeover, plan_sequence=2, plan_valid_until=44.0)),
        intelligence=_prediction_payload(ready=False, fallback=True, ratio=0.9),
    )
    assert len(manager.installed) == 2


def test_replan_signature_ignores_fallback_and_unusable_prediction() -> None:
    event = _active_event()
    first = _replan_signature(event, _prediction_payload(ready=True, fallback=False, ratio=0.0), allowed_scope=("j1",))
    second = _replan_signature(
        event,
        _prediction_payload(ready=True, fallback=False, ratio=0.4),
        allowed_scope=("j1",),
        last_prediction_token=first[1],
    )
    fallback = _replan_signature(
        event,
        _prediction_payload(ready=True, fallback=True, ratio=0.9),
        allowed_scope=("j1",),
        last_prediction_token=second[1],
    )
    unavailable = _replan_signature(
        event,
        {"prediction": {"ready": False, "fallback": False}},
        allowed_scope=("j1",),
        last_prediction_token=second[1],
    )
    assert first[2] is False
    assert second[2] is True
    assert fallback[2] is False
    assert unavailable[2] is False
    assert fallback[1] == second[1]
    assert unavailable[1] == second[1]


EAST_DENSE_IDS = ("demo_3", "demo_5", "demo_6", "demo_9")
WEST_DENSE_IDS = ("demo_14", "demo_15", "demo_19")
XIONGAN_20_IDS = tuple(f"demo_{index}" for index in range(1, 21))


def _hop_limited_topology(primary: str, neighbor: str) -> RoadTopology:
    """1-hop topology that cannot reach every intersection in a compact preset."""

    return RoadTopology(
        lane_to_intersection={"edge_a_0": primary},
        upstream_intersections={primary: (neighbor,)},
        downstream_intersections={primary: (neighbor,)},
    )


def test_allowed_scope_uses_full_session_for_compact_presets() -> None:
    event = _active_event()
    orchestrator = TakeoverOrchestrator(
        manager=_FakeManager(_snapshot(events=(event,))),
        settings=_control_settings(),
        history_repository=InMemoryHistoryRepository(),
    )
    orchestrator.configure(topology=_hop_limited_topology("demo_3", "demo_5"))

    east = orchestrator.allowed_scope(
        _snapshot(events=(event,), intersection_ids=EAST_DENSE_IDS),
        event,
    )
    west_orchestrator = TakeoverOrchestrator(
        manager=_FakeManager(_snapshot(events=(event,))),
        settings=_control_settings(),
        history_repository=InMemoryHistoryRepository(),
    )
    west_orchestrator.configure(topology=_hop_limited_topology("demo_14", "demo_15"))
    west = west_orchestrator.allowed_scope(
        _snapshot(events=(event,), intersection_ids=WEST_DENSE_IDS),
        event,
    )

    assert east == EAST_DENSE_IDS
    assert west == WEST_DENSE_IDS


def test_allowed_scope_still_uses_hops_on_full_network() -> None:
    event = _active_event()
    orchestrator = TakeoverOrchestrator(
        manager=_FakeManager(_snapshot(events=(event,))),
        settings=_control_settings(),
        history_repository=InMemoryHistoryRepository(),
    )
    orchestrator.configure(topology=_hop_limited_topology("demo_3", "demo_5"))

    scope = orchestrator.allowed_scope(
        _snapshot(
            events=(event,),
            intersection_ids=XIONGAN_20_IDS,
            primary_intersection="demo_3",
        ),
        event,
    )

    assert scope == ("demo_3", "demo_5")


def test_orchestrator_installs_full_east_dense_plan(monkeypatch) -> None:
    _stub_observation_v2(monkeypatch, region=EAST_DENSE_IDS, scope="east_dense")
    event = _active_event()
    snapshot = _snapshot(events=(event,), intersection_ids=EAST_DENSE_IDS)
    manager = _FakeManager(snapshot)
    provider = _FakeProvider(_plan(*EAST_DENSE_IDS))
    orchestrator = TakeoverOrchestrator(
        manager=manager,
        settings=_control_settings(),
        history_repository=InMemoryHistoryRepository(),
    )
    orchestrator.configure(
        provider=provider,
        topology=_hop_limited_topology("demo_3", "demo_5"),
    )

    orchestrator.observe(snapshot, intelligence={"event_detection": {}})

    assert manager.fallbacks == []
    assert len(manager.installed) == 1
    assert manager.installed[0][1]["allowed_scope"] == list(EAST_DENSE_IDS)
    assert manager.installed[0][1]["plan"]["controlled_intersections"] == list(
        EAST_DENSE_IDS
    )


def test_orchestrator_installs_full_west_dense_plan(monkeypatch) -> None:
    _stub_observation_v2(monkeypatch, region=WEST_DENSE_IDS, scope="west_dense")
    event = _active_event()
    snapshot = _snapshot(events=(event,), intersection_ids=WEST_DENSE_IDS)
    manager = _FakeManager(snapshot)
    provider = _FakeProvider(_plan(*WEST_DENSE_IDS))
    orchestrator = TakeoverOrchestrator(
        manager=manager,
        settings=_control_settings(),
        history_repository=InMemoryHistoryRepository(),
    )
    orchestrator.configure(
        provider=provider,
        topology=_hop_limited_topology("demo_14", "demo_15"),
    )

    orchestrator.observe(snapshot, intelligence={"event_detection": {}})

    assert manager.fallbacks == []
    assert len(manager.installed) == 1
    assert manager.installed[0][1]["allowed_scope"] == list(WEST_DENSE_IDS)


def test_clip_plan_keeps_in_scope_intersections() -> None:
    plan = AIControlPlan.from_mapping(_plan("demo_3", "demo_5", "demo_99"))
    clipped = _clip_plan_to_scope(
        plan,
        ("demo_3", "demo_5"),
        config=AIControlConfig(),
    )
    assert clipped.controlled_intersections == ("demo_3", "demo_5")
    assert set(clipped.signal_plan) == {"demo_3", "demo_5"}
