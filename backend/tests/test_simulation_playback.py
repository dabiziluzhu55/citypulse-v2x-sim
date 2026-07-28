"""仿真暂停/恢复接口测试"""

from unittest.mock import MagicMock

from simulation.sumo.session import SessionMetrics, SimulationSnapshot, UnknownSessionError


def _running_snapshot(session_id: str = "session-1", playback_speed: float | None = 1.0) -> SimulationSnapshot:
    return SimulationSnapshot(
        session_id=session_id,
        state="RUNNING",
        sequence=3,
        elapsed_seconds=12.0,
        duration_seconds=600.0,
        progress=0.02,
        official_time="07:30:12",
        playback_speed=playback_speed,
        metrics=SessionMetrics(active_vehicles=5),
    )


def _paused_snapshot(session_id: str = "session-1", playback_speed: float | None = 1.0) -> SimulationSnapshot:
    return SimulationSnapshot(
        session_id=session_id,
        state="PAUSED",
        sequence=4,
        elapsed_seconds=12.0,
        duration_seconds=600.0,
        progress=0.02,
        official_time="07:30:12",
        playback_speed=playback_speed,
        metrics=SessionMetrics(active_vehicles=5),
    )


def test_pause_simulation_returns_paused_state(client, mock_manager: MagicMock) -> None:
    mock_manager.snapshot.return_value = _paused_snapshot()

    response = client.post("/api/v1/simulations/session-1/pause")

    assert response.status_code == 200
    payload = response.json()
    assert payload == {
        "session_id": "session-1",
        "state": "PAUSED",
        "playback_speed": 1.0,
    }
    mock_manager.pause.assert_called_once_with("session-1")
    mock_manager.snapshot.assert_called_once_with("session-1")


def test_resume_simulation_returns_running_state(client, mock_manager: MagicMock) -> None:
    mock_manager.snapshot.return_value = _running_snapshot()

    response = client.post("/api/v1/simulations/session-1/resume")

    assert response.status_code == 200
    payload = response.json()
    assert payload == {
        "session_id": "session-1",
        "state": "RUNNING",
        "playback_speed": 1.0,
    }
    mock_manager.resume.assert_called_once_with("session-1")
    mock_manager.snapshot.assert_called_once_with("session-1")


def test_pause_unknown_session_maps_to_404(client, mock_manager: MagicMock) -> None:
    mock_manager.pause.side_effect = UnknownSessionError("Unknown session: missing")

    response = client.post("/api/v1/simulations/missing/pause")

    assert response.status_code == 404
    assert response.json()["detail"]["code"] == "UNKNOWN_SESSION"


def test_set_playback_speed_returns_updated_speed(client, mock_manager: MagicMock) -> None:
    mock_manager.snapshot.return_value = _running_snapshot(playback_speed=2.0)

    response = client.post(
        "/api/v1/simulations/session-1/playback-speed",
        json={"playback_speed": 2.0},
    )

    assert response.status_code == 200
    payload = response.json()
    assert payload == {
        "session_id": "session-1",
        "state": "RUNNING",
        "playback_speed": 2.0,
    }
    mock_manager.set_playback_speed.assert_called_once_with("session-1", 2.0)


def test_reject_invalid_playback_speed(client) -> None:
    response = client.post(
        "/api/v1/simulations/session-1/playback-speed",
        json={"playback_speed": 4.0},
    )

    assert response.status_code == 422


def test_pause_requires_artifacts(degraded_client) -> None:
    response = degraded_client.post("/api/v1/simulations/session-1/pause")

    assert response.status_code == 503
    assert response.json()["detail"]["code"] == "ARTIFACTS_NOT_READY"
