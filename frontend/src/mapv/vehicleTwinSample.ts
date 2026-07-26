import type { TrafficVehicleView } from '../types/traffic'

export interface VehicleTwinSample {
  [key: string]: unknown
  id: string
  point: [number, number, number]
  dir: number
  time: number
  modelType: number
}

export function createVehicleTwinSample(
  vehicle: TrafficVehicleView,
  longitude: number,
  latitude: number,
  time: number,
  modelType: number,
): VehicleTwinSample {
  return {
    id: vehicle.vehicle_id,
    point: [longitude, latitude, 0],
    dir: vehicle.angle * Math.PI / 180,
    time,
    modelType,
  }
}
