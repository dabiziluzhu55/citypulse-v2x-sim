"""后端信号管控算法命令行评估脚本（Backend API + /metrics）

通过 POST /api/v1/simulations 启动 SUMO（无头默认 libsumo），结束后读取
GET .../metrics 做对比。

若**不想启动后端**，请改用本机入口（同一 traffic_eval 口径）：
  python -m traffic_eval \\
    --preset xiongan_20 --period morning_peak --duration 900 \\
    --modes fixed,max_pressure,sotl --seed 42 \\
    --output outputs/eval_900_local.json

前置（本 HTTP 脚本）：
  1. 已构建 SUMO 产物（build_tls / build_traffic）
  2. 已设置 SUMO_HOME，且可用：python -c "import libsumo; import sumolib"
  3. 后端已启动（workers=1），例如：
       cd <repo-root>
       PYTHONPATH=. uvicorn backend.app.main:app --host 0.0.0.0 --port 8000 --workers 1

用法（在仓库根目录）：
  python backend/tools/eval.py
  python backend/tools/eval.py \\
    --preset xiongan_20 --period morning_peak --duration 900 \\
    --modes fixed,max_pressure,sotl,ippo,mappo --seed 42 \\
    --output outputs/eval_results.json

  - 同一时间只能有一个活动仿真会话，模式之间串行执行
  - realtime=false 以加速批跑；gui 默认关闭（走 libsumo，勿开 TraCI/GUI）
  - 默认步长 0.1s、快照间隔 0.5s，与当前 simulation 内核一致
"""

from __future__ import annotations

import argparse
import json
import sys
import time
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import httpx

DEFAULT_BASE_URL = "http://127.0.0.1:8000/api/v1"
# 与 traffic_control.registry / simulation 默认对齐
DEFAULT_MODES = ("fixed", "max_pressure", "sotl", "ippo", "mappo")
ALLOWED_MODES = frozenset(DEFAULT_MODES)
DEFAULT_STEP_LENGTH = 0.1
DEFAULT_SNAPSHOT_INTERVAL = 0.5
TERMINAL_STATES = frozenset({"COMPLETED", "STOPPED", "FAILED"})

METRIC_COLUMNS = (
    ("algorithm", "算法"),
    ("avg_waiting_time", "平均等待(s)"),
    ("avg_travel_time", "平均行程(s)"),
    ("avg_queue_length", "平均排队(辆)"),
    ("throughput", "吞吐(辆/h)"),
    ("fuel_consumption", "油耗强度(L/100km)"),
    ("hard_braking_events", "急刹车事件数"),
    ("hard_braking_rate", "急刹车率(次/100辆)"),
    ("avg_decision_latency_ms", "决策延迟(ms)"),
    ("departed", "出发"),
    ("arrived", "到达"),
)


@dataclass
class RunResult:
    control_mode: str
    session_id: str
    state: str
    elapsed_wall_s: float
    metrics: dict[str, Any] = field(default_factory=dict)
    error: str | None = None


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description=(
            "评估 backend 管控算法交通指标："
            "fixed / max_pressure / sotl / ippo / mappo"
        ),
    )
    parser.add_argument(
        "--base-url",
        default=DEFAULT_BASE_URL,
        help=f"Backend API 前缀（默认 {DEFAULT_BASE_URL}）",
    )
    parser.add_argument(
        "--preset",
        default="xiongan_20",
        help="场景预设 ID（默认 xiongan_20 = 20 路口）",
    )
    parser.add_argument(
        "--period",
        default="morning_peak",
        choices=("morning_peak", "off_peak", "evening_peak"),
        help="交通时段",
    )
    parser.add_argument(
        "--duration",
        type=float,
        default=900.0,
        help="仿真时长（秒），默认 900",
    )
    parser.add_argument(
        "--seed",
        type=int,
        default=42,
        help="随机种子（各模式共用，保证需求一致）",
    )
    parser.add_argument(
        "--modes",
        default=",".join(DEFAULT_MODES),
        help=(
            "逗号分隔的 control_mode 列表；"
            f"允许 {','.join(sorted(ALLOWED_MODES))}"
        ),
    )
    parser.add_argument(
        "--model-alias",
        default=None,
        help="ippo/mappo 模型别名；缺省由场景预设解析默认通用模型",
    )
    parser.add_argument(
        "--snapshot-interval",
        type=float,
        default=DEFAULT_SNAPSHOT_INTERVAL,
        help=(
            f"快照间隔（仿真秒）；默认 {DEFAULT_SNAPSHOT_INTERVAL}，"
            "与 simulation 内核一致"
        ),
    )
    parser.add_argument(
        "--step-length",
        type=float,
        default=DEFAULT_STEP_LENGTH,
        help=f"SUMO 步长（秒）；默认 {DEFAULT_STEP_LENGTH}，与当前仿真内核一致",
    )
    parser.add_argument(
        "--poll-interval",
        type=float,
        default=2.0,
        help="状态轮询间隔（秒）",
    )
    parser.add_argument(
        "--timeout",
        type=float,
        default=0.0,
        help="单次仿真墙钟超时（秒）；0 表示按 duration*20 自动估算",
    )
    parser.add_argument(
        "--output",
        type=Path,
        default=None,
        help="将完整结果写入 JSON 文件",
    )
    parser.add_argument(
        "--markdown",
        action="store_true",
        help="以 Markdown 表格输出对比结果",
    )
    return parser.parse_args(argv)


def _parse_modes(raw: str) -> list[str]:
    modes = [item.strip() for item in raw.split(",") if item.strip()]
    if not modes:
        raise SystemExit("--modes 不能为空")
    unknown = [m for m in modes if m not in ALLOWED_MODES]
    if unknown:
        raise SystemExit(
            f"不支持的 control_mode: {unknown}；允许 "
            f"{'/'.join(sorted(ALLOWED_MODES))}"
        )
    return modes


def check_health(client: httpx.Client) -> dict[str, Any]:
    response = client.get("/health")
    response.raise_for_status()
    payload = response.json()
    if payload.get("status") != "ok":
        raise RuntimeError(
            f"Backend 未就绪: {payload}. "
            "请确认 SUMO_HOME、libsumo、generated artifacts，"
            "并以 --workers 1 启动后端。"
        )
    return payload


def _start_payload(args: argparse.Namespace, control_mode: str) -> dict[str, Any]:
    payload: dict[str, Any] = {
        "scenario_preset_id": args.preset,
        "period": args.period,
        "origins": {},
        "window_start_seconds": 0.0,
        "duration_seconds": args.duration,
        "control_mode": control_mode,
        "seed": args.seed,
        "step_length": args.step_length,
        "realtime": False,
        # gui=false → simulation 走 libsumo；gui=true 才走 TraCI/sumo-gui 调试路径
        "gui": False,
        "snapshot_interval_seconds": args.snapshot_interval,
        "disturbance_targets": [],
    }
    if args.model_alias and control_mode in {"ippo", "mappo"}:
        payload["model_alias"] = args.model_alias
    return payload


def start_simulation(
    client: httpx.Client,
    payload: dict[str, Any],
    *,
    poll_interval: float,
    busy_timeout_s: float,
) -> dict[str, Any]:
    deadline = time.monotonic() + busy_timeout_s
    last_error: str | None = None
    while time.monotonic() < deadline:
        response = client.post("/simulations", json=payload)
        if response.status_code in (200, 201):
            return response.json()
        if response.status_code == 409:
            last_error = response.text
            print(f"  [等待] 已有活动会话，{poll_interval:.1f}s 后重试…")
            time.sleep(poll_interval)
            continue
        detail = response.text
        raise RuntimeError(f"启动仿真失败 HTTP {response.status_code}: {detail}")
    raise TimeoutError(f"等待活动会话释放超时: {last_error}")


def wait_for_terminal(
    client: httpx.Client,
    session_id: str,
    *,
    poll_interval: float,
    timeout_s: float,
) -> dict[str, Any]:
    deadline = time.monotonic() + timeout_s
    last: dict[str, Any] = {}
    while time.monotonic() < deadline:
        response = client.get(f"/simulations/{session_id}")
        response.raise_for_status()
        last = response.json()
        state = str(last.get("state", ""))
        progress = float(last.get("progress", 0.0) or 0.0)
        elapsed = float(last.get("elapsed_seconds", 0.0) or 0.0)
        print(
            f"  [{session_id[:8]}] state={state} "
            f"elapsed={elapsed:.1f}s progress={progress * 100:.1f}%",
            end="\r",
            flush=True,
        )
        if state in TERMINAL_STATES:
            print()
            return last
        time.sleep(poll_interval)
    print()
    raise TimeoutError(
        f"会话 {session_id} 在 {timeout_s:.0f}s 内未结束；最后状态={last.get('state')}"
    )


def fetch_metrics(
    client: httpx.Client,
    session_id: str,
    *,
    retries: int = 10,
    delay_s: float = 0.5,
) -> dict[str, Any]:
    """终态后指标 watcher 可能尚未 finalize，短暂重试直到 finished=true"""
    last: dict[str, Any] = {}
    for _ in range(retries):
        response = client.get(f"/simulations/{session_id}/metrics")
        response.raise_for_status()
        last = response.json()
        if last.get("finished"):
            return last
        time.sleep(delay_s)
    return last


def run_one(
    client: httpx.Client,
    args: argparse.Namespace,
    control_mode: str,
) -> RunResult:
    timeout_s = args.timeout if args.timeout > 0 else max(300.0, args.duration * 20.0)
    payload = _start_payload(args, control_mode)
    print(f"\n=== 启动 control_mode={control_mode} ===")
    print(
        f"  preset={args.preset} period={args.period} "
        f"duration={args.duration}s seed={args.seed} "
        f"step_length={args.step_length}s "
        f"snapshot_interval={args.snapshot_interval}s"
    )
    if args.model_alias and control_mode in {"ippo", "mappo"}:
        print(f"  model_alias={args.model_alias}")

    t0 = time.perf_counter()
    created = start_simulation(
        client,
        payload,
        poll_interval=args.poll_interval,
        busy_timeout_s=min(120.0, timeout_s),
    )
    session_id = str(created["session_id"])
    print(f"  session_id={session_id}")

    try:
        status = wait_for_terminal(
            client,
            session_id,
            poll_interval=args.poll_interval,
            timeout_s=timeout_s,
        )
    except Exception as exc:
        try:
            client.post(f"/simulations/{session_id}/stop")
        except Exception:
            pass
        return RunResult(
            control_mode=control_mode,
            session_id=session_id,
            state="FAILED",
            elapsed_wall_s=time.perf_counter() - t0,
            error=str(exc),
        )

    state = str(status.get("state", ""))
    error = status.get("error")
    metrics: dict[str, Any] = {}
    if state != "FAILED":
        metrics = fetch_metrics(client, session_id)
        if not metrics.get("algorithm"):
            metrics["algorithm"] = control_mode
    else:
        metrics = {"algorithm": control_mode}

    wall = time.perf_counter() - t0
    print(f"  完成: state={state} wall={wall:.1f}s")
    if error:
        print(f"  error={error}")
    return RunResult(
        control_mode=control_mode,
        session_id=session_id,
        state=state,
        elapsed_wall_s=wall,
        metrics=metrics,
        error=str(error) if error else None,
    )


def _fmt(value: Any) -> str:
    if value is None:
        return "-"
    if isinstance(value, float):
        return f"{value:.2f}"
    return str(value)


def print_table(results: list[RunResult], *, markdown: bool = False) -> None:
    rows: list[dict[str, Any]] = []
    for item in results:
        row = {"algorithm": item.metrics.get("algorithm") or item.control_mode}
        for key, _ in METRIC_COLUMNS[1:]:
            row[key] = item.metrics.get(key, "-")
        row["_state"] = item.state
        row["_error"] = item.error or ""
        rows.append(row)

    headers = [label for _, label in METRIC_COLUMNS]
    keys = [key for key, _ in METRIC_COLUMNS]

    if markdown:
        print("| " + " | ".join(headers) + " |")
        print("| " + " | ".join("---" for _ in headers) + " |")
        for row in rows:
            print("| " + " | ".join(_fmt(row.get(k)) for k in keys) + " |")
        return

    widths = [len(h) for h in headers]
    rendered = [[_fmt(row.get(k)) for k in keys] for row in rows]
    for cells in rendered:
        for i, cell in enumerate(cells):
            widths[i] = max(widths[i], len(cell))

    def line(cells: list[str]) -> str:
        return "  ".join(cell.ljust(widths[i]) for i, cell in enumerate(cells))

    print("\n======== 评估对比 ========")
    print(line(headers))
    print(line(["-" * w for w in widths]))
    for cells in rendered:
        print(line(cells))

    failed = [r for r in results if r.state == "FAILED" or r.error]
    if failed:
        print("\n失败明细:")
        for item in failed:
            print(f"  - {item.control_mode}: state={item.state} error={item.error}")


def write_output(path: Path, args: argparse.Namespace, results: list[RunResult]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    payload = {
        "created_at": datetime.now(timezone.utc).isoformat(),
        "config": {
            "base_url": args.base_url,
            "preset": args.preset,
            "period": args.period,
            "duration_seconds": args.duration,
            "seed": args.seed,
            "modes": _parse_modes(args.modes),
            "model_alias": args.model_alias,
            "snapshot_interval_seconds": args.snapshot_interval,
            "step_length": args.step_length,
            "realtime": False,
            "gui": False,
            "sumo_backend": "libsumo",
        },
        "results": [
            {
                "control_mode": r.control_mode,
                "session_id": r.session_id,
                "state": r.state,
                "elapsed_wall_s": round(r.elapsed_wall_s, 2),
                "error": r.error,
                "metrics": r.metrics,
            }
            for r in results
        ],
    }
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"\n结果已写入: {path}")


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    modes = _parse_modes(args.modes)
    if args.step_length <= 0:
        raise SystemExit("--step-length 必须 > 0")
    if args.snapshot_interval <= 0:
        raise SystemExit("--snapshot-interval 必须 > 0")
    if args.snapshot_interval + 1e-9 < args.step_length:
        raise SystemExit("--snapshot-interval 不能小于 --step-length")

    base = args.base_url.rstrip("/")

    # 单次仿真可能很长：timeout 覆盖启动 + 轮询
    http_timeout = httpx.Timeout(30.0, read=60.0)
    with httpx.Client(base_url=base, timeout=http_timeout) as client:
        print(f"检查后端健康: {base}/health")
        health = check_health(client)
        print(f"  health={health.get('status')} artifacts_ok")
        print(
            f"  eval defaults: step_length={args.step_length}s "
            f"snapshot_interval={args.snapshot_interval}s "
            f"gui=false(libsumo) modes={','.join(modes)}"
        )

        results: list[RunResult] = []
        for mode in modes:
            result = run_one(client, args, mode)
            results.append(result)
            # 终态后稍等，避免下一轮启动撞上清理窗口
            time.sleep(1.0)

    print_table(results, markdown=args.markdown)
    if args.output is not None:
        write_output(args.output, args, results)

    if any(r.state == "FAILED" or r.error for r in results):
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
