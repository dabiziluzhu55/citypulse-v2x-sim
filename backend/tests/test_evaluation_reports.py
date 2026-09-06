"""管控评估 PDF 导出：只读取已有终态结果，不重算指标。"""

from __future__ import annotations

from typing import Any
from urllib.parse import unquote

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from backend.app.api.deps import get_simulation_service
from backend.app.api.v1.evaluation_reports import router
from backend.app.core.exceptions import AppError, register_exception_handlers
from backend.app.schemas.evaluation_reports import (
    EvaluationReportRequest,
    EvaluationReportRun,
    EvaluationReportScenario,
)
from backend.app.services.evaluation_report_service import (
    MISSING_CELL,
    REPORT_ALGORITHM_LABELS,
    EvaluationReportService,
    build_content_disposition,
    build_report_rows,
    build_report_title,
    format_metric_value,
    format_time_window,
    generate_evaluation_report_pdf,
)

PDF_URL = "/api/v1/evaluation-reports/pdf"


def _finished_metrics(algorithm: str, **overrides: Any) -> dict[str, Any]:
    payload: dict[str, Any] = {
        "episode_id": f"{algorithm}-session",
        "algorithm": algorithm,
        "path_avg_speed_kmh": 35.21,
        "avg_stops_per_vehicle": 1.32,
        "regional_max_queue_length_m": 80.5,
        "avg_travel_time": 120.4,
        "avg_waiting_time": 18.0,
        "throughput": 900.1,
        "travel_time_index": 1.234,
        "delay_time_proportion": 0.321,
        "traffic_performance_index": 4.5,
        "spillback_rate": 0.0,
        "hard_braking_rate": 1.2,
        "fuel_intensity_L_per_100km": 8.5,
        "fuel_consumption": 8.5,
        "avg_decision_latency_ms": 3.21,
        "finished": True,
    }
    payload.update(overrides)
    return payload


def _request(
    *,
    sessions: dict[str, str | None] | None = None,
    scenario: EvaluationReportScenario | None = EvaluationReportScenario(
        scenario_preset_id="xiongan_20",
        period="morning_peak",
        window_start_seconds=0,
        duration_seconds=900,
    ),
) -> EvaluationReportRequest:
    mapped = {
        "fixed": None,
        "max_pressure": None,
        "sotl": None,
        "ippo": None,
        "mappo": None,
        "cov2x": None,
    }
    if sessions:
        mapped.update(sessions)
    return EvaluationReportRequest(
        scenario=scenario,
        runs=[
            EvaluationReportRun(algorithm=algorithm, session_id=session_id)
            for algorithm, session_id in mapped.items()
        ],
    )


def _payload(sessions: dict[str, str | None] | None = None) -> dict[str, Any]:
    return _request(sessions=sessions).model_dump()


class _FakeSimulationService:
    def __init__(self, metrics: dict[str, dict[str, Any] | Exception]) -> None:
        self._metrics = metrics

    def get_metrics(self, session_id: str) -> dict[str, Any]:
        result = self._metrics[session_id]
        if isinstance(result, Exception):
            raise result
        return result


def _report_client(metrics: dict[str, dict[str, Any] | Exception]) -> TestClient:
    app = FastAPI()
    register_exception_handlers(app)
    app.include_router(router, prefix="/api/v1")
    fake = _FakeSimulationService(metrics)
    app.dependency_overrides[get_simulation_service] = lambda: fake
    return TestClient(app)


def test_format_metric_value_keeps_zero_and_hides_none() -> None:
    assert format_metric_value(None, 2) == MISSING_CELL
    assert format_metric_value(0, 2) == "0.00"
    assert format_metric_value(0.0, 2) == "0.00"
    assert format_metric_value(1.234, 3) == "1.234"
    assert format_metric_value(float("nan"), 2) == MISSING_CELL


def test_titles_and_windows_follow_scenario_registry() -> None:
    morning = EvaluationReportScenario(
        scenario_preset_id="xiongan_20",
        period="morning_peak",
        window_start_seconds=0,
        duration_seconds=900,
    )
    evening = EvaluationReportScenario(
        scenario_preset_id="east_dense",
        period="evening_peak",
        window_start_seconds=300,
        duration_seconds=600,
    )
    assert format_time_window("morning_peak", 0, 900) == "07:00-07:15"
    assert format_time_window("off_peak", 0, 900) == "14:30-14:45"
    assert format_time_window("evening_peak", 300, 600) == "17:35-17:45"
    assert (
        build_report_title(1, morning, "管控算法通行效率对比")
        == "表 1 雄安20路口路网早高峰 07:00-07:15 管控算法通行效率对比"
    )
    assert (
        build_report_title(2, evening, "管控算法其他指标对比")
        == "表 2 校园周边场景晚高峰 17:35-17:45 管控算法其他指标对比"
    )
    assert build_report_title(1, None, "管控算法通行效率对比") == "表 1 管控算法通行效率对比"


def test_only_fixed_finished_fills_fixed_and_dashes_others() -> None:
    rows = build_report_rows(
        _request(sessions={"fixed": "fixed-1"}),
        {"fixed": _finished_metrics("fixed")},
    )
    by_algorithm = {row.algorithm: row for row in rows}
    assert [row.algorithm for row in rows] == list(REPORT_ALGORITHM_LABELS)
    assert by_algorithm["fixed"].completed is True
    assert by_algorithm["fixed"].values["path_avg_speed_kmh"] == "35.21"
    assert by_algorithm["fixed"].values["spillback_rate"] == "0.00"
    for algorithm in ("max_pressure", "sotl", "ippo", "mappo", "cov2x"):
        assert by_algorithm[algorithm].completed is False
        assert set(by_algorithm[algorithm].values.values()) == {MISSING_CELL}


def test_running_cov2x_does_not_use_provisional_metrics() -> None:
    rows = build_report_rows(
        _request(sessions={"fixed": "fixed-1", "cov2x": "cov2x-1"}),
        {
            "fixed": _finished_metrics("fixed"),
            "cov2x": _finished_metrics(
                "cov2x",
                finished=False,
                path_avg_speed_kmh=99.99,
                spillback_rate=12.34,
            ),
        },
    )
    by_algorithm = {row.algorithm: row for row in rows}
    assert by_algorithm["fixed"].values["path_avg_speed_kmh"] == "35.21"
    assert by_algorithm["cov2x"].completed is False
    assert "99.99" not in by_algorithm["cov2x"].values.values()
    assert set(by_algorithm["cov2x"].values.values()) == {MISSING_CELL}


def test_finished_fixed_and_cov2x_both_fill_values() -> None:
    rows = build_report_rows(
        _request(sessions={"fixed": "fixed-1", "cov2x": "cov2x-1"}),
        {
            "fixed": _finished_metrics("fixed"),
            "cov2x": _finished_metrics("cov2x", path_avg_speed_kmh=40.0),
        },
    )
    by_algorithm = {row.algorithm: row for row in rows}
    assert by_algorithm["fixed"].completed is True
    assert by_algorithm["cov2x"].values["path_avg_speed_kmh"] == "40.00"
    assert by_algorithm["sotl"].completed is False


def test_no_finished_algorithm_raises_conflict() -> None:
    with pytest.raises(AppError) as error:
        build_report_rows(
            _request(sessions={"fixed": "fixed-1"}),
            {"fixed": _finished_metrics("fixed", finished=False)},
        )
    assert error.value.code == "NO_FINAL_EVALUATION_AVAILABLE"
    assert error.value.status_code == 409


def test_none_metric_renders_dash_but_zero_does_not() -> None:
    rows = build_report_rows(
        _request(sessions={"fixed": "fixed-1"}),
        {
            "fixed": _finished_metrics(
                "fixed",
                avg_decision_latency_ms=None,
                spillback_rate=0.0,
            )
        },
    )
    values = {row.algorithm: row.values for row in rows}["fixed"]
    assert values["avg_decision_latency_ms"] == MISSING_CELL
    assert values["spillback_rate"] == "0.00"


def test_algorithm_session_mismatch_is_rejected() -> None:
    with pytest.raises(AppError) as error:
        build_report_rows(
            _request(sessions={"cov2x": "fixed-session"}),
            {"cov2x": _finished_metrics("fixed")},
        )
    assert error.value.code == "ALGORITHM_SESSION_MISMATCH"
    assert error.value.status_code == 400


def test_unreadable_session_is_skipped_when_another_final_result_exists() -> None:
    service = EvaluationReportService(
        _FakeSimulationService(
            {
                "fixed-1": _finished_metrics("fixed"),
                "cov2x-1": RuntimeError("session gone"),
            }
        )
    )
    rows = service.build_report_rows(
        _request(sessions={"fixed": "fixed-1", "cov2x": "cov2x-1"})
    )
    by_algorithm = {row.algorithm: row for row in rows}
    assert by_algorithm["fixed"].completed is True
    assert by_algorithm["cov2x"].completed is False


def test_generated_pdf_is_valid_and_keeps_six_algorithms() -> None:
    rows = build_report_rows(
        _request(sessions={"fixed": "fixed-1"}),
        {"fixed": _finished_metrics("fixed", avg_decision_latency_ms=None)},
    )
    pdf_bytes = generate_evaluation_report_pdf(
        "表 1 雄安20路口路网早高峰 07:00-07:15 管控算法通行效率对比",
        "表 2 雄安20路口路网早高峰 07:00-07:15 管控算法其他指标对比",
        rows,
    )
    assert pdf_bytes.startswith(b"%PDF")
    assert b"%%EOF" in pdf_bytes[-1024:]
    assert [row.label for row in rows] == [
        "固定配时",
        "Max Pressure",
        "SOTL",
        "IPPO",
        "MAPPO",
        "CoV2X",
    ]
    assert len(pdf_bytes) > 1000


def test_export_pdf_http_protocol() -> None:
    client = _report_client({"fixed-1": _finished_metrics("fixed")})
    response = client.post(PDF_URL, json=_payload({"fixed": "fixed-1"}))
    assert response.status_code == 200
    assert response.headers["content-type"].startswith("application/pdf")
    assert response.content.startswith(b"%PDF")
    disposition = response.headers["content-disposition"]
    assert "attachment" in disposition
    assert 'filename="control-evaluation.pdf"' in disposition
    assert "filename*=UTF-8''" in disposition
    decoded = unquote(disposition.split("filename*=UTF-8''", 1)[1])
    assert decoded == "雄安20路口路网_早高峰_07-00-07-15_管控评估结果.pdf"


def test_export_pdf_http_only_fixed_and_running_cov2x() -> None:
    client = _report_client({
        "fixed-1": _finished_metrics("fixed"),
        "cov2x-1": _finished_metrics("cov2x", finished=False, path_avg_speed_kmh=88.8),
    })
    response = client.post(
        PDF_URL,
        json=_payload({"fixed": "fixed-1", "cov2x": "cov2x-1"}),
    )
    assert response.status_code == 200
    assert response.content.startswith(b"%PDF")
    assert b"88.8" not in response.content


def test_export_pdf_http_conflict_when_nothing_finished() -> None:
    client = _report_client({"fixed-1": _finished_metrics("fixed", finished=False)})
    response = client.post(PDF_URL, json=_payload({"fixed": "fixed-1"}))
    assert response.status_code == 409
    assert response.json()["detail"]["code"] == "NO_FINAL_EVALUATION_AVAILABLE"
    assert response.content[:4] != b"%PDF"


def test_export_pdf_http_mismatch() -> None:
    client = _report_client({"fixed-session": _finished_metrics("fixed")})
    response = client.post(PDF_URL, json=_payload({"cov2x": "fixed-session"}))
    assert response.status_code == 400
    assert response.json()["detail"]["code"] == "ALGORITHM_SESSION_MISMATCH"
    assert response.content[:4] != b"%PDF"


def test_content_disposition_has_ascii_and_utf8() -> None:
    header = build_content_disposition("校园周边场景_平峰_14-30-14-45_管控评估结果.pdf")
    assert header.startswith('attachment; filename="control-evaluation.pdf"; ')
    assert "filename*=UTF-8''" in header
