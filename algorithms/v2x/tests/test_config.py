# algorithms/v2x/tests/test_config.py
import math
import os

import pytest
from algorithms.v2x.config import (
    RSUCoverageConfig,
    V2XConfig,
    V2XConfigError,
    v2x_config_from_env,
)


def test_defaults():
    cfg = V2XConfig()
    assert cfg.schema_version == "1.0"
    assert cfg.bsm_interval_s == 5.0
    assert cfg.penetration_rate == 1.0
    assert cfg.drop_rate == 0.0
    assert cfg.default_latency_ms == 20.0
    assert cfg.network_seed == 0
    assert cfg.capability_seed == 0
    assert cfg.detection_radius_m is None


def test_interval_lookup():
    cfg = V2XConfig(bsm_interval_s=2.0, rsm_interval_s=0.0)
    assert cfg.interval_for("BSM") == 2.0
    assert cfg.interval_for("RSM") == 0.0
    assert cfg.interval_for("UNKNOWN") == 0.0


def test_latency_link_mapping():
    cfg = V2XConfig(default_latency_ms=20.0, uplink_latency_ms=10.0,
                    downlink_latency_ms=30.0)
    assert cfg.latency_ms_for("BSM") == 10.0
    assert cfg.latency_ms_for("MAP") == 10.0
    assert cfg.latency_ms_for("RSI") == 30.0
    assert cfg.latency_ms_for("SIGNAL_CONTROL") == 30.0


@pytest.mark.parametrize("kwargs", [
    {"penetration_rate": 1.5},
    {"penetration_rate": -0.1},
    {"drop_rate": 1.1},
    {"drop_rate": -0.01},
    {"default_latency_ms": -1.0},
    {"latency_jitter_ms": -1.0},
    {"uplink_latency_ms": -1.0},
    {"downlink_latency_ms": -1.0},
    {"bsm_interval_s": math.nan},
    {"network_seed": 1.5},   # int 字段校验
])
def test_invalid(kwargs):
    with pytest.raises(V2XConfigError):
        V2XConfig(**kwargs)


def test_coverage_config_defaults():
    cov = RSUCoverageConfig()
    assert cov.positions == {}
    assert cov.extra_covered_lane_ids == {}


def test_v2x_config_from_env_matches_preregistration_defaults():
    os.environ.update({
        "COSLIGHT_V2X_PENETRATION": "0.6",
        "COSLIGHT_V2X_CAPABILITY_SEED": "123",
        "COSLIGHT_V2X_NETWORK_SEED": "123",
        "COSLIGHT_V2X_DEFAULT_LATENCY_MS": "100",
        "COSLIGHT_V2X_LATENCY_JITTER_MS": "50",
        "COSLIGHT_V2X_DROP_RATE": "0.05",
        "COSLIGHT_V2X_SPAT_INTERVAL_S": "5",
    })
    cfg = v2x_config_from_env()
    assert cfg.penetration_rate == 0.6
    assert cfg.capability_seed == 123 and cfg.network_seed == 123
    assert cfg.default_latency_ms == 100.0
    assert cfg.latency_jitter_ms == 50.0
    assert cfg.drop_rate == 0.05
    assert cfg.spat_interval_s == 5.0
