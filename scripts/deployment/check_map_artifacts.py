#!/usr/bin/env python3
"""Verify pre-generated WGS84 GeoJSON map artifacts before Backend deployment."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--generated-dir",
        type=Path,
        default=Path("data/maps/sumo/generated"),
    )
    parser.add_argument(
        "--intersections",
        nargs="*",
        default=[f"demo_{index}" for index in range(1, 21)],
    )
    args = parser.parse_args()
    geojson_dir = args.generated_dir / "geojson"
    missing: list[str] = []
    invalid: list[str] = []
    for intersection_id in args.intersections:
        path = geojson_dir / f"{intersection_id}.roads.wgs84.geojson"
        if not path.is_file():
            missing.append(str(path))
            continue
        try:
            payload = json.loads(path.read_text(encoding="utf-8"))
            metadata = payload.get("metadata", {})
            if metadata.get("intersection_id") != intersection_id:
                invalid.append(f"{path}: intersection_id mismatch")
            if metadata.get("output_crs") != "WGS84":
                invalid.append(f"{path}: output_crs must be WGS84")
        except (OSError, json.JSONDecodeError) as exc:
            invalid.append(f"{path}: {exc}")
    if missing:
        print("Missing GeoJSON artifacts:", file=sys.stderr)
        for item in missing:
            print(f"  - {item}", file=sys.stderr)
    if invalid:
        print("Invalid GeoJSON artifacts:", file=sys.stderr)
        for item in invalid:
            print(f"  - {item}", file=sys.stderr)
    if missing or invalid:
        return 1
    print(f"OK: {len(args.intersections)} map artifacts under {geojson_dir}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
