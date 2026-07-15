"""快照序列化与异常映射测试"""

from unittest.mock import MagicMock

from backend.app.services.snapshot_serializer import SnapshotSerializer
from simulation.sumo.session import SessionMetrics, SimulationSnapshot, VehicleRuntimeSnapshot


def test_snapshot_serialization(coordinate_converter: MagicMock) -> None:
    serializer = SnapshotSerializer(coordinate_converter)
    snapshot = SimulationSnapshot(
        session_id="session-1",
        state="RUNNING",
        sequence=1,
        elapsed_seconds=10.0,
        duration_seconds=600.0,
        progress=0.0167,
        official_time="07:30:10",
        vehicles=(
            VehicleRuntimeSnapshot(
                vehicle_id="veh-1",
                x=123.4567,
                y=567.8912,
                speed=8.5123,
                angle=90.1234,
                road_id="road-1",
                lane_id="-56734_0",
            ),
        ),
        metrics=SessionMetrics(active_vehicles=1),
    )

    payload = serializer.serialize(snapshot)
    vehicle = payload["vehicles"][0]
    assert vehicle["vehicle_id"] == "veh-1"
    assert vehicle["longitude"] == 116.1267
    assert vehicle["latitude"] == 38.9911
    assert vehicle["height"] == 0.0
    assert vehicle["x"] == 123.457
    assert vehicle["speed"] == 8.512
    assert payload["progress"] == 0.0167


def test_session_busy_maps_to_409(client, mock_manager: MagicMock) -> None:
    from simulation.sumo.session import SessionBusyError

    mock_manager.start.side_effect = SessionBusyError("A SUMO simulation is already active.")
    response = client.post(
        "/api/v1/simulations",
        json={
            "intersection_ids": ["demo_2"],
            "period": "morning_peak",
            "origins": {},
            "window_start_seconds": 0,
            "duration_seconds": 600,
            "flow_multiplier": 1.2,
            "control_mode": "fixed",
            "seed": 42,
            "step_length": 0.05,
            "realtime": True,
            "gui": False,
            "snapshot_interval_seconds": 0.2,
            "initial_events": [],
        },
    )
    assert response.status_code == 409
    assert response.json()["detail"]["code"] == "SIMULATION_BUSY"
