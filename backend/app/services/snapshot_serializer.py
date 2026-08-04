"""将SimulationSnapshot序列化为JSON字典"""

from __future__ import annotations

from dataclasses import asdict, is_dataclass
from enum import Enum
from typing import Any

from simulation.sumo.session import SimulationSnapshot, VehicleRuntimeSnapshot


class SnapshotSerializer:
    def __init__(self, coordinate_converter) -> None:
        self._coordinate_converter = coordinate_converter

    def serialize(self, snapshot: SimulationSnapshot) -> dict[str, Any]:
        payload = self._to_jsonable(snapshot)
        payload["vehicles"] = [
            self._serialize_vehicle(vehicle) for vehicle in snapshot.vehicles
        ]
        return payload

    def _serialize_vehicle(self, vehicle: VehicleRuntimeSnapshot) -> dict[str, Any]:
        data = asdict(vehicle)
        data["x"] = round(float(data["x"]), 3)
        data["y"] = round(float(data["y"]), 3)
        data["speed"] = round(float(data["speed"]), 3)
        data["angle"] = round(float(data["angle"]), 3)
        data["height"] = 0.0
        longitude, latitude = self._coordinate_converter.xy_to_lonlat(
            float(vehicle.x),
            float(vehicle.y),
        )
        data["longitude"] = longitude
        data["latitude"] = latitude
        return data

    def _to_jsonable(self, value: Any) -> Any:
        if is_dataclass(value):
            result = {}
            for key, item in asdict(value).items():
                result[key] = self._to_jsonable(item)
            if hasattr(value, "progress"):
                result["progress"] = round(float(result["progress"]), 4)
            return result
        if isinstance(value, Enum):
            return value.value
        if isinstance(value, dict):
            return {str(key): self._to_jsonable(item) for key, item in value.items()}
        if isinstance(value, (list, tuple)):
            return [self._to_jsonable(item) for item in value]
        if isinstance(value, float):
            return round(value, 6)
        return value
