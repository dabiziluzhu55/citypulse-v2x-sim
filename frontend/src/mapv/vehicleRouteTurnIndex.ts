import routeTurnIndexJson from '../assets/vehicle-route-turn-index.json' with { type: 'json' }
import type { TrafficVehicleView } from '../types/traffic'

export type VehicleRoutePeriod = 'morning_peak' | 'off_peak' | 'evening_peak'

export interface VehicleRouteTurnHint {
  period: VehicleRoutePeriod | null
  flowId: string
  routeIndex: number
  fromEdge: string
  toEdge: string
  intersectionId: string
  connectionKey: string
  motionPathKey: string
  source: 'fixed_route_index' | 'live_topology'
}

export type VehicleRouteTurnResolutionStatus =
  | 'hit'
  | 'pending'
  | 'unavailable'
  | 'mismatch'
  | 'ambiguous'

export type VehicleConnectionLockStage = 'unlocked' | 'internal' | 'exiting'

export interface VehicleConnectionLock {
  stage: VehicleConnectionLockStage
  connectionKey?: string
  motionPathKey?: string
  fromLaneId?: string
  toLaneId?: string
  viaLaneIds?: string[]
  source?: 'fixed_route_index' | 'live_topology'
}

export interface VehicleRouteTurnResolution {
  status: VehicleRouteTurnResolutionStatus
  hint: VehicleRouteTurnHint | null
  candidateCount: number
  reason?: string
  routeCursor?: number
  connectionLock: VehicleConnectionLock
  releasedStaleLock: boolean
}

export interface RouteTurnCandidate {
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

const unlockedConnection: VehicleConnectionLock = { stage: 'unlocked' }
const topologyCandidatesByIntersectionLane = new Map<string, RouteTurnCandidate[]>()
for (const candidate of Object.values(routeTurnIndex.connections)) {
  for (const laneId of new Set([
    candidate.fromLaneId,
    candidate.toLaneId,
    ...candidate.viaLaneIds,
  ])) {
    const key = `${candidate.intersectionId}\u0000${laneId}`
    const indexed = topologyCandidatesByIntersectionLane.get(key) ?? []
    indexed.push(candidate)
    topologyCandidatesByIntersectionLane.set(key, indexed)
  }
}

function connectionStage(
  vehicle: TrafficVehicleView,
  candidate: RouteTurnCandidate,
): VehicleConnectionLockStage {
  if (candidate.viaLaneIds.includes(vehicle.lane_id) || vehicle.road_id.startsWith(':')) {
    return 'internal'
  }
  if (vehicle.lane_id === candidate.toLaneId || vehicle.road_id === candidate.toEdge) {
    return 'exiting'
  }
  // A connection selected on the approach is already locked. The `internal`
  // stage covers the confirmed approach and the junction itself; `exiting`
  // starts once the vehicle reaches the connection's outgoing lane.
  return 'internal'
}

function lockForCandidate(
  vehicle: TrafficVehicleView,
  candidate: RouteTurnCandidate,
  source: VehicleConnectionLock['source'],
): VehicleConnectionLock {
  return {
    stage: connectionStage(vehicle, candidate),
    connectionKey: candidate.connectionKey,
    motionPathKey: candidate.motionPathKey,
    fromLaneId: candidate.fromLaneId,
    toLaneId: candidate.toLaneId,
    viaLaneIds: [...candidate.viaLaneIds],
    source,
  }
}

function normalizeConnectionLock(
  value?: VehicleConnectionLock | string,
): VehicleConnectionLock {
  if (typeof value === 'string') {
    const candidate = routeTurnIndex.connections[value]
    return candidate
      ? {
          stage: 'internal',
          connectionKey: candidate.connectionKey,
          motionPathKey: candidate.motionPathKey,
          fromLaneId: candidate.fromLaneId,
          toLaneId: candidate.toLaneId,
          viaLaneIds: [...candidate.viaLaneIds],
          source: 'fixed_route_index',
        }
      : unlockedConnection
  }
  return value ?? unlockedConnection
}

function connectionContainsLiveLane(
  candidate: RouteTurnCandidate,
  vehicle: TrafficVehicleView,
): boolean {
  return (vehicle.lane_id === candidate.fromLaneId && vehicle.road_id === candidate.fromEdge)
    || (vehicle.lane_id === candidate.toLaneId && vehicle.road_id === candidate.toEdge)
    || (vehicle.road_id.startsWith(':') && candidate.viaLaneIds.includes(vehicle.lane_id))
}

function liveTopologyCandidates(
  vehicle: TrafficVehicleView,
  intersectionId: string,
): RouteTurnCandidate[] {
  return topologyCandidatesByIntersectionLane.get(
    `${intersectionId}\u0000${vehicle.lane_id}`,
  ) ?? []
}

export function vehicleRouteConnection(connectionKey: string): RouteTurnCandidate | null {
  return routeTurnIndex.connections[connectionKey] ?? null
}

export function normalizeRuntimeVehicleFlowId(vehicleId: string): string {
  return vehicleId.replace(/\.\d+$/, '')
}

export function resolveVehicleRouteTurnResolution(
  vehicle: TrafficVehicleView,
  period: string | null | undefined,
  intersectionId: string | null | undefined,
  previousConnectionLock?: VehicleConnectionLock | string,
  previousRouteCursor?: number,
): VehicleRouteTurnResolution {
  if (!intersectionId) {
    return {
      status: 'unavailable',
      hint: null,
      candidateCount: 0,
      reason: 'route_context_unavailable',
      connectionLock: unlockedConnection,
      releasedStaleLock: false,
    }
  }
  const typedPeriod = period && period in routeTurnIndex.periods
    ? period as VehicleRoutePeriod
    : null
  const flowId = normalizeRuntimeVehicleFlowId(vehicle.vehicle_id)
  const periodIndex = typedPeriod ? routeTurnIndex.periods[typedPeriod] : null
  const compactRouteIndex = periodIndex?.flows[flowId]
  const turns = Number.isInteger(compactRouteIndex)
    ? periodIndex!.routes[compactRouteIndex!]
    : null
  const encodedEdgeRoute = Number.isInteger(compactRouteIndex)
    ? periodIndex!.edgeRoutes?.[compactRouteIndex!]
    : null
  const edgeRoute = encodedEdgeRoute?.map((index) => routeTurnIndex.edgeTable?.[index] ?? '')
  const previousLock = normalizeConnectionLock(previousConnectionLock)
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
  const entries = (turns ?? []).filter((turn) => {
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
  const indexedCandidates = entries.flatMap((turn) => turn[3]
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
  const topologyCandidates = liveTopologyCandidates(vehicle, intersectionId)
  const candidates = indexedCandidates.length > 0
    ? indexedCandidates
    : topologyCandidates.map((candidate) => ({
        turn: null as CompactRouteTurnEntry | null,
        candidate,
      }))
  let unique = [...new Map(candidates.map((item) => [item.candidate.connectionKey, item])).values()]
  const alreadyOnOutgoingEdge = unique.length > 0 && unique.every(({ turn }) => (
    turn != null && vehicle.road_id === turn[2]
  ))
  if (
    unique.length > 1
    && !alreadyOnOutgoingEdge
    && Number.isInteger(vehicle.target_lane_index)
    && Number(vehicle.target_lane_index) >= 0
  ) {
    unique = unique.filter(({ candidate }) => (
      candidate.toLaneIndex === Number(vehicle.target_lane_index)
    ))
  }
  const previousKey = previousLock.connectionKey
  const previousCandidate = previousKey ? routeTurnIndex.connections[previousKey] : undefined
  const previousLockCompatible = Boolean(
    previousCandidate
    && previousCandidate.intersectionId === intersectionId
    && connectionContainsLiveLane(previousCandidate, vehicle),
  )
  const releasedStaleLock = Boolean(previousKey && !previousLockCompatible)
  if (previousKey && previousLockCompatible) {
    unique = unique.filter(({ candidate }) => candidate.connectionKey === previousKey)
  }
  if (unique.length !== 1) {
    const incomingPending = !vehicle.road_id.startsWith(':')
      && unique.length > 1
      && unique.every(({ candidate }) => vehicle.lane_id === candidate.fromLaneId)
    return {
      status: incomingPending ? 'pending' : unique.length > 1 ? 'ambiguous' : (
        turns?.length ? 'mismatch' : 'unavailable'
      ),
      hint: null,
      candidateCount: unique.length,
      reason: incomingPending
        ? 'awaiting_unique_internal_lane'
        : unique.length > 1
        ? 'multiple_route_connections_match_vehicle'
        : turns?.length
          ? 'indexed_route_does_not_match_live_edge_or_lane'
          : 'vehicle_not_in_fixed_route_index',
      routeCursor,
      connectionLock: releasedStaleLock || incomingPending
        ? unlockedConnection
        : previousLock,
      releasedStaleLock,
    }
  }
  const { turn, candidate } = unique[0]
  const lockSource: VehicleConnectionLock['source'] = turn
    ? 'fixed_route_index'
    : 'live_topology'
  const connectionLock = lockForCandidate(vehicle, candidate, lockSource)
  if (connectionLock.stage === 'exiting' && !previousLockCompatible) {
    return {
      status: 'unavailable',
      hint: null,
      candidateCount: 1,
      reason: 'outgoing_lane_requires_confirmed_connection_lock',
      routeCursor: turn == null ? previousRouteCursor : turn[0] + 1,
      connectionLock: unlockedConnection,
      releasedStaleLock,
    }
  }
  return {
    status: 'hit',
    candidateCount: 1,
    routeCursor: turn == null
      ? previousRouteCursor
      : vehicle.road_id === turn[2] ? turn[0] + 1 : turn[0],
    connectionLock,
    releasedStaleLock,
    hint: {
      period: typedPeriod,
      flowId,
      routeIndex: turn?.[0] ?? routeCursor ?? -1,
      fromEdge: turn?.[1] ?? candidate.fromEdge,
      toEdge: turn?.[2] ?? candidate.toEdge,
      intersectionId: candidate.intersectionId,
      connectionKey: candidate.connectionKey,
      motionPathKey: candidate.motionPathKey,
      source: lockSource,
    },
  }
}

export function resolveVehicleRouteTurnHint(
  vehicle: TrafficVehicleView,
  period: string | null | undefined,
  intersectionId: string | null | undefined,
  lockedConnectionKey?: VehicleConnectionLock | string,
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
