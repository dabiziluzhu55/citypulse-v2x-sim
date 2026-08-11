import pytest

from algorithms.ippo.evaluate_ckpt import (
    OFFICIAL_METRIC_NAMES,
    _missing_official_metrics,
    evaluate,
)


def test_missing_safety_metrics_remain_na_without_invalidating_run() -> None:
    official_metrics = {name: 1.0 for name in OFFICIAL_METRIC_NAMES}
    official_metrics["emergency_braking_exposure_per_1000"] = None

    required_missing, optional_missing = _missing_official_metrics(official_metrics)

    assert required_missing == []
    assert optional_missing == ["emergency_braking_exposure_per_1000"]


def test_missing_core_metric_still_invalidates_run() -> None:
    official_metrics = {name: 1.0 for name in OFFICIAL_METRIC_NAMES}
    official_metrics["fuel_intensity_L_per_100km"] = None

    required_missing, optional_missing = _missing_official_metrics(official_metrics)

    assert required_missing == ["fuel_intensity_L_per_100km"]
    assert optional_missing == []


def test_evaluation_rejects_unknown_period_before_loading_checkpoint() -> None:
    with pytest.raises(ValueError, match="unsupported evaluation period"):
        evaluate("missing.pt", period="weekend")
