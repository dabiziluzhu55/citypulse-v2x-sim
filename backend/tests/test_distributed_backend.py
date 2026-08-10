"""多仿真并发 / Redis管理模式单元测试（不启动真实SUMO）"""

from __future__ import annotations

import threading
import time
from dataclasses import dataclass, field, replace
from queue import Empty, Queue
from typing import Any
from unittest.mock import MagicMock, patch
from uuid import uuid4

import pytest
from fastapi.testclient import TestClient

from backend.app.controllers.runtime import AlgorithmRuntimeStore
from backend.app.core.config import Settings
from backend.app.main import create_app
from backend.app.metrics.session_hub import SessionMetricsHub
from backend.app.services.manager_factory import create_simulation_manager
from backend.app.services.session_metadata import (
    InMemorySessionMetadataStore,
    create_session_metadata_store,
)
from backend.app.services.simulation_service import SimulationService
from backend.app.services.snapshot_serializer import SnapshotSerializer
from simulation.sumo.engine.distributed import RedisUnavailableError
from simulation.sumo.engine.session import (
    SessionMetrics,
    SimulationConfig,
    SimulationSnapshot,
    UnknownSessionError,
)


@dataclass
class _FakeSubscription:
    snapshots: Queue
    closed: bool = False

    def get(self, timeout: float | None = None) -> SimulationSnapshot:
        try:
            return self.snapshots.get(timeout=0.05 if timeout is None else timeout)
        except Empty:
            raise Empty from None

    def close(self) -> None:
        self.closed = True


class FakeMultiSessionManager:
    """模拟RedisSimulationManager：支持多QUEUED会话与状态推进"""

    def __init__(self, catalog) -> None:
        self._catalog = catalog
        self._sessions: dict[str, SimulationSnapshot] = {}
        self._subs: dict[str, list[_FakeSubscription]] = {}
        self._lock = threading.Lock()
        self.stop_calls: list[str] = []

    def catalog(self):
        return self._catalog

    def start(self, config: SimulationConfig) -> str:
        session_id = str(uuid4())
        snap = SimulationSnapshot(
            session_id=session_id,
            state="QUEUED",
            sequence=0,
            elapsed_seconds=0.0,
            duration_seconds=float(config.duration_seconds or 60.0),
            progress=0.0,
            official_time="00:00:00",
            playback_speed=1.0,
            metrics=SessionMetrics(),
        )
        with self._lock:
            self._sessions[session_id] = snap
        return session_id

    def snapshot(self, session_id: str) -> SimulationSnapshot:
        with self._lock:
            snap = self._sessions.get(session_id)
            if snap is None:
                raise UnknownSessionError(f"Unknown session: {session_id}")
            return snap

    def subscribe(self, session_id: str) -> _FakeSubscription:
        with self._lock:
            if session_id not in self._sessions:
                raise UnknownSessionError(f"Unknown session: {session_id}")
            sub = _FakeSubscription(Queue())
            sub.snapshots.put(self._sessions[session_id])
            self._subs.setdefault(session_id, []).append(sub)
            return sub

    def stop(self, session_id: str) -> None:
        self.stop_calls.append(session_id)
        self.advance(session_id, "STOPPED")

    def pause(self, session_id: str) -> None:
        snap = self.snapshot(session_id)
        if snap.state == "QUEUED":
            raise Exception(
                f"Session {session_id} is queued; only stop is available before startup."
            )
        self.advance(session_id, "PAUSED")

    def resume(self, session_id: str) -> None:
        snap = self.snapshot(session_id)
        if snap.state == "QUEUED":
            raise Exception(
                f"Session {session_id} is queued; only stop is available before startup."
            )
        self.advance(session_id, "RUNNING")

    def set_playback_speed(self, session_id: str, speed: float) -> None:
        snap = self.snapshot(session_id)
        if snap.state == "QUEUED":
            raise Exception(
                f"Session {session_id} is queued; only stop is available before startup."
            )
        with self._lock:
            current = self._sessions[session_id]
            self._sessions[session_id] = replace(current, playback_speed=speed)

    def add_event(self, session_id: str, event: Any) -> str:
        snap = self.snapshot(session_id)
        if snap.state == "QUEUED":
            raise Exception(
                f"Session {session_id} is queued; only stop is available before startup."
            )
        return getattr(event, "event_id", None) or "evt"

    def cancel_event(self, session_id: str, event_id: str) -> None:
        snap = self.snapshot(session_id)
        if snap.state == "QUEUED":
            raise Exception(
                f"Session {session_id} is queued; only stop is available before startup."
            )

    def advance(
        self,
        session_id: str,
        state: str,
        *,
        progress: float | None = None,
        metrics: SessionMetrics | None = None,
        elapsed_seconds: float | None = None,
    ) -> None:
        with self._lock:
            current = self._sessions[session_id]
            updated = replace(
                current,
                state=state,
                sequence=current.sequence + 1,
                progress=current.progress if progress is None else progress,
                elapsed_seconds=(
                    current.duration_seconds
                    if state in {"COMPLETED", "STOPPED", "FAILED"}
                    and elapsed_seconds is None
                    else (
                        current.elapsed_seconds
                        if elapsed_seconds is None
                        else elapsed_seconds
                    )
                ),
                metrics=current.metrics if metrics is None else metrics,
            )
            self._sessions[session_id] = updated
            for sub in self._subs.get(session_id, []):
                sub.snapshots.put(updated)


@pytest.fixture
def memory_meta() -> InMemorySessionMetadataStore:
    return InMemorySessionMetadataStore(terminal_ttl_seconds=3600)


@pytest.fixture
def multi_manager(demo_catalog) -> FakeMultiSessionManager:
    return FakeMultiSessionManager(demo_catalog)


@pytest.fixture
def redis_like_service(
    multi_manager: FakeMultiSessionManager,
    serializer: SnapshotSerializer,
    algorithm_store: AlgorithmRuntimeStore,
    memory_meta: InMemorySessionMetadataStore,
) -> SimulationService:
    settings = Settings(simulation_manager_mode="redis")
    hub = SessionMetricsHub(
        session_root=settings.session_root,
        traffic_manifest_path=settings.generated_dir
        / "manifests"
        / "traffic_manifest.json",
        metadata_store=memory_meta,
    )
    return SimulationService(
        manager=multi_manager,
        serializer=serializer,
        settings=settings,
        algorithm_store=algorithm_store,
        metrics_hub=hub,
        metadata_store=memory_meta,
    )


@pytest.fixture
def local_service(
    multi_manager: FakeMultiSessionManager,
    serializer: SnapshotSerializer,
    algorithm_store: AlgorithmRuntimeStore,
    memory_meta: InMemorySessionMetadataStore,
) -> SimulationService:
    settings = Settings(simulation_manager_mode="local")
    hub = SessionMetricsHub(metadata_store=memory_meta)
    return SimulationService(
        manager=multi_manager,
        serializer=serializer,
        settings=settings,
        algorithm_store=algorithm_store,
        metrics_hub=hub,
        metadata_store=memory_meta,
    )


def _start_body(**overrides: Any) -> dict[str, Any]:
    body = {
        "scenario_preset_id": "east_dense",
        "period": "morning_peak",
        "duration_seconds": 60,
        "control_mode": "fixed",
        "realtime": False,
        "gui": False,
    }
    body.update(overrides)
    return body


def test_create_manager_local_mode() -> None:
    settings = Settings(simulation_manager_mode="local")
    with patch("backend.app.services.manager_factory.SimulationManager") as cls:
        cls.return_value = MagicMock(name="local-manager")
        manager = create_simulation_manager(settings)
        cls.assert_called_once()
        assert manager is cls.return_value


def test_create_manager_redis_mode() -> None:
    settings = Settings(simulation_manager_mode="redis")
    with patch("backend.app.services.manager_factory.RedisSimulationManager") as cls:
        cls.return_value = MagicMock(name="redis-manager")
        manager = create_simulation_manager(settings)
        cls.assert_called_once()
        assert manager is cls.return_value


def test_create_manager_redis_unavailable_does_not_fallback() -> None:
    settings = Settings(simulation_manager_mode="redis")
    with patch(
        "backend.app.services.manager_factory.RedisSimulationManager",
        side_effect=RedisUnavailableError("boom"),
    ):
        with pytest.raises(RedisUnavailableError):
            create_simulation_manager(settings)


def test_metadata_store_factory_modes() -> None:
    mem = create_session_metadata_store(
        mode="local",
        redis_url="redis://127.0.0.1:9/0",
        key_prefix="citypulse:backend",
        terminal_ttl_seconds=60,
    )
    assert isinstance(mem, InMemorySessionMetadataStore)
    mem.upsert("s1", control_mode="fixed", scenario_preset_id="east_dense", state="QUEUED")
    assert mem.get("s1") is not None


def test_can_create_multiple_queued_sessions(redis_like_service: SimulationService, multi_manager):
    from backend.app.schemas.simulations import StartSimulationRequest

    a, snap_a = redis_like_service.start(StartSimulationRequest(**_start_body()))
    b, snap_b = redis_like_service.start(
        StartSimulationRequest(**_start_body(control_mode="max_pressure"))
    )
    assert a != b
    assert snap_a.state == "QUEUED"
    assert snap_b.state == "QUEUED"
    listed = redis_like_service.list_sessions()
    assert listed["total"] >= 2
    modes = {item["session_id"]: item["control_mode"] for item in listed["items"]}
    assert modes[a] == "fixed"
    assert modes[b] == "max_pressure"


def test_sessions_metrics_do_not_cross_talk(redis_like_service: SimulationService, multi_manager):
    from backend.app.schemas.simulations import StartSimulationRequest

    a, _ = redis_like_service.start(StartSimulationRequest(**_start_body()))
    b, _ = redis_like_service.start(
        StartSimulationRequest(**_start_body(control_mode="sotl"))
    )
    multi_manager.advance(a, "RUNNING", progress=0.2)
    multi_manager.advance(b, "RUNNING", progress=0.8)
    time.sleep(0.05)
    meta_a = redis_like_service._metadata.get(a)
    meta_b = redis_like_service._metadata.get(b)
    assert meta_a is not None and meta_a.control_mode == "fixed"
    assert meta_b is not None and meta_b.control_mode == "sotl"
    metrics_a = redis_like_service.get_metrics(a)
    metrics_b = redis_like_service.get_metrics(b)
    assert metrics_a["algorithm"] == "fixed"
    assert metrics_b["algorithm"] == "sotl"
    assert metrics_a["episode_id"] == a
    assert metrics_b["episode_id"] == b


def test_session_recovery_after_restart(
    multi_manager: FakeMultiSessionManager,
    serializer: SnapshotSerializer,
    algorithm_store: AlgorithmRuntimeStore,
    memory_meta: InMemorySessionMetadataStore,
    demo_catalog,
):
    from backend.app.schemas.simulations import StartSimulationRequest

    settings = Settings(simulation_manager_mode="redis")
    service1 = SimulationService(
        manager=multi_manager,
        serializer=serializer,
        settings=settings,
        algorithm_store=algorithm_store,
        metrics_hub=SessionMetricsHub(metadata_store=memory_meta),
        metadata_store=memory_meta,
    )
    session_id, _ = service1.start(StartSimulationRequest(**_start_body()))
    multi_manager.advance(session_id, "RUNNING", progress=0.3)
    service1._stop_all_watchers()

    # 模拟后端重启：新service，复用同一metadata + manager
    service2 = SimulationService(
        manager=multi_manager,
        serializer=serializer,
        settings=settings,
        algorithm_store=AlgorithmRuntimeStore(),
        metrics_hub=SessionMetricsHub(metadata_store=memory_meta),
        metadata_store=memory_meta,
    )
    recovered = service2.recover_sessions()
    assert recovered >= 1
    meta = memory_meta.get(session_id)
    assert meta is not None
    assert meta.control_mode == "fixed"
    assert meta.scenario_preset_id == "east_dense"
    listed = service2.list_sessions()
    assert any(item["session_id"] == session_id for item in listed["items"])


def test_redis_unavailable_returns_503(demo_catalog) -> None:
    app = create_app()
    with TestClient(app) as client:
        app.state.artifacts_ready = True
        app.state.sumo_home_configured = False
        app.state.simulation_manager_mode = "redis"
        app.state.redis_ready = False
        app.state.redis_error = "Cannot connect to Redis"
        app.state.simulation_manager_ready = False
        app.state.simulation_service = None
        app.state.missing_files = []

        health = client.get("/api/v1/health").json()
        assert health["status"] == "degraded"
        assert health["simulation_manager_mode"] == "redis"
        assert health["redis_ready"] is False
        assert health["sumo_home_configured"] is False  # 不应单独判定redis不可用

        response = client.post("/api/v1/simulations", json=_start_body())
        assert response.status_code == 503
        assert response.json()["detail"]["code"] == "REDIS_UNAVAILABLE"


def test_queued_command_restrictions(redis_like_service: SimulationService):
    from backend.app.core.exceptions import AppError
    from backend.app.schemas.simulations import StartSimulationRequest

    session_id, _ = redis_like_service.start(StartSimulationRequest(**_start_body()))
    with pytest.raises(AppError) as exc:
        redis_like_service.pause(session_id)
    assert exc.value.code == "SESSION_QUEUED"
    with pytest.raises(AppError):
        redis_like_service.resume(session_id)
    with pytest.raises(AppError):
        redis_like_service.set_playback_speed(session_id, 2.0)
    # stop允许
    snap = redis_like_service.stop(session_id)
    assert snap.state == "STOPPED"


def test_websocket_streams_from_queued_to_terminal(
    redis_like_service: SimulationService,
    multi_manager: FakeMultiSessionManager,
    algorithm_store: AlgorithmRuntimeStore,
):
    from backend.app.schemas.simulations import StartSimulationRequest

    app = create_app()
    with TestClient(app) as client:
        app.state.artifacts_ready = True
        app.state.sumo_home_configured = True
        app.state.simulation_manager_mode = "redis"
        app.state.redis_ready = True
        app.state.simulation_manager_ready = True
        app.state.simulation_service = redis_like_service
        app.state.algorithm_store = algorithm_store
        app.state.missing_files = []

        session_id, _ = redis_like_service.start(StartSimulationRequest(**_start_body()))

        def advance_later() -> None:
            time.sleep(0.1)
            multi_manager.advance(session_id, "STARTING")
            time.sleep(0.05)
            multi_manager.advance(session_id, "RUNNING", progress=0.5)
            time.sleep(0.05)
            multi_manager.advance(session_id, "COMPLETED", progress=1.0)

        threading.Thread(target=advance_later, daemon=True).start()
        states: list[str] = []
        with client.websocket_connect(f"/api/v1/simulations/{session_id}/stream") as ws:
            while True:
                message = ws.receive_json()
                if message.get("type") != "snapshot":
                    continue
                state = message["data"]["state"]
                states.append(state)
                if state in {"COMPLETED", "STOPPED", "FAILED"}:
                    break
        assert states[0] == "QUEUED"
        assert "COMPLETED" in states


def test_websocket_final_snapshot_waits_for_tripinfo_race(
    multi_manager: FakeMultiSessionManager,
    serializer: SnapshotSerializer,
    algorithm_store: AlgorithmRuntimeStore,
    memory_meta: InMemorySessionMetadataStore,
    tmp_path,
):
    """终态快照先到、TripInfo稍后写出：WS最终帧必须是回填后的finished指标"""

    from backend.app.schemas.simulations import StartSimulationRequest

    session_root = tmp_path / "sessions"
    session_root.mkdir()
    settings = Settings(
        simulation_manager_mode="redis",
        sumo_session_root=str(session_root),
    )

    hub = SessionMetricsHub(
        session_root=session_root,
        traffic_manifest_path=settings.generated_dir
        / "manifests"
        / "traffic_manifest.json",
        metadata_store=memory_meta,
    )
    service = SimulationService(
        manager=multi_manager,
        serializer=serializer,
        settings=settings,
        algorithm_store=algorithm_store,
        metrics_hub=hub,
        metadata_store=memory_meta,
    )

    app = create_app()
    with TestClient(app) as client:
        app.state.artifacts_ready = True
        app.state.sumo_home_configured = True
        app.state.simulation_manager_mode = "redis"
        app.state.redis_ready = True
        app.state.simulation_manager_ready = True
        app.state.simulation_service = service
        app.state.algorithm_store = algorithm_store
        app.state.missing_files = []

        session_id, _ = service.start(StartSimulationRequest(**_start_body()))
        session_dir = session_root / session_id
        session_dir.mkdir(parents=True, exist_ok=True)

        def advance_and_delay_tripinfo() -> None:
            time.sleep(0.08)
            multi_manager.advance(
                session_id,
                "RUNNING",
                progress=0.5,
                metrics=SessionMetrics(departed_vehicles=1, arrived_vehicles=0),
                elapsed_seconds=10.0,
            )
            time.sleep(0.05)
            # 终态先到，TripInfo故意晚到，覆盖watcher/WS竞态
            multi_manager.advance(
                session_id,
                "COMPLETED",
                progress=1.0,
                metrics=SessionMetrics(departed_vehicles=1, arrived_vehicles=1),
            )

            def write_tripinfo() -> None:
                time.sleep(0.4)
                (session_dir / "tripinfo.xml").write_text(
                    '<?xml version="1.0"?>\n'
                    "<tripinfos>\n"
                    "  <tripinfo id='v1' depart='1' arrival='19' "
                    "duration='12' waitingTime='3.5'/>\n"
                    "</tripinfos>\n",
                    encoding="utf-8",
                )

            threading.Thread(target=write_tripinfo, daemon=True).start()

        threading.Thread(target=advance_and_delay_tripinfo, daemon=True).start()

        terminal_evaluations: list[dict[str, Any]] = []
        with client.websocket_connect(f"/api/v1/simulations/{session_id}/stream") as ws:
            while True:
                message = ws.receive_json()
                if message.get("type") != "snapshot":
                    continue
                data = message["data"]
                if data["state"] in {"COMPLETED", "STOPPED", "FAILED"}:
                    terminal_evaluations.append(data.get("evaluation") or {})
                    break

        assert len(terminal_evaluations) == 1
        evaluation = terminal_evaluations[0]
        assert evaluation.get("finished") is True
        sources = evaluation.get("metric_sources") or {}
        assert sources.get("avg_travel_time_s") == "tripinfo_departed"
        assert sources.get("avg_waiting_time_s") == "tripinfo_departed"
        assert "snapshot_provisional" not in sources.values()
        assert evaluation.get("avg_travel_time") == pytest.approx(12.0)
        assert evaluation.get("avg_waiting_time") == pytest.approx(3.5)


def test_redis_shutdown_does_not_stop_sumo_sessions(
    redis_like_service: SimulationService, multi_manager: FakeMultiSessionManager
):
    from backend.app.schemas.simulations import StartSimulationRequest

    session_id, _ = redis_like_service.start(StartSimulationRequest(**_start_body()))
    multi_manager.advance(session_id, "RUNNING")
    redis_like_service.shutdown()
    assert session_id not in multi_manager.stop_calls
    # 会话仍在manager中运行
    assert multi_manager.snapshot(session_id).state == "RUNNING"


def test_local_shutdown_stops_sessions(local_service: SimulationService, multi_manager):
    from backend.app.schemas.simulations import StartSimulationRequest

    session_id, _ = local_service.start(StartSimulationRequest(**_start_body()))
    multi_manager.advance(session_id, "RUNNING")
    local_service.shutdown()
    assert session_id in multi_manager.stop_calls


def test_health_includes_manager_mode(client: TestClient) -> None:
    response = client.get("/api/v1/health")
    assert response.status_code == 200
    payload = response.json()
    assert "simulation_manager_mode" in payload
    assert "algorithm_base_url" in payload
    assert payload["recommended_uvicorn_workers"] == 1


def test_list_sessions_api(
    redis_like_service: SimulationService,
    algorithm_store: AlgorithmRuntimeStore,
):
    from backend.app.schemas.simulations import StartSimulationRequest

    app = create_app()
    with TestClient(app) as client:
        app.state.artifacts_ready = True
        app.state.sumo_home_configured = True
        app.state.simulation_manager_mode = "redis"
        app.state.redis_ready = True
        app.state.simulation_manager_ready = True
        app.state.simulation_service = redis_like_service
        app.state.algorithm_store = algorithm_store
        app.state.missing_files = []

        s1, _ = redis_like_service.start(StartSimulationRequest(**_start_body()))
        s2, _ = redis_like_service.start(
            StartSimulationRequest(**_start_body(control_mode="sotl"))
        )
        response = client.get("/api/v1/simulations")
        assert response.status_code == 200
        body = response.json()
        ids = {item["session_id"] for item in body["items"]}
        assert s1 in ids and s2 in ids

        filtered = client.get("/api/v1/simulations", params={"state": "QUEUED"})
        assert filtered.status_code == 200
        assert all(item["state"] == "QUEUED" for item in filtered.json()["items"])


def test_unknown_session_returns_404(
    redis_like_service: SimulationService,
    algorithm_store: AlgorithmRuntimeStore,
):
    app = create_app()
    with TestClient(app) as client:
        app.state.artifacts_ready = True
        app.state.sumo_home_configured = True
        app.state.simulation_manager_mode = "redis"
        app.state.redis_ready = True
        app.state.simulation_manager_ready = True
        app.state.simulation_service = redis_like_service
        app.state.algorithm_store = algorithm_store
        app.state.missing_files = []

        response = client.get("/api/v1/simulations/does-not-exist")
        assert response.status_code == 404


def test_metrics_lock_exclusive(memory_meta: InMemorySessionMetadataStore) -> None:
    assert memory_meta.try_acquire_metrics_lock("s", owner="a", ttl_seconds=5)
    assert not memory_meta.try_acquire_metrics_lock("s", owner="b", ttl_seconds=5)
    assert memory_meta.refresh_metrics_lock("s", owner="a", ttl_seconds=5)
    memory_meta.release_metrics_lock("s", owner="a")
    assert memory_meta.try_acquire_metrics_lock("s", owner="b", ttl_seconds=5)
