"""无Backend的本机评估命令行入口

用法（仓库根目录）：
  python -m traffic_eval \\
    --preset xiongan_20 --period morning_peak --duration 900 \\
    --modes fixed,max_pressure,sotl --seed 42 \\
    --output outputs/eval_900_local.json

前置：
  1. 已构建SUMO产物（build_tls / build_traffic）
  2. SUMO_HOME已设置，且可import libsumo
  3. 不需要启动uvicorn/Redis

"""

from __future__ import annotations

import argparse
import json
import sys
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from traffic_control.registry import CONTROL_MODE_REGISTRY

from .runner import (
    LocalEvalRunResult,
    load_preset_intersection_ids,
    print_progress,
    project_paths,
    run_local_episode,
)

DEFAULT_MODES = ("fixed", "max_pressure", "sotl")
ALLOWED_MODES = frozenset(CONTROL_MODE_REGISTRY)
DEFAULT_STEP_LENGTH = 0.1
DEFAULT_SNAPSHOT_INTERVAL = 0.5

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


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description=(
            "本机评估traffic_control管控算法（不启动Backend）"
        ),
    )
    parser.add_argument(
        "--preset",
        default="xiongan_20",
        help="场景预设ID（默认 xiongan_20）",
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
        help="仿真时长（秒）",
    )
    parser.add_argument(
        "--seed",
        type=int,
        default=42,
        help="随机种子",
    )
    parser.add_argument(
        "--modes",
        default=",".join(DEFAULT_MODES),
        help=f"逗号分隔 control_mode；允许 {','.join(sorted(ALLOWED_MODES))}",
    )
    parser.add_argument(
        "--model-alias",
        default=None,
        help="ippo/mappo模型别名",
    )
    parser.add_argument(
        "--snapshot-interval",
        type=float,
        default=DEFAULT_SNAPSHOT_INTERVAL,
        help=f"快照间隔（仿真秒），默认 {DEFAULT_SNAPSHOT_INTERVAL}",
    )
    parser.add_argument(
        "--step-length",
        type=float,
        default=DEFAULT_STEP_LENGTH,
        help=f"SUMO步长（秒），默认 {DEFAULT_STEP_LENGTH}",
    )
    parser.add_argument(
        "--decision-interval",
        type=float,
        default=5.0,
        help="算法决策周期（秒），默认5.0",
    )
    parser.add_argument(
        "--timeout",
        type=float,
        default=0.0,
        help="单次仿真墙钟超时；0表示duration*20",
    )
    parser.add_argument(
        "--output",
        type=Path,
        default=None,
        help="将完整结果写入json",
    )
    parser.add_argument(
        "--markdown",
        action="store_true",
        help="以markdown表格输出",
    )
    return parser.parse_args(argv)


def _parse_modes(raw: str) -> list[str]:
    modes = [item.strip() for item in raw.split(",") if item.strip()]
    if not modes:
        raise SystemExit("--modes不能为空")
    unknown = [m for m in modes if m not in ALLOWED_MODES]
    if unknown:
        raise SystemExit(
            f"不支持的control_mode: {unknown}；允许 "
            f"{'/'.join(sorted(ALLOWED_MODES))}"
        )
    return modes


def _fmt(value: Any) -> str:
    if value is None:
        return "-"
    if isinstance(value, float):
        return f"{value:.2f}"
    return str(value)


def print_table(results: list[LocalEvalRunResult], *, markdown: bool = False) -> None:
    rows: list[dict[str, Any]] = []
    for item in results:
        row = {"algorithm": item.metrics.get("algorithm") or item.control_mode}
        for key, _ in METRIC_COLUMNS[1:]:
            row[key] = item.metrics.get(key, "-")
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


def write_output(
    path: Path, args: argparse.Namespace, results: list[LocalEvalRunResult]
) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    paths = project_paths()
    payload = {
        "created_at": datetime.now(timezone.utc).isoformat(),
        "runner": "traffic_eval.eval_cli",
        "backend_required": False,
        "config": {
            "preset": args.preset,
            "period": args.period,
            "duration_seconds": args.duration,
            "seed": args.seed,
            "modes": _parse_modes(args.modes),
            "model_alias": args.model_alias,
            "snapshot_interval_seconds": args.snapshot_interval,
            "step_length": args.step_length,
            "decision_interval": args.decision_interval,
            "realtime": False,
            "gui": False,
            "sumo_backend": "libsumo",
            "generated_dir": str(paths["generated_dir"]),
            "session_root": str(paths["session_root"]),
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
        raise SystemExit("--step-length必须大于0")
    if args.snapshot_interval <= 0:
        raise SystemExit("--snapshot-interval必须大于0")
    if args.snapshot_interval + 1e-9 < args.step_length:
        raise SystemExit("--snapshot-interval不能小于--step-length")

    try:
        intersection_ids = load_preset_intersection_ids(args.preset)
    except ValueError as exc:
        raise SystemExit(str(exc)) from exc

    for mode in modes:
        spec = CONTROL_MODE_REGISTRY[mode]
        if not spec.allows_preset(args.preset):
            raise SystemExit(
                f"control_mode={mode!r} 不支持preset={args.preset!r}；"
                f"允许 {spec.supported_presets}"
            )

    paths = project_paths()
    print("本机评估")
    print(f"  preset={args.preset} intersections={len(intersection_ids)}")
    print(f"  period={args.period} duration={args.duration}s seed={args.seed}")
    print(
        f"  step_length={args.step_length}s "
        f"snapshot_interval={args.snapshot_interval}s"
    )
    print(f"  modes={','.join(modes)}")
    print(f"  generated_dir={paths['generated_dir']}")
    print(f"  session_root={paths['session_root']}")
    if not paths["traffic_manifest"].is_file():
        print(
            f" 警告: 缺少{paths['traffic_manifest']}，燃油强度可能为null"
        )

    wall_timeout = args.timeout if args.timeout > 0 else None
    results: list[LocalEvalRunResult] = []
    for mode in modes:
        print(f"\n=== 启动 control_mode={mode} ===", flush=True)
        result = run_local_episode(
            control_mode=mode,
            intersection_ids=intersection_ids,
            period=args.period,
            duration_seconds=args.duration,
            seed=args.seed,
            step_length=args.step_length,
            snapshot_interval_seconds=args.snapshot_interval,
            decision_interval=args.decision_interval,
            model_alias=args.model_alias,
            wall_timeout_s=wall_timeout,
            on_progress=print_progress,
        )
        # 进度行在 stderr 用 \\r 刷新；结束后换行再打摘要
        print(file=sys.stderr, flush=True)
        print(
            f"  session_id={result.session_id or '-'} "
            f"state={result.state} wall={result.elapsed_wall_s:.1f}s",
            flush=True,
        )
        if result.error:
            print(f"  error={result.error}", flush=True)
        results.append(result)
        time.sleep(0.5)

    print_table(results, markdown=args.markdown)
    if args.output is not None:
        write_output(args.output, args, results)

    if any(r.state == "FAILED" or r.error for r in results):
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
