# algorithms/v2x/tests/test_entities.py
import pytest
from algorithms.v2x.config import V2XConfig
from algorithms.v2x.entities import (
    VehicleCapability, Vehicle, RSU, resolve_v2x_enabled,
    DEFAULT_V2X_CAPABILITY, build_rsu_covered_lanes,
)


def test_explicit_overrides_everything():
    cfg = V2XConfig(penetration_rate=0.0)
    assert resolve_v2x_enabled(
        vehicle_id="v1", vehicle_class="passenger",
        explicit=True, type_v2x=False, config=cfg) is True
    assert resolve_v2x_enabled(
        vehicle_id="v1", vehicle_class="passenger",
        explicit=False, type_v2x=True, config=cfg) is False


def test_type_field_forward_compat():
    cfg = V2XConfig(penetration_rate=0.0)
    assert resolve_v2x_enabled(
        vehicle_id="v1", vehicle_class="passenger",
        explicit=None, type_v2x=True, config=cfg) is True


def test_penetration_stable_and_reproducible():
    cfg = V2XConfig(penetration_rate=0.5, capability_seed=7)
    ids = [f"veh_{i}" for i in range(50)]
    r1 = [resolve_v2x_enabled(vehicle_id=v, vehicle_class="passenger",
                              explicit=None, type_v2x=None, config=cfg) for v in ids]
    r2 = [resolve_v2x_enabled(vehicle_id=v, vehicle_class="passenger",
                              explicit=None, type_v2x=None, config=cfg) for v in ids]
    assert r1 == r2
    assert 0 < sum(r1) < len(ids)  # 50 辆车 0.5 渗透率不是全 0/全 1


def test_bicycle_never_connected():
    cfg = V2XConfig(penetration_rate=1.0)
    assert resolve_v2x_enabled(
        vehicle_id="b1", vehicle_class="bicycle",
        explicit=None, type_v2x=None, config=cfg) is False


def test_defaults_table():
    assert DEFAULT_V2X_CAPABILITY["passenger"] is True
    assert DEFAULT_V2X_CAPABILITY["bicycle"] is False


def test_rsu_covered_lanes_merges_protocol_and_extra():
    protocol_lanes = {"r1": frozenset({"A_0", ":r1_0"})}
    extra = {"r1": frozenset({"B_0"})}
    result = build_rsu_covered_lanes(protocol_lanes, extra)
    assert result["r1"] == frozenset({"A_0", ":r1_0", "B_0"})


def test_capability_hash_namespace_matches_preregistration():
    # 预注册 1.1：v2x_enabled = stable_hash01(f"{capability_seed}|capability|{vehicle_id}") < penetration_rate
    from algorithms.v2x.messages import stable_hash01
    cfg = V2XConfig(penetration_rate=0.5, capability_seed=7)
    assert resolve_v2x_enabled(
        vehicle_id="veh_1", vehicle_class="passenger",
        explicit=None, type_v2x=None, config=cfg) == (
        stable_hash01("7|capability|veh_1") < 0.5)
    # 旧命名空间必须不再影响结果（域分离）
    assert stable_hash01("7|veh_1") != stable_hash01("7|capability|veh_1")
