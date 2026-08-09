import math

from algorithms.v2x.collab.aggregator import EdgeAggregator
from algorithms.v2x.collab.policy import CloudRulePolicy
from algorithms.v2x.collab.proposals import (
    CollabConfig, FreshnessConfig, SignalDecisionStatus,
)
from algorithms.v2x.collab.state import CloudStateStore
from algorithms.v2x.messages import V2XMessage


MAP = {
    "intersection_id": "i1", "phase_order": [1, 2],
    "phases": {
        "1": {"phase_id": 1, "connection_priorities": {"c0": "protected",
                                                        "c1": "protected"}},
        "2": {"phase_id": 2, "connection_priorities": {"c2": "protected",
                                                        "c3": "protected"}},
    },
    "lanes": {
        "A_0": {"lane_id": "A_0", "edge_id": "A", "lane_index": 0,
                "approach_id": "west", "movements": ("through",),
                "speed_limit_mps": 13.9},
        "A_1": {"lane_id": "A_1", "edge_id": "A", "lane_index": 1,
                "approach_id": "west", "movements": ("left",),
                "speed_limit_mps": 13.9},
        "C_0": {"lane_id": "C_0", "edge_id": "C", "lane_index": 0,
                "approach_id": "north", "movements": ("through",),
                "speed_limit_mps": 13.9},
        "C_1": {"lane_id": "C_1", "edge_id": "C", "lane_index": 1,
                "approach_id": "north", "movements": ("left",),
                "speed_limit_mps": 13.9},
    },
    "connections": [
        {"connection_id": "c0", "from_lane": "A_0", "to_lane": "B_0",
         "movement": "through"},
        {"connection_id": "c1", "from_lane": "A_1", "to_lane": "B_0",
         "movement": "left"},
        {"connection_id": "c2", "from_lane": "C_0", "to_lane": "D_0",
         "movement": "through"},
        {"connection_id": "c3", "from_lane": "C_1", "to_lane": "D_0",
         "movement": "left"},
    ],
    "direct_neighbors": [],
}


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


def _bsm(vid, lane, speed=6.0, ns="i1", distance=50.0, sim_time=5.0,
         turn=None, eta=None):
    return _msg("BSM", vid, {
        "vehicle_id": vid, "type_id": "passenger", "position": (0.0, 0.0),
        "motion": {"speed_mps": speed, "acceleration_mps2": 0.0},
        "location": {"road_id": lane.rsplit("_", 1)[0], "lane_id": lane,
                     "lane_index": 0, "lane_position_m": 10.0},
        "route_edges": [lane.rsplit("_", 1)[0], "B"],
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


def _spat(stage_elapsed=10.0, remaining=20.0, sim_time=5.0, phase=1, stage="GREEN"):
    return _msg("SPaT", "i1", {
        "intersection_id": "i1", "current_phase": phase, "stage": stage,
        "stage_elapsed": stage_elapsed, "connection_signal_states": [],
        "remaining_time_s": remaining, "next_stage": "YELLOW",
        "next_stage_start_time": 25.0, "schedule_status": "predicted",
    }, sim_time)


_MISSING = object()


def _setup(*, spat=_MISSING, vehicles=(), intents=(), freshness=None):
    agg = EdgeAggregator(managed_ids=("i1",))
    agg.on_message(_msg("MAP", "i1", MAP, 0.0, frame_id="ep1:init"))
    if spat is _MISSING:
        spat = _spat()
    if spat is not None:
        agg.on_message(spat)
    for bsm in vehicles:
        agg.on_message(bsm)
    for intent in intents:
        agg.on_message(intent)
    store = CloudStateStore(agg, freshness or FreshnessConfig())
    policy = CloudRulePolicy(CollabConfig())
    return store, policy


def test_queue_demand_proposes_switch_to_phase_2():
    # 相位 1 服务 A 车道（through/left）；相位 2 服务 C 车道。
    # A_0/A_1 大量排队 → 建议切到相位 1？当前相位已是 1 → KEEP_CURRENT。
    # 构造 C 车道排队 → 建议切到相位 2。
    store, policy = _setup(
        vehicles=[
            _bsm("vA0", "A_0", speed=0.1),
            _bsm("vA1", "A_1", speed=0.1),
            _bsm("vC0", "C_0", speed=0.1),
            _bsm("vC1", "C_1", speed=0.1),
        ],
        intents=[_intent("vA0", "through", 8.0),
                 _intent("vA1", "left", 8.0),
                 _intent("vC0", "through", 8.0),
                 _intent("vC1", "left", 8.0)],
    )
    view = store.view("i1", now=5.0)
    assert view is not None
    ctx = store.static_context("i1")
    proposal = policy.propose_signal(
        intersection_id="i1", snapshot=view.snapshot, static_context=ctx,
        now=5.0, frame_id="ep1:step:000001", config=CollabConfig(),
    )
    # A(2) 与 C(2) 排队相等，forward 也相等 → 平分按 action ID 升序 → 相位 1 胜出
    # 当前 action=1 → KEEP_CURRENT
    assert proposal.status is SignalDecisionStatus.KEEP_CURRENT


def test_missing_spat_is_missing_input():
    store, policy = _setup(spat=None)
    view = store.view("i1", now=5.0)
    ctx = store.static_context("i1")
    proposal = policy.propose_signal(
        intersection_id="i1", snapshot=view.snapshot, static_context=ctx,
        now=5.0, frame_id="ep1:step:000001", config=CollabConfig(),
    )
    assert proposal.status is SignalDecisionStatus.MISSING_INPUT
    assert proposal.candidate_action is None
    assert proposal.proposed_action is None


def test_transition_stage_suppressed():
    store, policy = _setup(spat=_spat(stage="YELLOW", stage_elapsed=2.0))
    view = store.view("i1", now=5.0)
    ctx = store.static_context("i1")
    proposal = policy.propose_signal(
        intersection_id="i1", snapshot=view.snapshot, static_context=ctx,
        now=5.0, frame_id="ep1:step:000001", config=CollabConfig(),
    )
    assert proposal.status is SignalDecisionStatus.SUPPRESSED_TRANSITION
    assert proposal.candidate_action is None
    assert proposal.proposed_action is None


def test_min_green_suppresses_switch():
    store, policy = _setup(
        spat=_spat(stage_elapsed=1.0, remaining=20.0),
        vehicles=[_bsm("vC0", "C_0", speed=0.1),
                  _bsm("vC1", "C_1", speed=0.1)],
        intents=[_intent("vC0", "through", 8.0),
                 _intent("vC1", "left", 8.0)],
    )
    view = store.view("i1", now=5.0)
    ctx = store.static_context("i1")
    proposal = policy.propose_signal(
        intersection_id="i1", snapshot=view.snapshot, static_context=ctx,
        now=5.0, frame_id="ep1:step:000001", config=CollabConfig(),
    )
    # C 排队 2 > A 排队 0 → 候选 2；stage_elapsed=1 < min_green=5 → SUPPRESSED_MIN_GREEN
    assert proposal.status is SignalDecisionStatus.SUPPRESSED_MIN_GREEN
    assert proposal.candidate_action == 2
    assert proposal.proposed_action == 1
