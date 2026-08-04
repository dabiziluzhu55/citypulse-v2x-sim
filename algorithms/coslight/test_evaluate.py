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
    assert metrics["all_time_loss_total_s"] == pytest.approx(15.0)


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
        {"method": "fixed", "seed": 1, "waiting": 100.0, "arrived": 10},
        {"method": "fixed", "seed": 2, "waiting": 120.0, "arrived": 11},
        {"method": "model", "seed": 1, "waiting": 80.0, "arrived": 12},
        {"method": "model", "seed": 2, "waiting": 90.0, "arrived": 13},
    ]

    summary = evaluate._summarize(rows)

    assert summary["model"]["waiting"]["mean"] == pytest.approx(85.0)
    assert summary["model"]["paired_vs_fixed"]["waiting_mean_delta"] == pytest.approx(
        -25.0
    )
    assert summary["model"]["paired_vs_fixed"]["arrived_mean_delta"] == pytest.approx(
        2.0
    )


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



def test_scenario_preset_and_intersections_mutually_exclusive(tmp_path):
    with pytest.raises(SystemExit, match="2"):
        evaluate.main(
            [
                "--methods", "fixed",
                "--scenario-preset", "east_dense",
                "--intersections", "demo_1",
                "--output", str(tmp_path / "evaluation.json"),
            ]
        )


def test_invalid_intersections_rejected_at_startup(tmp_path):
    with pytest.raises(SystemExit, match="2"):
        evaluate.main(
            [
                "--methods", "fixed",
                "--intersections", "demo_1,foo",
                "--output", str(tmp_path / "evaluation.json"),
            ]
        )


def test_unknown_scenario_preset_rejected_by_choices(tmp_path):
    with pytest.raises(SystemExit, match="2"):
        evaluate.main(
            [
                "--methods", "fixed",
                "--scenario-preset", "nope",
                "--output", str(tmp_path / "evaluation.json"),
            ]
        )


def test_v2x_collab_active_rejected_at_startup(tmp_path):
    with pytest.raises(SystemExit, match="2"):
        evaluate.main(
            [
                "--methods", "fixed",
                "--v2x-collab",
                "--v2x-collab-mode", "active",
                "--output", str(tmp_path / "evaluation.json"),
            ]
        )
