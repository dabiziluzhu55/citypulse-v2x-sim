#!/usr/bin/env python3
"""List junctions from a SUMO .net.xml file to assist with ID discovery.

Usage:
    python list_junctions.py                     # 默认源路网(经 toolchain_env 解析)
    python list_junctions.py TotalMap.net.xml    # 显式指定
    python list_junctions.py --type traffic_light
    python list_junctions.py --all  # include internal junctions
"""

import argparse
import sys
import xml.etree.ElementTree as ET

import toolchain_env  # 统一源路网路径解析(config/toolchain.json totalmap_net)


def main():
    parser = argparse.ArgumentParser(
        description="List junctions from a SUMO .net.xml file."
    )
    parser.add_argument(
        "net", nargs="?", default="",
        help="Path to .net.xml file (default: config/toolchain.json totalmap_net, "
             "即 ../../data/maps/sumo/generated/network/TotalMap_20.signals.net.xml)",
    )
    parser.add_argument(
        "--type", default="traffic_light",
        help="Filter by junction type (default: traffic_light). "
             "Use 'all' for all types."
    )
    parser.add_argument(
        "--all", action="store_true",
        help="Show all junctions including internal ones."
    )
    args = parser.parse_args()
    args.net = args.net or toolchain_env.resolve_totalmap_net("")

    filter_type = None if args.type == "all" else args.type
    junctions = []
    tl_logic_ids = set()

    # ── Scan tlLogics ──
    for event, elem in ET.iterparse(args.net, events=("end",)):
        if elem.tag == "tlLogic":
            tl_logic_ids.add(elem.get("id"))
        elif elem.tag == "junction":
            jid = elem.get("id", "")
            jtype = elem.get("type", "")
            if not args.all and jtype == "internal":
                elem.clear()
                continue
            if filter_type and jtype != filter_type:
                elem.clear()
                continue
            junctions.append((
                jid,
                jtype,
                float(elem.get("x", 0)),
                float(elem.get("y", 0)),
                len(elem.get("incLanes", "").split()),
                len(elem.get("intLanes", "").split()),
            ))
        # Free XML element memory
        elem.clear()

    # ── Print ──
    print(f"Total junctions found: {len(junctions)}")
    if filter_type:
        print(f"(filtered to type='{filter_type}')\n")
    else:
        print()

    header = f"{'ID':>8s}  {'Type':<18s}  {'X (m)':>12s}  {'Y (m)':>12s}  {'incLns':>6s}  {'intLns':>6s}  tlLogic"
    print(header)
    print("-" * len(header))

    for jid, jtype, x, y, inc, inl in junctions:
        has_tl = "✓" if jid in tl_logic_ids else ""
        print(f"{jid:>8s}  {jtype:<18s}  {x:>12.2f}  {y:>12.2f}  {inc:>6d}  {inl:>6d}  {has_tl}")


if __name__ == "__main__":
    main()
