import copy
import json

import pytest

from algorithms.v2x.collab.aggregator import EdgeAggregator
from algorithms.v2x.collab.arbiter import ActionArbiter, ActiveModeUnavailableError
from algorithms.v2x.collab.engine import CollabDecisionEngine
from algorithms.v2x.collab.policy import CloudRulePolicy
from algorithms.v2x.collab.proposals import (
    CollabConfig, DecisionMode, GuidanceEmissionMode,
)
from algorithms.v2x.collab.records import InMemoryRecordCollector
from algorithms.v2x.collab.state import CloudStateStore
from algorithms.v2x.hub import V2XHub
from algorithms.v2x.messages import MessageDraft
from algorithms.v2x.protocol import build_bsm_draft, build_intent_draft, build_spat_draft
from algorithms.v2x.config import V2XConfig
from algorithms.config.scenario_presets import ResolvedScenarioScope


SCOPE = ResolvedScenarioScope(source="preset", preset_id="east_dense",
                              managed_ids=("i1",))

INIT = {
    "episode_id": "ep1",
    "vehicle_types": {"official_passenger": {"vehicle_class": "passenger"}},
    "intersections": {
        "i1": {
            "intersection_id": "i1", "phase_order": [1],
            "phases": {"1": {"phase_id": 1,
                             "connection_priorities": {"c0": "protected"},
                             "green_seconds": 31.0, "yellow_seconds": 3.0,
                             "clearance_seconds": 2.0}},
            "lanes": {
                "A_0": {"lane_id": "A_0", "edge_id": "A", "lane_index": 0,
                        "approach_id": "west", "movements": ("through",),
                        "speed_limit_mps": 16.0},
            },
            "connections": [{"connection_id": "c0", "from_lane": "A_0",
                             "to_lane": "B_0", "movement": "through"}],
            "direct_neighbors": [],
        },
    },
}

STEP = {
    "episode_id": "ep1", "simulation_time": 5.0,
    "intersections": {},
    "vehicles": {},
}

ACTIONS = {"signals": {"i1": {"target_phase": 1}}, "vehicles": {}}


def _engine(mode=DecisionMode.SHADOW, guidance_mode=GuidanceEmissionMode.THRESHOLD):
    config = V2XConfig(default_latency_ms=0.0, drop_rate=0.0, network_seed=0)
    collector = InMemoryRecordCollector()
    hub = V2XHub(config=config, sink=collector)
    aggregator = EdgeAggregator(managed_ids=("i1",))
    store = CloudStateStore(aggregator, CollabConfig().freshness)
    policy = CloudRulePolicy(CollabConfig(
        decision_mode=mode, guidance_mode=guidance_mode))
    arbiter = ActionArbiter(mode)
    engine = CollabDecisionEngine(
        hub=hub, aggregator=aggregator, store=store, policy=policy,
        arbiter=arbiter, collector=collector,
        config=CollabConfig(decision_mode=mode, guidance_mode=guidance_mode),
        scope=SCOPE, run_id="run1", episode_id="ep1",
        registered_ids=("i1",))
    return hub, engine, collector


def _deliver_uplink(hub, frame):
    hub.publish(build_spat_draft("i1", {
        "current_phase": 1, "stage": "GREEN", "stage_elapsed": 10.0,
        "remaining_time_s": 21.0, "lanes": {},
    }, INIT["intersections"]["i1"]["phases"], sim_time=frame.sim_time),
        frame_id=frame.frame_id)
    hub.publish(build_bsm_draft("car1", {
        "type_id": "official_passenger", "position": {"x_m": 0.0, "y_m": 0.0},
        "motion": {"speed_mps": 8.0, "acceleration_mps2": 0.0},
        "location": {"road_id": "A", "lane_id": "A_0", "lane_index": 0,
                     "lane_position_m": 10.0},
        "route_edges": ["A", "B"],
        "next_signal": {"intersection_id": "i1", "distance_m": 205.0,
                        "state": "G"},
        "leader_gap_m": None, "follower_gap_m": None,
        "_sim_time": frame.sim_time,
    }), frame_id=frame.frame_id)
    hub.publish(build_intent_draft(
        "car1", {}, sim_time=frame.sim_time, turn="through",
        lane_change=None, arrival=15.0, turn_conf=1.0,
        lane_change_conf=0.0, arrival_conf=1.0, origin="derived"),
        frame_id=frame.frame_id)
    hub.advance(frame.sim_time)


def _normalize(actions):
    return json.dumps(actions, sort_keys=True, ensure_ascii=False,
                      default=lambda o: list(o) if isinstance(o, tuple) else o)


def _tick(hub, engine):
    hub.ingest_initialize(INIT, run_id="run1", episode_id="ep1")
    frame = hub.ingest_step(STEP)      # t=5：投递 MAP
    _deliver_uplink(hub, frame)        # 投递 SPaT/BSM/INTENT
    return engine.tick(frame=frame, baseline_actions=ACTIONS)


def test_shadow_tick_preserves_baseline_and_emits_rsi():
    hub, engine, collector = _engine()
    original = copy.deepcopy(ACTIONS)
    result = _tick(hub, engine)
    # applied == baseline（规范化一致；输入对象不被原地修改）
    assert _normalize(result.protocol_actions) == _normalize(ACTIONS)
    assert ACTIONS == original
    assert result.signal_sources["i1"].value == "baseline"
    assert result.stats_delta.baseline_slots == 1
    assert result.stats_delta.decision_records == 1
    # RSI：1 条已发布（阈值触发 fixture）
    assert result.stats_delta.guidance_funnel["published"] == 1
    assert len(result.emitted_rsi_message_ids) == 1
    assert result.emitted_rsi_message_ids_by_intersection["i1"] == \
        result.emitted_rsi_message_ids
    # tick stats 原子写入
    assert any(r.record_type == "collab_tick_stats"
               for r in collector.episode_records)
    # 完整性：collab RSI 恰 1 条 message + 终态 delivery
    hub.finish_episode(5.0, drain_pending=True)
    from algorithms.v2x.collab.stats import build_collab_summary
    summary = build_collab_summary(
        records=collector.episode_records, config=CollabConfig(),
        scope=SCOPE, registered_ids=("i1",), hub=hub,
        run_id="run1", episode_id="ep1")
    assert summary["collab"]["guidance"]["delivered_count"] == 1
    assert summary["collab"]["integrity"]["orphan_rsi_messages"] == 0


def test_off_mode_short_circuits_without_records():
    hub, engine, collector = _engine(mode=DecisionMode.OFF)
    result = _tick(hub, engine)
    assert result.stats_delta.baseline_slots == 0
    assert result.stats_delta.emitted_rsi_count == 0
    assert not any(r.record_type == "collab_tick_stats"
                   for r in collector.episode_records)
    assert _normalize(result.protocol_actions) == _normalize(ACTIONS)


def test_disabled_guidance_no_rsi():
    hub, engine, _ = _engine(guidance_mode=GuidanceEmissionMode.DISABLED)
    result = _tick(hub, engine)
    assert result.emitted_rsi_message_ids == ()
    assert result.stats_delta.guidance_funnel["published"] == 0


def test_active_mode_unavailable_at_construction():
    with pytest.raises(ActiveModeUnavailableError):
        _engine(mode=DecisionMode.ACTIVE)


def test_finalize_writes_episode_end_and_scope():
    hub, engine, collector = _engine()
    _tick(hub, engine)
    summary = engine.finalize_episode(episode_id="ep1", registered_ids=("i1",))
    assert summary["collab"]["schema_version"] == "1.0"
    assert summary["scope"]["managed_ids"] == ["i1"]
    assert any(r.record_type == "collab_episode_end"
               for r in collector.episode_records)


def test_reset_episode_clears_last_emitted():
    hub, engine, collector = _engine()
    first = _tick(hub, engine)
    assert len(first.emitted_rsi_message_ids) == 1
    # hub 状态机：新 episode 前必须先 finish（spec §1.3）
    hub.finish_episode(5.0, drain_pending=True)
    engine.reset_episode(episode_id="ep2")
    # 新 episode 首帧：无 last_emitted → 再次发布
    hub.ingest_initialize(INIT, run_id="run1", episode_id="ep2")
    frame = hub.ingest_step(STEP)
    _deliver_uplink(hub, frame)
    second = engine.tick(frame=frame, baseline_actions=ACTIONS)
    assert len(second.emitted_rsi_message_ids) == 1