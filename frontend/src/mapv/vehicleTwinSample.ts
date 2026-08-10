import type { TrafficVehicleView } from '../types/traffic'
import { VEHICLE_MODEL_BASE_Z } from './sceneElevation.ts'
import type { VehicleModelProfile } from './vehicleModelProfiles.ts'
import { moveFromFrontBumperToModelCenter } from './vehicleOrientation.ts'

export interface VehicleTwinSample {
  [key: string]: unknown
  id: string
  point: [number, number, number]
  dir: number
  time: number
  modelType: number
  scale: [number, number, number]
  color: string
  vehicleHeading: number
  modelForwardAxisAngle: number
}

const VEHICLE_COLORS = [
  '#f2f5f7',
  '#1f78d1',
  '#d94747',
  '#f2b84b',
  '#4b5663',
] as const

function hashVehicleId(vehicleId: string): number {
  return [...vehicleId].reduce(
    (value, character) => (value * 31 + character.charCodeAt(0)) >>> 0,
    0,
  )
}

export function createVehicleTwinSample(
  vehicle: TrafficVehicleView,
  longitude: number,
  latitude: number,
  time: number,
  profile: VehicleModelProfile,
  vehicleHeading: number,
  positionIsModelCenter = false,
): VehicleTwinSample {
  const color = VEHICLE_COLORS[hashVehicleId(vehicle.vehicle_id) % VEHICLE_COLORS.length]
  const center = positionIsModelCenter
    ? { longitude, latitude }
    : moveFromFrontBumperToModelCenter(
      { longitude, latitude },
      vehicleHeading,
      profile.targetLengthMeters / 2,
    )
  return {
    id: vehicle.vehicle_id,
    point: [center.longitude, center.latitude, VEHICLE_MODEL_BASE_Z],
    dir: vehicleHeading - profile.modelForwardAxisAngle,
    time,
    modelType: profile.modelType,
    scale: profile.scale,
    color,
    vehicleHeading,
    modelForwardAxisAngle: profile.modelForwardAxisAngle,
  }
}
