"""请求Schema校验测试"""

import pytest
from pydantic import ValidationError

from backend.app.schemas.events import AccidentRequest, LaneClosureRequest, SpeedLimitRequest
from backend.app.schemas.simulations import StartSimulationRequest


def test_start_simulation_request_valid() -> None:
    request = StartSimulationRequest(
        intersection_ids=["demo_2"],
        period="morning_peak",
        duration_seconds=600,
        flow_multiplier=1.2,
        control_mode="fixed",
    )
    assert request.realtime is True
    assert request.gui is False


def test_start_simulation_accepts_max_pressure() -> None:
    request = StartSimulationRequest(
        intersection_ids=["demo_2"],
        period="morning_peak",
        duration_seconds=600,
        control_mode="max_pressure",
    )
    assert request.control_mode == "max_pressure"


def test_flow_multiplier_out_of_range() -> None:
    with pytest.raises(ValidationError):
        StartSimulationRequest(
            intersection_ids=["demo_2"],
            period="morning_peak",
            duration_seconds=600,
            flow_multiplier=6.0,
            control_mode="fixed",
        )


def test_reject_non_demo_2_intersection() -> None:
    with pytest.raises(ValidationError):
        StartSimulationRequest(
            intersection_ids=["demo_1"],
            period="morning_peak",
            duration_seconds=600,
            control_mode="fixed",
        )


@pytest.mark.parametrize("control_mode", ["algorithm", "ippo", "unknown"])
def test_reject_unsupported_control_mode(control_mode: str) -> None:
    with pytest.raises(ValidationError):
        StartSimulationRequest(
            intersection_ids=["demo_2"],
            period="morning_peak",
            duration_seconds=600,
            control_mode=control_mode,
        )


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
    assert lane_closure.event_type == "lane_closure"
    assert speed_limit.max_speed == 5.0
    assert accident.position_ratio == 0.6
