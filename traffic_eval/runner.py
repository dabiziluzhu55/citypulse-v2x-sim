"""无Backend：直连SimulationManager+traffic_control+traffic_eval"""

from __future__ import annotations

import os
import queue
import sys
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable, Mapping, Optional

from simulation.sumo.building.artifacts import DEFAULT_GENERATED_DIR, PROJECT_ROOT
from simulation.sumo.engine.scenario import DEFAULT_SESSION_ROOT
from simulation.sumo.engine.session import SimulationConfig, SimulationManager, SimulationSnapshot
from traffic_control.registry import CONTROL_MODE_REGISTRY, require_control_mode

from .session_hub import SessionMetricsHub

TERMINAL_STATES = frozenset({"COMPLETED", "STOPPED", "FAILED"})

ProgressCallback = Callable[[SimulationSnapshot], None]


@dataclass(frozen=True)
class LocalEvalRunResult:
    control_mode: str
    session_id: str
    state: str
    elapsed_wall_s: float
    metrics: dict[str, Any]
    error: str | None = None


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


def _apply_model_alias_env(control_mode: str, model_alias: str | None) -> None:
    if not model_alias:
        return
    if control_mode == "ippo":
        os.environ["IPPO_MODEL_ALIAS"] = model_alias
    elif control_mode == "mappo":
        os.environ["MAPPO_MODEL_ALIAS"] = model_alias


def build_simulation_config(
    *,
    intersection_ids: tuple[str, ...],
    period: str,
    duration_seconds: float,
    control_mode: str,
    seed: int,
    step_length: float,
    snapshot_interval_seconds: float,
    decision_interval: float = 5.0,
) -> SimulationConfig:
    spec = require_control_mode(control_mode)
    if not spec.allows_preset:  # pragma: no cover - always true for registry entries
        pass
    return SimulationConfig(
        intersection_ids=intersection_ids,
        period=period,
        window_start_seconds=0.0,
        duration_seconds=float(duration_seconds),
        flow_multiplier=1.0,
        control_mode=spec.kernel_mode,
        algorithm_transport=spec.algorithm_transport or "local",
        algorithm_module=spec.algorithm_module,
        decision_interval=float(decision_interval),
        seed=int(seed),
        step_length=float(step_length),
        gui=False,
        realtime=False,
        snapshot_interval_seconds=float(snapshot_interval_seconds),
    )


def format_progress_line(snap: SimulationSnapshot) -> str:
    """单行进度：百分比 + SUMO仿真秒 + 官方时钟"""

    duration = float(snap.duration_seconds) if snap.duration_seconds else 0.0
    elapsed = float(snap.elapsed_seconds)
    if snap.progress is not None:
        pct = float(snap.progress) * 100.0
    elif duration > 0:
        pct = min(100.0, elapsed / duration * 100.0)
    else:
        pct = 0.0
    sid = (snap.session_id or "")[:8] or "-"
    clock = snap.official_time or "-"
    if duration > 0:
        time_part = f"{elapsed:.1f}/{duration:.0f}s"
    else:
        time_part = f"{elapsed:.1f}s"
    return (
        f"  [{sid}] {snap.state}  {pct:5.1f}%  "
        f"sim={time_part}  clock={clock}"
    )


def print_progress(snap: SimulationSnapshot, *, stream=None) -> None:
    """覆盖同行刷新进度（适合TTY）；结束态会由调用方换行"""

    out = sys.stderr if stream is None else stream
    line = format_progress_line(snap)
    # 留尾部空格清掉上一帧更长内容
    print(f"\r{line:<100}", end="", file=out, flush=True)


def run_local_episode(
    *,
    control_mode: str,
    intersection_ids: tuple[str, ...],
    period: str,
    duration_seconds: float,
    seed: int = 42,
    step_length: float = 0.1,
    snapshot_interval_seconds: float = 0.5,
    decision_interval: float = 5.0,
    model_alias: str | None = None,
    generated_dir: Path | None = None,
    session_root: Path | None = None,
    traffic_manifest_path: Path | None = None,
    poll_timeout_s: float = 2.0,
    wall_timeout_s: float | None = None,
    on_progress: ProgressCallback | None = None,
    progress_interval_s: float = 0.5,
) -> LocalEvalRunResult:
    """跑一轮仿真并用traffic_eval结算指标（不启FastAPI/Redis）"""

    _ensure_sumo_env()
    if control_mode not in CONTROL_MODE_REGISTRY:
        raise ValueError(
            f"Unsupported control_mode={control_mode!r}; "
            f"allowed={sorted(CONTROL_MODE_REGISTRY)}"
        )
    require_control_mode(control_mode)

    gen_dir = Path(generated_dir) if generated_dir is not None else DEFAULT_GENERATED_DIR
    sess_root = Path(session_root) if session_root is not None else DEFAULT_SESSION_ROOT
    manifest = (
        Path(traffic_manifest_path)
        if traffic_manifest_path is not None
        else gen_dir / "manifests" / "traffic_manifest.json"
    )
    timeout = (
        float(wall_timeout_s)
        if wall_timeout_s is not None
        else max(300.0, float(duration_seconds) * 20.0)
    )

    _apply_model_alias_env(control_mode, model_alias)

    manager = SimulationManager(generated_dir=gen_dir, session_root=sess_root)
    hub = SessionMetricsHub(
        session_root=sess_root,
        traffic_manifest_path=manifest if manifest.is_file() else None,
    )
    config = build_simulation_config(
        intersection_ids=intersection_ids,
        period=period,
        duration_seconds=duration_seconds,
        control_mode=control_mode,
        seed=seed,
        step_length=step_length,
        snapshot_interval_seconds=snapshot_interval_seconds,
        decision_interval=decision_interval,
    )

    t0 = time.perf_counter()
    session_id = ""
    subscription = None
    last_progress_at = 0.0
    try:
        session_id = manager.start(config)
        hub.start_session(session_id, control_mode)
        subscription = manager.subscribe(session_id)
        deadline = time.monotonic() + timeout
        final_snap: Optional[SimulationSnapshot] = None

        while time.monotonic() < deadline:
            try:
                snap = subscription.get(timeout=poll_timeout_s)
            except queue.Empty:
                snap = manager.snapshot(session_id)
                if snap.state not in TERMINAL_STATES:
                    if on_progress is not None:
                        now = time.monotonic()
                        if now - last_progress_at >= progress_interval_s:
                            on_progress(snap)
                            last_progress_at = now
                    continue

            if snap.state not in TERMINAL_STATES:
                hub.observe(snap)
                if on_progress is not None:
                    now = time.monotonic()
                    if now - last_progress_at >= progress_interval_s:
                        on_progress(snap)
                        last_progress_at = now
                continue

            final_snap = snap
            if on_progress is not None:
                on_progress(snap)
            break
        else:
            try:
                manager.stop(session_id)
            except Exception:
                pass
            raise TimeoutError(
                f"Session {session_id} did not finish within {timeout:.0f}s"
            )

        assert final_snap is not None
        result = hub.finalize(final_snap)
        payload = result.to_frontend_metrics()
        payload["episode_id"] = session_id
        payload["finished"] = True
        if not payload.get("algorithm"):
            payload["algorithm"] = control_mode

        return LocalEvalRunResult(
            control_mode=control_mode,
            session_id=session_id,
            state=str(final_snap.state),
            elapsed_wall_s=time.perf_counter() - t0,
            metrics=payload,
            error=str(final_snap.error) if final_snap.error else None,
        )
    except Exception as exc:
        if session_id:
            try:
                manager.stop(session_id)
            except Exception:
                pass
        return LocalEvalRunResult(
            control_mode=control_mode,
            session_id=session_id or "",
            state="FAILED",
            elapsed_wall_s=time.perf_counter() - t0,
            metrics={"algorithm": control_mode},
            error=str(exc),
        )
    finally:
        if subscription is not None:
            try:
                subscription.close()
            except Exception:
                pass


def load_preset_intersection_ids(preset_id: str) -> tuple[str, ...]:
    """读取与Backend一致的场景预设"""

    from backend.app.scenario.presets import require_scenario_preset

    return require_scenario_preset(preset_id).intersection_ids


def project_paths() -> Mapping[str, Path]:
    return {
        "project_root": PROJECT_ROOT,
        "generated_dir": DEFAULT_GENERATED_DIR,
        "session_root": DEFAULT_SESSION_ROOT,
        "traffic_manifest": DEFAULT_GENERATED_DIR / "manifests" / "traffic_manifest.json",
    }
