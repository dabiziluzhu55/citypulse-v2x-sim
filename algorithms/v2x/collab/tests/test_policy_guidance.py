import math
import pytest

from algorithms.v2x.collab.aggregator import EdgeAggregator
from algorithms.v2x.collab.policy import CloudRulePolicy, GUIDANCE_FUNNEL_STAGES
from algorithms.v2x.collab.proposals import (
    CollabConfig, FreshnessConfig, GuidanceDecisionStatus,
    GuidanceEmissionMode, LastEmittedGuidanceState,
)
from algorithms.v2x.collab.state import CloudStateStore
from algorithms.v2x.messages import V2XMessage
from algorithms.v2x.collab.scope import ResolvedScenarioScope


MAP = {
    "intersection_id": "i1", "phase_order": [1],
    "phases": {"1": {"phase_id": 1,
                     "connection_priorities": {"c0": "protected",
                                               "c1": "protected"}}},
    "lanes": {
        "A_0": {"lane_id": "A_0", "edge_id": "A", "lane_index": 0,
                "approach_id": "west", "movements": ("through",),
                "speed_limit_mps": 16.0},
        "A_1": {"lane_id": "A_1", "edge_id": "A", "lane_index": 1,
                "approach_id": "west", "movements": ("left",),
                "speed_limit_mps": 16.0},
    },
    "connections": [
        {"connection_id": "c0", "from_lane": "A_0", "to_lane": "B_0",
         "movement": "through"},
        {"connection_id": "c1", "from_lane": "A_1", "to_lane": "B_0",
         "movement": "left"},
    ],
    "direct_neighbors": [],
}

SCOPE = ResolvedScenarioScope(source="preset", preset_id="east_dense",
                              managed_ids=("i1",))


def _msg(message_type, source_id, payload, sim_time, frame_id="ep1:step:000001",
         message_id=None):
    return V2XMessage(
        message_type=message_type,
        message_id=message_id or f"{message_type}-{source_id}-{sim_time}",
        schema_version="1.0", run_id="run1", episode_id="ep1",
        frame_id=frame_id, sequence_no=1, sim_time=sim_time,
        source_id=source_id, destination="cloud", correlation_id=None,
        payload=payload,
    )


def _bsm(vid, lane="A_0", speed=8.0, ns="i1", distance=205.0, sim_time=5.0):
    return _msg("BSM", vid, {
        "vehicle_id": vid, "type_id": "passenger", "position": (0.0, 0.0),
        "motion": {"speed_mps": speed, "acceleration_mps2": 0.0},
        "location": {"road_id": "A", "lane_id": lane, "lane_index": 0,
                     "lane_position_m": 10.0},
        "route_edges": ["A", "B"],
        "next_signal": {"intersection_id": ns, "distance_m": distance,
                        "state": "G"},
        "front_gap_m": None, "rear_gap_m": None, "gap_source": None,
    }, sim_time)


def _intent(vid, turn, eta, sim_time=5.0):
    return _msg("INTENT", vid, {
        "vehicle_id": vid, "turn_intent": turn, "lane_change_intent": None,
        "estimated_arrival_s": eta, "turn_confidence": 1.0,
        "lane_change_confidence": 0.0, "arrival_confidence": 1.0,
        "intent_origin": "derived",
    }, sim_time)


def _spat(remaining=21.0, stage="GREEN", sim_time=5.0):
    return _msg("SPaT", "i1", {
        "intersection_id": "i1", "current_phase": 1, "stage": stage,
        "stage_elapsed": 10.0, "connection_signal_states": [],
        "remaining_time_s": remaining, "next_stage": "YELLOW",
        "next_stage_start_time": 26.0, "schedule_status": "predicted",
    }, sim_time)


def _setup(*, vehicles=(), intents=(), spat=None, freshness=None):
    agg = EdgeAggregator(managed_ids=("i1",))
    agg.on_message(_msg("MAP", "i1", MAP, 0.0, frame_id="ep1:init"))
    agg.on_message(spat if spat is not None else _spat())
    for bsm in vehicles:
        agg.on_message(bsm)
    for intent in intents:
        agg.on_message(intent)
    store = CloudStateStore(agg, freshness or FreshnessConfig())
    policy = CloudRulePolicy(CollabConfig())
    return store, policy


def _propose(policy, store, *, last_emitted=None, config=None, vehicle_id="car1"):
    view = store.view("i1", now=5.0)
    assert view is not None
    ctx = store.static_context("i1")
    vehicle = view.snapshot.connected_vehicles.get(vehicle_id)
    if vehicle is None:
        # 非 managed next_signal 车辆不在路口快照中，直接从聚合器缓存取状态
        vehicle = store._aggregator._vehicles[vehicle_id].to_state()
    return policy.propose_guidance(
        vehicle=vehicle, snapshot=view.snapshot, static_context=ctx,
        now=5.0, frame_id="ep1:step:000001", config=config or CollabConfig(),
        scope=SCOPE, last_emitted=last_emitted)


def test_threshold_published_speed_catchup():
    # 车 8 m/s、距离 205m、剩余绿灯 21s（available=20）→ raw=10.25 ≤ upper=10.4
    # |10.25-8|=2.25 ≥ trigger 2.0 → PROPOSED（无历史 → published）
    store, policy = _setup(
        vehicles=[_bsm("car1", speed=8.0, distance=205.0)],
        intents=[_intent("car1", "through", 15.0)],
    )
    outcome = _propose(policy, store)
    assert outcome.funnel_stage == "published"
    assert outcome.filter_reason is None
    p = outcome.proposal
    assert p is not None
    assert p.status is GuidanceDecisionStatus.PROPOSED
    assert p.speed_status is GuidanceDecisionStatus.PROPOSED
    assert p.target_speed_mps == pytest.approx(205.0 / 20.0)
    assert p.guidance_type == "speed"


def test_threshold_suppressed_when_delta_below_trigger():
    # 6 m/s、distance=140、available=20 → raw=7.0、target=7.0、delta=1.0 < 2.0
    # → SUPPRESSED_THRESHOLD（raw 合法但未达发射阈值）
    store, policy = _setup(
        vehicles=[_bsm("car1", speed=6.0, distance=140.0)],
        intents=[_intent("car1", "through", 15.0)],
    )
    outcome = _propose(policy, store)
    assert outcome.funnel_stage == "raw_proposals"
    assert outcome.filter_reason == "speed_below_trigger"
    assert outcome.proposal.status is GuidanceDecisionStatus.SUPPRESSED_THRESHOLD


def _rsm(objects, sim_time=5.0):
    return _msg("RSM", "i1", {"rsu_id": "i1", "objects": objects}, sim_time,
                message_id="rsm-i1-5")


def test_lane_only_proposal_published_without_speed():
    # turn=left：A_1 相邻同 edge、movement 允许 left；
    # A_0 排队 3（3 辆停车网联车）、A_1 排队 1（RSM 观察卡车）→ 收益 2 ≥ 2 → lane PROPOSED
    # 速度分量：phase 1 服务 left 且当前可绿灯内通过 → NO_ACTION_NEEDED，不否决 lane
    store, policy = _setup(
        vehicles=[_bsm("car1", lane="A_0", speed=6.0, distance=100.0),
                  _bsm("car2", lane="A_0", speed=0.1, distance=90.0),
                  _bsm("car3", lane="A_0", speed=0.1, distance=80.0),
                  _bsm("car4", lane="A_0", speed=0.1, distance=70.0)],
        intents=[_intent("car1", "left", 15.0),
                 _intent("car2", "left", 15.0),
                 _intent("car3", "left", 15.0),
                 _intent("car4", "left", 15.0)],
    )
    # A_1 上的 RSM 低速观察（卡车）
    _inject_rsm(store, _rsm([{"object_id": "truck1", "object_class": "truck",
                              "position": (1.0, 1.0), "speed_mps": 0.1,
                              "lane_id": "A_1", "confidence": 1.0}]), policy)
    outcome = _propose(policy, store)
    assert outcome.funnel_stage == "published"
    p = outcome.proposal
    assert p.status is GuidanceDecisionStatus.PROPOSED
    assert p.speed_status is GuidanceDecisionStatus.NO_ACTION_NEEDED
    assert p.lane_status is GuidanceDecisionStatus.PROPOSED
    assert p.target_lane_id == "A_1"
    assert p.target_lane_index == 1
    assert p.guidance_type == "lane"


def _inject_rsm(store, rsm_message, policy):
    """在快照构建前注入 RSM（等价于已投递消息回调）。"""
    store._aggregator.on_message(rsm_message)
    outcome = _propose(policy, store)
    assert outcome.funnel_stage == "published"
    p = outcome.proposal
    assert p.status is GuidanceDecisionStatus.PROPOSED
    assert p.speed_status is GuidanceDecisionStatus.NO_ACTION_NEEDED
    assert p.lane_status is GuidanceDecisionStatus.PROPOSED
    assert p.target_lane_id == "A_1"
    assert p.target_lane_index == 1
    assert p.guidance_type == "lane"


def test_duplicate_suppressed_and_cooldown():
    store, policy = _setup(
        vehicles=[_bsm("car1", speed=8.0, distance=205.0)],
        intents=[_intent("car1", "through", 15.0)],
    )
    first = _propose(policy, store)
    assert first.funnel_stage == "published"
    last = LastEmittedGuidanceState(
        target_speed_mps=first.proposal.target_speed_mps,
        target_lane_id=None, target_lane_index=None,
        emitted_at=5.0, valid_until=15.0, reason=first.proposal.reason,
        emitted_message_id="rsi-1")
    dup = _propose(policy, store, last_emitted=last)
    assert dup.funnel_stage == "threshold_passed"
    assert dup.proposal.status is GuidanceDecisionStatus.SUPPRESSED_DUPLICATE
    # 速度变化 ≥1.0 但冷却未到（5s 内）→ SUPPRESSED_COOLDOWN
    changed = LastEmittedGuidanceState(
        target_speed_mps=9.0, target_lane_id=None, target_lane_index=None,
        emitted_at=5.0, valid_until=15.0, reason="speed_catchup",
        emitted_message_id="rsi-1")
    cooldown = _propose(policy, store, last_emitted=changed)
    assert cooldown.funnel_stage == "dedup_passed"
    assert cooldown.proposal.status is GuidanceDecisionStatus.SUPPRESSED_COOLDOWN


def test_full_mode_bypasses_threshold_and_cooldown():
    # speed=9、distance=195 → raw=9.75、delta=0.75 < trigger 2.0；FULL 绕过阈值直接发布
    store, policy = _setup(
        vehicles=[_bsm("car1", speed=9.0, distance=195.0)],
        intents=[_intent("car1", "through", 15.0)],
    )
    cfg = CollabConfig(guidance_mode=GuidanceEmissionMode.FULL)
    outcome = _propose(policy, store, config=cfg)
    assert outcome.funnel_stage == "published"
    assert outcome.proposal.status is GuidanceDecisionStatus.PROPOSED
    # FULL 诊断：按 THRESHOLD 规则该建议会被速度阈值抑制 → would_pass_threshold=False
    assert outcome.would_pass_threshold is False
    assert outcome.would_be_duplicate is None
    assert outcome.would_be_in_cooldown is None


def test_full_mode_diagnostics_when_threshold_would_pass():
    # speed=8、distance=205 → raw=10.25、delta=2.25 ≥ trigger 2.0；FULL 发布，
    # 诊断显示按 THRESHOLD 同样可达发射
    store, policy = _setup(
        vehicles=[_bsm("car1", speed=8.0, distance=205.0)],
        intents=[_intent("car1", "through", 15.0)],
    )
    cfg = CollabConfig(guidance_mode=GuidanceEmissionMode.FULL)
    outcome = _propose(policy, store, config=cfg)
    assert outcome.funnel_stage == "published"
    assert outcome.proposal.status is GuidanceDecisionStatus.PROPOSED
    assert outcome.would_pass_threshold is True
    assert outcome.would_be_duplicate is False
    assert outcome.would_be_in_cooldown is False


def test_full_mode_diagnostics_duplicate_under_threshold_rules():
    # FULL 绕过去重/冷却直接发布，但诊断按 THRESHOLD 规则标记 duplicate
    store, policy = _setup(
        vehicles=[_bsm("car1", speed=8.0, distance=205.0)],
        intents=[_intent("car1", "through", 15.0)],
    )
    cfg = CollabConfig(guidance_mode=GuidanceEmissionMode.FULL)
    first = _propose(policy, store, config=cfg)
    assert first.funnel_stage == "published"
    last = LastEmittedGuidanceState(
        target_speed_mps=first.proposal.target_speed_mps,
        target_lane_id=None, target_lane_index=None,
        emitted_at=5.0, valid_until=15.0, reason=first.proposal.reason,
        emitted_message_id="rsi-1")
    dup = _propose(policy, store, config=cfg, last_emitted=last)
    assert dup.funnel_stage == "published"
    assert dup.proposal.status is GuidanceDecisionStatus.PROPOSED
    assert dup.would_pass_threshold is True
    assert dup.would_be_duplicate is True
    assert dup.would_be_in_cooldown is False


def test_next_signal_not_managed_not_candidate():
    store, policy = _setup(
        vehicles=[_bsm("car1", ns="i9", distance=100.0)],
        intents=[_intent("car1", "through", 15.0)],
    )
    outcome = _propose(policy, store)
    assert outcome.proposal is None
    assert outcome.funnel_stage == "next_signal_known"
    assert outcome.filter_reason == "next_signal_not_managed"


def test_funnel_stages_order():
    assert GUIDANCE_FUNNEL_STAGES[0] == "connected_seen"
    assert GUIDANCE_FUNNEL_STAGES[-1] == "published"
