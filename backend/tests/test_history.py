"""历史交通数据仓库和 5 秒采样记录器测试。"""

from __future__ import annotations

from typing import Any

from backend.app.services.history import (
    HistoryQuery,
    HistoryRecorder,
    InMemoryHistoryRepository,
    RedisHistoryRepository,
)
from backend.app.copilot.traffic_tools import (
    SimulationServiceTrafficDataSource,
    TrafficToolService,
)


def _snapshot(
    elapsed: float,
    *,
    vehicle_count: int = 4,
    event_cards: list[dict[str, Any]] | None = None,
    events: list[dict[str, Any]] | None = None,
) -> dict[str, Any]:
    return {
        "session_id": "session-1",
        "state": "RUNNING",
        "sequence": int(elapsed * 10),
        "elapsed_seconds": elapsed,
        "duration_seconds": 60.0,
        "progress": elapsed / 60.0,
        "official_time": f"t{elapsed:g}",
        "intersections": {
            "demo_1": {
                "current_phase": 1,
                "pending_phase": 2,
                "stage": "through",
                "stage_elapsed": elapsed,
                "lanes": {
                    "lane-1": {
                        "vehicle_count": vehicle_count,
                        "halting_count": max(0, vehicle_count - 2),
                        "mean_speed": max(0.0, 10.0 - elapsed / 2.0),
                        "waiting_time": elapsed,
                        "occupancy": 20.0,
                        "edge_id": "edge-1",
                        "role": "incoming",
                        "approach_id": "west",
                        "downstream_lane_ids": ["lane-2"],
                    }
                },
            }
        },
        # 即使原始快照带有车辆字段，HistoryFrame 也不能保存它。
        "vehicles": [{"vehicle_id": "should-not-be-stored", "x": 1.0}],
        "events": events or [],
        "event_detection": {"as_of_seconds": elapsed, "cards": event_cards or []},
        "prediction": {
            "as_of_seconds": elapsed,
            "model": "NarrowNet-TDP",
            "intersections": {
                "demo_1": {
                    "current_vehicle_count": float(vehicle_count),
                    "predicted_vehicle_count": float(vehicle_count + 2),
                }
            },
        },
        "traffic_style": {
            "as_of_seconds": elapsed,
            "edges": {"edge-1": {"level": "congested", "score": 0.75}},
        },
    }


def _card(*, severity: str = "medium", traffic_state: str = "localized_blockage") -> dict[str, Any]:
    return {
        "event_id": "det-1",
        "status": "active",
        "event_type": "lane_blocked",
        "traffic_state": traffic_state,
        "intersection_id": "demo_1",
        "lane_ids": ["lane-1"],
        "edge_id": "edge-1",
        "severity": severity,
        "confidence": 0.9,
    }


def _intelligence(snapshot: dict[str, Any]) -> dict[str, Any]:
    return {
        "event_detection": snapshot["event_detection"],
        "prediction": snapshot["prediction"],
        "traffic_style": snapshot["traffic_style"],
    }


def test_recorder_samples_once_per_five_second_bucket_and_excludes_vehicles() -> None:
    repository = InMemoryHistoryRepository()
    recorder = HistoryRecorder(repository, sample_seconds=5.0)

    initial = _snapshot(
        0.0,
        events=[
            {"event_id": "scheduled-1", "state": "SCHEDULED"},
            {"event_id": "active-1", "state": "ACTIVE", "lane_id": "lane-1"},
        ],
    )
    assert recorder.record(initial, _intelligence(initial)) is True
    # 同一个 5 秒桶不应重复写入。
    assert recorder.record(_snapshot(4.9), _intelligence(_snapshot(4.9))) is False
    assert recorder.record(_snapshot(5.0), _intelligence(_snapshot(5.0))) is True

    result = repository.query(HistoryQuery(session_id="session-1", lookback_seconds=None))
    assert result.total_frames == 2
    assert len(result.frames) == 2
    assert "vehicles" not in result.frames[0]
    assert result.frames[0]["intersections"]["demo_1"]["lanes"]["lane-1"][
        "vehicle_count"
    ] == 4
    assert [item["event_id"] for item in result.frames[0]["events"]] == ["active-1"]


def test_recorder_writes_event_lifecycle_changes() -> None:
    repository = InMemoryHistoryRepository()
    recorder = HistoryRecorder(repository, sample_seconds=5.0)

    first = _snapshot(0.0, event_cards=[_card(severity="medium")])
    second = _snapshot(5.0, event_cards=[_card(severity="high", traffic_state="spillback")])
    third = _snapshot(10.0, event_cards=[])
    for snapshot in (first, second, third):
        assert recorder.record(snapshot, _intelligence(snapshot)) is True

    result = repository.query(HistoryQuery(session_id="session-1", lookback_seconds=None))
    assert [item["change_type"] for item in result.events] == [
        "created",
        "risk_changed",
        "trend_changed",
        "resolved",
    ]
    assert result.events[-1]["event"]["status"] == "resolved"


def test_query_filters_time_and_downsamples_without_cross_session_leak() -> None:
    repository = InMemoryHistoryRepository()
    recorder = HistoryRecorder(repository, sample_seconds=5.0)
    for elapsed in (0.0, 5.0, 10.0, 15.0, 20.0):
        snapshot = _snapshot(elapsed, vehicle_count=int(elapsed) + 1)
        assert recorder.record(snapshot, _intelligence(snapshot)) is True

    result = repository.query(
        HistoryQuery(
            session_id="session-1",
            from_seconds=5.0,
            to_seconds=20.0,
            lookback_seconds=None,
            max_points=3,
        )
    )
    assert result.total_frames == 4
    assert result.downsampled is True
    assert [item["elapsed_seconds"] for item in result.frames] == [5.0, 15.0, 20.0]

    other = repository.query(HistoryQuery(session_id="session-2", lookback_seconds=None))
    assert other.frames == ()
    assert other.events == ()


class _SnapshotReader:
    def __init__(self, snapshot: dict[str, Any]) -> None:
        self.snapshot_value = snapshot

    def snapshot(self, session_id: str) -> dict[str, Any]:
        assert session_id == self.snapshot_value["session_id"]
        return self.snapshot_value


def test_traffic_history_tool_reads_repository_and_event_timeline() -> None:
    repository = InMemoryHistoryRepository()
    recorder = HistoryRecorder(repository, sample_seconds=5.0)
    first = _snapshot(0.0, event_cards=[_card(severity="medium")])
    second = _snapshot(5.0, event_cards=[_card(severity="high")])
    third = _snapshot(10.0, event_cards=[])
    for snapshot in (first, second, third):
        assert recorder.record(snapshot, _intelligence(snapshot)) is True

    source = SimulationServiceTrafficDataSource(
        _SnapshotReader(third), history_repository=repository
    )
    service = TrafficToolService(source, session_id="session-1")
    result = service.execute(
        "get_traffic_history",
        {
            "intersection_ids": ["demo_1"],
            "lookback_seconds": 30,
            "metrics": ["halting_count", "events"],
        },
    )

    assert result["data"]["history_available"] is True
    assert result["data"]["sample_count"] == 3
    assert [item["change_type"] for item in result["data"]["event_timeline"]] == [
        "created",
        "risk_changed",
        "resolved",
    ]
    assert set(result["data"]["series"][0]) == {
        "scope",
        "timestamp",
        "halting_count",
        "events",
    }


class _FakeRedisPipeline:
    def __init__(self, client: "_FakeRedis") -> None:
        self.client = client
        self.operations: list[tuple[str, tuple[Any, ...], dict[str, Any]]] = []

    def xadd(self, name: str, fields: dict[str, Any]) -> None:
        self.operations.append(("xadd", (name, fields), {}))

    def hset(self, name: str, *, mapping: dict[str, Any]) -> None:
        self.operations.append(("hset", (name,), {"mapping": mapping}))

    def expire(self, name: str, seconds: int) -> None:
        self.operations.append(("expire", (name, seconds), {}))

    def execute(self) -> list[Any]:
        result = []
        for name, args, kwargs in self.operations:
            result.append(getattr(self.client, name)(*args, **kwargs))
        return result


class _FakeRedis:
    def __init__(self) -> None:
        self.values: dict[str, Any] = {}
        self.streams: dict[str, list[tuple[str, dict[str, Any]]]] = {}
        self.hashes: dict[str, dict[str, Any]] = {}
        self.expirations: dict[str, int] = {}
        self._entry_id = 0

    def ping(self) -> bool:
        return True

    def set(self, name: str, value: Any, *, nx: bool = False, ex: int | None = None) -> bool:
        if nx and name in self.values:
            return False
        self.values[name] = value
        if ex is not None:
            self.expirations[name] = ex
        return True

    def delete(self, name: str) -> int:
        return int(self.values.pop(name, None) is not None)

    def pipeline(self, *, transaction: bool = True) -> _FakeRedisPipeline:
        assert transaction is True
        return _FakeRedisPipeline(self)

    def xadd(self, name: str, fields: dict[str, Any]) -> str:
        self._entry_id += 1
        entry_id = f"{self._entry_id}-0"
        self.streams.setdefault(name, []).append((entry_id, dict(fields)))
        return entry_id

    def hset(self, name: str, *, mapping: dict[str, Any]) -> int:
        self.hashes.setdefault(name, {}).update(mapping)
        return len(mapping)

    def expire(self, name: str, seconds: int) -> bool:
        self.expirations[name] = seconds
        return True

    def xrange(self, name: str, minimum: str, maximum: str):
        assert (minimum, maximum) == ("-", "+")
        return list(self.streams.get(name, ()))

    def xrevrange(self, name: str, maximum: str, minimum: str, *, count: int):
        assert (maximum, minimum) == ("+", "-")
        return list(reversed(self.streams.get(name, ())))[:count]


def test_redis_history_is_idempotent_and_expires_with_session() -> None:
    client = _FakeRedis()
    repository = RedisHistoryRepository(
        "redis://unused",
        key_prefix="citypulse:backend",
        terminal_ttl_seconds=3600,
        client=client,
    )
    recorder = HistoryRecorder(repository, sample_seconds=5.0)
    snapshot = _snapshot(0.0)

    assert recorder.record(snapshot, _intelligence(snapshot)) is True
    assert recorder.record(snapshot, _intelligence(snapshot)) is False
    result = repository.query(HistoryQuery(session_id="session-1", lookback_seconds=None))
    assert result.total_frames == 1
    assert len(client.streams["citypulse:backend:history:session-1:frames"]) == 1

    repository.expire("session-1")
    assert client.expirations["citypulse:backend:history:session-1:frames"] == 3600
    assert client.expirations["citypulse:backend:history:session-1:events"] == 3600
    assert client.expirations["citypulse:backend:history:session-1:meta"] == 3600
