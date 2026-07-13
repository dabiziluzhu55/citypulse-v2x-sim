#!/usr/bin/env python

import argparse
import csv
import json
import os
import sys
import xml.etree.ElementTree as ET
from pathlib import Path

if "SUMO_HOME" in os.environ:
    sys.path.append(os.path.join(os.environ["SUMO_HOME"], "tools"))
else:
    sys.exit("please declare environment variable 'SUMO_HOME'")

import sumolib


def read_intersections(csv_file):
    for encoding in ("utf-8-sig", "utf-8", "gbk"):
        try:
            with open(csv_file, "r", encoding=encoding, newline="") as f:
                rows = list(csv.reader(f))
            break
        except UnicodeDecodeError:
            rows = None

    if rows is None:
        raise RuntimeError(f"Cannot decode csv: {csv_file}")

    result = []
    for line_no, row in enumerate(rows, start=1):
        if len(row) < 3:
            continue
        try:
            result.append(
                {
                    "id": row[0].strip(),
                    "lon": float(row[1]),
                    "lat": float(row[2]),
                }
            )
        except ValueError:
            if line_no != 1:
                raise
    return result


def load_junction_xy(net_xml):
    junctions = {}
    for _, elem in ET.iterparse(net_xml, events=("end",)):
        if elem.tag == "junction" and elem.get("id") and elem.get("x") and elem.get("y"):
            junctions[elem.get("id")] = (float(elem.get("x")), float(elem.get("y")))
        elem.clear()
    return junctions


def add_poi(root, poi_id, x, y, color, poi_type, name):
    ET.SubElement(
        root,
        "poi",
        {
            "id": poi_id,
            "type": poi_type,
            "color": color,
            "x": f"{x:.2f}",
            "y": f"{y:.2f}",
            "layer": "100",
            "width": "18",
            "height": "18",
            "name": name,
        },
    )


def add_line(root, line_id, x1, y1, x2, y2):
    ET.SubElement(
        root,
        "poly",
        {
            "id": line_id,
            "type": "citypulse_match_error",
            "color": "255,200,0",
            "fill": "false",
            "layer": "90",
            "lineWidth": "4",
            "shape": f"{x1:.2f},{y1:.2f} {x2:.2f},{y2:.2f}",
        },
    )


def indent(elem, level=0):
    spaces = "\n" + level * "  "
    child_spaces = "\n" + (level + 1) * "  "
    if len(elem):
        if not elem.text or not elem.text.strip():
            elem.text = child_spaces
        for child in elem:
            indent(child, level + 1)
        if not child.tail or not child.tail.strip():
            child.tail = spaces
    if level and (not elem.tail or not elem.tail.strip()):
        elem.tail = spaces


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--net", default="data/maps/sumo/TotalMap_20.net.xml")
    parser.add_argument("--csv", default="data/maps/osm/latitude_longitude_intersection_20.csv")
    parser.add_argument("--report", default="data/maps/sumo/TotalMap_20.intersections.json")
    parser.add_argument("--output", default="data/maps/sumo/TotalMap_20_debug_pois.add.xml")
    args = parser.parse_args()

    sumo_net = sumolib.net.readNet(args.net)
    intersections = read_intersections(args.csv)
    report = json.loads(Path(args.report).read_text(encoding="utf-8"))
    junction_xy = load_junction_xy(args.net)

    root = ET.Element("additional")

    for item in intersections:
        intersection_id = item["id"]
        csv_x, csv_y = sumo_net.convertLonLat2XY(item["lon"], item["lat"])

        add_poi(
            root,
            f"csv.{intersection_id}",
            csv_x,
            csv_y,
            "0,80,255",
            "citypulse_csv_point",
            f"{intersection_id} CSV",
        )

        if intersection_id not in report:
            continue

        junction_id = str(report[intersection_id]["junction_id"])
        if junction_id not in junction_xy:
            continue

        jx, jy = junction_xy[junction_id]
        distance = report[intersection_id].get("match_distance_m", "")

        add_poi(
            root,
            f"matched.{intersection_id}.junction.{junction_id}",
            jx,
            jy,
            "255,0,0",
            "citypulse_matched_junction",
            f"{intersection_id} matched {junction_id} {distance}m",
        )

        add_line(root, f"error_line.{intersection_id}", csv_x, csv_y, jx, jy)

    indent(root)
    output_path = Path(args.output)
    output_path.parent.mkdir(parents=True, exist_ok=True)
    ET.ElementTree(root).write(output_path, encoding="utf-8", xml_declaration=True)

    print(f"Saved debug POIs to {output_path}")


if __name__ == "__main__":
    main()