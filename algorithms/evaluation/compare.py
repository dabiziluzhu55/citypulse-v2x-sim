"""
一键对比 Multiple 算法的 6 大指标。

用法：
  python3 -m evaluation.compare \
    --tripinfo-fixed    /path/to/fixed_tripinfo.xml \
    --tripinfo-sotl     /path/to/sotl_tripinfo.xml \
    --tripinfo-maxpressure /path/to/mp_tripinfo.xml \
    --eval-duration 3600

或直接对比两个：
  python3 -m evaluation.compare \
    --a tripinfo_a.xml --a-name MaxPressure \
    --b tripinfo_b.xml --b-name FixedTime
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path
from typing import List

# 确保 algorithms/ 在 path 里
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from evaluation.metrics import (
    BenchmarkResult,
    compute_from_tripinfo,
    print_comparison_table,
    print_markdown_table,
)


def main() -> None:
    parser = argparse.ArgumentParser(description="对比信号控制算法")

    parser.add_argument("--eval-duration", type=float, default=3600.0, help="评估时长（秒）")
    parser.add_argument("--total-planned", type=int, default=0, help="计划车辆数")
    parser.add_argument("--markdown", action="store_true", help="输出 Markdown 表格")

    # 支持两种模式：
    # 模式 1：预定义三算法
    parser.add_argument("--tripinfo-maxpressure", type=str, default="", help="MaxPressure tripinfo")
    parser.add_argument("--tripinfo-sotl", type=str, default="", help="SOTL tripinfo")
    parser.add_argument("--tripinfo-fixed", type=str, default="", help="FixedTime tripinfo")
    parser.add_argument("--tripinfo-ippo", type=str, default="", help="IPPO tripinfo")

    # 模式 2：任意两两对比
    parser.add_argument("--a", type=str, default="")
    parser.add_argument("--a-name", type=str, default="A")
    parser.add_argument("--b", type=str, default="")
    parser.add_argument("--b-name", type=str, default="B")

    args = parser.parse_args()

    results: List[BenchmarkResult] = []

    # ── 预定义模式 ──
    predefined = [
        (args.tripinfo_maxpressure, "MaxPressure"),
        (args.tripinfo_sotl, "SOTL"),
        (args.tripinfo_fixed, "固定配时"),
        (args.tripinfo_ippo, "IPPO"),
    ]

    for path, name in predefined:
        if path and Path(path).exists():
            r = compute_from_tripinfo(
                path,
                eval_duration_s=args.eval_duration,
                total_planned=args.total_planned,
                algorithm=name,
            )
            results.append(r)
        elif path:
            print(f"[WARN] 文件不存在: {path}")

    # ── 两两对比模式 ──
    if args.a and Path(args.a).exists():
        r = compute_from_tripinfo(
            args.a,
            eval_duration_s=args.eval_duration,
            total_planned=args.total_planned,
            algorithm=args.a_name,
        )
        results.append(r)
    if args.b and Path(args.b).exists():
        r = compute_from_tripinfo(
            args.b,
            eval_duration_s=args.eval_duration,
            total_planned=args.total_planned,
            algorithm=args.b_name,
        )
        results.append(r)

    if not results:
        print("没有找到有效的 tripinfo 文件。")
        print("用法示例：")
        print("  python3 -m benchmark.compare --tripinfo-fixed tripinfo.xml \\")
        print("      --tripinfo-maxpressure tripinfo_mp.xml --eval-duration 3600")
        sys.exit(1)

    # ── 输出 ──
    if args.markdown:
        print(print_markdown_table(results))
    else:
        print(print_comparison_table(results))


if __name__ == "__main__":
    main()
