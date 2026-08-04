from algorithms.v2x.collab.aggregator import EdgeAggregator
from algorithms.v2x.collab.proposals import FreshnessConfig
from algorithms.v2x.collab.state import CloudStateStore


MAP = {
    "intersection_id": "i1", "phase_order": [1],
    "phases": {"1": {"phase_id": 1, "connection_priorities": {}}},
    "lanes": {
        "A_0": {"lane_id": "A_0", "edge_id": "A", "lane_index": 0,
                "approach_id": "west", "movements": ("through",),
                "speed_limit_mps": 13.9},
    },
    "connections": [], "direct_neighbors": [],
}


def _message(message_type, source_id, payload, sim_time):
    from algorithms.v2x.messages import V2XMessage
    return V2XMessage(
        message_type=message_type, message_id=f"{message_type}-{source_id}-{sim_time}",
        schema_version="1.0", run_id="run1", episode_id="ep1",
        frame_id="ep1:step:000001", sequence_no=1, sim_time=sim_time,
        source_id=source_id, destination="cloud", correlation_id=None,
        payload=payload,
    )


def _spat(sim_time):
    return _message("SPaT", "i1", {
        "intersection_id": "i1", "current_phase": 1, "stage": "GREEN",
        "stage_elapsed": 2.0, "connection_signal_states": [],
        "remaining_time_s": 20.0, "next_stage": "YELLOW",
        "next_stage_start_time": 25.0, "schedule_status": "predicted",
    }, sim_time)


def _build_store(freshness=None):
    agg = EdgeAggregator(managed_ids=("i1",))
    agg.on_message(_message("MAP", "i1", MAP, 0.0))
    agg.on_message(_spat(5.0))
    store = CloudStateStore(agg, freshness or FreshnessConfig())
    return store, agg


def test_view_freshness_and_stale():
    store, _ = _build_store()
    view = store.view("i1", now=10.0)
    assert view is not None
    assert view.snapshot.intersection_id == "i1"
    assert view.age_s["SPaT"] == 5.0
    assert "SPaT" not in view.missing
    assert "SPaT" not in view.stale
    assert "BSM" in view.missing  # 从未收到 BSM


def test_view_stale_when_over_threshold():
    store, _ = _build_store(freshness=FreshnessConfig(spat_s=3.0))
    view = store.view("i1", now=10.0)
    assert "SPaT" in view.stale
    assert view.age_s["SPaT"] == 5.0


def test_static_context_via_store():
    store, _ = _build_store()
    ctx = store.static_context("i1")
    assert ctx is not None
    assert ctx.valid_actions == (1,)


def test_reset_episode_clears_state():
    store, agg = _build_store()
    store.view("i1", now=10.0)
    agg.reset_episode()
    assert store.view("i1", now=10.0) is None
