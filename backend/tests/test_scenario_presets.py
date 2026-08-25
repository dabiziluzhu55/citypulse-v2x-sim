"""场景预设与启动请求解析测试"""

import pytest
from pydantic import ValidationError

from backend.app.core.exceptions import AppError
from backend.app.scenario.presets import SCENARIO_PRESET_REGISTRY, list_scenario_presets
from backend.app.scenario.resolver import resolve_disturbance_targets, resolve_start_simulation
from backend.app.schemas.disturbance_targets import DisturbanceTargetSpeedLimit
from backend.app.schemas.simulations import StartSimulationRequest
from simulation.sumo.engine.session import (
    IntersectionCapability,
    LaneCapability,
    OriginCapability,
    SimulationCatalog,
)


def _intersection(intersection_id: str, lane_id: str) -> IntersectionCapability:
    return IntersectionCapability(
        intersection_id=intersection_id,
        longitude=116.0,
        latitude=38.9,
        periods=("morning_peak", "off_peak", "evening_peak"),
        origins=(
            OriginCapability(
                origin_id="incoming",
                label="Incoming",
                lane_ids=(lane_id,),
            ),
        ),
        lanes=(
            LaneCapability(
                lane_id=lane_id,
                edge_id=lane_id.rsplit("_", 1)[0],
                lane_index=0,
                role="incoming",
                approach="west",
                approach_label="West",
                length=100.0,
                max_speed=13.9,
            ),
        ),
    )


@pytest.fixture
def east_dense_catalog() -> SimulationCatalog:
    return SimulationCatalog(
        intersections={
            "demo_3": _intersection("demo_3", "-30_0"),
            "demo_5": _intersection("demo_5", "-50_0"),
            "demo_6": _intersection("demo_6", "-60_0"),
            "demo_9": _intersection("demo_9", "-90_0"),
        }
    )


def test_list_scenario_presets_contains_three_presets() -> None:
    presets = list_scenario_presets()
    assert [preset.preset_id for preset in presets] == [
        "east_dense",
        "west_dense",
        "xiongan_20",
    ]
    assert len(SCENARIO_PRESET_REGISTRY["xiongan_20"].intersection_ids) == 20


def test_start_simulation_request_requires_known_preset() -> None:
    with pytest.raises(ValidationError):
        StartSimulationRequest(
            scenario_preset_id="unknown",
            period="morning_peak",
            duration_seconds=600,
        )


def test_resolve_east_dense_disturbance_targets(east_dense_catalog: SimulationCatalog) -> None:
    request = StartSimulationRequest(
        scenario_preset_id="east_dense",
        period="morning_peak",
        duration_seconds=600,
        disturbance_targets=[
            {
                "event_type": "lane_closure",
                "intersection_id": "demo_3",
                "start_seconds": 60,
                "end_seconds": 300,
            },
            {
                "event_type": "accident",
                "intersection_id": "demo_9",
                "start_seconds": 120,
                "end_seconds": 420,
                "position_ratio": 0.4,
            },
        ],
    )

    resolved = resolve_start_simulation(request, east_dense_catalog)

    assert resolved.scenario_preset_id == "east_dense"
    assert resolved.intersection_ids == ("demo_3", "demo_5", "demo_6", "demo_9")
    assert len(resolved.initial_events) == 2
    assert resolved.initial_events[0].lane_ids == ["-30_0"]
    assert resolved.initial_events[1].lane_id == "-90_0"


def test_reject_disturbance_outside_preset(east_dense_catalog: SimulationCatalog) -> None:
    request = StartSimulationRequest(
        scenario_preset_id="east_dense",
        period="morning_peak",
        duration_seconds=600,
        disturbance_targets=[
            {
                "event_type": "lane_closure",
                "intersection_id": "demo_14",
                "start_seconds": 60,
                "end_seconds": 300,
            },
        ],
    )

    with pytest.raises(Exception) as exc_info:
        resolve_start_simulation(request, east_dense_catalog)

    assert exc_info.value.status_code == 422


def test_resolve_disturbance_targets_only(east_dense_catalog: SimulationCatalog) -> None:
    preset = SCENARIO_PRESET_REGISTRY["east_dense"]
    events = resolve_disturbance_targets(
        [
            DisturbanceTargetSpeedLimit(
                event_type="speed_limit",
                intersection_id="demo_5",
                start_seconds=10,
                end_seconds=100,
                max_speed=4.0,
            )
        ],
        preset,
        east_dense_catalog,
    )

    assert events[0].event_type == "speed_limit"
    assert events[0].lane_ids == ["-50_0"]
    assert events[0].max_speed == 4.0


def test_resolve_speed_limit_from_speed_kmh(east_dense_catalog: SimulationCatalog) -> None:
    preset = SCENARIO_PRESET_REGISTRY["east_dense"]
    events = resolve_disturbance_targets(
        [
            DisturbanceTargetSpeedLimit(
                event_type="speed_limit",
                intersection_id="demo_5",
                start_seconds=10,
                end_seconds=100,
                speed_kmh=30,
            )
        ],
        preset,
        east_dense_catalog,
    )

    assert events[0].event_type == "speed_limit"
    assert events[0].max_speed == pytest.approx(30 / 3.6)


def test_reject_speed_limit_not_below_lane_baseline(
    east_dense_catalog: SimulationCatalog,
) -> None:
    preset = SCENARIO_PRESET_REGISTRY["east_dense"]
    with pytest.raises(AppError) as exc_info:
        resolve_disturbance_targets(
            [
                DisturbanceTargetSpeedLimit(
                    event_type="speed_limit",
                    intersection_id="demo_5",
                    start_seconds=10,
                    end_seconds=100,
                    speed_kmh=60,
                )
            ],
            preset,
            east_dense_catalog,
        )

    assert exc_info.value.status_code == 422
    assert exc_info.value.code == "INVALID_SPEED_LIMIT"




def test_resolve_mappo_east_dense_zero_shot(
    east_dense_catalog: SimulationCatalog,
) -> None:
    request = StartSimulationRequest(
        scenario_preset_id="east_dense",
        period="morning_peak",
        duration_seconds=600,
        control_mode="mappo",
    )

    resolved = resolve_start_simulation(request, east_dense_catalog)

    assert resolved.control_mode == "mappo"
    assert resolved.intersection_ids == ("demo_3", "demo_5", "demo_6", "demo_9")
    assert resolved.model_alias == "mappo_cooperative_20tls_ep160"


def test_backend_presets_are_self_contained() -> None:
    """backend 场景预设必须独立，不得依赖 algorithms/config。"""
    assert SCENARIO_PRESET_REGISTRY["east_dense"].intersection_ids == (
        "demo_3",
        "demo_5",
        "demo_6",
        "demo_9",
    )
    assert SCENARIO_PRESET_REGISTRY["east_dense"].map_template == "east_dense"
    assert SCENARIO_PRESET_REGISTRY["west_dense"].intersection_ids == (
        "demo_14",
        "demo_15",
        "demo_19",
    )
    assert len(SCENARIO_PRESET_REGISTRY["xiongan_20"].intersection_ids) == 20
