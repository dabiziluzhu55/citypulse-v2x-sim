# algorithms/v2x/tests/test_coslight_learned.py
"""learned VRC 桥（Task 1.1）纯桥级测试：不依赖 SUMO。

覆盖冻结语义：
- candidate set = 已投递 ∧ age<TTL ∧ vrc_valid 且字段合法（j≠i）；
- nocollab（config=None）→ 全 False mask 与零张量、不启动 hub；
- penetration=0 → 无连接车辆 → derived features invalid → 移出候选集；
- 特征布局常量冻结（LOCAL=8、DERIVED=6、MSG=14）。
"""
from __future__ import annotations

import numpy as np
import pytest

from algorithms.v2x.adapters.coslight_learned import (
    LearnedV2XBridge,
    LOCAL_FEATURE_DIM,
    DERIVED_FEATURE_DIM,
    MSG_FEATURE_DIM,
)
from algorithms.v2x.config import V2XConfig
from algorithms.v2x.protocol import (
    VRC_LOCAL_FEATURE_DIM,
    VRC_DERIVED_FEATURE_DIM,
)

INIT = {
    "episode_id": "ep_vrc",
    "protocol_version": "2.0",
    "vehicle_types": {
        "passenger_car": {"type_id": "passenger_car",
                          "profile_id": "passenger", "vehicle_class": "passenger"},
    },
    "intersections": {
        "a": {"intersection_id": "a",
              "phases": {"1": {"green_seconds": 25.0, "yellow_seconds": 3.0,
                               "clearance_seconds": 2.0}},
              "lanes": {"A_0": {"lane_id": "A_0", "edge_id": "A",
                                "connection_signal_states": []}},
              "connections": [{"from_lane": "A_0", "to_lane": "B_0",
                               "movement": "through"}],
              "direct_neighbors": ["b"]},
        "b": {"intersection_id": "b", "phases": {},
              "lanes": {"B_0": {"lane_id": "B_0", "edge_id": "B",
                                "connection_signal_states": []}},
              "connections": [], "direct_neighbors": ["a"]},
    },
}


def _step(sim_time: float) -> dict:
    return {
        "episode_id": "ep_vrc",
        "step_id": int(sim_time),
        "simulation_time": sim_time,
        "intersections": {
            "a": {"current_phase": 1, "stage": "GREEN", "stage_elapsed": 1.0,
                  "lanes": {"A_0": {
                      "vehicle_count": 3.0, "halting_count": 1.0,
                      "occupancy": 0.2, "queue_length_m": 12.0,
                      "mean_speed": 6.0, "waiting_time": 4.0,
                      "connection_signal_states": [
                          {"connection_id": "c0", "movement": "through",
                           "downstream_lane_id": "B_0", "signal_state": "G"}]}}},
            "b": {"current_phase": 1, "stage": "GREEN", "stage_elapsed": 2.0,
                  "lanes": {"B_0": {
                      "vehicle_count": 5.0, "halting_count": 2.0,
                      "occupancy": 0.35, "queue_length_m": 20.0,
                      "mean_speed": 4.0, "waiting_time": 8.0,
                      "connection_signal_states": []}}},
        },
        "vehicles": {
            "car_a": {"type_id": "passenger_car",
                      "position": {"x_m": 10.0, "y_m": 10.0},
                      "motion": {"speed_mps": 6.0},
                      "location": {"road_id": "A", "lane_id": "A_0",
                                   "lane_index": 0, "lane_position_m": 50.0},
                      "route_edges": ["A", "B"],
                      "next_signal": {"intersection_id": "a", "distance_m": 60.0,
                                      "state": "G"},
                      "leader_gap_m": None, "follower_gap_m": None},
            "car_b": {"type_id": "passenger_car",
                      "position": {"x_m": 20.0, "y_m": 20.0},
                      "motion": {"speed_mps": 4.0},
                      "location": {"road_id": "B", "lane_id": "B_0",
                                   "lane_index": 0, "lane_position_m": 40.0},
                      "route_edges": ["B", "A"],
                      "next_signal": {"intersection_id": "b", "distance_m": 50.0,
                                      "state": "G"},
                      "leader_gap_m": None, "follower_gap_m": None},
        },
    }


@pytest.fixture(autouse=True)
def _ttl(monkeypatch):
    # 冻结 TTL=10s，避免受远程环境变量影响
    monkeypatch.setenv("COSLIGHT_VRC_TTL", "10.0")


def _init(bridge: LearnedV2XBridge, episode_id: str = "ep_vrc") -> None:
    bridge.on_initialize(INIT, run_id="run_vrc", episode_id=episode_id,
                         initial_sim_time=0.0)


def test_policy_tensors_candidate_set_filters_by_delivered_age_valid():
    bridge = LearnedV2XBridge(config=V2XConfig(
        penetration_rate=1.0, default_latency_ms=0.0, drop_rate=0.0))
    _init(bridge)
    # 冷启动：未投递 → C=∅
    tensors = bridge.policy_tensors(0.0, ["a", "b"])
    assert tensors["candidate_mask"].shape == (2, 2)
    assert not tensors["candidate_mask"].any()
    # t=0 发布扩展 SPaT（零延迟）；t=5 推进时 hub 投递 t=0 的消息
    bridge.on_step(_step(0.0), {})
    bridge.on_step(_step(5.0), {})
    tensors = bridge.policy_tensors(5.0, ["a", "b"])
    assert tensors["candidate_mask"][0, 1]       # a 可用 b 的 SPaT
    assert tensors["candidate_mask"][1, 0]       # b 可用 a 的 SPaT
    assert not tensors["candidate_mask"][0, 0]   # 不允许自环
    assert tensors["message_features"].shape == (2, 2, MSG_FEATURE_DIM)
    assert tensors["message_features"].dtype == np.float32
    # age=(5-0)/10=0.5；delay=(0-0)/10=0
    assert tensors["message_age"][0, 1] == pytest.approx(0.5)
    assert tensors["message_delay"][0, 1] == pytest.approx(0.0)
    # 冻结特征布局：b 的 local(8) + derived(6)
    expected_b = np.array(
        [1.0, 2.0, 5.0, 2.0, 0.35, 20.0, 4.0, 8.0,   # local
         1.0, 1.0, 0.0, 0.0, 4.0, 0.0],              # derived
        dtype=np.float32)
    np.testing.assert_allclose(tensors["message_features"][0, 1], expected_b)
    assert not tensors["message_features"][0, 0].any()  # padding 行全 0
    # 超过 TTL（未再投递）→ 移出候选；age 特征 clamp 到 1
    tensors = bridge.policy_tensors(12.0, ["a", "b"])
    assert not tensors["candidate_mask"][0, 1]
    assert tensors["message_age"][0, 1] == pytest.approx(1.0)
    # ideal 模式：TTL 不失效、age/delay 恒 0
    ideal = LearnedV2XBridge(config=V2XConfig(
        penetration_rate=1.0, default_latency_ms=0.0,
        latency_jitter_ms=0.0, drop_rate=0.0), mode="ideal")
    _init(ideal, episode_id="ep_ideal")
    ideal.on_step(_step(0.0), {})
    ideal.on_step(_step(5.0), {})
    t = ideal.policy_tensors(12.0, ["a", "b"])
    assert t["candidate_mask"][0, 1]
    assert t["message_age"][0, 1] == 0.0
    assert t["message_delay"][0, 1] == 0.0


def test_nocollab_mode_returns_all_false_mask():
    bridge = LearnedV2XBridge(config=None)  # nocollab
    _init(bridge)
    tensors = bridge.policy_tensors(5.0, ["a", "b"])
    assert tensors["candidate_mask"].shape == (2, 2)
    assert not tensors["candidate_mask"].any()
    assert tensors["message_features"].shape == (2, 2, MSG_FEATURE_DIM)
    assert not tensors["message_features"].any()
    assert tensors["message_age"].shape == (2, 2)
    assert not tensors["message_age"].any()
    assert not tensors["message_delay"].any()
    bridge.on_step(_step(5.0), {})          # 无 hub：安全 no-op
    assert bridge.on_finish(5.0) == {}      # 无 hub：空 summary


def test_penetration_zero_makes_derived_features_invalid():
    cfg = V2XConfig(penetration_rate=0.0, capability_seed=7,
                    default_latency_ms=0.0, drop_rate=0.0)
    bridge = LearnedV2XBridge(config=cfg)
    _init(bridge)
    delivered = []
    bridge._hub.subscribe("SPaT", delivered.append)
    bridge.on_step(_step(0.0), {})
    bridge.on_step(_step(5.0), {})
    spat = [m for m in delivered if m.message_type == "SPaT"]
    assert len(spat) >= 2
    for msg in spat:
        assert msg.payload["vrc_valid"] is False
        assert msg.payload["vrc_derived_features"][0] == 0.0  # connected_count=0
    # 已投递但 vrc_valid=False → 移出候选集
    tensors = bridge.policy_tensors(5.0, ["a", "b"])
    assert not tensors["candidate_mask"].any()


def test_feature_dimensions_frozen():
    assert LOCAL_FEATURE_DIM == VRC_LOCAL_FEATURE_DIM == 8
    assert DERIVED_FEATURE_DIM == VRC_DERIVED_FEATURE_DIM == 6
    assert MSG_FEATURE_DIM == LOCAL_FEATURE_DIM + DERIVED_FEATURE_DIM == 14


def _spat_message(source_id, sim_time, delivered_at, vrc_valid=True):
    """Deterministic white-box delivered SPaT (bypasses hub timing)."""
    from algorithms.v2x.messages import V2XMessage
    payload = {
        "vrc_valid": vrc_valid,
        "vrc_local_features": [0.0] * VRC_LOCAL_FEATURE_DIM,
        "vrc_derived_features": [0.0] * VRC_DERIVED_FEATURE_DIM,
    }
    return V2XMessage(
        message_type="SPaT",
        message_id=f"spat-{source_id}",
        schema_version="1.0",
        run_id="run_vrc",
        episode_id="ep_vrc",
        frame_id="f",
        sequence_no=1,
        sim_time=sim_time,
        source_id=source_id,
        destination="cloud",
        correlation_id=None,
        payload=payload,
        delivered_at=delivered_at,
    )


def test_cold_start_empty_candidate():
    bridge = LearnedV2XBridge(config=V2XConfig(
        penetration_rate=1.0, default_latency_ms=0.0, drop_rate=0.0))
    _init(bridge)
    tensors = bridge.policy_tensors(0.0, ["a", "b"])
    assert tensors["candidate_mask"].shape == (2, 2)
    assert not tensors["candidate_mask"].any()
    assert not tensors["message_features"].any()
    assert not tensors["message_age"].any()
    assert not tensors["message_delay"].any()


def test_ttl_hard_filter_and_soft_age_feature():
    bridge = LearnedV2XBridge(config=V2XConfig(
        penetration_rate=1.0, default_latency_ms=0.0, drop_rate=0.0))
    _init(bridge)
    bridge._delivered_spat["b"] = _spat_message("b", sim_time=0.0, delivered_at=0.0)
    # 5.1s / 9.9s：均未过期，age_norm 可区分（0.51 vs 0.99）
    early = bridge.policy_tensors(5.1, ["a", "b"])
    late = bridge.policy_tensors(9.9, ["a", "b"])
    assert early["candidate_mask"][0, 1]
    assert late["candidate_mask"][0, 1]
    assert early["message_age"][0, 1] == pytest.approx(0.51)
    assert late["message_age"][0, 1] == pytest.approx(0.99)
    assert early["message_age"][0, 1] != late["message_age"][0, 1]
    # 10.0s：严格 < TTL → 移出候选；age 特征仍可见（soft，clamp 到 1）
    boundary = bridge.policy_tensors(10.0, ["a", "b"])
    assert not boundary["candidate_mask"][0, 1]
    assert boundary["message_age"][0, 1] == pytest.approx(1.0)
    # delay = (delivered_at - sim_time)/TTL：投递时刻 6.0、生成时刻 1.0 → 0.5
    bridge._delivered_spat["b"] = _spat_message("b", sim_time=1.0, delivered_at=6.0)
    delayed = bridge.policy_tensors(10.0, ["a", "b"])
    assert delayed["message_delay"][0, 1] == pytest.approx(0.5)
    assert delayed["message_age"][0, 1] == pytest.approx(0.4)
    assert delayed["candidate_mask"][0, 1]          # age=4 < 10 → fresh
    stale = bridge.policy_tensors(16.0, ["a", "b"])
    assert not stale["candidate_mask"][0, 1]        # age=10 → 过期
    assert stale["message_age"][0, 1] == pytest.approx(1.0)


def test_real_mode_frozen_config_partial_penetration():
    # 冻结配置：渗透率 0.6（capability_seed=7 → 2 辆车中恰好 1 辆网联）、
    # 基础延迟 100ms + 抖动 ±50ms、丢包 5%、消息周期 5s。
    cfg = V2XConfig(
        penetration_rate=0.6, capability_seed=7, network_seed=7,
        default_latency_ms=100.0, latency_jitter_ms=50.0, drop_rate=0.05,
        bsm_interval_s=5.0, intent_interval_s=5.0, spat_interval_s=5.0,
        rsm_interval_s=5.0)
    bridge = LearnedV2XBridge(config=cfg)
    _init(bridge)
    # SPaT 从首个发布帧（t=0）起统计，保证 delivered ⊆ published 口径一致
    delivered = []
    bridge._hub.subscribe("SPaT", delivered.append)
    bridge.on_step(_step(0.0), {})
    published = 2                       # t=0 帧：a、b 各一帧
    enabled = {vid: cap.v2x_enabled for vid, cap in bridge._capabilities.items()}
    # 部分渗透：至少一辆网联、至少一辆非网联
    assert any(enabled.values())
    assert not all(enabled.values())
    assert enabled["car_a"] is True and enabled["car_b"] is False
    # 非网联车辆不产生 BSM/INTENT；网联车辆产生（t=0 与 t=5 各一轮）
    bsm_events = []
    bridge._hub.subscribe("BSM", bsm_events.append)
    intent_events = []
    bridge._hub.subscribe("INTENT", intent_events.append)
    bridge.on_step(_step(5.0), {})      # 顺带投递 t=0 帧（延迟 50–150ms）
    bridge._hub.advance(5.2)            # 投递 t=5 帧
    published += 2                       # t=5 帧：a、b 各一帧
    assert len(bsm_events) >= 1
    assert all(e.source_id == "car_a" for e in bsm_events)
    assert len(intent_events) >= 1
    assert all(e.source_id == "car_a" for e in intent_events)
    # 丢包统计：投递数不超过发布数；固定 network_seed=7 下窗口内必有丢包帧
    for t in range(10, 130, 5):
        bridge.on_step(_step(float(t)), {})
        bridge._hub.advance(float(t) + 0.2)
        published += 2                  # a、b 各一帧
    assert len(delivered) <= published
    assert len(delivered) < published   # 0.95^52 ≈ 0.07（seed=7 下确定性成立）

def test_pre_step_delivers_spat_before_policy_tensors():
    """回归：决策前先 ingest/advance，使决策看到最新投递 SPaT（age≈0.49TTL）。

    旧顺序（decision → ingest）：t=20 决策只能看到 t=10 发布、10.1 投递的
    SPaT，age_norm=(20-10.1)/10=0.99；新顺序（pre_step → decision）：
    t=20 决策看到 t=15 发布、15.1 投递的 SPaT，age_norm=(20-15.1)/10=0.49。
    """
    bridge = LearnedV2XBridge(config=V2XConfig(
        penetration_rate=1.0, default_latency_ms=100.0, latency_jitter_ms=0.0,
        drop_rate=0.0, bsm_interval_s=5.0, intent_interval_s=5.0,
        spat_interval_s=5.0, rsm_interval_s=5.0))
    _init(bridge)
    for t in (0.0, 5.0, 10.0, 15.0):
        bridge.on_step(_step(t), {})     # 只 advance 到 t；15.1 尚未投递
    # 旧顺序（先决策后 ingest）：只能看到 10.1 投递的消息 → age=0.99
    stale = bridge.policy_tensors(20.0, ["a", "b"])
    assert stale["message_age"][0, 1] == pytest.approx(0.99, abs=0.01)
    # 新顺序（先 pre_step 再决策）：15.1 投递的消息已可见 → age=4.9/10=0.49
    frame = bridge.pre_step(_step(20.0))
    tensors = bridge.policy_tensors(20.0, ["a", "b"])
    assert tensors["candidate_mask"][0, 1]               # 4.9s < TTL=10s → fresh
    assert tensors["message_age"][0, 1] == pytest.approx(0.49, abs=0.02)
    # post_step 沿用 pre_step 的 frame（ingest_actions 要求最新 step frame）
    bridge.post_step(_step(20.0), {}, frame)
    assert frame.frame_id in bridge._hub._consumed_frames
