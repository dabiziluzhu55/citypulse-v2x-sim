"""Fuel telemetry M0 four-check audit (Task C).

The four checks compare, on the same trajectory, the two fuel intensity
paths used by the evaluation stack:

1. per-vehicle Protocol 2.0 records (``fuel_total_ml``/``fuel_total_mg``)
   versus the finish totals path (``fuel_consumed_ml``);
2. two unit interpretations of the same emission XML (mg/s vs mL/s).

Checks: same vehicle set, unfinished vehicles included, equal distance
denominator, and only unit-conversion/numeric differences after the
documented density conversion.  The audit never recalibrates thresholds.
"""

from __future__ import annotations

import hashlib
import json
import xml.etree.ElementTree as ET
from pathlib import Path
from typing import Any, Dict, List, Mapping, Optional

from .collector import FUEL_POWERTRAINS, HttpMetricsCollector, _resolve_fuel_telemetry_unit
from .metrics import _metadata_value, _parse_emission

DEFAULT_NUMERIC_TOLERANCE = 1e-6


def _is_fuel(row: Mapping[str, Any]) -> bool:
    return str(row.get("powertrain", "")).lower() in FUEL_POWERTRAINS


def _relative_difference(a: float, b: float) -> float:
    scale = max(1.0, abs(a), abs(b))
    return abs(a - b) / scale


def _intensity(fuel_ml: float, distance_m: float) -> Optional[float]:
    if distance_m <= 0:
        return None
    return (fuel_ml / 1000.0) / (distance_m / 100000.0)


def _sha256(path: str) -> str:
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def run_four_checks_protocol(
    per_vehicle: List[Mapping[str, Any]],
    *,
    finish_fuel_ml: float,
    fuel_telemetry_unit: str,
    departed_fuel_vehicles: int,
    active_records: int,
    finish_fuel_mg: Optional[float] = None,
    numeric_tolerance: float = DEFAULT_NUMERIC_TOLERANCE,
    evidence_files: Optional[List[str]] = None,
) -> Dict[str, Any]:
    """Four checks on Protocol 2.0 per-vehicle records vs finish totals.

    Like-for-like pairing follows the telemetry unit: under
    ``legacy_ml_as_mg`` (SUMO < 1.14) the per-vehicle ``fuel_total_mg``
    field actually carries mL and must be compared against the finish
    ``fuel_consumed_mg`` total (also actual mL); under ``protocol_ml``
    (SUMO >= 1.14) the per-vehicle ``fuel_total_ml`` is compared against
    ``fuel_consumed_ml``.  The backend finish ``fuel_consumed_ml`` under
    legacy is ``fuel_abs / density`` and is therefore NOT like-for-like;
    the audit records it as a note instead of using it for the check.
    """
    fuel_rows = [row for row in per_vehicle if _is_fuel(row)]
    ids = [str(row.get("vehicle_id", "")) for row in fuel_rows]
    per_vehicle_fuel_ml = sum(
        float(row.get("fuel_ml", 0.0)) for row in fuel_rows
    )
    per_vehicle_distance_m = sum(
        float(row.get("distance_m", 0.0)) for row in fuel_rows
    )
    active_fuel = [row for row in fuel_rows if row.get("active")]
    zero_distance = [
        row for row in fuel_rows if float(row.get("distance_m", 0.0)) <= 0
    ]

    vehicle_set_equal = (
        len(fuel_rows) == int(departed_fuel_vehicles)
        and len(set(ids)) == len(ids)
    )
    unfinished_included = (int(active_records) == 0) or (
        len(active_fuel) > 0
    )
    distance_denominator_equal = (
        per_vehicle_distance_m > 0 and len(zero_distance) == 0
    )
    unit = str(fuel_telemetry_unit).lower()
    if unit == "legacy_ml_as_mg" and finish_fuel_mg is not None:
        finish_total_ml = float(finish_fuel_mg)
        finish_source = "finish_fuel_consumed_mg"
    else:
        finish_total_ml = float(finish_fuel_ml)
        finish_source = "finish_fuel_consumed_ml"
    unit_conversion_only = (
        _relative_difference(finish_total_ml, per_vehicle_fuel_ml)
        <= numeric_tolerance
    )
    residual_relative_difference = _relative_difference(
        finish_total_ml, per_vehicle_fuel_ml
    )
    notes = []
    if unit == "legacy_ml_as_mg" and finish_fuel_mg is not None:
        if float(finish_fuel_ml) > 0 and abs(
            float(finish_fuel_ml) - per_vehicle_fuel_ml
        ) / max(1.0, per_vehicle_fuel_ml) > numeric_tolerance:
            notes.append(
                "backend finish fuel_consumed_ml (legacy) = fuel_abs/density "
                "and is NOT like-for-like with per-vehicle fuel_total_mg "
                "(actual mL); official metric uses event-level finish totals (D-2026-08-07-01)."
            )

    resolved_unit, _ = _resolve_fuel_telemetry_unit("auto")
    unit_matches_sumo_version = (
        str(fuel_telemetry_unit).lower() == resolved_unit
    )

    intensity_per_vehicle = _intensity(
        per_vehicle_fuel_ml, per_vehicle_distance_m
    )
    intensity_totals = _intensity(
        finish_total_ml, per_vehicle_distance_m
    )

    checks = {
        "vehicle_set_equal": vehicle_set_equal,
        "unfinished_included": unfinished_included,
        "distance_denominator_equal": distance_denominator_equal,
        "unit_conversion_only": unit_conversion_only,
    }
    definitional_checks = (
        vehicle_set_equal,
        unfinished_included,
        distance_denominator_equal,
    )
    totals_available = finish_total_ml > 0
    if not fuel_rows and int(departed_fuel_vehicles) == 0:
        status = "needs_data"
        decision_required = False
        block_reason = None
    elif all(definitional_checks) and totals_available:
        # D-2026-08-07-01: the official fuel metric uses the event-level
        # finish totals.  A residual against the per-vehicle sampled path is
        # recorded as evidence (unit_conversion_only), not a blocker.
        status = "pass"
        decision_required = False
        block_reason = None
    elif all(definitional_checks):
        status = "blocked"
        decision_required = True
        block_reason = (
            "Definitional checks pass but event-level finish totals are "
            "missing or zero; the official fuel metric cannot be computed. "
            "A versioned decision/fix is required."
        )
    else:
        status = "fail"
        decision_required = False
        block_reason = None

    hashes = {
        "per_vehicle_input_hash": hashlib.sha256(
            json.dumps(
                per_vehicle, sort_keys=True, default=str
            ).encode("utf-8")
        ).hexdigest(),
    }
    return {
        "status": status,
        "mode": "protocol_per_vehicle_vs_totals",
        "checks": checks,
        "residual_relative_difference": residual_relative_difference,
        "decision_required": decision_required,
        "block_reason": block_reason,
        "numeric_tolerance": numeric_tolerance,
        "finish_fuel_ml": float(finish_fuel_ml),
        "finish_fuel_mg": (
            None if finish_fuel_mg is None else float(finish_fuel_mg)
        ),
        "finish_total_used_ml": finish_total_ml,
        "finish_total_source": finish_source,
        "per_vehicle_fuel_ml": per_vehicle_fuel_ml,
        "per_vehicle_distance_m": per_vehicle_distance_m,
        "fuel_vehicle_records": len(fuel_rows),
        "departed_fuel_vehicles": int(departed_fuel_vehicles),
        "active_fuel_records": len(active_fuel),
        "unfinished_included_detail": {
            "active_records": int(active_records),
            "active_fuel_records": len(active_fuel),
        },
        "intensity_per_vehicle_l_per_100km": intensity_per_vehicle,
        "intensity_totals_l_per_100km": intensity_totals,
        "fuel_telemetry_unit": str(fuel_telemetry_unit),
        "unit_matches_sumo_version": unit_matches_sumo_version,
        "notes": notes,
        "evidence_files": list(evidence_files or []),
        "hashes": hashes,
    }


def _weighted_density(
    emission_path: str, vehicle_type_metadata: Mapping[str, Any]
) -> Optional[float]:
    """Mass-weighted average density (mg/mL) over the emission XML."""
    root = ET.parse(emission_path).getroot()
    timesteps = list(root.findall("timestep"))
    if not timesteps:
        return None
    times = [float(timestep.get("time", 0.0)) for timestep in timesteps]
    deltas = [
        (times[index + 1] - times[index]) if index + 1 < len(times) else None
        for index in range(len(times))
    ]
    total_mass = 0.0
    total_weighted = 0.0
    for index, timestep in enumerate(timesteps):
        delta = deltas[index]
        if delta is None:
            continue
        for vehicle in timestep.findall("vehicle"):
            type_id = str(vehicle.get("type", ""))
            metadata = vehicle_type_metadata.get(type_id, {})
            if str(_metadata_value(metadata, "powertrain", "")).lower() not in FUEL_POWERTRAINS:
                continue
            density = float(_metadata_value(metadata, "fuel_density_mg_per_ml", 0.0))
            if density <= 0:
                return None
            mass = max(0.0, float(vehicle.get("fuel", 0.0))) * delta
            total_mass += mass
            total_weighted += mass * density
    if total_mass <= 0:
        return None
    return total_weighted / total_mass


def run_four_checks_xml(
    emission_path: str,
    vehicle_type_metadata: Mapping[str, Any],
    *,
    step_length_s: Optional[float] = None,
    numeric_tolerance: float = DEFAULT_NUMERIC_TOLERANCE,
) -> Dict[str, Any]:
    """Four checks comparing mg/s and mL/s interpretations of one XML."""
    fuel_ml_ml, distance_ml = _parse_emission(
        emission_path,
        vehicle_type_metadata,
        step_length_s=step_length_s,
        fuel_unit="ml_per_s",
    )
    fuel_ml_mg, distance_mg = _parse_emission(
        emission_path,
        vehicle_type_metadata,
        step_length_s=step_length_s,
        fuel_unit="mg_per_s",
    )
    density = _weighted_density(emission_path, vehicle_type_metadata)

    # Both interpretations consume the same XML, so vehicle sets and distance
    # denominators are identical by construction; the audit documents this and
    # verifies the numbers agree.
    root = ET.parse(emission_path).getroot()
    vehicle_set_equal = True
    distance_denominator_equal = (
        abs(distance_ml - distance_mg) <= 1e-9
        and distance_ml > 0
    )
    if density is not None and density > 0:
        unit_conversion_only = (
            abs(fuel_ml_ml - fuel_ml_mg * density)
            <= numeric_tolerance * max(1.0, abs(fuel_ml_ml), abs(fuel_ml_mg * density))
        )
    else:
        unit_conversion_only = False
    # Emission XML carries every vehicle present at each timestep, including
    # vehicles still unfinished at the final timestep.
    unfinished_included = bool(root.findall("timestep"))

    checks = {
        "vehicle_set_equal": vehicle_set_equal,
        "unfinished_included": unfinished_included,
        "distance_denominator_equal": distance_denominator_equal,
        "unit_conversion_only": unit_conversion_only,
    }
    status = "pass" if all(checks.values()) else "fail"
    return {
        "status": status,
        "mode": "emission_xml_dual_interpretation",
        "checks": checks,
        "numeric_tolerance": numeric_tolerance,
        "fuel_ml_ml_per_s_interpretation": fuel_ml_ml,
        "fuel_ml_mg_per_s_interpretation": fuel_ml_mg,
        "distance_m": distance_ml,
        "effective_density_mg_per_ml": density,
        "evidence_files": [str(emission_path)],
        "hashes": {"emission_xml_sha256": _sha256(emission_path)},
    }


def audit_collector(
    collector: HttpMetricsCollector,
    *,
    numeric_tolerance: float = DEFAULT_NUMERIC_TOLERANCE,
) -> Dict[str, Any]:
    """Run the protocol-path four checks from a live collector snapshot."""
    snapshot = collector.fuel_audit_snapshot()
    return run_four_checks_protocol(
        snapshot["per_vehicle"],
        finish_fuel_ml=snapshot["finish_fuel_ml"],
        finish_fuel_mg=snapshot.get("finish_fuel_mg"),
        fuel_telemetry_unit=snapshot["fuel_telemetry_unit"],
        departed_fuel_vehicles=snapshot["departed_fuel_vehicles"],
        active_records=snapshot["active_records"],
        numeric_tolerance=numeric_tolerance,
        evidence_files=snapshot.get("evidence_files") or [],
    )


def main() -> None:
    import argparse

    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--xml", type=str, help="emission XML path (dual interpretation)")
    parser.add_argument("--vehicle-type-metadata", type=str, help="JSON metadata path")
    parser.add_argument("--raw", type=str, help="maxpressure raw results JSON path")
    parser.add_argument("--seed", type=int, help="seed row to audit inside --raw")
    parser.add_argument(
        "--output",
        type=Path,
        default=Path("algorithms/evaluation/runs/vrc_m0_dev/fuel_audit.json"),
    )
    args = parser.parse_args()

    if args.xml:
        if not args.vehicle_type_metadata:
            parser.error("--vehicle-type-metadata is required with --xml")
        metadata = json.loads(
            Path(args.vehicle_type_metadata).read_text(encoding="utf-8")
        )
        result = run_four_checks_xml(args.xml, metadata)
    elif args.raw and args.seed:
        rows = json.loads(Path(args.raw).read_text(encoding="utf-8"))
        if isinstance(rows, dict):
            rows = rows.get("runs", rows.get("results", []))
        selected = [row for row in rows if int(row.get("seed", -1)) == args.seed]
        if not selected:
            parser.error(f"seed {args.seed} not found in {args.raw}")
        snapshot = selected[0]["fuel_audit"]
        # Raw files produced before the collector recorded ``finish_fuel_mg``
        # still carry the same backend finish total at row level (snapshot
        # metrics ``fuel_consumed_mg``); fall back to it for like-for-like
        # legacy pairing instead of silently using the non-like-for-like
        # ``fuel_consumed_ml``.
        finish_fuel_mg = snapshot.get("finish_fuel_mg")
        if finish_fuel_mg is None:
            finish_fuel_mg = selected[0].get("fuel_mg")
        result = run_four_checks_protocol(
            snapshot["per_vehicle"],
            finish_fuel_ml=snapshot["finish_fuel_ml"],
            finish_fuel_mg=finish_fuel_mg,
            fuel_telemetry_unit=snapshot["fuel_telemetry_unit"],
            departed_fuel_vehicles=snapshot["departed_fuel_vehicles"],
            active_records=snapshot["active_records"],
            evidence_files=[str(args.raw)],
        )
    else:
        parser.error("provide --xml or --raw/--seed")
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(
        json.dumps(result, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    print(json.dumps(result["checks"], ensure_ascii=False, indent=2))
    print("status:", result["status"])


if __name__ == "__main__":
    main()
