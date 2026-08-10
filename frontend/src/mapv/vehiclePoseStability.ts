import type { TrafficVehicleView } from '../types/traffic'

export const MAX_VISUAL_BACKTRACK_METERS = 0.5
export const MIN_VISUAL_BUMPER_GAP_METERS = 1

export interface VehiclePoseState {
  backendDistance?: number
  routeId?: string
  routeIndex?: number
  laneId: string
  trackKey?: string
  trackProgress?: number
  trackDistanceMeters?: number
  crossedStopLine: boolean
  laneResolutionFailures: number
  longitude: number
  latitude: number
  heading: number
  lastSeenSequence: number
}

export interface VisualQueueVehicle {
  id: string
  trackKey: string
  lanePosition?: number
  naturalCenterDistanceMeters: number
  lengthMeters: number
  previousCenterDistanceMeters?: number
}

export interface VisualQueueConstraint {
  maximumCenterDistanceMeters: number | null
  hidden: boolean
}

function finiteNumber(value: number | undefined): number | undefined {
  return typeof value === 'number' && Number.isFinite(value) ? value : undefined
}

export function routeAdvanced(
  previous: VehiclePoseState | null,
  vehicle: TrafficVehicleView,
): boolean {
  if (!previous) return false
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
  const byTrack = new Map<string, VisualQueueVehicle[]>()
  for (const vehicle of vehicles) {
    const group = byTrack.get(vehicle.trackKey) ?? []
    group.push(vehicle)
    byTrack.set(vehicle.trackKey, group)
  }
  for (const group of byTrack.values()) {
    group.sort((left, right) => (
      (right.lanePosition ?? right.naturalCenterDistanceMeters)
      - (left.lanePosition ?? left.naturalCenterDistanceMeters)
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
      const hidden = adjustedCenter < 0 || wouldReverseTooFar
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
