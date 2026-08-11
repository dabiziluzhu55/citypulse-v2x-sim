import type { TrafficVehicleView } from '../types/traffic'
import { VEHICLE_MODEL_BASE_Z } from './sceneElevation.ts'
import type { VehicleModelProfile } from './vehicleModelProfiles.ts'
import { moveFromFrontBumperToModelCenter, unwrapHeading } from './vehicleOrientation.ts'
import type { LanePoseTransitionKind } from './realistic/intersectionLaneHeading.ts'

export type VehiclePoseSource = 'topology' | 'lane_change' | 'raw' | 'held'
export type VehicleMotionSampleQuality = 'authoritative' | 'predicted' | 'held' | 'missing'
export type RoadTransitionKind =
  | 'same_path'
  | 'topology_successor'
  | 'lane_change'
  | 'raw_continuous'
  | 'incompatible'

export interface VehicleTwinMotionMetadata {
  motionPathKey?: string
  segmentKey?: string
  occupancyKey?: string
  arcDistanceMeters?: number
  pathArcDistanceMeters?: number
  sourceSpeedMetersPerSecond?: number
  transitionKind?: LanePoseTransitionKind | 'raw_fallback'
  roadTransitionKind?: RoadTransitionKind
  poseSource?: VehiclePoseSource
  sampleQuality?: VehicleMotionSampleQuality
  predictionBlocked?: boolean
  stopReason?: string
  vehicleLengthMeters?: number
  predictionMaximumPathArcDistanceMeters?: number
}

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
  sceneGeneration?: number
  motionEpoch?: number
  motionPathKey?: string
  segmentKey?: string
  occupancyKey?: string
  arcDistanceMeters?: number
  pathArcDistanceMeters?: number
  sourceSpeedMetersPerSecond?: number
  unwrappedModelDirection?: number
  transitionKind?: LanePoseTransitionKind | 'raw_fallback'
  roadTransitionKind?: RoadTransitionKind
  poseSource?: VehiclePoseSource
  sampleQuality?: VehicleMotionSampleQuality
  predictionBlocked?: boolean
  stopReason?: string
  predictionElapsedSeconds?: number
  reconciling?: boolean
  vehicleLengthMeters?: number
  predictionMaximumPathArcDistanceMeters?: number
}

const VEHICLE_COLORS = [
  '#f2f5f7',
  '#1f78d1',
  '#d94747',
  '#f2b84b',
  '#4b5663',
] as const

export function unwrapVehicleModelDirection(
  previousModelDirection: number | null,
  vehicleHeading: number,
  modelForwardAxisAngle: number,
): number {
  return unwrapHeading(previousModelDirection, vehicleHeading - modelForwardAxisAngle)
}

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
  motion: VehicleTwinMotionMetadata = {},
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
    vehicleLengthMeters: profile.targetLengthMeters,
    sourceSpeedMetersPerSecond: Math.max(0, Number(vehicle.speed) || 0),
    sampleQuality: motion.poseSource === 'held' ? 'held' : 'authoritative',
    ...motion,
  }
}
