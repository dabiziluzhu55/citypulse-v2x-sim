"""Run one scenario × algorithm episode via in-process SimulationManager."""

from __future__ import annotations

import os
import queue
import time
from pathlib import Path
from typing import Any, Mapping

from simulation.sumo.building.artifacts import DEFAULT_GENERATED_DIR
from simulation.sumo.engine.scenario import DEFAULT_SESSION_ROOT
from simulation.sumo.engine.session import SimulationConfig, SimulationManager, SimulationSnapshot
from traffic_control.registry import CONTROL_MODE_REGISTRY, require_control_mode
from traffic_eval.session_hub import SessionMetricsHub

from .action_parser import parse_target_phases, validate_phases_against_allowed
from .catalog import apply_model_alias_env, make_provenance, probe_control_mode, tls_phase_orders
from .event_window import compute_event_window_metrics, compute_recovery_metrics
from .io_utils import dump_json, write_jsonl_gz
from .scenario_generator import event_spec_to_disturbance
from .schema import ScenarioSpec
from .trace_collector import TraceCollector, compact_snapshot_summary

TERMINAL_STATES = frozenset({"COMPLETED", "STOPPED", "FAILED"})


def _ensure_sumo_env() -> None:
    os.environ.setdefault("SUMO_HOME", "/usr/share/sumo")
    sumo_home = Path(os.environ["SUMO_HOME"])
    sumo_bin = sumo_home / "bin"
    if sumo_bin.is_dir():
        path_entries = [
            entry
            for entry in os.environ.get("PATH", "").split(os.pathsep)
            if entry and entry != str(sumo_bin)
        ]
        os.environ["PATH"] = os.pathsep.join([*path_entries, str(sumo_bin)])


def run_id_for(scenario_id: str, control_mode: str) -> str:
    return f"{scenario_id}_{control_mode}"


def _build_config(
    spec: ScenarioSpec,
    control_mode: str,
    observer,
) -> SimulationConfig:
    mode = require_control_mode(control_mode)
    event = event_spec_to_disturbance(spec.event, event_id=f"{spec.scenario_id}_event")
    return SimulationConfig(
        intersection_ids=spec.intersection_ids,
        period=spec.period,
        scenario_preset_id=spec.scenario_preset_id,
        scenario_scope=spec.scenario_scope,
        duration_seconds=spec.duration_seconds,
        control_mode=mode.kernel_mode,
        algorithm_transport=mode.algorithm_transport or "local",
        algorithm_module=mode.algorithm_module,
        decision_interval=spec.decision_interval,
        seed=spec.seed,
        step_length=spec.step_length,
        snapshot_interval_seconds=spec.snapshot_interval_seconds,
        initial_events=(event,),
        baseline_controller=control_mode,
        algorithm_decision_observer=observer,
    )


def run_episode(
    spec: ScenarioSpec,
    control_mode: str,
    *,
    output_dir: Path,
    scoring: Mapping[str, Any],
    generated_dir: Path | None = None,
    session_root: Path | None = None,
    store_full_vehicles: bool = False,
    wall_timeout_s: float | None = None,
) -> dict[str, Any]:
    _ensure_sumo_env()
    run_id = run_id_for(spec.scenario_id, control_mode)
    gen_dir = Path(generated_dir) if generated_dir is not None else DEFAULT_GENERATED_DIR
    sess_root = Path(session_root) if session_root is not None else (output_dir / "sessions")
    sess_root.mkdir(parents=True, exist_ok=True)
    ok, reason, model_alias, checkpoint = probe_control_mode(
        control_mode, spec.scenario_preset_id, spec.intersection_ids
    )
    provenance = make_provenance(
        control_mode=control_mode,
        dataset_version=spec.dataset_version,
        scoring_version=str(scoring.get("selection_version") or ""),
        step_length=spec.step_length,
        decision_interval=spec.decision_interval,
        model_alias=model_alias,
        checkpoint_path=checkpoint,
    ).to_dict()
    result: dict[str, Any] = {
        "run_id": run_id,
        "scenario_id": spec.scenario_id,
        "scenario_group_id": spec.scenario_group_id,
        "control_mode": control_mode,
        "state": "FAILED",
        "init_ok": ok,
        "error": None if ok else reason,
        "elapsed_seconds": 0.0,
        "elapsed_wall_s": 0.0,
        "session_id": "",
        "traffic_eval": {},
        "event_window": {},
        "recovery": {},
        "teacher_action_space": "signal_only",
        "has_vehicle_actions": False,
        "has_valid_action": False,
        "illegal_phase": False,
        "control_range_error": False,
        "n_decisions": 0,
        "provenance": provenance,
        "model_alias": model_alias,
        "checkpoint_path": checkpoint,
    }
    if not ok:
        return result
    if control_mode not in CONTROL_MODE_REGISTRY:
        result["error"] = f"unknown control_mode={control_mode}"
        return result

    apply_model_alias_env(control_mode, model_alias)
    collector = TraceCollector(store_full_vehicles=store_full_vehicles)
    snapshots: list[SimulationSnapshot] = []
    last_fixed_decision_at = -1e9

    def observer(payload: Mapping[str, Any]) -> None:
        collector.on_decision(payload)

    config = _build_config(spec, control_mode, observer if control_mode != "fixed" else None)
    timeout = wall_timeout_s if wall_timeout_s is not None else max(
        180.0, float(spec.duration_seconds) * 25.0
    )
    manager = SimulationManager(generated_dir=gen_dir, session_root=sess_root)
    hub = SessionMetricsHub(
        session_root=sess_root,
        traffic_manifest_path=gen_dir / "manifests" / "traffic_manifest.json",
    )
    t0 = time.perf_counter()
    session_id = ""
    subscription = None
    allowed_phases = tls_phase_orders(gen_dir)
    allowed_phases = {
        iid: phases
        for iid, phases in allowed_phases.items()
        if iid in set(spec.intersection_ids)
    }
    try:
        session_id = manager.start(config)
        hub.start_session(session_id, control_mode)
        subscription = manager.subscribe(session_id)
        result["session_id"] = session_id
        deadline = time.monotonic() + timeout
        final_snap: SimulationSnapshot | None = None
        while time.monotonic() < deadline:
            try:
                snap = subscription.get(timeout=2.0)
            except queue.Empty:
                snap = manager.snapshot(session_id)
            if snap.state not in TERMINAL_STATES:
                hub.observe(snap)
                snapshots.append(snap)
                if control_mode == "fixed":
                    elapsed = float(snap.elapsed_seconds)
                    if elapsed + 1e-9 >= last_fixed_decision_at + spec.decision_interval:
                        collector.on_fixed_snapshot(snap, step_id=len(collector.records))
                        last_fixed_decision_at = elapsed
                continue
            hub.observe(snap)
            snapshots.append(snap)
            final_snap = snap
            break
        else:
            try:
                manager.stop(session_id)
            except Exception:
                pass
            raise TimeoutError(f"Session {session_id} timed out after {timeout:.0f}s")

        eval_result = hub.finalize(final_snap) if final_snap is not None else None
        traffic_eval = eval_result.to_dict() if eval_result is not None else {}
        trace_summary = collector.summary()
        if control_mode != "fixed":
            for record in collector.records:
                phases = parse_target_phases((record.get("actions") or {}).get("signals") or {})
                errors = validate_phases_against_allowed(phases, allowed_phases)
                if any("illegal phase" in item for item in errors):
                    result["illegal_phase"] = True
                if any("control range" in item for item in errors):
                    result["control_range_error"] = True
        event_cfg = dict(scoring.get("event_windows") or {})
        recovery_cfg = dict(scoring.get("recovery") or {})
        event_window = compute_event_window_metrics(
            snapshots,
            event_start=spec.event.start_seconds,
            event_end=spec.event.end_seconds,
            offsets_seconds=tuple(event_cfg.get("offsets_seconds") or (30, 60)),
            include_active_span=bool(event_cfg.get("include_active_event_span", True)),
        )
        recovery = compute_recovery_metrics(
            snapshots,
            event_start=spec.event.start_seconds,
            event_end=spec.event.end_seconds,
            config=recovery_cfg,
        )
        result.update(
            {
                "state": str(final_snap.state) if final_snap is not None else "FAILED",
                "error": str(final_snap.error) if final_snap is not None and final_snap.error else None,
                "elapsed_seconds": float(final_snap.elapsed_seconds) if final_snap is not None else 0.0,
                "elapsed_wall_s": time.perf_counter() - t0,
                "traffic_eval": traffic_eval,
                "event_window": event_window,
                "recovery": recovery,
                "teacher_action_space": trace_summary["teacher_action_space"],
                "has_vehicle_actions": trace_summary["has_vehicle_actions"],
                "has_valid_action": trace_summary["has_valid_action"] or control_mode == "fixed",
                "n_decisions": trace_summary["n_decisions"],
            }
        )
        if result["state"] != "COMPLETED":
            result["state"] = str(final_snap.state) if final_snap is not None else "FAILED"
        _persist_run(output_dir, spec, result, collector, snapshots)
        return result
    except Exception as exc:
        result["state"] = "FAILED"
        result["error"] = str(exc)
        result["elapsed_wall_s"] = time.perf_counter() - t0
        _persist_run(output_dir, spec, result, collector, snapshots)
        return result
    finally:
        if subscription is not None:
            try:
                subscription.close()
            except Exception:
                pass


def _persist_run(
    output_dir: Path,
    spec: ScenarioSpec,
    result: dict[str, Any],
    collector: TraceCollector,
    snapshots: list[SimulationSnapshot],
) -> None:
    run_id = result["run_id"]
    dump_json(output_dir / "runs" / f"{run_id}.json", {**result, "scenario": spec.to_dict()})
    dump_json(
        output_dir / "evaluations" / f"{run_id}.json",
        {
            "run_id": run_id,
            "traffic_eval": result.get("traffic_eval") or {},
            "event_window": result.get("event_window") or {},
            "recovery": result.get("recovery") or {},
            "state": result.get("state"),
        },
    )
    write_jsonl_gz(
        output_dir / "traces" / f"{run_id}.jsonl.gz",
        collector.records,
    )
    compact_snaps = [
        {
            "elapsed_seconds": snap.elapsed_seconds,
            "state": snap.state,
            "summary": compact_snapshot_summary(snap),
        }
        for snap in snapshots
    ]
    write_jsonl_gz(output_dir / "traces" / f"{run_id}.snapshots.jsonl.gz", compact_snaps)
