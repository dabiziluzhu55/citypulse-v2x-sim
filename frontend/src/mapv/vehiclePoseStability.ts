import type { TrafficVehicleView } from '../types/traffic'
import type { LanePoseTransitionKind } from './realistic/intersectionLaneHeading.ts'
import type { RoadTransitionKind } from './vehicleTwinSample.ts'

export const MAX_VISUAL_BACKTRACK_METERS = 0.5
export const MIN_VISUAL_BUMPER_GAP_METERS = 1
export const MIN_STABLE_DISPLACEMENT_LIMIT_METERS = 6

export interface VehiclePoseState {
  telemetryReliable: boolean
  backendDistance?: number
  routeId?: string
  routeIndex?: number
  roadId: string
  laneIndex?: number
  laneId: string
  trackKey?: string
  motionPathKey?: string
  segmentKey?: string
  occupancyKey?: string
  trackProgress?: number
  trackDistanceMeters?: number
  arcDistanceMeters?: number
  pathArcDistanceMeters?: number
  matchConfidence?: number
  transitionKind?: LanePoseTransitionKind | 'raw_fallback'
  roadTransitionKind?: RoadTransitionKind
  crossedStopLine: boolean
  laneResolutionFailures: number
  lifecycle: 'stable' | 'laneChanging' | 'recovering' | 'rawFallback' | 'missing'
  lastStableElapsedSeconds: number
  longitude: number
  latitude: number
  heading: number
  elapsedSeconds: number
  motionEpoch: number
  lastSeenSequence: number
}

export const MAX_RAW_TRANSITION_HEADING_DELTA = 35 * Math.PI / 180

export interface RoadTransitionInput {
  previous: Pick<VehiclePoseState, 'roadId' | 'laneId' | 'motionPathKey'> | null
  roadId: string
  laneId: string
  motionPathKey?: string
  laneTransitionKind?: LanePoseTransitionKind | 'raw_fallback'
  laneChanging: boolean
  rawFallback: boolean
  displacementStable: boolean
  headingDeltaRadians?: number
}

export function classifyRoadTransition(input: RoadTransitionInput): RoadTransitionKind {
  const previous = input.previous
  if (!previous) return 'same_path'
  if (input.laneChanging || input.laneTransitionKind === 'lane_change') return 'lane_change'
  if (
    input.motionPathKey
    && previous.motionPathKey
    && input.motionPathKey === previous.motionPathKey
  ) return 'same_path'
  if (input.laneTransitionKind === 'topological') return 'topology_successor'
  if (previous.roadId === input.roadId && previous.laneId === input.laneId) return 'same_path'
  const headingCompatible = input.headingDeltaRadians == null
    || input.headingDeltaRadians <= MAX_RAW_TRANSITION_HEADING_DELTA
  if (input.rawFallback && input.displacementStable && headingCompatible) {
    return 'raw_continuous'
  }
  return 'incompatible'
}

export interface VehiclePosePoint {
  longitude: number
  latitude: number
}

export interface VisualQueueVehicle {
  id: string
  occupancyKey: string
  lanePosition?: number
  naturalCenterDistanceMeters: number
  lengthMeters: number
  previousCenterDistanceMeters?: number
  minimumCenterDistanceMeters?: number
}

export interface VisualQueueConstraint {
  maximumCenterDistanceMeters: number | null
  hidden: boolean
}

function finiteNumber(value: number | undefined): number | undefined {
  return typeof value === 'number' && Number.isFinite(value) ? value : undefined
}

export function vehicleTelemetryIsPlaceholder(vehicle: TrafficVehicleView): boolean {
  return (vehicle.lane_position ?? 0) === 0
    && (vehicle.distance ?? 0) === 0
    && (vehicle.route_id ?? '') === ''
    && (vehicle.route_index ?? -1) === -1
}

export function reliableVehicleLanePosition(vehicle: TrafficVehicleView): number | undefined {
  if (vehicleTelemetryIsPlaceholder(vehicle)) return undefined
  return finiteNumber(vehicle.lane_position)
}

export function maximumStableVehicleDisplacementMeters(
  speedMetersPerSecond: number,
  deltaSimulationSeconds: number,
): number {
  return Math.max(
    MIN_STABLE_DISPLACEMENT_LIMIT_METERS,
    Math.max(0, speedMetersPerSecond) * Math.max(0, deltaSimulationSeconds) * 2 + 2,
  )
}

export function vehiclePoseDisplacementMeters(
  left: VehiclePosePoint,
  right: VehiclePosePoint,
): number {
  const latitude = (left.latitude + right.latitude) / 2 * Math.PI / 180
  const east = (right.longitude - left.longitude) * Math.cos(latitude) * 110_900
  const north = (right.latitude - left.latitude) * 110_900
  return Math.hypot(east, north)
}

export function vehiclePoseDisplacementIsStable(
  previous: Pick<VehiclePoseState, 'longitude' | 'latitude' | 'elapsedSeconds'>,
  current: VehiclePosePoint,
  speedMetersPerSecond: number,
  elapsedSeconds: number,
): boolean {
  return vehiclePoseDisplacementMeters(previous, current) <= maximumStableVehicleDisplacementMeters(
    speedMetersPerSecond,
    elapsedSeconds - previous.elapsedSeconds,
  )
}

export function routeAdvanced(
  previous: VehiclePoseState | null,
  vehicle: TrafficVehicleView,
): boolean {
  if (!previous || !previous.telemetryReliable || vehicleTelemetryIsPlaceholder(vehicle)) return false
  const previousIndex = finiteNumber(previous.routeIndex)
  const nextIndex = finiteNumber(vehicle.route_index)
  if (previousIndex != null && nextIndex != null && nextIndex > previousIndex) return true
  return Boolean(previous.routeId && vehicle.route_id && previous.routeId !== vehicle.route_id)
}

export function minimumForwardTrackDistance(
  previous: VehiclePoseState | null,
  vehicle: TrafficVehicleView,
): number | undefined {
  if (!previous?.trackKey || previous.trackDistanceMeters == null) return undefined
  if (!previous.telemetryReliable || vehicleTelemetryIsPlaceholder(vehicle)) return undefined
  if (previous.laneId !== vehicle.lane_id || routeAdvanced(previous, vehicle)) return undefined
  const previousBackendDistance = finiteNumber(previous.backendDistance)
  const currentBackendDistance = finiteNumber(vehicle.distance)
  const sameRoute = !previous.routeId || !vehicle.route_id || previous.routeId === vehicle.route_id
  const sameRouteIndex = previous.routeIndex == null
    || vehicle.route_index == null
    || previous.routeIndex === vehicle.route_index
  if (
    sameRoute
    && sameRouteIndex
    && previousBackendDistance != null
    && currentBackendDistance != null
    && currentBackendDistance + 1e-6 >= previousBackendDistance
  ) return previous.trackDistanceMeters - MAX_VISUAL_BACKTRACK_METERS
  return undefined
}

export function shouldAllowStopClamp(
  previous: VehiclePoseState | null,
  vehicle: TrafficVehicleView,
): boolean {
  if (previous?.crossedStopLine) return false
  if (routeAdvanced(previous, vehicle)) return false
  return true
}

export function resolveCrossedStopLine(
  previous: VehiclePoseState | null,
  vehicle: TrafficVehicleView,
  naturalFrontDistanceMeters: number,
  stopFrontLimitDistanceMeters?: number,
  stopClamped = false,
): boolean {
  if (previous?.crossedStopLine) return true
  if (routeAdvanced(previous, vehicle)) return true
  if (stopFrontLimitDistanceMeters == null || stopClamped) return false
  return naturalFrontDistanceMeters > stopFrontLimitDistanceMeters + 0.05
}

export function resolveVisualQueueConstraints(
  vehicles: VisualQueueVehicle[],
  bumperGapMeters = MIN_VISUAL_BUMPER_GAP_METERS,
): Map<string, VisualQueueConstraint> {
  const output = new Map<string, VisualQueueConstraint>()
  const byOccupancy = new Map<string, VisualQueueVehicle[]>()
  for (const vehicle of vehicles) {
    const group = byOccupancy.get(vehicle.occupancyKey) ?? []
    group.push(vehicle)
    byOccupancy.set(vehicle.occupancyKey, group)
  }
  for (const group of byOccupancy.values()) {
    group.sort((left, right) => (
      right.naturalCenterDistanceMeters - left.naturalCenterDistanceMeters
      || (right.lanePosition ?? 0) - (left.lanePosition ?? 0)
    ))
    let front: { center: number; halfLength: number } | null = null
    for (const vehicle of group) {
      if (!front) {
        output.set(vehicle.id, { maximumCenterDistanceMeters: null, hidden: false })
        front = {
          center: vehicle.naturalCenterDistanceMeters,
          halfLength: vehicle.lengthMeters / 2,
        }
        continue
      }
      const maximumCenter = front.center
        - front.halfLength
        - vehicle.lengthMeters / 2
        - bumperGapMeters
      const adjustedCenter = Math.min(vehicle.naturalCenterDistanceMeters, maximumCenter)
      const wouldReverseTooFar = vehicle.previousCenterDistanceMeters != null
        && adjustedCenter < vehicle.previousCenterDistanceMeters - MAX_VISUAL_BACKTRACK_METERS
      const belowTrackStart = vehicle.minimumCenterDistanceMeters != null
        && adjustedCenter < vehicle.minimumCenterDistanceMeters
      const hidden = belowTrackStart || wouldReverseTooFar
      output.set(vehicle.id, {
        maximumCenterDistanceMeters: hidden ? null : maximumCenter,
        hidden,
      })
      if (!hidden) {
        front = { center: adjustedCenter, halfLength: vehicle.lengthMeters / 2 }
      }
    }
  }
  return output
}
