"""Tests for the frozen unified per-run schema (Task A)."""

from algorithms.evaluation.unified_schema import (
    ALL_FIELDS,
    DEFAULT_PROVENANCE,
    is_valid,
    normalize,
    schema_keys,
)

ALL_METHODS = (
    "fixed",
    "senior",
    "ippo_v8_ep160",
    "mappo_cooperative_ep160",
    "vrc_full",
    "vrc_nocollab",
)


def _full_row(**overrides):
    row = {
        "method": "vrc_full",
        "seed": 66501,
        "all_waiting_total_s": 1000.0,
        "unfinished_waiting_total_s": 100.0,
        "departed_count": 500,
        "trip_records": 500,
        "end_waiting_total_s": 1000.0,
        "end_queue_veh": 1.2,
        "avg_travel_time_s": 300.0,
        "avg_waiting_time_s": 2.0,
        "avg_queue_length_veh": 3.0,
        "fuel_intensity_l_per_100km": 8.5,
        "arrived_count": 50,
        "simulation_duration_s": 300.0,
        "emergency_braking_event_count": 3,
        "passage_count": 4000,
        "ai_frame_interval_seconds": 1.0,
    }
    row.update(overrides)
    return row


def test_normalize_full_row_is_valid_and_preserves_fields():
    row = _full_row()
    out = normalize(row)
    assert out["valid"] is True
    assert out["invalid_reasons"] == []
    assert schema_keys() <= set(out)
    for field in ALL_FIELDS:
        assert out[field] == row[field]


def test_missing_end_queue_veh_invalidates():
    row = _full_row()
    del row["end_queue_veh"]
    out = normalize(row)
    assert out["valid"] is False
    assert "missing field: end_queue_veh" in out["invalid_reasons"]


def test_none_end_queue_veh_invalidates():
    out = normalize(_full_row(end_queue_veh=None))
    assert out["valid"] is False
    assert "field is None: end_queue_veh" in out["invalid_reasons"]


def test_departed_count_mismatch_invalidates():
    out = normalize(_full_row(departed_count=499, trip_records=500))
    assert out["valid"] is False
    assert any(
        "departed_count=499 != trip_records=500" in reason
        for reason in out["invalid_reasons"]
    )


def test_end_waiting_provenance_is_frozen_redundancy_text():
    out = normalize(_full_row())
    assert out["provenance"]["end_waiting_total_s"] == (
        "tripinfo_all_departed (== all_waiting_total_s, frozen redundancy)"
    )


def test_avg_travel_waiting_provenance_all_departed():
    out = normalize(_full_row())
    assert out["provenance"]["avg_travel_time_s"] == "tripinfo_all_departed"
    assert out["provenance"]["avg_waiting_time_s"] == "tripinfo_all_departed"


def test_fuel_missing_does_not_invalidate():
    out = normalize(_full_row(fuel_intensity_l_per_100km=None))
    assert out["valid"] is True
    assert out["availability"]["fuel_intensity_l_per_100km"] == "missing"


def test_ai_frame_interval_must_equal_1s():
    out = normalize(_full_row(ai_frame_interval_seconds=0.5))
    assert out["valid"] is False
    assert any(
        "ai_frame_interval_seconds=0.5" in reason
        for reason in out["invalid_reasons"]
    )


def test_cross_method_schema_keys_identical():
    normalized = [normalize(_full_row(method=method)) for method in ALL_METHODS]
    assert all(is_valid(run) for run in normalized)
    key_sets = [set(run) for run in normalized]
    assert all(keys == key_sets[0] for keys in key_sets)


def test_default_provenance_covers_every_schema_field():
    for field in ALL_FIELDS:
        assert field in DEFAULT_PROVENANCE
