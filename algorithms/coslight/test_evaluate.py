import pytest

from algorithms.coslight import evaluate


def test_tripinfo_metrics_separate_completed_and_unfinished_trips(tmp_path):
    tripinfo = tmp_path / "tripinfo.xml"
    tripinfo.write_text(
        """<?xml version="1.0"?>
<tripinfos>
  <tripinfo id="done-1" arrival="20" duration="10" waitingTime="2" timeLoss="3"/>
  <tripinfo id="done-2" arrival="30" duration="20" waitingTime="4" timeLoss="7"/>
  <tripinfo id="open" arrival="-1" duration="8" waitingTime="6" timeLoss="5"/>
</tripinfos>
""",
        encoding="utf-8",
    )

    metrics = evaluate._parse_tripinfo(tripinfo)

    assert metrics["trip_records"] == 3
    assert metrics["completed_trips"] == 2
    assert metrics["unfinished_trips"] == 1
    assert metrics["completion_rate"] == pytest.approx(2 / 3)
    assert metrics["completed_duration_mean_s"] == pytest.approx(15.0)
    assert metrics["completed_waiting_mean_s"] == pytest.approx(3.0)
    assert metrics["completed_time_loss_mean_s"] == pytest.approx(5.0)
    assert metrics["unfinished_waiting_total_s"] == pytest.approx(6.0)
    assert metrics["all_waiting_total_s"] == pytest.approx(12.0)
    assert metrics["end_waiting_total_s"] == pytest.approx(12.0)
    assert metrics["end_waiting_mean_s"] == pytest.approx(4.0)
    assert metrics["all_time_loss_total_s"] == pytest.approx(15.0)


def test_tripinfo_vaporized_end_counts_as_unfinished(tmp_path):
    """截断时 vaporized=end 的车辆（arrival=-1）计入 TripInfo 残余车辆。"""
    tripinfo = tmp_path / "tripinfo.xml"
    tripinfo.write_text(
        """<?xml version="1.0"?>
<tripinfos>
  <tripinfo id="done" arrival="30" duration="20" waitingTime="4" timeLoss="7" vaporized=""/>
  <tripinfo id="open" arrival="-1" duration="300" waitingTime="6" timeLoss="5" vaporized="end"/>
</tripinfos>
""",
        encoding="utf-8",
    )

    metrics = evaluate._parse_tripinfo(tripinfo)

    assert metrics["completed_trips"] == 1
    assert metrics["unfinished_trips"] == 1
    assert metrics["end_waiting_total_s"] == pytest.approx(10.0)
    assert metrics["end_waiting_mean_s"] == pytest.approx(5.0)


def test_official_fixed_uses_sumo_fixed_control():
    fixed = evaluate._simulation_config(
        method="fixed",
        intersections=("demo_1",),
        period="off_peak",
        duration=60,
        seed=101,
    )
    random = evaluate._simulation_config(
        method="random",
        intersections=("demo_1",),
        period="off_peak",
        duration=60,
        seed=101,
    )

    assert fixed.control_mode == "fixed"
    assert fixed.algorithm_module == ""
    assert random.control_mode == "algorithm"
    assert random.algorithm_module == "algorithms.coslight"


def test_summary_reports_paired_delta_against_fixed():
    rows = [
        {
            "method": "fixed",
            "seed": 1,
            "waiting": 100.0,
            "arrived": 10,
            "end_waiting_total_s": 500.0,
            "unfinished_waiting_total_s": 400.0,
        },
        {
            "method": "fixed",
            "seed": 2,
            "waiting": 120.0,
            "arrived": 11,
            "end_waiting_total_s": 600.0,
            "unfinished_waiting_total_s": 480.0,
        },
        {
            "method": "model",
            "seed": 1,
            "waiting": 80.0,
            "arrived": 12,
            "end_waiting_total_s": 700.0,
            "unfinished_waiting_total_s": 560.0,
        },
        {
            "method": "model",
            "seed": 2,
            "waiting": 90.0,
            "arrived": 13,
            "end_waiting_total_s": 900.0,
            "unfinished_waiting_total_s": 720.0,
        },
    ]

    summary = evaluate._summarize(rows)

    assert summary["model"]["waiting"]["mean"] == pytest.approx(85.0)
    assert summary["model"]["paired_vs_fixed"]["waiting_mean_delta"] == pytest.approx(
        -25.0
    )
    assert summary["model"]["paired_vs_fixed"]["arrived_mean_delta"] == pytest.approx(
        2.0
    )
    assert summary["model"]["paired_vs_fixed"][
        "end_waiting_total_mean_delta_s"
    ] == pytest.approx(250.0)
    assert summary["model"]["paired_vs_fixed"][
        "unfinished_waiting_total_mean_delta_s"
    ] == pytest.approx(200.0)


def test_evaluation_rejects_directory_output_before_starting_sumo(tmp_path):
    with pytest.raises(SystemExit, match="2"):
        evaluate.main(["--methods", "fixed", "--output", str(tmp_path)])


def test_cloud_evaluation_requires_model_and_topology(tmp_path):
    with pytest.raises(SystemExit, match="2"):
        evaluate.main(
            [
                "--methods",
                "fixed",
                "--cloud-mode",
                "regional_rule",
                "--output",
                str(tmp_path / "evaluation.json"),
            ]
        )


def test_evaluation_rejects_negative_pressure_shield_margin(tmp_path):
    with pytest.raises(SystemExit, match="2"):
        evaluate.main(
            [
                "--methods",
                "fixed",
                "--pressure-shield-margin",
                "-0.1",
                "--output",
                str(tmp_path / "evaluation.json"),
            ]
        )


def test_evaluation_rejects_nan_residual_pressure_floor(tmp_path):
    with pytest.raises(SystemExit, match="2"):
        evaluate.main(
            [
                "--methods",
                "fixed",
                "--residual-min-best-pressure",
                "nan",
                "--output",
                str(tmp_path / "evaluation.json"),
            ]
        )


@pytest.mark.parametrize("value", ["-0.1", "nan", "inf"])
def test_evaluation_rejects_invalid_switch_logit_margin(tmp_path, value):
    with pytest.raises(SystemExit, match="2"):
        evaluate.main(
            [
                "--methods",
                "fixed",
                "--switch-logit-margin",
                value,
                "--output",
                str(tmp_path / "evaluation.json"),
            ]
        )
