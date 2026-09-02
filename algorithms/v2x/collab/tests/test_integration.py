"""集成测试（spec §6.3）：shadow 不变量、RSI 恰 1 条 message + 1 条终态 delivery、
summary 可重建、确定性 fixture。"""
import copy
import json

from algorithms.v2x.collab.aggregator import EdgeAggregator
from algorithms.v2x.collab.arbiter import ActionArbiter
from algorithms.v2x.collab.engine import CollabDecisionEngine
from algorithms.v2x.collab.policy import CloudRulePolicy
from algorithms.v2x.collab.proposals import (
    CollabConfig, DecisionMode, GuidanceEmissionMode,
)
from algorithms.v2x.collab.records import InMemoryRecordCollector
from algorithms.v2x.collab.state import CloudStateStore
from algorithms.v2x.collab.stats import build_collab_summary
from algorithms.v2x.config import V2XConfig
from algorithms.v2x.hub import V2XHub
from algorithms.v2x.messages import MessageDraft
from algorithms.v2x.protocol import build_bsm_draft, build_intent_draft, build_spat_draft
from algorithms.v2x.collab.scope import ResolvedScenarioScope


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

STEP = {"episode_id": "ep1", "simulation_time": 5.0,
        "intersections": {}, "vehicles": {}}
ACTIONS = {"signals": {"i1": {"target_phase": 1}}, "vehicles": {}}


def _normalize(value):
    return json.dumps(value, sort_keys=True, ensure_ascii=False,
                      default=lambda o: list(o) if isinstance(o, tuple) else o)


def _make_env():
    collector = InMemoryRecordCollector()
    hub = V2XHub(config=V2XConfig(default_latency_ms=0.0, drop_rate=0.0),
                 sink=collector)
    aggregator = EdgeAggregator(managed_ids=("i1",))
    store = CloudStateStore(aggregator, CollabConfig().freshness)
    policy = CloudRulePolicy(CollabConfig())
    arbiter = ActionArbiter(DecisionMode.SHADOW)
    engine = CollabDecisionEngine(
        hub=hub, aggregator=aggregator, store=store, policy=policy,
        arbiter=arbiter, collector=collector, config=CollabConfig(),
        scope=SCOPE, run_id="run1", episode_id="ep1",
        registered_ids=("i1",))
    return hub, engine, collector


def _deliver(hub, frame):
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


def _run_episode():
    hub, engine, collector = _make_env()
    hub.ingest_initialize(INIT, run_id="run1", episode_id="ep1")
    results = []
    for _ in range(3):
        frame = hub.ingest_step(STEP)
        _deliver(hub, frame)
        result = engine.tick(frame=frame, baseline_actions=ACTIONS)
        results.append(result)
        # 与 adapter 一致：engine 拥有 RSI 发射权，hub 只记录 SIGNAL_CONTROL（剥离 vehicles）
        hub.ingest_actions(
            {k: v for k, v in result.protocol_actions.items()
             if k != "vehicles"},
            frame=frame)
    network = hub.finish_episode(5.0, drain_pending=True)
    collab = engine.finalize_episode(episode_id="ep1", registered_ids=("i1",))
    engine.close()
    return hub, engine, collector, results, network, collab


def test_shadow_invariant_every_frame():
    hub, engine, collector, results, network, collab = _run_episode()
    for result in results:
        assert _normalize(result.protocol_actions) == _normalize(ACTIONS)
        for source in result.signal_sources.values():
            assert source.value == "baseline"


def test_collab_rsi_exactly_one_message_and_terminal_delivery():
    hub, engine, collector, results, network, collab = _run_episode()
    emitted = [mid for r in results for mid in r.emitted_rsi_message_ids]
    # 确定性 fixture：每帧同一条 RSI 受去重/冷却抑制 → 全程恰 1 条发布
    assert len(emitted) == 1
    rsi_sent = [rec for rec in hub.sent_records
                if rec["message_type"] == "RSI"]
    assert len(rsi_sent) == 1
    rsi_deliveries = [rec for rec in hub.delivery_records
                      if rec["message_id"] == emitted[0]]
    assert len(rsi_deliveries) == 1
    assert rsi_deliveries[0]["status"] == "delivered"


def test_summary_rebuild_matches_runtime():
    hub, engine, collector, results, network, collab = _run_episode()
    rebuilt = build_collab_summary(
        records=collector.episode_records, config=CollabConfig(),
        scope=SCOPE, registered_ids=("i1",), hub=hub,
        run_id="run1", episode_id="ep1")
    # 运行时 finalize 与从记录重建规范化一致（collab 块）
    assert _normalize(rebuilt["collab"]) == _normalize(collab["collab"])
    # 完整性全零
    assert rebuilt["collab"]["integrity"] == {
        "missing_source_delivery_refs": 0,
        "orphan_rsi_messages": 0,
        "orphan_rsi_deliveries": 0,
        "missing_signal_event_refs": 0,
        "duplicate_terminal_delivery_records": 0,
    }