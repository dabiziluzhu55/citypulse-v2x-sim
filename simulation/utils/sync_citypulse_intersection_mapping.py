#!/usr/bin/env python

import argparse
import csv
import json
import math
import os
import shutil
import sys
from datetime import datetime
from pathlib import Path
import xml.etree.ElementTree as ET


CITYPULSE_PARAM_PREFIX = "citypulse."


def load_sumolib():
    sumo_home = os.environ.get("SUMO_HOME")
    if not sumo_home:
        raise RuntimeError("please declare environment variable 'SUMO_HOME'")

    tools_path = os.path.join(sumo_home, "tools")
    if tools_path not in sys.path:
        sys.path.append(tools_path)

    import sumolib  # pylint: disable=import-error,import-outside-toplevel

    return sumolib


def read_intersections(csv_file):
    rows = None
    last_error = None

    for encoding in ("utf-8-sig", "utf-8", "gbk"):
        try:
            with open(csv_file, "r", encoding=encoding, newline="") as f:
                rows = list(csv.reader(f))
            break
        except UnicodeDecodeError as exc:
            last_error = exc

    if rows is None:
        raise RuntimeError(f"Cannot decode csv: {csv_file}") from last_error

    result = {}
    for line_no, row in enumerate(rows, start=1):
        if len(row) < 3:
            continue

        intersection_id = row[0].strip()
        try:
            lon = float(row[1])
            lat = float(row[2])
        except ValueError:
            if line_no == 1:
                continue
            raise RuntimeError(f"CSV line {line_no} has invalid lon/lat: {row}")

        if intersection_id in result:
            raise RuntimeError(f"Duplicate intersection id in CSV: {intersection_id}")

        result[intersection_id] = {
            "id": intersection_id,
            "lon": lon,
            "lat": lat,
        }

    return result


def load_report(report_file):
    path = Path(report_file)
    if not path.exists():
        return {}
    return json.loads(path.read_text(encoding="utf-8"))


def get_citypulse_params(junction):
    return {
        param.get("key"): param.get("value")
        for param in junction.findall("param")
        if param.get("key")
    }


def get_intersection_ids(junction):
    params = get_citypulse_params(junction)
    return [
        item.strip()
        for item in params.get("citypulse.intersection_ids", "").split(",")
        if item.strip()
    ]


def remove_citypulse_params(junction):
    for param in list(junction.findall("param")):
        key = param.get("key", "")
        if key.startswith(CITYPULSE_PARAM_PREFIX):
            junction.remove(param)


def format_float(value, precision=8):
    text = f"{value:.{precision}f}"
    return text.rstrip("0").rstrip(".")


def append_citypulse_params(junction, intersection_ids, intersections, sumo_net):
    remove_citypulse_params(junction)

    if not intersection_ids:
        return

    jx = float(junction.get("x"))
    jy = float(junction.get("y"))

    ET.SubElement(junction, "param", {"key": "citypulse.target", "value": "true"})
    ET.SubElement(
        junction,
        "param",
        {"key": "citypulse.intersection_count", "value": str(len(intersection_ids))},
    )
    ET.SubElement(
        junction,
        "param",
        {"key": "citypulse.intersection_ids", "value": ",".join(intersection_ids)},
    )

    for index, intersection_id in enumerate(intersection_ids):
        intersection = intersections.get(intersection_id)
        if intersection is None:
            raise RuntimeError(
                f"{intersection_id} is present in net XML annotations but not in CSV."
            )

        target_x, target_y = sumo_net.convertLonLat2XY(
            intersection["lon"],
            intersection["lat"],
        )
        distance = math.hypot(jx - target_x, jy - target_y)
        prefix = f"citypulse.intersection.{index}"

        ET.SubElement(
            junction,
            "param",
            {"key": f"{prefix}.id", "value": intersection_id},
        )
        ET.SubElement(
            junction,
            "param",
            {"key": f"{prefix}.lon", "value": format_float(intersection["lon"])},
        )
        ET.SubElement(
            junction,
            "param",
            {"key": f"{prefix}.lat", "value": format_float(intersection["lat"])},
        )
        ET.SubElement(
            junction,
            "param",
            {"key": f"{prefix}.match_distance_m", "value": f"{distance:.2f}"},
        )
        ET.SubElement(
            junction,
            "param",
            {"key": f"{prefix}.xodr_junction_name", "value": ""},
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


def backup_file(path):
    source = Path(path)
    timestamp = datetime.now().strftime("%Y%m%d-%H%M%S")
    backup = source.with_name(f"{source.name}.{timestamp}.bak")
    shutil.copy2(source, backup)
    return backup


def update_mapping(
    net_xml,
    intersections_csv,
    report_json,
    intersection_id,
    junction_ids,
    primary_junction_id=None,
    dry_run=False,
    no_backup=False,
):
    sumolib = load_sumolib()

    net_path = Path(net_xml)
    csv_path = Path(intersections_csv)
    report_path = Path(report_json)

    intersections = read_intersections(csv_path)
    if intersection_id not in intersections:
        raise RuntimeError(f"{intersection_id} was not found in {csv_path}")

    if isinstance(junction_ids, str):
        junction_ids = [junction_ids]
    junction_ids = [str(item) for item in junction_ids]
    if not junction_ids:
        raise RuntimeError("At least one junction id is required.")
    if len(set(junction_ids)) != len(junction_ids):
        raise RuntimeError(f"Duplicate junction ids: {junction_ids}")

    if primary_junction_id is None:
        primary_junction_id = junction_ids[0]
    primary_junction_id = str(primary_junction_id)
    if primary_junction_id not in junction_ids:
        raise RuntimeError(
            f"primary junction {primary_junction_id} is not in {junction_ids}"
        )

    sumo_net = sumolib.net.readNet(str(net_path))
    tree = ET.parse(net_path)
    root = tree.getroot()

    junctions = {
        junction.get("id"): junction
        for junction in root.findall("junction")
        if junction.get("id") and not junction.get("id").startswith(":")
    }

    missing = [junction_id for junction_id in junction_ids if junction_id not in junctions]
    if missing:
        raise RuntimeError(f"junctions not found in {net_path}: {missing}")

    for junction_id in junction_ids:
        junction = junctions[junction_id]
        if not junction.get("x") or not junction.get("y"):
            raise RuntimeError(f"junction {junction_id} does not have x/y attributes")

    touched_junctions = set()
    old_junction_ids = []

    for current_junction_id, junction in junctions.items():
        ids = get_intersection_ids(junction)
        if intersection_id not in ids:
            continue

        if current_junction_id not in junction_ids:
            old_junction_ids.append(current_junction_id)
            ids = [item for item in ids if item != intersection_id]
            append_citypulse_params(junction, ids, intersections, sumo_net)
            touched_junctions.add(current_junction_id)

    for junction_id in junction_ids:
        target_junction = junctions[junction_id]
        target_ids = get_intersection_ids(target_junction)
        if intersection_id not in target_ids:
            target_ids.append(intersection_id)
        append_citypulse_params(target_junction, target_ids, intersections, sumo_net)
        touched_junctions.add(junction_id)

    intersection = intersections[intersection_id]
    target_x, target_y = sumo_net.convertLonLat2XY(
        intersection["lon"],
        intersection["lat"],
    )
    junction_matches = []
    for junction_id in junction_ids:
        junction = junctions[junction_id]
        jx = float(junction.get("x"))
        jy = float(junction.get("y"))
        distance = math.hypot(jx - target_x, jy - target_y)
        junction_lon, junction_lat = sumo_net.convertXY2LonLat(jx, jy)
        junction_matches.append(
            {
                "junction_id": junction_id,
                "junction_lon": junction_lon,
                "junction_lat": junction_lat,
                "match_distance_m": round(distance, 2),
            }
        )

    primary_match = next(
        item for item in junction_matches if item["junction_id"] == primary_junction_id
    )

    report = load_report(report_path)
    previous = report.get(intersection_id, {})
    report[intersection_id] = {
        "junction_id": str(primary_junction_id),
        "primary_junction_id": str(primary_junction_id),
        "junction_ids": junction_ids,
        "lon": intersection["lon"],
        "lat": intersection["lat"],
        "match_distance_m": primary_match["match_distance_m"],
        "xodr_junction_name": "",
        "junction_lon": primary_match["junction_lon"],
        "junction_lat": primary_match["junction_lat"],
        "junction_matches": junction_matches,
        "match_type": "multi_junction" if len(junction_ids) > 1 else "single_junction",
        "source_osm_node_ids": previous.get("source_osm_node_ids", []),
        "manual_override": True,
    }

    if dry_run:
        print(
            f"[dry-run] {intersection_id}: "
            f"{previous.get('junction_id')} -> {primary_junction_id}, "
            f"junction_ids={junction_ids}"
        )
        for match in junction_matches:
            print(
                "[dry-run]   {junction_id}: distance={match_distance_m}m".format(
                    **match
                )
            )
        print(f"[dry-run] touched junction annotations: {sorted(touched_junctions)}")
        if old_junction_ids:
            print(f"[dry-run] removed old annotations from: {old_junction_ids}")
        return

    backups = []
    if not no_backup:
        backups.append(backup_file(net_path))
        if report_path.exists():
            backups.append(backup_file(report_path))

    indent(root)
    tree.write(net_path, encoding="utf-8", xml_declaration=True)
    report_path.parent.mkdir(parents=True, exist_ok=True)
    report_path.write_text(
        json.dumps(report, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )

    print(
        f"{intersection_id}: {previous.get('junction_id')} -> {primary_junction_id}, "
        f"junction_ids={junction_ids}"
    )
    for match in junction_matches:
        print(
            "  {junction_id}: distance={match_distance_m}m".format(
                **match
            )
        )
    print(f"Updated {net_path}")
    print(f"Updated {report_path}")
    if backups:
        for backup in backups:
            print(f"Backup: {backup}")


def main():
    parser = argparse.ArgumentParser(
        description=(
            "Move one CityPulse official intersection mapping to one or more "
            "SUMO junctions and keep net.xml annotations plus the JSON report "
            "in sync."
        )
    )
    parser.add_argument("--net", default="data/maps/sumo/TotalMap_20.net.xml")
    parser.add_argument(
        "--csv",
        default="data/maps/osm/latitude_longitude_intersection_20.csv",
        help="official intersection csv with id, lon, lat",
    )
    parser.add_argument(
        "--report",
        default="data/maps/sumo/TotalMap_20.intersections.json",
    )
    parser.add_argument("--intersection-id", required=True)
    parser.add_argument(
        "--junction-id",
        required=True,
        nargs="+",
        help=(
            "one or more SUMO junction ids for this official intersection. "
            "The first id is used as primary unless --primary-junction-id is set."
        ),
    )
    parser.add_argument(
        "--primary-junction-id",
        default=None,
        help="primary SUMO junction id for backward-compatible junction_id fields",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="validate and print the intended update without writing files",
    )
    parser.add_argument(
        "--no-backup",
        action="store_true",
        help="do not create timestamped .bak files before writing",
    )
    args = parser.parse_args()

    update_mapping(
        net_xml=args.net,
        intersections_csv=args.csv,
        report_json=args.report,
        intersection_id=args.intersection_id,
        junction_ids=args.junction_id,
        primary_junction_id=args.primary_junction_id,
        dry_run=args.dry_run,
        no_backup=args.no_backup,
    )


if __name__ == "__main__":
    main()
