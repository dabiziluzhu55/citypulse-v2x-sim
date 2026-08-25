"""请求Schema校验测试"""

import pytest
from pydantic import ValidationError

from backend.app.schemas.disturbance_targets import (
    DisturbanceTargetAccident,
    DisturbanceTargetLaneClosure,
    DisturbanceTargetMajorEventClosing,
    DisturbanceTargetMajorEventOpening,
    DisturbanceTargetSpeedLimit,
)
from backend.app.schemas.events import (
    AccidentRequest,
    LaneClosureRequest,
    MajorEventClosingRequest,
    MajorEventOpeningRequest,
    SpeedLimitRequest,
)
from backend.app.schemas.simulations import StartSimulationRequest


def test_start_simulation_request_valid() -> None:
    request = StartSimulationRequest(
        scenario_preset_id="east_dense",
        period="morning_peak",
        duration_seconds=600,
        control_mode="fixed",
    )
    assert request.realtime is True
    assert request.gui is False
    assert request.disturbance_targets == []


def test_start_simulation_accepts_max_pressure() -> None:
    request = StartSimulationRequest(
        scenario_preset_id="west_dense",
        period="morning_peak",
        duration_seconds=600,
        control_mode="max_pressure",
    )
    assert request.control_mode == "max_pressure"


def test_start_simulation_accepts_playback_speed() -> None:
    request = StartSimulationRequest(
        scenario_preset_id="east_dense",
        period="morning_peak",
        duration_seconds=600,
        playback_speed=2.0,
    )
    assert request.playback_speed == 2.0


def test_start_simulation_accepts_sotl() -> None:
    request = StartSimulationRequest(
        scenario_preset_id="west_dense",
        period="morning_peak",
        duration_seconds=600,
        control_mode="sotl",
    )
    assert request.control_mode == "sotl"


def test_start_simulation_accepts_ippo() -> None:
    request = StartSimulationRequest(
        scenario_preset_id="xiongan_20",
        period="morning_peak",
        duration_seconds=600,
        control_mode="ippo",
    )
    assert request.control_mode == "ippo"


def test_reject_invalid_playback_speed() -> None:
    with pytest.raises(ValidationError):
        StartSimulationRequest(
            scenario_preset_id="east_dense",
            period="morning_peak",
            duration_seconds=600,
            playback_speed=4.0,
        )


def test_reject_unknown_scenario_preset() -> None:
    with pytest.raises(ValidationError):
        StartSimulationRequest(
            scenario_preset_id="demo_2_single",
            period="morning_peak",
            duration_seconds=600,
            control_mode="fixed",
        )


@pytest.mark.parametrize("control_mode", ["algorithm", "unknown"])
def test_reject_unsupported_control_mode(control_mode: str) -> None:
    with pytest.raises(ValidationError):
        StartSimulationRequest(
            scenario_preset_id="east_dense",
            period="morning_peak",
            duration_seconds=600,
            control_mode=control_mode,
        )


def test_disturbance_target_discriminated_union() -> None:
    lane_closure = DisturbanceTargetLaneClosure(
        event_type="lane_closure",
        intersection_id="demo_14",
        start_seconds=60,
        end_seconds=300,
    )
    speed_limit = DisturbanceTargetSpeedLimit(
        event_type="speed_limit",
        intersection_id="demo_15",
        start_seconds=60,
        end_seconds=300,
        max_speed=5.0,
    )
    accident = DisturbanceTargetAccident(
        event_type="accident",
        intersection_id="demo_19",
        start_seconds=60,
        end_seconds=300,
        position_ratio=0.6,
    )
    assert lane_closure.intersection_id == "demo_14"
    assert speed_limit.max_speed == 5.0
    assert accident.position_ratio == 0.6


def test_speed_limit_accepts_speed_kmh() -> None:
    target = DisturbanceTargetSpeedLimit(
        event_type="speed_limit",
        intersection_id="demo_15",
        start_seconds=60,
        end_seconds=300,
        speed_kmh=30,
    )
    runtime = SpeedLimitRequest(
        event_type="speed_limit",
        event_id="speed-limit-kmh",
        start_seconds=60,
        end_seconds=300,
        lane_ids=["-56734_0"],
        speed_kmh=30,
    )
    assert target.max_speed == pytest.approx(30 / 3.6)
    assert runtime.max_speed == pytest.approx(30 / 3.6)


def test_speed_limit_rejects_inconsistent_units() -> None:
    with pytest.raises(ValidationError):
        DisturbanceTargetSpeedLimit(
            event_type="speed_limit",
            intersection_id="demo_15",
            start_seconds=60,
            end_seconds=300,
            max_speed=5.0,
            speed_kmh=30,
        )
    with pytest.raises(ValidationError):
        SpeedLimitRequest(
            event_type="speed_limit",
            event_id="speed-limit-1",
            start_seconds=60,
            end_seconds=300,
            lane_ids=["-56734_0"],
            max_speed=5.0,
            speed_kmh=30,
        )


def test_runtime_speed_limit_requires_speed_value() -> None:
    with pytest.raises(ValidationError):
        SpeedLimitRequest(
            event_type="speed_limit",
            event_id="speed-limit-1",
            start_seconds=60,
            end_seconds=300,
            lane_ids=["-56734_0"],
        )


def test_disturbance_speed_limit_defaults_max_speed() -> None:
    target = DisturbanceTargetSpeedLimit(
        event_type="speed_limit",
        intersection_id="demo_15",
        start_seconds=60,
        end_seconds=300,
    )
    assert target.max_speed == 5.0


def test_event_discriminated_union() -> None:
    lane_closure = LaneClosureRequest(
        event_type="lane_closure",
        event_id="construction-1",
        start_seconds=60,
        end_seconds=300,
        lane_ids=["-56734_0"],
    )
    speed_limit = SpeedLimitRequest(
        event_type="speed_limit",
        event_id="speed-limit-1",
        start_seconds=60,
        end_seconds=300,
        lane_ids=["-56734_0"],
        max_speed=5.0,
    )
    accident = AccidentRequest(
        event_type="accident",
        event_id="accident-1",
        start_seconds=60,
        end_seconds=300,
        lane_id="-56734_0",
        position_ratio=0.6,
    )
    opening = MajorEventOpeningRequest(
        event_type="major_event_opening",
        event_id="open-1",
        start_seconds=60,
        end_seconds=300,
        venue_lane_id="-56734_0",
        vehicle_count=10,
        source_lane_ids=[],
    )
    closing = MajorEventClosingRequest(
        event_type="major_event_closing",
        event_id="close-1",
        start_seconds=60,
        end_seconds=300,
        venue_lane_id="-56734_0",
        vehicle_count=10,
        destination_lane_ids=[],
    )
    assert lane_closure.event_type == "lane_closure"
    assert speed_limit.max_speed == 5.0
    assert accident.position_ratio == 0.6
    assert opening.vehicle_count == 10
    assert closing.destination_lane_ids == []


def test_major_event_disturbance_targets() -> None:
    opening = DisturbanceTargetMajorEventOpening(
        event_type="major_event_opening",
        intersection_id="demo_2",
        start_seconds=60,
        end_seconds=300,
        vehicle_count=12,
    )
    closing = DisturbanceTargetMajorEventClosing(
        event_type="major_event_closing",
        intersection_id="demo_2",
        start_seconds=60,
        end_seconds=300,
        vehicle_count=12,
        destination_lane_ids=["-3000_0"],
    )
    assert opening.intersection_id == "demo_2"
    assert closing.destination_lane_ids == ["-3000_0"]
