import routeTurnIndexJson from '../assets/vehicle-route-turn-index.json' with { type: 'json' }
import type { TrafficVehicleView } from '../types/traffic'

export type VehicleRoutePeriod = 'morning_peak' | 'off_peak' | 'evening_peak'

export interface VehicleRouteTurnHint {
  period: VehicleRoutePeriod
  flowId: string
  routeIndex: number
  fromEdge: string
  toEdge: string
  intersectionId: string
  connectionKey: string
  motionPathKey: string
  source: 'fixed_route_index'
}

export type VehicleRouteTurnResolutionStatus =
  | 'hit'
  | 'unavailable'
  | 'mismatch'
  | 'ambiguous'

export interface VehicleRouteTurnResolution {
  status: VehicleRouteTurnResolutionStatus
  hint: VehicleRouteTurnHint | null
  candidateCount: number
  reason?: string
  routeCursor?: number
}

interface RouteTurnCandidate {
  intersectionId: string
  connectionKey: string
  motionPathKey: string
  fromEdge: string
  fromLaneId: string
  fromLaneIndex: number
  toEdge: string
  toLaneId: string
  toLaneIndex: number
  viaLaneIds: string[]
}

type CompactRouteTurnEntry = [number, string, string, string[]]

interface VehicleRouteTurnIndexAsset {
  schemaVersion: 1
  networkSource: { sha256: string }
  edgeTable?: string[]
  connections: Record<string, RouteTurnCandidate>
  periods: Record<VehicleRoutePeriod, {
    routes: CompactRouteTurnEntry[][]
    edgeRoutes?: number[][]
    flows: Record<string, number>
  }>
}

const routeTurnIndex = routeTurnIndexJson as unknown as VehicleRouteTurnIndexAsset

export function normalizeRuntimeVehicleFlowId(vehicleId: string): string {
  return vehicleId.replace(/\.\d+$/, '')
}

export function resolveVehicleRouteTurnResolution(
  vehicle: TrafficVehicleView,
  period: string | null | undefined,
  intersectionId: string | null | undefined,
  lockedConnectionKey?: string,
  previousRouteCursor?: number,
): VehicleRouteTurnResolution {
  if (!period || !(period in routeTurnIndex.periods) || !intersectionId) {
    return {
      status: 'unavailable',
      hint: null,
      candidateCount: 0,
      reason: 'route_context_unavailable',
    }
  }
  const typedPeriod = period as VehicleRoutePeriod
  const flowId = normalizeRuntimeVehicleFlowId(vehicle.vehicle_id)
  const periodIndex = routeTurnIndex.periods[typedPeriod]
  const compactRouteIndex = periodIndex.flows[flowId]
  const turns = Number.isInteger(compactRouteIndex)
    ? periodIndex.routes[compactRouteIndex]
    : null
  const encodedEdgeRoute = Number.isInteger(compactRouteIndex)
    ? periodIndex.edgeRoutes?.[compactRouteIndex]
    : null
  const edgeRoute = encodedEdgeRoute?.map((index) => routeTurnIndex.edgeTable?.[index] ?? '')
  if (!turns?.length) {
    return {
      status: 'unavailable',
      hint: null,
      candidateCount: 0,
      reason: 'vehicle_not_in_fixed_route_index',
    }
  }
  // The backend uses -1 for snapshots that do not carry route telemetry. Treat
  // it as unknown; filtering the fixed route with -1 rejects every valid turn.
  const routeIndex = Number.isInteger(vehicle.route_index) && Number(vehicle.route_index) >= 0
    ? Number(vehicle.route_index)
    : null
  const matchingEdgeIndexes = edgeRoute?.flatMap((edge, index) => (
    edge === vehicle.road_id ? [index] : []
  )) ?? []
  const routeCursor = routeIndex != null
    && edgeRoute?.[routeIndex] === vehicle.road_id
    ? routeIndex
    : vehicle.road_id.startsWith(':')
      ? previousRouteCursor
      : matchingEdgeIndexes.find((index) => index >= (previousRouteCursor ?? 0))
        ?? matchingEdgeIndexes.at(-1)
  const entries = turns.filter((turn) => {
    const onIncomingEdge = vehicle.road_id === turn[1]
    const onOutgoingEdge = vehicle.road_id === turn[2]
    const onInternalEdge = vehicle.road_id.startsWith(':')
    const effectiveIndex = routeIndex ?? routeCursor
    const routeIndexMatches = effectiveIndex == null
      || (onIncomingEdge && turn[0] === effectiveIndex)
      || (onOutgoingEdge && turn[0] + 1 === effectiveIndex)
      || (onInternalEdge && (turn[0] === effectiveIndex || turn[0] + 1 === effectiveIndex))
    return routeIndexMatches && (onIncomingEdge || onOutgoingEdge || onInternalEdge)
  })
  const candidates = entries.flatMap((turn) => turn[3]
    .map((key) => routeTurnIndex.connections[key])
    .filter((candidate): candidate is RouteTurnCandidate => Boolean(candidate))
    .map((candidate) => ({ turn, candidate })))
    .filter(({ candidate }) => (
      candidate.intersectionId === intersectionId
      && (
        vehicle.lane_id === candidate.fromLaneId
        || vehicle.lane_id === candidate.toLaneId
        || candidate.viaLaneIds.includes(vehicle.lane_id)
      )
    ))
  let unique = [...new Map(candidates.map((item) => [item.candidate.connectionKey, item])).values()]
  const alreadyOnOutgoingEdge = unique.length > 0 && unique.every(({ turn }) => (
    vehicle.road_id === turn[2]
  ))
  if (
    unique.length > 1
    && !alreadyOnOutgoingEdge
    && Number.isInteger(vehicle.target_lane_index)
  ) {
    unique = unique.filter(({ candidate }) => (
      candidate.toLaneIndex === Number(vehicle.target_lane_index)
    ))
  }
  if (lockedConnectionKey) {
    unique = unique.filter(({ candidate }) => candidate.connectionKey === lockedConnectionKey)
  }
  if (unique.length !== 1) {
    return {
      status: unique.length > 1 ? 'ambiguous' : 'mismatch',
      hint: null,
      candidateCount: unique.length,
      reason: unique.length > 1
        ? 'multiple_route_connections_match_vehicle'
        : 'indexed_route_does_not_match_live_edge_or_lane',
      routeCursor,
    }
  }
  const { turn, candidate } = unique[0]
  return {
    status: 'hit',
    candidateCount: 1,
    routeCursor: vehicle.road_id === turn[2] ? turn[0] + 1 : turn[0],
    hint: {
      period: typedPeriod,
      flowId,
      routeIndex: turn[0],
      fromEdge: turn[1],
      toEdge: turn[2],
      intersectionId: candidate.intersectionId,
      connectionKey: candidate.connectionKey,
      motionPathKey: candidate.motionPathKey,
      source: 'fixed_route_index',
    },
  }
}

export function resolveVehicleRouteTurnHint(
  vehicle: TrafficVehicleView,
  period: string | null | undefined,
  intersectionId: string | null | undefined,
  lockedConnectionKey?: string,
  previousRouteCursor?: number,
): VehicleRouteTurnHint | null {
  return resolveVehicleRouteTurnResolution(
    vehicle,
    period,
    intersectionId,
    lockedConnectionKey,
    previousRouteCursor,
  ).hint
}

export function vehicleRouteTurnIndexNetworkSha256(): string {
  return routeTurnIndex.networkSource.sha256
}
