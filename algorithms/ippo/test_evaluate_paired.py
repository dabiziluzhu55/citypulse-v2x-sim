from algorithms.ippo.evaluate_paired import (
    OFFICIAL_METRIC_NAMES,
    _missing_official_metrics,
)


def test_missing_safety_metrics_remain_na_without_invalidating_run() -> None:
    official_metrics = {name: 1.0 for name in OFFICIAL_METRIC_NAMES}
    official_metrics["emergency_braking_exposure_per_1000"] = None

    required_missing, optional_missing = _missing_official_metrics(
        "model", official_metrics
    )

    assert required_missing == []
    assert optional_missing == ["emergency_braking_exposure_per_1000"]


def test_missing_core_metric_still_invalidates_run() -> None:
    official_metrics = {name: 1.0 for name in OFFICIAL_METRIC_NAMES}
    official_metrics["throughput_veh_per_h"] = None

    required_missing, optional_missing = _missing_official_metrics(
        "model", official_metrics
    )

    assert required_missing == ["throughput_veh_per_h"]
    assert optional_missing == []
