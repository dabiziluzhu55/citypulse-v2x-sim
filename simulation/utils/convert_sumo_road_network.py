"""Convert a local SUMO road network subset to WGS-84 GeoJSON."""

from __future__ import annotations

import argparse
import json
import math
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable

from pyproj import Transformer
from shapely.geometry import GeometryCollection, LineString, MultiLineString, Point, mapping
from shapely.ops import transform

DEFAULT_LANE_WIDTH_METERS = 3.5
WGS84_CRS = "EPSG:4326"
LOCAL_METRIC_CRS = "EPSG:32650"


def _deduplicate_consecutive(points: Iterable[tuple[float, float]]) -> list[tuple[float, float]]:
    result: list[tuple[float, float]] = []
    for point in points:
        normalized = (float(point[0]), float(point[1]))
        if not result or normalized != result[-1]:
            result.append(normalized)
    return result


def _line_parts(geometry: Any) -> list[LineString]:
    if isinstance(geometry, LineString):
        return [geometry] if not geometry.is_empty else []
    if isinstance(geometry, (MultiLineString, GeometryCollection)):
        return [part for item in geometry.geoms for part in _line_parts(item)]
    return []


def _resolve_lane_width(edge: Any) -> float:
    widths = [float(lane.getWidth()) for lane in edge.getLanes() if float(lane.getWidth()) > 0]
    return sum(widths) if widths else len(edge.getLanes()) * DEFAULT_LANE_WIDTH_METERS


def convert_network(
    net: Any,
    *,
    intersection_id: str,
    center_lon: float,
    center_lat: float,
    radius_m: float,
    source_name: str,
    simplify_tolerance_m: float = 0.0,
) -> dict[str, Any]:
    if radius_m <= 0:
        raise ValueError("radius_m must be positive")
    if simplify_tolerance_m < 0:
        raise ValueError("simplify_tolerance_m cannot be negative")

    to_metric = Transformer.from_crs(WGS84_CRS, LOCAL_METRIC_CRS, always_xy=True).transform
    to_wgs84 = Transformer.from_crs(LOCAL_METRIC_CRS, WGS84_CRS, always_xy=True).transform
    center_metric = transform(to_metric, Point(center_lon, center_lat))
    clip_area = center_metric.buffer(radius_m)

    features: list[dict[str, Any]] = []
    vertex_count = 0
    for edge in net.getEdges():
        edge_id = str(edge.getID())
        if edge_id.startswith(":"):
            continue

        xy_points = _deduplicate_consecutive(edge.getShape())
        if len(xy_points) < 2:
            continue
        wgs84_points = _deduplicate_consecutive(net.convertXY2LonLat(x, y) for x, y in xy_points)
        if len(wgs84_points) < 2 or not all(math.isfinite(value) for point in wgs84_points for value in point):
            continue

        metric_line = transform(to_metric, LineString(wgs84_points))
        clipped = metric_line.intersection(clip_area)
        if simplify_tolerance_m:
            clipped = clipped.simplify(simplify_tolerance_m, preserve_topology=True)

        parts = [part for part in _line_parts(clipped) if part.length > 0 and len(part.coords) >= 2]
        for segment_index, part in enumerate(parts):
            wgs84_line = transform(to_wgs84, part)
            coordinates = [[round(float(lon), 7), round(float(lat), 7)] for lon, lat in wgs84_line.coords]
            if len(coordinates) < 2:
                continue
            vertex_count += len(coordinates)
            features.append(
                {
                    "type": "Feature",
                    "id": edge_id if len(parts) == 1 else f"{edge_id}:{segment_index}",
                    "properties": {
                        "edge_id": edge_id,
                        "segment_index": segment_index,
                        "lane_count": len(edge.getLanes()),
                        "speed": round(float(edge.getSpeed()), 3),
                        "priority": int(edge.getPriority()),
                        "width_m": round(_resolve_lane_width(edge), 3),
                        "length_m": round(float(part.length), 3),
                        "source_crs": "WGS84",
                    },
                    "geometry": {"type": "LineString", "coordinates": coordinates},
                }
            )

    features.sort(key=lambda item: str(item["id"]))
    return {
        "type": "FeatureCollection",
        "metadata": {
            "intersection_id": intersection_id,
            "center": {"longitude": center_lon, "latitude": center_lat},
            "radius_m": radius_m,
            "source_network": source_name,
            "source_crs": "SUMO_XY",
            "output_crs": "WGS84",
            "metric_crs": LOCAL_METRIC_CRS,
            "simplify_tolerance_m": simplify_tolerance_m,
            "feature_count": len(features),
            "vertex_count": vertex_count,
        },
        "features": features,
    }


def write_geojson(collection: dict[str, Any], output: Path) -> None:
    output.parent.mkdir(parents=True, exist_ok=True)
    payload = dict(collection)
    metadata = dict(payload.get("metadata", {}))
    metadata["generated_at"] = datetime.now(timezone.utc).isoformat()
    payload["metadata"] = metadata
    output.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--net-file", required=True, type=Path)
    parser.add_argument("--intersection-id", required=True)
    parser.add_argument("--center-lon", required=True, type=float)
    parser.add_argument("--center-lat", required=True, type=float)
    parser.add_argument("--radius-m", required=True, type=float)
    parser.add_argument("--output", required=True, type=Path)
    parser.add_argument("--simplify-tolerance-m", type=float, default=0.0)
    return parser


def main() -> None:
    args = build_parser().parse_args()
    if not args.net_file.is_file():
        raise SystemExit(f"SUMO network does not exist: {args.net_file}")

    from backend.app.core.config import get_settings
    from backend.app.core.sumo_env import configure_sumo_home, import_sumolib

    configure_sumo_home(get_settings())
    sumolib = import_sumolib()
    net = sumolib.net.readNet(str(args.net_file))
    collection = convert_network(
        net,
        intersection_id=args.intersection_id,
        center_lon=args.center_lon,
        center_lat=args.center_lat,
        radius_m=args.radius_m,
        source_name=args.net_file.name,
        simplify_tolerance_m=args.simplify_tolerance_m,
    )
    write_geojson(collection, args.output)
    metadata = collection["metadata"]
    print(
        f"Wrote {metadata['feature_count']} roads / {metadata['vertex_count']} vertices "
        f"to {args.output} ({metadata['output_crs']})"
    )


if __name__ == "__main__":
    main()
