#!/usr/bin/env python

import argparse
import csv
import sys
import xml.etree.ElementTree as ET


def load_citypulse_junctions(root):
    result = []

    for junction in root.findall("junction"):
        params = {
            param.get("key"): param.get("value")
            for param in junction.findall("param")
        }

        if params.get("citypulse.target") != "true":
            continue

        intersection_ids = [
            item.strip()
            for item in params.get("citypulse.intersection_ids", "").split(",")
            if item.strip()
        ]

        for intersection_id in intersection_ids:
            result.append(
                {
                    "intersection_id": intersection_id,
                    "junction_id": junction.get("id"),
                    "x": junction.get("x"),
                    "y": junction.get("y"),
                    "inc_lanes": set(junction.get("incLanes", "").split()),
                }
            )

    return result


def lane_id_from_connection(connection):
    from_edge = connection.get("from")
    from_lane = connection.get("fromLane")
    if from_edge is None or from_lane is None:
        return None
    return f"{from_edge}_{from_lane}"


def inspect(net_xml, output_csv):
    tree = ET.parse(net_xml)
    root = tree.getroot()

    targets = load_citypulse_junctions(root)
    rows = []

    for connection in root.findall("connection"):
        tl_id = connection.get("tl")
        link_index = connection.get("linkIndex")
        if tl_id is None or link_index is None:
            continue

        from_lane_id = lane_id_from_connection(connection)
        if from_lane_id is None:
            continue

        for target in targets:
            if from_lane_id not in target["inc_lanes"]:
                continue

            rows.append(
                {
                    "intersection_id": target["intersection_id"],
                    "junction_id": target["junction_id"],
                    "tls_id": tl_id,
                    "linkIndex": link_index,
                    "from_edge": connection.get("from", ""),
                    "from_lane": connection.get("fromLane", ""),
                    "to_edge": connection.get("to", ""),
                    "to_lane": connection.get("toLane", ""),
                    "dir": connection.get("dir", ""),
                    "state": connection.get("state", ""),
                    "via": connection.get("via", ""),
                }
            )

    rows.sort(
        key=lambda item: (
            item["intersection_id"],
            item["junction_id"],
            item["tls_id"],
            int(item["linkIndex"]),
        )
    )

    fieldnames = [
        "intersection_id",
        "junction_id",
        "tls_id",
        "linkIndex",
        "from_edge",
        "from_lane",
        "to_edge",
        "to_lane",
        "dir",
        "state",
        "via",
    ]

    if output_csv:
        f = open(output_csv, "w", encoding="utf-8-sig", newline="")
        close_file = True
    else:
        f = sys.stdout
        close_file = False

    try:
        writer = csv.DictWriter(f, fieldnames=fieldnames)
        writer.writeheader()
        writer.writerows(rows)
    finally:
        if close_file:
            f.close()

    print(f"Found {len(rows)} controlled connections.", file=sys.stderr)


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--net",
        default="data/maps/sumo/TotalMap_20.net.xml",
        help="SUMO net.xml path",
    )
    parser.add_argument(
        "--output",
        default="data/maps/sumo/TotalMap_20_tls_mapping.csv",
        help="output csv path",
    )
    args = parser.parse_args()

    inspect(args.net, args.output)


if __name__ == "__main__":
    main()
