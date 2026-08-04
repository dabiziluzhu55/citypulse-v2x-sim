import math
import pytest

from algorithms.v2x.collab.proposals import (
    CollabConfig,
    DecisionMode,
    FreshnessConfig,
    GuidanceDecisionStatus,
    GuidanceEmissionMode,
    GuidancePolicyConfig,
    SignalDecisionStatus,
    SignalPolicyConfig,
)


def test_enum_values():
    assert DecisionMode.OFF.value == "off"
    assert DecisionMode.SHADOW.value == "shadow"
    assert DecisionMode.ACTIVE.value == "active"
    assert GuidanceEmissionMode.THRESHOLD.value == "threshold"
    assert GuidanceEmissionMode.FULL.value == "full"
    assert GuidanceEmissionMode.DISABLED.value == "disabled"
    assert SignalDecisionStatus.PROPOSED.value == "proposed"
    assert GuidanceDecisionStatus.SUPPRESSED_THRESHOLD.value == "suppressed_threshold"


def test_freshness_defaults():
    cfg = FreshnessConfig()
    assert cfg.bsm_s == 10.0
    assert cfg.intent_s == 10.0
    assert cfg.spat_s == 10.0
    assert cfg.rsm_s == 10.0


def test_signal_policy_defaults_and_validation():
    cfg = SignalPolicyConfig()
    assert cfg.queue_weight == 1.0
    assert cfg.forward_horizon_s == 30.0
    with pytest.raises(ValueError):
        SignalPolicyConfig(queue_weight=-1.0)
    with pytest.raises(ValueError):
        SignalPolicyConfig(forward_horizon_s=0.0)


def test_guidance_policy_defaults_and_validation():
    cfg = GuidancePolicyConfig()
    assert cfg.guidance_horizon_m == 300.0
    assert cfg.speed_trigger_delta_mps == 2.0
    with pytest.raises(ValueError):
        GuidancePolicyConfig(guidance_horizon_m=-1.0)
    with pytest.raises(ValueError):
        GuidancePolicyConfig(v_min_mps=10.0, v_max_mps=5.0)
    with pytest.raises(ValueError):
        GuidancePolicyConfig(speed_scale_low=1.5, speed_scale_high=1.0)


def test_collab_config_defaults_are_immutable_and_fresh():
    cfg = CollabConfig()
    assert cfg.decision_mode is DecisionMode.SHADOW
    assert cfg.guidance_mode is GuidanceEmissionMode.THRESHOLD
    assert cfg.freshness.bsm_s == 10.0
    assert cfg.signal_policy.queue_weight == 1.0
    # frozen dataclass 不可变
    with pytest.raises(Exception):
        cfg.freshness.bsm_s = 99.0  # type: ignore[misc]
