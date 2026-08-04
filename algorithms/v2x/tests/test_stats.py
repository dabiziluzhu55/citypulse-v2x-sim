# algorithms/v2x/tests/test_stats.py
from algorithms.v2x.stats import (
    delivery_rate, latency_stats, rsm_coverage_stats, rsi_funnel,
)

def test_delivery_rate_null_when_no_sent():
    assert delivery_rate(sent=0, delivered=0) is None
    assert delivery_rate(sent=10, delivered=10) == 1.0
    assert delivery_rate(sent=10, delivered=3) == 0.3

def test_latency_stats_null_when_empty():
    assert latency_stats([]) == {"mean": None, "p50": None, "p95": None, "max": None}
    stats = latency_stats([20.0, 20.0, 40.0])
    assert stats["mean"] == 20.0 + 20.0 / 3.0
    assert stats["max"] == 40.0
    assert stats["p50"] == 20.0

def test_rsm_coverage_structured_zero_denominator():
    s = rsm_coverage_stats(observed=0, eligible=0)
    assert s == {"observed_unique_objects": 0, "eligible_unique_objects": 0,
                 "rate": None, "defined": False}
    s2 = rsm_coverage_stats(observed=3, eligible=4)
    assert s2["rate"] == 0.75 and s2["defined"] is True

def test_rsi_funnel():
    f = rsi_funnel(requested=10, existing=9, enabled=5, sent=5, delivered=5,
                   reasons={"vehicle_not_found": 1, "not_v2x_enabled": 4})
    assert f["requested"] == 10
    assert f["delivered"] == 5
    assert f["filter_reasons"]["vehicle_not_found"] == 1
