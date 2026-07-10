#!/usr/bin/env python

# Copyright (c) 2020 Computer Vision Center (CVC) at the Universitat Autonoma de
# Barcelona (UAB).
#
# This work is licensed under the terms of the MIT license.
# For a copy, see <https://opensource.org/licenses/MIT>.
"""
Script to generate sumo nets based on opendrive files. Internally, it uses netconvert to generate
the net and inserts, manually, the traffic light landmarks retrieved from the opendrive.
"""

# ==================================================================================================
# -- imports ---------------------------------------------------------------------------------------
# ==================================================================================================

import argparse
import bisect
import collections
import csv
import json
import logging
import math
import os
import re
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

import lxml.etree as ET  # pylint: disable=import-error


# ==================================================================================================
# -- find sumo modules -----------------------------------------------------------------------------
# ==================================================================================================

if 'SUMO_HOME' in os.environ:
    sys.path.append(os.path.join(os.environ['SUMO_HOME'], 'tools'))
else:
    sys.exit("please declare environment variable 'SUMO_HOME'")

# ==================================================================================================
# -- imports ---------------------------------------------------------------------------------------
# ==================================================================================================

import carla
import sumolib

PROJECT_ROOT = Path(__file__).resolve().parents[2]

DEFAULT_OSM_FILE = PROJECT_ROOT / "data" / "maps" / "osm" / "TotalMap.osm"
DEFAULT_INTERSECTIONS_CSV = (
    PROJECT_ROOT / "data" / "maps" / "osm" / "latitude_longitude_intersection_20.csv"
)
DEFAULT_OUTPUT_NET = PROJECT_ROOT / "data" / "maps" / "sumo" / "TotalMap_20.net.xml"
DEFAULT_KEEP_XODR = PROJECT_ROOT / "data" / "maps" / "carla" / "TotalMap_20.xodr"

OSM_WAY_TYPES = [
    "motorway",
    "motorway_link",
    "trunk",
    "trunk_link",
    "primary",
    "primary_link",
    "secondary",
    "secondary_link",
    "tertiary",
    "tertiary_link",
    "living_street",
    "unclassified",
    "residential",
    "service",
]

CITYPULSE_PARAM_PREFIX = "citypulse."


def _format_float(value, precision=8):
    text = f"{value:.{precision}f}"
    return text.rstrip("0").rstrip(".")


def _format_distance(value):
    return f"{value:.2f}"


def _haversine_m(lon1, lat1, lon2, lat2):
    radius = 6371000.0
    phi1 = math.radians(lat1)
    phi2 = math.radians(lat2)
    d_phi = math.radians(lat2 - lat1)
    d_lambda = math.radians(lon2 - lon1)

    a = (
        math.sin(d_phi / 2.0) ** 2
        + math.cos(phi1) * math.cos(phi2) * math.sin(d_lambda / 2.0) ** 2
    )
    return 2.0 * radius * math.asin(math.sqrt(a))


def _default_report_path(output_file):
    output_path = Path(output_file)
    output_text = str(output_path)
    if output_text.endswith(".net.xml"):
        return output_text[: -len(".net.xml")] + ".intersections.json"
    return str(output_path.with_suffix(".intersections.json"))


def _read_text_utf8(path):
    return Path(path).read_text(encoding="utf-8")


def _read_intersections_csv(csv_file):
    last_error = None
    rows = None

    for encoding in ("utf-8-sig", "utf-8", "gbk"):
        try:
            with open(csv_file, "r", encoding=encoding, newline="") as f:
                rows = list(csv.reader(f))
            break
        except UnicodeDecodeError as exc:
            last_error = exc

    if rows is None:
        raise RuntimeError(f"Could not decode intersection csv: {csv_file}") from last_error

    intersections = []
    for line_no, row in enumerate(rows, start=1):
        if not row or all(not cell.strip() for cell in row):
            continue

        if len(row) < 3:
            raise RuntimeError(f"CSV line {line_no} has fewer than 3 columns: {row}")

        code = row[0].strip()
        try:
            lon = float(row[1])
            lat = float(row[2])
        except ValueError:
            if line_no == 1:
                continue
            raise RuntimeError(f"CSV line {line_no} has invalid lon/lat values: {row}")

        if not code:
            raise RuntimeError(f"CSV line {line_no} has empty intersection id.")

        intersections.append(
            {
                "id": code,
                "lon": lon,
                "lat": lat,
                "line_no": line_no,
            }
        )

    if len(intersections) != 20:
        raise RuntimeError(
            f"Expected 20 intersections in {csv_file}, got {len(intersections)}."
        )

    duplicate_ids = [
        item
        for item, count in collections.Counter(i["id"] for i in intersections).items()
        if count > 1
    ]
    if duplicate_ids:
        raise RuntimeError(f"Duplicate intersection ids in CSV: {duplicate_ids}")

    return intersections


def _load_osm_nodes(osm_file):
    nodes = {}

    for _, elem in ET.iterparse(str(osm_file), events=("end",), tag="node"):
        node_id = elem.get("id")
        lon = elem.get("lon")
        lat = elem.get("lat")

        if node_id and lon is not None and lat is not None:
            nodes[node_id] = (float(lon), float(lat))

        elem.clear()

    if not nodes:
        raise RuntimeError(f"No OSM nodes found in {osm_file}.")

    return nodes


def _extract_osm_ids_from_junction_name(name):
    if not name:
        return []
    return re.findall(r"\d+", name)


def _build_xodr_junction_candidates(xodr_file, osm_nodes):
    tree = ET.parse(str(xodr_file))
    candidates = []

    for junction in tree.xpath("//junction"):
        junction_id = junction.get("id")
        junction_name = junction.get("name", "")
        osm_ids = _extract_osm_ids_from_junction_name(junction_name)

        coords = []
        used_osm_ids = []
        for osm_id in osm_ids:
            if osm_id in osm_nodes:
                lon, lat = osm_nodes[osm_id]
                coords.append((lon, lat))
                used_osm_ids.append(osm_id)

        if not junction_id or not coords:
            continue

        lon = sum(item[0] for item in coords) / len(coords)
        lat = sum(item[1] for item in coords) / len(coords)

        candidates.append(
            {
                "junction_id": junction_id,
                "xodr_junction_name": junction_name,
                "lon": lon,
                "lat": lat,
                "source_osm_node_ids": used_osm_ids,
            }
        )

    if not candidates:
        raise RuntimeError(
            "No XODR junction candidates could be mapped back to OSM nodes. "
            "Check whether the XODR junction names still contain OSM node ids."
        )

    return candidates


def _match_intersections_to_junctions(intersections, candidates, max_distance_m):
    assignments = []
    failures = []

    for intersection in intersections:
        best = None
        best_distance = None

        for candidate in candidates:
            distance = _haversine_m(
                intersection["lon"],
                intersection["lat"],
                candidate["lon"],
                candidate["lat"],
            )
            if best is None or distance < best_distance:
                best = candidate
                best_distance = distance

        assignment = {
            "intersection_id": intersection["id"],
            "csv_lon": intersection["lon"],
            "csv_lat": intersection["lat"],
            "junction_id": best["junction_id"],
            "junction_lon": best["lon"],
            "junction_lat": best["lat"],
            "match_distance_m": best_distance,
            "xodr_junction_name": best["xodr_junction_name"],
            "source_osm_node_ids": best["source_osm_node_ids"],
        }

        assignments.append(assignment)

        if best_distance > max_distance_m:
            failures.append(assignment)

    if failures:
        details = "\n".join(
            "  {intersection_id}: nearest junction={junction_id}, distance={distance}m, "
            "csv=({lon},{lat}), xodr_name={name}".format(
                intersection_id=item["intersection_id"],
                junction_id=item["junction_id"],
                distance=_format_distance(item["match_distance_m"]),
                lon=_format_float(item["csv_lon"]),
                lat=_format_float(item["csv_lat"]),
                name=item["xodr_junction_name"],
            )
            for item in failures
        )
        raise RuntimeError(
            f"Some intersections are farther than {max_distance_m}m from the nearest junction:\n"
            f"{details}"
        )

    junction_counts = collections.Counter(item["junction_id"] for item in assignments)
    for junction_id, count in sorted(junction_counts.items()):
        if count > 1:
            ids = [
                item["intersection_id"]
                for item in assignments
                if item["junction_id"] == junction_id
            ]
            logging.warning(
                "Multiple configured intersections map to SUMO junction %s: %s",
                junction_id,
                ", ".join(ids),
            )

    return assignments


def _remove_existing_citypulse_params(junction_tag):
    for param in list(junction_tag.xpath("./param[starts-with(@key, 'citypulse.')]")):
        junction_tag.remove(param)


def _annotate_sumo_junctions(tree, assignments):
    root = tree.getroot()
    junction_tags = {
        junction.get("id"): junction
        for junction in root.xpath("//junction")
        if junction.get("id") and not junction.get("id").startswith(":")
    }

    grouped = collections.defaultdict(list)
    for item in assignments:
        grouped[item["junction_id"]].append(item)

    missing = sorted(junction_id for junction_id in grouped if junction_id not in junction_tags)
    if missing:
        raise RuntimeError(f"Matched XODR junctions not found in SUMO net XML: {missing}")

    for junction_id, items in sorted(grouped.items()):
        junction = junction_tags[junction_id]
        _remove_existing_citypulse_params(junction)

        intersection_ids = [item["intersection_id"] for item in items]

        ET.SubElement(junction, "param", {"key": "citypulse.target", "value": "true"})
        ET.SubElement(
            junction,
            "param",
            {"key": "citypulse.intersection_count", "value": str(len(items))},
        )
        ET.SubElement(
            junction,
            "param",
            {"key": "citypulse.intersection_ids", "value": ",".join(intersection_ids)},
        )

        for index, item in enumerate(items):
            prefix = f"citypulse.intersection.{index}"
            ET.SubElement(
                junction,
                "param",
                {"key": f"{prefix}.id", "value": item["intersection_id"]},
            )
            ET.SubElement(
                junction,
                "param",
                {"key": f"{prefix}.lon", "value": _format_float(item["csv_lon"])},
            )
            ET.SubElement(
                junction,
                "param",
                {"key": f"{prefix}.lat", "value": _format_float(item["csv_lat"])},
            )
            ET.SubElement(
                junction,
                "param",
                {
                    "key": f"{prefix}.match_distance_m",
                    "value": _format_distance(item["match_distance_m"]),
                },
            )
            ET.SubElement(
                junction,
                "param",
                {
                    "key": f"{prefix}.xodr_junction_name",
                    "value": item["xodr_junction_name"],
                },
            )


def _write_intersection_report(report_file, assignments):
    report_path = Path(report_file)
    report_path.parent.mkdir(parents=True, exist_ok=True)

    report = {}
    for item in assignments:
        report[item["intersection_id"]] = {
            "junction_id": item["junction_id"],
            "lon": item["csv_lon"],
            "lat": item["csv_lat"],
            "match_distance_m": round(item["match_distance_m"], 2),
            "xodr_junction_name": item["xodr_junction_name"],
            "junction_lon": item["junction_lon"],
            "junction_lat": item["junction_lat"],
            "source_osm_node_ids": item["source_osm_node_ids"],
        }

    report_path.write_text(
        json.dumps(report, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )


def _build_sumo_junction_candidates(tree):
    candidates = []

    for junction in tree.xpath("//junction[@id and @x and @y]"):
        junction_id = junction.get("id")
        junction_type = junction.get("type", "")

        if junction_id.startswith(":"):
            continue
        if junction_type in ("internal", "dead_end"):
            continue

        inc_lanes = junction.get("incLanes", "").split()
        int_lanes = junction.get("intLanes", "").split()
        if not inc_lanes and not int_lanes:
            continue

        candidates.append(
            {
                "junction_id": junction_id,
                "junction_type": junction_type,
                "x": float(junction.get("x")),
                "y": float(junction.get("y")),
            }
        )

    if not candidates:
        raise RuntimeError("No usable SUMO junctions found in generated net XML.")

    return candidates


def _match_intersections_to_sumo_junctions(
    sumo_net,
    intersections,
    candidates,
    max_distance_m,
):
    assignments = []
    failures = []

    for intersection in intersections:
        target_x, target_y = sumo_net.convertLonLat2XY(
            intersection["lon"],
            intersection["lat"],
        )

        best = None
        best_distance = None

        for candidate in candidates:
            distance = math.hypot(
                target_x - candidate["x"],
                target_y - candidate["y"],
            )
            if best is None or distance < best_distance:
                best = candidate
                best_distance = distance

        try:
            junction_lon, junction_lat = sumo_net.convertXY2LonLat(best["x"], best["y"])
        except Exception:
            junction_lon, junction_lat = None, None

        assignment = {
            "intersection_id": intersection["id"],
            "csv_lon": intersection["lon"],
            "csv_lat": intersection["lat"],
            "target_x": target_x,
            "target_y": target_y,
            "junction_id": best["junction_id"],
            "junction_type": best["junction_type"],
            "junction_x": best["x"],
            "junction_y": best["y"],
            "junction_lon": junction_lon,
            "junction_lat": junction_lat,
            "match_distance_m": best_distance,
            "xodr_junction_name": "",
            "source_osm_node_ids": [],
        }

        assignments.append(assignment)

        if best_distance > max_distance_m:
            failures.append(assignment)

    if failures:
        details = "\n".join(
            "  {intersection_id}: nearest junction={junction_id}, distance={distance}m, "
            "csv=({lon},{lat})".format(
                intersection_id=item["intersection_id"],
                junction_id=item["junction_id"],
                distance=_format_distance(item["match_distance_m"]),
                lon=_format_float(item["csv_lon"]),
                lat=_format_float(item["csv_lat"]),
            )
            for item in failures
        )
        raise RuntimeError(
            f"Some intersections are farther than {max_distance_m}m from the nearest "
            f"SUMO junction:\n{details}"
        )

    junction_counts = collections.Counter(item["junction_id"] for item in assignments)
    for junction_id, count in sorted(junction_counts.items()):
        if count > 1:
            ids = [
                item["intersection_id"]
                for item in assignments
                if item["junction_id"] == junction_id
            ]
            logging.warning(
                "Multiple configured intersections map to SUMO junction %s: %s",
                junction_id,
                ", ".join(ids),
            )

    return assignments


def annotate_citypulse_intersections(
    tree,
    sumo_net,
    intersections_csv,
    report_file,
    max_distance_m,
):
    intersections = _read_intersections_csv(intersections_csv)
    candidates = _build_sumo_junction_candidates(tree)
    assignments = _match_intersections_to_sumo_junctions(
        sumo_net,
        intersections,
        candidates,
        max_distance_m,
    )

    _annotate_sumo_junctions(tree, assignments)

    if report_file:
        _write_intersection_report(report_file, assignments)

    logging.info(
        "Annotated %d configured intersections on %d SUMO junctions.",
        len(assignments),
        len(set(item["junction_id"] for item in assignments)),
    )


def find_opendrive_type_file(type_file=None):
    candidates = []

    if type_file:
        candidates.append(Path(type_file))

    basedir = Path(__file__).resolve().parent
    candidates.append(basedir / "data" / "opendrive_netconvert.typ.xml")

    carla_root = os.environ.get("CARLA_ROOT")
    if carla_root:
        candidates.append(
            Path(carla_root)
            / "Co-Simulation"
            / "Sumo"
            / "util"
            / "data"
            / "opendrive_netconvert.typ.xml"
        )

    for candidate in candidates:
        if candidate and candidate.is_file():
            return str(candidate)

    raise RuntimeError(
        "Could not find opendrive_netconvert.typ.xml. "
        "Pass --type-file or set CARLA_ROOT to your CARLA installation."
    )


def convert_osm_to_xodr(osm_file, tmpdir, keep_xodr=None):
    osm_path = Path(osm_file)
    if not osm_path.is_file():
        raise RuntimeError(f"OSM file not found: {osm_path}")

    settings = carla.Osm2OdrSettings()
    settings.set_osm_way_types(OSM_WAY_TYPES)
    settings.generate_traffic_lights = True

    xodr_data = carla.Osm2Odr.convert(_read_text_utf8(osm_path), settings)

    tmp_xodr = Path(tmpdir) / f"{osm_path.stem}.xodr"
    tmp_xodr.write_text(xodr_data, encoding="utf-8")

    if keep_xodr:
        keep_path = Path(keep_xodr)
        keep_path.parent.mkdir(parents=True, exist_ok=True)
        keep_path.write_text(xodr_data, encoding="utf-8")
        logging.info("Saved intermediate XODR to %s", keep_path)

    return str(tmp_xodr)

# ==================================================================================================
# -- topology --------------------------------------------------------------------------------------
# ==================================================================================================


class SumoTopology(object):
    """
    This object holds the topology of a sumo net. Internally, the information is structured as
    follows:

        - topology: {
            (road_id, lane_id): [(successor_road_id, succesor_lane_id), ...], ...}
        - paths: {
            (road_id, lane_id): [
                ((in_road_id, in_lane_id), (out_road_id, out_lane_id)), ...
            ], ...}
        - odr2sumo_ids: {
            (odr_road_id, odr_lane_id): [(sumo_edge_id, sumo_lane_id), ...], ...}
    """
    def __init__(self, topology, paths, odr2sumo_ids):
        # Contains only standard roads.
        self._topology = topology
        # Contaions only roads that belong to a junction.
        self._paths = paths
        # Mapped ids between sumo and opendrive.
        self._odr2sumo_ids = odr2sumo_ids

    # http://sumo.sourceforge.net/userdoc/Networks/Import/OpenDRIVE.html#dealing_with_lane_sections
    def get_sumo_id(self, odr_road_id, odr_lane_id, s=0):
        """
        Returns the pair (sumo_edge_id, sumo_lane index) corresponding to the provided odr pair. The
        argument 's' allows selecting the better sumo edge when it has been split into different
        edges due to different odr lane sections.
        """
        if (odr_road_id, odr_lane_id) not in self._odr2sumo_ids:
            return None

        sumo_ids = list(self._odr2sumo_ids[(odr_road_id, odr_lane_id)])

        if (len(sumo_ids)) == 1:
            return sumo_ids[0]

        # The edge is split into different lane sections. We return the nearest edge based on the
        # s coordinate of the provided landmark.
        else:
            # Ensures that all the related sumo edges belongs to the same opendrive road but to
            # different lane sections.
            assert set([edge.split('.', 1)[0] for edge, lane_index in sumo_ids]) == 1

            s_coords = [float(edge.split('.', 1)[1]) for edge, lane_index in sumo_ids]

            s_coords, sumo_ids = zip(*sorted(zip(s_coords, sumo_ids)))
            index = bisect.bisect_left(s_coords, s, lo=1) - 1
            return sumo_ids[index]

    def is_junction(self, odr_road_id, odr_lane_id):
        """
        Checks whether the provided pair (odr_road_id, odr_lane_id) belongs to a junction.
        """
        return (odr_road_id, odr_lane_id) in self._paths

    def get_successors(self, sumo_edge_id, sumo_lane_index):
        """
        Returns the successors (standard roads) of the provided pair (sumo_edge_id, sumo_lane_index)
        """
        if self.is_junction(sumo_edge_id, sumo_lane_index):
            return []

        return list(self._topology.get((sumo_edge_id, sumo_lane_index), set()))

    def get_incoming(self, odr_road_id, odr_lane_id):
        """
        If the pair (odr_road_id, odr_lane_id) belongs to a junction, returns the incoming edges of
        the path. Otherwise, return and empty list.
        """
        if not self.is_junction(odr_road_id, odr_lane_id):
            return []

        result = set([(connection[0][0], connection[0][1])
                      for connection in self._paths[(odr_road_id, odr_lane_id)]])
        return list(result)

    def get_outgoing(self, odr_road_id, odr_lane_id):
        """
        If the pair (odr_road_id, odr_lane_id) belongs to a junction, returns the outgoing edges of
        the path. Otherwise, return and empty list.
        """
        if not self.is_junction(odr_road_id, odr_lane_id):
            return []

        result = set([(connection[1][0], connection[1][1])
                      for connection in self._paths[(odr_road_id, odr_lane_id)]])
        return list(result)

    def get_path_connectivity(self, odr_road_id, odr_lane_id):
        """
        Returns incoming and outgoing roads of the pair (odr_road_id, odr_lane_id). If the provided
        pair not belongs to a junction, returns an empty list.
        """
        return list(self._paths.get((odr_road_id, odr_lane_id), set()))


def build_topology(sumo_net):
    """
    Builds sumo topology.
    """
    # --------------------------
    # OpenDrive->Sumo mapped ids
    # --------------------------
    # Only takes into account standard roads.
    #
    #   odr2sumo_ids = {(odr_road_id, odr_lane_id) : [(sumo_edge_id, sumo_lane_index), ...], ...}
    odr2sumo_ids = {}
    for edge in sumo_net.getEdges():
        for lane in edge.getLanes():
            if lane.getParam('origId') is None:
                raise RuntimeError(
                    'Sumo lane {} does not have "origId" parameter. Make sure that the --output.original-names parameter is active when running netconvert.'
                    .format(lane.getID()))

            if len(lane.getParam('origId').split()) > 1:
                logging.warning('[Building topology] Sumo net contains joined opendrive roads.')

            for odr_id in lane.getParam('origId').split():
                odr_road_id, odr_lane_id = odr_id.split('_')
                if (odr_road_id, int(odr_lane_id)) not in odr2sumo_ids:
                    odr2sumo_ids[(odr_road_id, int(odr_lane_id))] = set()
                odr2sumo_ids[(odr_road_id, int(odr_lane_id))].add((edge.getID(), lane.getIndex()))

    # -----------
    # Connections
    # -----------
    #
    #   topology -- {(sumo_road_id, sumo_lane_index): [(sumo_road_id, sumo_lane_index), ...], ...}
    #   paths    -- {(odr_road_id, odr_lane_id): [
    #                   ((sumo_edge_id, sumo_lane_index), (sumo_edge_id, sumo_lane_index))
    #               ]}
    topology = {}
    paths = {}

    for from_edge in sumo_net.getEdges():
        for to_edge in sumo_net.getEdges():
            connections = from_edge.getConnections(to_edge)
            for connection in connections:
                from_ = connection.getFromLane()
                to_ = connection.getToLane()
                from_edge_id, from_lane_index = from_.getEdge().getID(), from_.getIndex()
                to_edge_id, to_lane_index = to_.getEdge().getID(), to_.getIndex()

                if (from_edge_id, from_lane_index) not in topology:
                    topology[(from_edge_id, from_lane_index)] = set()

                topology[(from_edge_id, from_lane_index)].add((to_edge_id, to_lane_index))

                # Checking if the connection is an opendrive path.
                conn_odr_ids = connection.getParam('origId')
                if conn_odr_ids is not None:
                    if len(conn_odr_ids.split()) > 1:
                        logging.warning(
                            '[Building topology] Sumo net contains joined opendrive paths.')

                    for odr_id in conn_odr_ids.split():

                        odr_road_id, odr_lane_id = odr_id.split('_')
                        if (odr_road_id, int(odr_lane_id)) not in paths:
                            paths[(odr_road_id, int(odr_lane_id))] = set()

                        paths[(odr_road_id, int(odr_lane_id))].add(
                            ((from_edge_id, from_lane_index), (to_edge_id, to_lane_index)))

    return SumoTopology(topology, paths, odr2sumo_ids)


# ==================================================================================================
# -- sumo definitions ------------------------------------------------------------------------------
# ==================================================================================================


class SumoTrafficLight(object):
    """
    SumoTrafficLight holds all the necessary data to define a traffic light in sumo:

        * connections (tlid, from_road, to_road, from_lane, to_lane, link_index).
        * phases (duration, state, min_dur, max_dur, nex, name).
        * parameters.
    """
    DEFAULT_DURATION_GREEN_PHASE = 42
    DEFAULT_DURATION_YELLOW_PHASE = 3
    DEFAULT_DURATION_RED_PHASE = 3

    Phase = collections.namedtuple('Phase', 'duration state min_dur max_dur next name')
    Connection = collections.namedtuple('Connection',
                                        'tlid from_road to_road from_lane to_lane link_index')

    def __init__(self, tlid, program_id='0', offset=0, tltype='static'):
        self.id = tlid
        self.program_id = program_id
        self.offset = offset
        self.type = tltype

        self.phases = []
        self.parameters = set()
        self.connections = set()

    @staticmethod
    def generate_tl_id(from_edge, to_edge):
        """
        Generates sumo traffic light id based on the junction connectivity.
        """
        return '{}:{}'.format(from_edge, to_edge)

    @staticmethod
    def generate_default_program(tl):
        """
        Generates a default program for the given sumo traffic light
        """
        incoming_roads = [connection.from_road for connection in tl.connections]
        for road in set(incoming_roads):
            phase_green = ['r'] * len(tl.connections)
            phase_yellow = ['r'] * len(tl.connections)
            phase_red = ['r'] * len(tl.connections)

            for connection in tl.connections:
                if connection.from_road == road:
                    phase_green[connection.link_index] = 'g'
                    phase_yellow[connection.link_index] = 'y'

            tl.add_phase(SumoTrafficLight.DEFAULT_DURATION_GREEN_PHASE, ''.join(phase_green))
            tl.add_phase(SumoTrafficLight.DEFAULT_DURATION_YELLOW_PHASE, ''.join(phase_yellow))
            tl.add_phase(SumoTrafficLight.DEFAULT_DURATION_RED_PHASE, ''.join(phase_red))

    def add_phase(self, duration, state, min_dur=-1, max_dur=-1, next_phase=None, name=''):
        """
        Adds a new phase.
        """
        self.phases.append(
            SumoTrafficLight.Phase(duration, state, min_dur, max_dur, next_phase, name))

    def add_parameter(self, key, value):
        """
        Adds a new parameter.
        """
        self.parameters.add((key, value))

    def add_connection(self, connection):
        """
        Adds a new connection.
        """
        self.connections.add(connection)

    def add_landmark(self,
                     landmark_id,
                     tlid,
                     from_road,
                     to_road,
                     from_lane,
                     to_lane,
                     link_index=-1):
        """
        Adds a new landmark.

        Returns True if the landmark is successfully included. Otherwise, returns False.
        """
        if link_index == -1:
            link_index = len(self.connections)

        def is_same_connection(c1, c2):
            return c1.from_road == c2.from_road and c1.to_road == c2.to_road and \
                   c1.from_lane == c2.from_lane and c1.to_lane == c2.to_lane

        connection = SumoTrafficLight.Connection(tlid, from_road, to_road, from_lane, to_lane,
                                                 link_index)
        if any([is_same_connection(connection, c) for c in self.connections]):
            logging.warning(
                'Different landmarks controlling the same connection. Only one will be included.')
            return False

        self.add_connection(connection)
        self.add_parameter(link_index, landmark_id)
        return True

    def to_xml(self):
        info = {
            'id': self.id,
            'type': self.type,
            'programID': self.program_id,
            'offset': str(self.offset)
        }

        xml_tag = ET.Element('tlLogic', info)
        for phase in self.phases:
            ET.SubElement(xml_tag, 'phase', {'state': phase.state, 'duration': str(phase.duration)})
        for parameter in sorted(self.parameters, key=lambda x: x[0]):
            ET.SubElement(xml_tag, 'param', {
                'key': 'linkSignalID:' + str(parameter[0]),
                'value': str(parameter[1])
            })

        return xml_tag


# ==================================================================================================
# -- main ------------------------------------------------------------------------------------------
# ==================================================================================================


def _netconvert_carla_impl(
    xodr_file,
    output,
    tmpdir,
    guess_tls=False,
    osm_file=None,
    intersections_csv=None,
    report_file=None,
    max_match_distance_m=120.0,
    type_file=None,
):
    """
    Implements netconvert carla.
    """
    # ----------
    # netconvert
    # ----------
    basename = os.path.splitext(os.path.basename(xodr_file))[0]
    tmp_sumo_net = os.path.join(tmpdir, basename + '.net.xml')

    try:
        opendrive_type_file = find_opendrive_type_file(type_file)
        netconvert_cmd = [
            'netconvert',
            '--opendrive', xodr_file,
            '--output-file', tmp_sumo_net,
            '--geometry.min-radius.fix',
            '--geometry.remove',
            '--opendrive.curve-resolution', '1',
            '--opendrive.import-all-lanes',
            '--type-files', opendrive_type_file,
            # Necessary to link odr and sumo ids.
            '--output.original-names',
            # Discard loading traffic lights as them will be inserted manually afterwards.
            '--tls.discard-loaded', 'true',
        ]
        logging.debug("Running netconvert command: %s", " ".join(netconvert_cmd))
        result = subprocess.call(netconvert_cmd)
    except subprocess.CalledProcessError:
        raise RuntimeError('There was an error when executing netconvert.')
    else:
        if result != 0:
            raise RuntimeError('There was an error when executing netconvert.')

    # --------
    # Sumo net
    # --------
    sumo_net = sumolib.net.readNet(tmp_sumo_net)
    sumo_topology = build_topology(sumo_net)

    # ---------
    # Carla map
    # ---------
    with open(xodr_file, 'r') as f:
        carla_map = carla.Map('netconvert', str(f.read()))

    # ---------
    # Landmarks
    # ---------
    tls = {}  # {tlsid: SumoTrafficLight}

    landmarks = carla_map.get_all_landmarks_of_type('1000001')
    for landmark in landmarks:
        if landmark.name == '':
            # This is a workaround to avoid adding traffic lights without controllers.
            logging.warning('Landmark %s has not a valid name.', landmark.name)
            continue

        road_id = str(landmark.road_id)
        for from_lane, to_lane in landmark.get_lane_validities():
            for lane_id in range(from_lane, to_lane + 1):
                if lane_id == 0:
                    continue

                wp = carla_map.get_waypoint_xodr(landmark.road_id, lane_id, landmark.s)
                if wp is None:
                    logging.warning(
                        'Could not find waypoint for landmark {} (road_id: {}, lane_id: {}, s:{}'.
                        format(landmark.id, landmark.road_id, lane_id, landmark.s))
                    continue

                # When the landmark belongs to a junction, we place te traffic light at the
                # entrance of the junction.
                if wp.is_junction and sumo_topology.is_junction(road_id, lane_id):
                    tlid = str(wp.get_junction().id)
                    if tlid not in tls:
                        tls[tlid] = SumoTrafficLight(tlid)
                    tl = tls[tlid]

                    if guess_tls:
                        for from_edge, from_lane in sumo_topology.get_incoming(road_id, lane_id):
                            successors = sumo_topology.get_successors(from_edge, from_lane)
                            for to_edge, to_lane in successors:
                                tl.add_landmark(landmark.id, tl.id, from_edge, to_edge, from_lane,
                                                to_lane)

                    else:
                        connections = sumo_topology.get_path_connectivity(road_id, lane_id)
                        for from_, to_ in connections:
                            from_edge, from_lane = from_
                            to_edge, to_lane = to_

                            tl.add_landmark(landmark.id, tl.id, from_edge, to_edge, from_lane,
                                            to_lane)

                # When the landmarks does not belong to a junction (i.e., belongs to a std road),
                # we place the traffic light between that std road and its successor.
                elif not wp.is_junction and not sumo_topology.is_junction(road_id, lane_id):
                    from_edge, from_lane = sumo_topology.get_sumo_id(road_id, lane_id, landmark.s)

                    for to_edge, to_lane in sumo_topology.get_successors(from_edge, from_lane):
                        tlid = SumoTrafficLight.generate_tl_id(from_edge, to_edge)
                        if tlid not in tls:
                            tls[tlid] = SumoTrafficLight(tlid)
                        tl = tls[tlid]

                        tl.add_landmark(landmark.id, tl.id, from_edge, to_edge, from_lane, to_lane)

                else:
                    logging.warning('Landmark %s could not be added.', landmark.id)

    # ---------------
    # Modify sumo net
    # ---------------
    parser = ET.XMLParser(remove_blank_text=True)
    tree = ET.parse(tmp_sumo_net, parser)
    root = tree.getroot()

    for tl in tls.values():
        SumoTrafficLight.generate_default_program(tl)
        edges_tags = tree.xpath('//edge')
        if not edges_tags:
            raise RuntimeError('No edges found in sumo net.')
        root.insert(root.index(edges_tags[-1]) + 1, tl.to_xml())

        for connection in tl.connections:
            tags = tree.xpath(
                '//connection[@from="{}" and @to="{}" and @fromLane="{}" and @toLane="{}"]'.format(
                    connection.from_road, connection.to_road, connection.from_lane,
                    connection.to_lane))

            if tags:
                if len(tags) > 1:
                    logging.warning(
                        'Found repeated connections from={} to={} fromLane={} toLane={}.'.format(
                            connection.from_road, connection.to_road, connection.from_lane,
                            connection.to_lane))

                tags[0].set('tl', str(connection.tlid))
                tags[0].set('linkIndex', str(connection.link_index))
            else:
                logging.warning('Not found connection from={} to={} fromLane={} toLane={}.'.format(
                    connection.from_road, connection.to_road, connection.from_lane,
                    connection.to_lane))

    if osm_file and intersections_csv:
        annotate_citypulse_intersections(
            tree,
            sumo_net=sumo_net,
            intersections_csv=intersections_csv,
            report_file=report_file,
            max_distance_m=max_match_distance_m,
        )

    output_path = Path(output)
    output_path.parent.mkdir(parents=True, exist_ok=True)
    tree.write(str(output_path), pretty_print=True, encoding='UTF-8', xml_declaration=True)


def netconvert_osm_20(
    osm_file,
    intersections_csv,
    output,
    report_file=None,
    guess_tls=False,
    keep_xodr=None,
    max_match_distance_m=120.0,
    type_file=None,
):
    """
    Generates a SUMO net XML directly from an OSM file and annotates the 20 configured
    CityPulse intersections.

        :param osm_file: input OpenStreetMap file (*.osm)
        :param intersections_csv: csv with columns: id, lon, lat
        :param output: output SUMO net file (*.net.xml)
        :param report_file: optional json report for intersection-to-junction mapping
        :param guess_tls: guess traffic lights at intersections
        :param keep_xodr: optional path to keep the intermediate OpenDRIVE file
        :param max_match_distance_m: max allowed distance between csv point and junction
        :param type_file: optional opendrive_netconvert.typ.xml path
        :returns: path to the generated sumo net.
    """
    tmpdir = tempfile.mkdtemp()
    try:
        if report_file is None:
            report_file = _default_report_path(output)

        xodr_file = convert_osm_to_xodr(osm_file, tmpdir, keep_xodr=keep_xodr)

        _netconvert_carla_impl(
            xodr_file,
            output,
            tmpdir,
            guess_tls=guess_tls,
            osm_file=osm_file,
            intersections_csv=intersections_csv,
            report_file=report_file,
            max_match_distance_m=max_match_distance_m,
            type_file=type_file,
        )

        return output

    finally:
        if os.path.exists(tmpdir):
            shutil.rmtree(tmpdir)


if __name__ == '__main__':
    argparser = argparse.ArgumentParser(description=__doc__)
    argparser.add_argument(
        '--osm',
        default=str(DEFAULT_OSM_FILE),
        type=str,
        help=f'input OSM file (default: {DEFAULT_OSM_FILE})',
    )
    argparser.add_argument(
        '--intersections',
        default=str(DEFAULT_INTERSECTIONS_CSV),
        type=str,
        help=f'intersection csv file (default: {DEFAULT_INTERSECTIONS_CSV})',
    )
    argparser.add_argument(
        '--output',
        '-o',
        default=str(DEFAULT_OUTPUT_NET),
        type=str,
        help=f'output SUMO net file (default: {DEFAULT_OUTPUT_NET})',
    )
    argparser.add_argument(
        '--report',
        default=None,
        type=str,
        help='intersection mapping json report. Default: output path with .intersections.json',
    )
    argparser.add_argument(
        '--keep-xodr',
        nargs='?',
        const=str(DEFAULT_KEEP_XODR),
        default=None,
        help=f'keep intermediate OpenDRIVE file. Optional path default: {DEFAULT_KEEP_XODR}',
    )
    argparser.add_argument(
        '--max-match-distance-m',
        default=120.0,
        type=float,
        help='max allowed distance between csv coordinate and matched junction (default: 120)',
    )
    argparser.add_argument(
        '--type-file',
        default=None,
        type=str,
        help='optional path to opendrive_netconvert.typ.xml',
    )
    argparser.add_argument(
        '--guess-tls',
        action='store_true',
        help='guess traffic lights at intersections (default: False)',
    )
    argparser.add_argument(
        '--debug',
        action='store_true',
        help='enable debug logging',
    )
    args = argparser.parse_args()

    logging.basicConfig(
        format='%(levelname)s: %(message)s',
        level=logging.DEBUG if args.debug else logging.INFO,
    )

    netconvert_osm_20(
        osm_file=args.osm,
        intersections_csv=args.intersections,
        output=args.output,
        report_file=args.report,
        guess_tls=args.guess_tls,
        keep_xodr=args.keep_xodr,
        max_match_distance_m=args.max_match_distance_m,
        type_file=args.type_file,
    )
