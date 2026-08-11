"""Tests for the fuel telemetry M0 four-check audit (Task C)."""

import textwrap

import pytest

from algorithms.evaluation.fuel_audit import (
    run_four_checks_protocol,
    run_four_checks_xml,
)

VEHICLE_TYPES = {
    "passenger": {
        "powertrain": "gasoline",
        "fuel_density_mg_per_ml": 745.0,
    },
    "official_electric_bicycle": {
        "powertrain": "electric",
        "fuel_density_mg_per_ml": 1.0,
    },
}


def _fuel_row(
    vehicle_id, *, fuel_ml, distance_m, active=False, powertrain="gasoline"
):
    return {
        "vehicle_id": vehicle_id,
        "type_id": (
            "passenger"
            if powertrain == "gasoline"
            else "official_electric_bicycle"
        ),
        "powertrain": powertrain,
        "fuel_ml": fuel_ml,
        "distance_m": distance_m,
        "active": active,
    }


def test_protocol_four_checks_pass():
    rows = [
        _fuel_row("v1", fuel_ml=100.0, distance_m=1000.0, active=False),
        _fuel_row("v2", fuel_ml=50.0, distance_m=500.0, active=True),
    ]
    result = run_four_checks_protocol(
        rows,
        finish_fuel_ml=150.0,
        fuel_telemetry_unit="protocol_ml",
        departed_fuel_vehicles=2,
        active_records=1,
    )
    assert result["status"] == "pass"
    assert all(result["checks"].values())
    # 150 mL over 1500 m = 10 L/100km.
    assert result["intensity_per_vehicle_l_per_100km"] == pytest.approx(10.0)
    assert result["intensity_totals_l_per_100km"] == pytest.approx(10.0)


def test_protocol_vehicle_set_mismatch_fails():
    rows = [_fuel_row("v1", fuel_ml=100.0, distance_m=1000.0)]
    result = run_four_checks_protocol(
        rows,
        finish_fuel_ml=100.0,
        fuel_telemetry_unit="protocol_ml",
        departed_fuel_vehicles=2,
        active_records=0,
    )
    assert result["status"] == "fail"
    assert result["checks"]["vehicle_set_equal"] is False


def test_protocol_unfinished_missing_fails():
    rows = [_fuel_row("v1", fuel_ml=100.0, distance_m=1000.0)]
    result = run_four_checks_protocol(
        rows,
        finish_fuel_ml=100.0,
        fuel_telemetry_unit="protocol_ml",
        departed_fuel_vehicles=1,
        active_records=1,
    )
    assert result["status"] == "fail"
    assert result["checks"]["unfinished_included"] is False


def test_protocol_zero_distance_fails():
    rows = [
        _fuel_row("v1", fuel_ml=100.0, distance_m=0.0),
    ]
    result = run_four_checks_protocol(
        rows,
        finish_fuel_ml=100.0,
        fuel_telemetry_unit="protocol_ml",
        departed_fuel_vehicles=1,
        active_records=0,
    )
    assert result["status"] == "fail"
    assert result["checks"]["distance_denominator_equal"] is False


def test_protocol_finish_total_mismatch_records_but_passes():
    # D-2026-08-07-01: the official fuel metric uses the event-level finish
    # totals.  A residual between the per-vehicle sampled path and the finish
    # totals is recorded as evidence but no longer blocks the audit.
    rows = [_fuel_row("v1", fuel_ml=100.0, distance_m=1000.0)]
    result = run_four_checks_protocol(
        rows,
        finish_fuel_ml=101.0,
        fuel_telemetry_unit="protocol_ml",
        departed_fuel_vehicles=1,
        active_records=0,
    )
    assert result["status"] == "pass"
    assert result["decision_required"] is False
    assert result["checks"]["unit_conversion_only"] is False
    assert result["residual_relative_difference"] > 0.0
    assert all(
        result["checks"][name]
        for name in (
            "vehicle_set_equal",
            "unfinished_included",
            "distance_denominator_equal",
        )
    )


def test_protocol_finish_totals_missing_blocked():
    # Definitional checks pass but the finish totals are absent, so the
    # official metric cannot be computed; requires a fix/decision.
    rows = [_fuel_row("v1", fuel_ml=100.0, distance_m=1000.0)]
    result = run_four_checks_protocol(
        rows,
        finish_fuel_ml=0.0,
        fuel_telemetry_unit="protocol_ml",
        departed_fuel_vehicles=1,
        active_records=0,
    )
    assert result["status"] == "blocked"
    assert result["decision_required"] is True
    assert "finish totals" in result["block_reason"]
    assert all(
        result["checks"][name]
        for name in (
            "vehicle_set_equal",
            "unfinished_included",
            "distance_denominator_equal",
        )
    )


def test_xml_dual_interpretation_pass(tmp_path):
    emissions = tmp_path / "emissions.xml"
    emissions.write_text(
        textwrap.dedent(
            """\
            <emission-export>
              <timestep time="1">
                <vehicle id="car" type="passenger" fuel="745" speed="10"/>
              </timestep>
              <timestep time="2">
                <vehicle id="car" type="passenger" fuel="745" speed="10"/>
                <vehicle id="truck" type="passenger" fuel="1490" speed="20"/>
              </timestep>
            </emission-export>
            """
        ),
        encoding="utf-8",
    )
    result = run_four_checks_xml(str(emissions), VEHICLE_TYPES)
    assert result["status"] == "pass"
    assert all(result["checks"].values())
    # mL/s interpretation: (745 + 745 + 1490) mL = 2980 mL.
    assert result["fuel_ml_ml_per_s_interpretation"] == pytest.approx(2980.0)
    # mg/s interpretation: same mass divided by 745 mg/mL = 4 mL.
    assert result["fuel_ml_mg_per_s_interpretation"] == pytest.approx(4.0)
    assert result["effective_density_mg_per_ml"] == pytest.approx(745.0)
    assert result["distance_m"] == pytest.approx(40.0)


def test_protocol_legacy_pairing_uses_finish_fuel_mg():
    rows = [_fuel_row("v1", fuel_ml=100.0, distance_m=1000.0)]
    result = run_four_checks_protocol(
        rows,
        finish_fuel_ml=0.134,  # backend legacy value = fuel_abs/density (not like-for-like)
        finish_fuel_mg=100.0,  # actual mL under legacy field naming
        fuel_telemetry_unit="legacy_ml_as_mg",
        departed_fuel_vehicles=1,
        active_records=0,
    )
    assert result["status"] == "pass"
    assert all(result["checks"].values())
    assert result["finish_total_used_ml"] == pytest.approx(100.0)
    assert result["finish_total_source"] == "finish_fuel_consumed_mg"
    assert any("NOT like-for-like" in note for note in result["notes"])
