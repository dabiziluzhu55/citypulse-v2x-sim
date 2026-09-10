"""Offline closed-loop SUMO evaluation for Traffic-Qwen / Base Qwen.

Does not use Backend, RAG, or traffic_control algorithm code. Baseline is
Fixed-time; the model installs 30s AIControlPlan windows while the
disturbance event is ACTIVE.
"""

from __future__ import annotations

import queue
import time
import uuid
from pathlib import Path
from typing import Any, Mapping, Sequence

from simulation.sumo.building.artifacts import DEFAULT_GENERATED_DIR
from simulation.sumo.engine.ai_control import AIControlConfig
from simulation.sumo.engine.session import SimulationConfig, SimulationManager, SimulationSnapshot
from traffic_eval.session_hub import SessionMetricsHub

from algorithms.traffic_llm.dataset.catalog import neighbor_map, tls_phase_orders
from algorithms.traffic_llm.dataset.episode_runner import TERMINAL_STATES, _ensure_sumo_env
from algorithms.traffic_llm.dataset.event_window import (
    compute_local_event_window_metrics,
    compute_recovery_metrics,
    resolve_local_intersection_ids,
)
from algorithms.traffic_llm.dataset.feature_builder import build_observation_v2
from algorithms.traffic_llm.dataset.io_utils import dump_json
from algorithms.traffic_llm.dataset.phase_service import load_phase_service_index
from algorithms.traffic_llm.dataset.scenario_generator import event_spec_to_disturbance
from algorithms.traffic_llm.dataset.schema import ScenarioSpec
from algorithms.traffic_llm.dataset.trace_collector import compact_snapshot_summary
from algorithms.traffic_llm.evaluation.event_lifecycle import collect_event_lifecycle
from algorithms.traffic_llm.evaluation.policy import PolicyDecision


def replan_times(event_start: float, event_end: float, interval: float = 30.0) -> list[float]:
    """Plan at event start, then every `interval` seconds while the event is ACTIVE.

    The SUMO AI executor only accepts plans while the disturbance is ACTIVE, so
    times are strictly before `event_end`. A 120–180s event yields [120, 150].
    """

    times: list[float] = []
    t = float(event_start)
    end = float(event_end)
    step = float(interval)
    while t + 1e-9 < end:
        times.append(t)
        t += step
    return times


def replan_fire_times(
    event_start: float,
    event_end: float,
    interval: float = 30.0,
    *,
    lead_seconds: float = 1.0,
) -> list[float]:
    """When to pause SUMO to install the next 30s plan.

    SUMO expires a plan at `plan_started_at + 30` and then enters baseline
    recovery, which rejects a new install. The first plan still fires at
    event start; later plans fire `lead_seconds` early so the previous window
    is still ACTIVE. A 120–180s event therefore fires at [120, 149].
    """

    scheduled = replan_times(event_start, event_end, interval)
    if not scheduled:
        return scheduled
    lead = max(0.0, float(lead_seconds))
    fired = [scheduled[0]]
    for t in scheduled[1:]:
        fired.append(max(scheduled[0], float(t) - lead))
    return fired


def _active_ai_event(snapshot: SimulationSnapshot) -> Any | None:
    for event in snapshot.events or ():
        if str(event.state) == "ACTIVE" and bool((event.details or {}).get("ai_control_enabled")):
            return event
    return None


def _wait_paused(manager: SimulationManager, session_id: str, timeout_s: float = 10.0) -> SimulationSnapshot:
    deadline = time.monotonic() + timeout_s
    snap = manager.snapshot(session_id)
    while time.monotonic() < deadline:
        if snap.state == "PAUSED":
            return snap
        if snap.state in TERMINAL_STATES:
            return snap
        time.sleep(0.02)
        snap = manager.snapshot(session_id)
    return snap


def run_closed_loop_episode(
    spec: ScenarioSpec,
    *,
    policy: Any,
    policy_name: str,
    output_dir: Path,
    scoring: Mapping[str, Any],
    generated_dir: Path | None = None,
    session_root: Path | None = None,
    wall_timeout_s: float | None = None,
) -> dict[str, Any]:
    _ensure_sumo_env()
    run_id = f"{spec.scenario_id}_{policy_name}"
    gen_dir = Path(generated_dir) if generated_dir is not None else DEFAULT_GENERATED_DIR
    sess_root = Path(session_root) if session_root is not None else (output_dir / "sessions")
    sess_root.mkdir(parents=True, exist_ok=True)
    event_id = f"{spec.scenario_id}_event"
    event = event_spec_to_disturbance(spec.event, event_id, ai_control_enabled=True)
    ai_cfg = AIControlConfig(plan_valid_seconds=30.0, slot_seconds=5.0, replan_seconds=30.0)
    config = SimulationConfig(
        intersection_ids=spec.intersection_ids,
        period=spec.period,
        scenario_preset_id=spec.scenario_preset_id,
        scenario_scope=spec.scenario_scope,
        duration_seconds=spec.duration_seconds,
        control_mode="fixed",
        decision_interval=spec.decision_interval,
        seed=spec.seed,
        step_length=spec.step_length,
        snapshot_interval_seconds=spec.snapshot_interval_seconds,
        initial_events=(event,),
        baseline_controller="fixed",
        ai_control=ai_cfg,
    )
    neighbors = neighbor_map(gen_dir)
    allowed = tls_phase_orders(gen_dir)
    phase_index = load_phase_service_index(str(gen_dir) if gen_dir else None)
    hops = 1
    plan_schedule = replan_times(spec.event.start_seconds, spec.event.end_seconds, 30.0)
    fire_schedule = replan_fire_times(
        spec.event.start_seconds,
        spec.event.end_seconds,
        30.0,
        lead_seconds=max(float(spec.snapshot_interval_seconds), 1.0),
    )
    next_idx = 0
    decisions: list[dict[str, Any]] = []
    snapshots: list[SimulationSnapshot] = []
    timeout = wall_timeout_s if wall_timeout_s is not None else max(
        300.0, float(spec.duration_seconds) * 25.0
    )
    result: dict[str, Any] = {
        "run_id": run_id,
        "scenario_id": spec.scenario_id,
        "scenario_group_id": spec.scenario_group_id,
        "control_mode": policy_name,
        "policy_name": policy_name,
        "adapter": getattr(policy, "adapter", None),
        "state": "FAILED",
        "error": None,
        "elapsed_seconds": 0.0,
        "elapsed_wall_s": 0.0,
        "session_id": "",
        "traffic_eval": {},
        "local_event_window": {},
        "recovery": {},
        "n_plans": 0,
        "n_invalid_plans": 0,
        "n_fallback": 0,
        "inference_latency_ms": [],
        "decisions": [],
        "replan_times": plan_schedule,
        "replan_fire_times": fire_schedule,
        "note": (
            "AI plans are installed only while the disturbance is ACTIVE; "
            "after event_end SUMO recovers to Fixed. This is offline policy "
            "efficacy evaluation, not a realtime deployment."
        ),
    }
    manager = SimulationManager(generated_dir=gen_dir, session_root=sess_root)
    hub = SessionMetricsHub(
        session_root=sess_root,
        traffic_manifest_path=gen_dir / "manifests" / "traffic_manifest.json",
    )
    t0 = time.perf_counter()
    session_id = ""
    subscription = None
    try:
        session_id = manager.start(config)
        hub.start_session(session_id, "fixed")
        subscription = manager.subscribe(session_id)
        result["session_id"] = session_id
        deadline = time.monotonic() + timeout
        final_snap: SimulationSnapshot | None = None
        while time.monotonic() < deadline:
            try:
                snap = subscription.get(timeout=2.0)
            except queue.Empty:
                snap = manager.snapshot(session_id)
            if snap.state in TERMINAL_STATES:
                hub.observe(snap)
                snapshots.append(snap)
                final_snap = snap
                break
            if snap.state == "RUNNING":
                hub.observe(snap)
                snapshots.append(snap)
            elapsed = float(snap.elapsed_seconds)
            due = (
                next_idx < len(fire_schedule)
                and elapsed + 1e-6 >= fire_schedule[next_idx]
                and snap.state == "RUNNING"
            )
            if due:
                manager.set_playing(session_id, False)
                paused = _wait_paused(manager, session_id)
                live_event = _active_ai_event(paused)
                target_t = plan_schedule[next_idx] if next_idx < len(plan_schedule) else fire_schedule[next_idx]
                fire_t = fire_schedule[next_idx]
                next_idx += 1
                if live_event is None:
                    event_view = [
                        {
                            "event_id": item.event_id,
                            "event_type": item.event_type,
                            "state": item.state,
                            "error": item.error,
                            "ai_control_enabled": (item.details or {}).get("ai_control_enabled"),
                        }
                        for item in (paused.events or ())
                    ]
                    decisions.append(
                        {
                            "sim_time": float(paused.elapsed_seconds),
                            "target_time": target_t,
                            "fire_time": fire_t,
                            "skipped": "event_not_active",
                            "invalid_plan": False,
                            "fallback": False,
                            "inference_latency_ms": 0.0,
                            "paused_state": paused.state,
                            "events": event_view,
                        }
                    )
                    if paused.state not in TERMINAL_STATES:
                        manager.set_playing(session_id, True)
                    continue
                hub.observe(paused)
                snapshots.append(paused)
                obs_summary = compact_snapshot_summary(paused)
                observation = build_observation_v2(
                    spec=spec,
                    simulation_time=float(paused.elapsed_seconds),
                    snapshot_summary=obs_summary,
                    allowed_phases=allowed,
                    neighbors=neighbors,
                    scope_hops=hops,
                    phase_service=phase_index,
                )
                region = list(observation.get("controlled_region") or ())
                allowed_here = {
                    iid: list(allowed.get(iid) or ())
                    for iid in region
                }
                decision: PolicyDecision = policy.generate(
                    observation,
                    allowed_phases=allowed_here,
                    allowed_region=region,
                )
                record = {
                    "sim_time": float(paused.elapsed_seconds),
                    "target_time": target_t,
                    "fire_time": fire_t,
                    "event_id": live_event.event_id,
                    "json_ok": decision.json_ok,
                    "schema_ok": decision.schema_ok,
                    "phase_ok": decision.phase_ok,
                    "region_ok": decision.region_ok,
                    "invalid_plan": decision.invalid_plan,
                    "fallback": decision.fallback,
                    "inference_latency_ms": decision.latency_ms,
                    "error": decision.error,
                    "raw_text_prefix": (decision.raw_text or "")[:240],
                    "plan": decision.plan,
                }
                result["inference_latency_ms"].append(decision.latency_ms)
                result["n_plans"] += 1
                if decision.invalid_plan:
                    result["n_invalid_plans"] += 1
                if decision.fallback:
                    result["n_fallback"] += 1
                try:
                    if decision.parsed is not None:
                        plan_sequence = int(paused.ai_takeover.plan_sequence) + 1
                        payload = {
                            "event_id": live_event.event_id,
                            "plan": decision.parsed.to_dict(),
                            "allowed_scope": region,
                            "plan_id": f"{session_id}:{live_event.event_id}:{plan_sequence}:{uuid.uuid4().hex[:8]}",
                            "plan_started_at": float(paused.elapsed_seconds),
                        }
                        manager.install_ai_plan(session_id, payload)
                        record["installed"] = True
                    else:
                        manager.fallback_ai_control(
                            session_id,
                            {
                                "event_id": live_event.event_id,
                                "reason": decision.error or "invalid_plan",
                            },
                        )
                        record["installed"] = False
                except Exception as exc:
                    record["installed"] = False
                    record["install_error"] = str(exc)
                    record["fallback"] = True
                    if not decision.fallback:
                        result["n_fallback"] += 1
                    try:
                        manager.fallback_ai_control(
                            session_id,
                            {
                                "event_id": live_event.event_id,
                                "reason": f"install_failed: {exc}",
                            },
                        )
                    except Exception:
                        pass
                decisions.append(record)
                if paused.state not in TERMINAL_STATES:
                    manager.set_playing(session_id, True)
        else:
            try:
                manager.stop(session_id)
            except Exception:
                pass
            raise TimeoutError(f"Session {session_id} timed out after {timeout:.0f}s")

        eval_result = hub.finalize(final_snap) if final_snap is not None else None
        traffic_eval = eval_result.to_dict() if eval_result is not None else {}
        local_cfg = dict(scoring.get("local_event_windows") or {})
        event_cfg = dict(scoring.get("event_windows") or {})
        recovery_cfg = dict(scoring.get("recovery") or {})
        local_ids = resolve_local_intersection_ids(
            spec.intersection_ids,
            spec.event.intersection_id,
            neighbors,
            hops=int(local_cfg.get("hops", 1)),
            small_preset_max_intersections=int(
                local_cfg.get("small_preset_max_intersections", 6)
            ),
        )
        local_event_window = compute_local_event_window_metrics(
            snapshots,
            event_start=spec.event.start_seconds,
            event_end=spec.event.end_seconds,
            offsets_seconds=tuple(
                local_cfg.get("offsets_seconds")
                or event_cfg.get("offsets_seconds")
                or (30, 60)
            ),
            include_active_span=bool(local_cfg.get("include_active_event_span", True)),
            intersection_ids=local_ids,
        )
        recovery = compute_recovery_metrics(
            snapshots,
            event_start=spec.event.start_seconds,
            event_end=spec.event.end_seconds,
            config=recovery_cfg,
        )
        n_dec = result["n_plans"]
        planned = [item for item in decisions if not item.get("skipped")]
        result.update(
            {
                "state": str(final_snap.state) if final_snap is not None else "FAILED",
                "error": str(final_snap.error) if final_snap is not None and final_snap.error else None,
                "elapsed_seconds": float(final_snap.elapsed_seconds) if final_snap is not None else 0.0,
                "elapsed_wall_s": time.perf_counter() - t0,
                "traffic_eval": traffic_eval,
                "local_event_window": local_event_window,
                "recovery": recovery,
                "decisions": decisions,
                "json_ok_rate": (
                    sum(1 for item in planned if item.get("json_ok")) / n_dec if n_dec else None
                ),
                "schema_ok_rate": (
                    sum(1 for item in planned if item.get("schema_ok")) / n_dec if n_dec else None
                ),
                "phase_ok_rate": (
                    sum(1 for item in planned if item.get("phase_ok")) / n_dec if n_dec else None
                ),
                "region_ok_rate": (
                    sum(1 for item in planned if item.get("region_ok")) / n_dec if n_dec else None
                ),
                "fallback_rate": result["n_fallback"] / n_dec if n_dec else None,
                "invalid_plan_rate": result["n_invalid_plans"] / n_dec if n_dec else None,
                "event_lifecycle": collect_event_lifecycle(snapshots),
            }
        )
        _persist(output_dir, spec, result)
        return result
    except Exception as exc:
        result["state"] = "FAILED"
        result["error"] = str(exc)
        result["elapsed_wall_s"] = time.perf_counter() - t0
        result["decisions"] = decisions
        _persist(output_dir, spec, result)
        return result
    finally:
        if subscription is not None:
            try:
                subscription.close()
            except Exception:
                pass
        if session_id:
            try:
                snap = manager.snapshot(session_id)
                if snap.state not in TERMINAL_STATES:
                    manager.stop(session_id)
            except Exception:
                pass
            try:
                manager.wait(session_id, timeout=30.0)
            except Exception:
                pass


def _persist(output_dir: Path, spec: ScenarioSpec, result: Mapping[str, Any]) -> None:
    run_id = str(result["run_id"])
    dump_json(output_dir / "runs" / f"{run_id}.json", {**dict(result), "scenario": spec.to_dict()})
    dump_json(
        output_dir / "evaluations" / f"{run_id}.json",
        {
            "run_id": run_id,
            "traffic_eval": result.get("traffic_eval") or {},
            "local_event_window": result.get("local_event_window") or {},
            "recovery": result.get("recovery") or {},
            "state": result.get("state"),
            "n_plans": result.get("n_plans"),
            "fallback_rate": result.get("fallback_rate"),
            "invalid_plan_rate": result.get("invalid_plan_rate"),
            "inference_latency_ms": result.get("inference_latency_ms"),
            "event_lifecycle": result.get("event_lifecycle") or {},
        },
    )
