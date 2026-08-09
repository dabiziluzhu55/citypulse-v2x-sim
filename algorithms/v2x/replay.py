# algorithms/v2x/replay.py
"""回放 CLI：python -m algorithms.v2x.replay <log> [--summary|--print]。"""
from __future__ import annotations

import argparse
import json
from typing import Any


def summarize_log(path: str) -> dict:
    episodes: list[str] = []
    counts: dict[str, int] = {}
    delivered = 0
    dropped = 0
    with open(path, encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            rec = json.loads(line)
            kind = rec.get("record_type")
            if kind == "episode_start":
                episodes.append(str(rec.get("episode_id")))
            elif kind == "message":
                mtype = (rec.get("message") or {}).get("message_type", "?")
                counts[mtype] = counts.get(mtype, 0) + 1
            elif kind == "delivery":
                if rec.get("status") == "delivered":
                    delivered += 1
                else:
                    dropped += 1
    return {"episodes": episodes, "counts": counts,
            "delivered": delivered, "dropped": dropped}


def format_summary(summary: dict) -> str:
    lines = ["=== V2X log summary ===",
             f"episodes: {', '.join(summary['episodes'])}",
             "counts: " + ", ".join(
                 f"{k}={v}" for k, v in sorted(summary["counts"].items())),
             f"delivered={summary['delivered']} dropped={summary['dropped']}"]
    return "\n".join(lines)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="v2x.replay")
    parser.add_argument("log", help="path to JSONL log")
    group = parser.add_mutually_exclusive_group()
    group.add_argument("--summary", action="store_true", help="print summary only")
    group.add_argument("--print", action="store_true", dest="print_all",
                       help="print every record")
    args = parser.parse_args(argv)
    if args.print_all:
        with open(args.log, encoding="utf-8") as f:
            for line in f:
                if line.strip():
                    print(json.dumps(json.loads(line), ensure_ascii=False))
        return 0
    print(format_summary(summarize_log(args.log)))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
