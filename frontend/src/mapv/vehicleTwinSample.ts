import type { TrafficVehicleView } from '../types/traffic'
import { VEHICLE_MODEL_BASE_Z } from './sceneElevation.ts'
import type { VehicleModelProfile } from './vehicleModelProfiles.ts'
import { moveFromFrontBumperToModelCenter, unwrapHeading } from './vehicleOrientation.ts'
import type { LanePoseTransitionKind } from './realistic/intersectionLaneHeading.ts'
import type { VehicleConnectionLockStage } from './vehicleRouteTurnIndex.ts'

export type VehiclePoseSource = 'topology' | 'lane_change' | 'raw' | 'held'
export type VehicleMotionSampleQuality = 'authoritative' | 'held' | 'missing'
export type VehiclePresentationState = 'normal' | 'globalHeld'
export type DynamicVehicleRouteSource =
  | 'fixed_route'
  | 'live_via'
  | 'buffered_lookahead'
  | 'unresolved'

export interface DynamicConnectionEvidence {
  source: DynamicVehicleRouteSource
  connectionKey?: string
  observedLaneId: string
  fromLaneId?: string
  toLaneId?: string
  viaLaneIds?: string[]
}
export type DynamicRouteEvidence = DynamicConnectionEvidence
export type RoadTransitionKind =
  | 'same_path'
  | 'topology_successor'
  | 'lane_change'
  | 'raw_continuous'
  | 'incompatible'

export interface VehicleTwinMotionMetadata {
  canonicalSegmentId?: string
  canonicalRouteEvidence?: string
  connectionKey?: string
  routeHintSource?: 'fixed_route_index' | 'live_topology'
  connectionLockStage?: VehicleConnectionLockStage
  dynamicConnectionEvidence?: DynamicConnectionEvidence
  laneChangeCorridorKey?: string
  motionPathBridgeKey?: string
  corridorMotionPathKeys?: string[]
  detailedCorridorValidation?: boolean
  rawTransitionValidated?: boolean
  intermediatePoseValid?: boolean
  intermediateValidationReason?: string
  authoritativeSourceTimeSeconds?: number
  authoritativeSourceLongitude?: number
  authoritativeSourceLatitude?: number
  displayElapsedSeconds?: number
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
  vehicleWidthMeters?: number
  sourceAllowedSpeedMetersPerSecond?: number
  maximumAccelerationMetersPerSecondSquared?: number
  predictionMaximumPathArcDistanceMeters?: number
  sourceArcDistanceMeters?: number
  sourceLateralOffsetMeters?: number
  authoritativeArcDistanceMeters?: number
  authoritativePathArcDistanceMeters?: number
  presentationState?: VehiclePresentationState
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
  canonicalSegmentId?: string
  canonicalRouteEvidence?: string
  connectionKey?: string
  routeHintSource?: 'fixed_route_index' | 'live_topology'
  connectionLockStage?: VehicleConnectionLockStage
  dynamicConnectionEvidence?: DynamicConnectionEvidence
  laneChangeCorridorKey?: string
  motionPathBridgeKey?: string
  corridorMotionPathKeys?: string[]
  detailedCorridorValidation?: boolean
  rawTransitionValidated?: boolean
  intermediatePoseValid?: boolean
  intermediateValidationReason?: string
  authoritativeSourceTimeSeconds?: number
  authoritativeSourceLongitude?: number
  authoritativeSourceLatitude?: number
  displayElapsedSeconds?: number
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
  vehicleWidthMeters?: number
  sourceAllowedSpeedMetersPerSecond?: number
  maximumAccelerationMetersPerSecondSquared?: number
  predictionMaximumPathArcDistanceMeters?: number
  sourceArcDistanceMeters?: number
  sourceLateralOffsetMeters?: number
  authoritativeArcDistanceMeters?: number
  authoritativePathArcDistanceMeters?: number
  presentationState?: VehiclePresentationState
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
    vehicleWidthMeters: profile.targetWidthMeters,
    sourceAllowedSpeedMetersPerSecond: Math.max(
      0,
      Number(vehicle.allowed_speed) || Number(vehicle.speed) || 0,
    ),
    maximumAccelerationMetersPerSecondSquared:
      profile.maximumAccelerationMetersPerSecondSquared,
    sourceSpeedMetersPerSecond: Math.max(0, Number(vehicle.speed) || 0),
    authoritativeSourceTimeSeconds: time / 1_000,
    sampleQuality: motion.poseSource === 'held' ? 'held' : 'authoritative',
    ...motion,
  }
}
