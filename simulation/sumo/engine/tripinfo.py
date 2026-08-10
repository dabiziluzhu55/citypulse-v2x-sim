"""Parse authoritative end-of-session metrics from SUMO tripinfo output."""

from __future__ import annotations

import math
import xml.etree.ElementTree as ET
from dataclasses import dataclass
from pathlib import Path
from typing import Mapping

from ..algorithm.policy import VehicleTypeMetadata


class TripInfoError(RuntimeError):
    """Raised when SUMO's tripinfo output cannot be used as a final result."""


@dataclass(frozen=True)
class TripInfoTotals:
    departed_vehicles: int = 0
    arrived_vehicles: int = 0
    fuel_consumed_mg: float = 0.0
    fuel_consumed_ml: float = 0.0


def load_tripinfo_totals(
    path: Path,
    vehicle_types: Mapping[str, VehicleTypeMetadata],
) -> TripInfoTotals:
    """Aggregate completed and unfinished trips after SUMO closes the output file."""

    try:
        root = ET.parse(path).getroot()
    except FileNotFoundError as exc:
        raise TripInfoError(f"SUMO did not write tripinfo output: {path}") from exc
    except ET.ParseError as exc:
        raise TripInfoError(f"SUMO wrote invalid tripinfo output {path}: {exc}") from exc

    departed = 0
    arrived = 0
    fuel_mg = 0.0
    fuel_ml = 0.0
    for trip in root.findall("tripinfo"):
        metadata = vehicle_types.get(str(trip.get("vType", "")))
        if metadata is None:
            # Disturbance and venue-event vehicles are not official controllable traffic.
            continue
        departed += 1
        try:
            arrival = float(trip.get("arrival", "-1"))
        except ValueError as exc:
            raise TripInfoError(
                f"Trip {trip.get('id')!r} has an invalid arrival time."
            ) from exc
        if not math.isfinite(arrival):
            raise TripInfoError(
                f"Trip {trip.get('id')!r} has a non-finite arrival time."
            )
        if arrival >= 0:
            arrived += 1

        emissions = trip.find("emissions")
        if emissions is None:
            raise TripInfoError(
                f"Trip {trip.get('id')!r} has no emissions result; "
                "the SUMO emissions device was not enabled."
            )
        try:
            vehicle_fuel_mg = float(emissions.get("fuel_abs", "0"))
        except ValueError as exc:
            raise TripInfoError(
                f"Trip {trip.get('id')!r} has invalid fuel consumption."
            ) from exc
        if not math.isfinite(vehicle_fuel_mg) or vehicle_fuel_mg < 0:
            raise TripInfoError(
                f"Trip {trip.get('id')!r} has invalid fuel consumption."
            )
        fuel_mg += vehicle_fuel_mg
        fuel_ml += vehicle_fuel_mg / metadata.fuel_density_mg_per_ml

    return TripInfoTotals(
        departed_vehicles=departed,
        arrived_vehicles=arrived,
        fuel_consumed_mg=fuel_mg,
        fuel_consumed_ml=fuel_ml,
    )
