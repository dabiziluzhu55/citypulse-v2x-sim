from algorithms.ippo import evaluate_paired as shared
from algorithms.maxpressure_benchmark.evaluate import _missing_official_metrics


def test_missing_safety_metrics_remain_na_without_invalidating_run() -> None:
    official_metrics = {name: 1.0 for name in shared.OFFICIAL_METRIC_NAMES}
    official_metrics["emergency_braking_exposure_per_1000"] = None
    official_metrics["severe_conflict_exposure_per_10000"] = None

    required_missing, optional_missing = _missing_official_metrics(
        "ours", official_metrics
    )

    assert required_missing == []
    assert optional_missing == [
        "emergency_braking_exposure_per_1000",
        "severe_conflict_exposure_per_10000",
    ]


def test_missing_core_metric_still_invalidates_run() -> None:
    official_metrics = {name: 1.0 for name in shared.OFFICIAL_METRIC_NAMES}
    official_metrics["avg_queue_length_veh"] = None

    required_missing, optional_missing = _missing_official_metrics(
        "senior", official_metrics
    )

    assert required_missing == ["avg_queue_length_veh"]
    assert optional_missing == []
