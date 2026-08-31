import type { TrafficVehicleView } from '../types/traffic'
import type { EventLanePositionIndexEntry } from '../utils/eventLanePositionIndex'
import type {
  VehicleMotionConnectionIndexEntry,
  VehicleMotionIndexGeneration,
  VehicleMotionLaneIndexEntry,
} from './vehicleMotionIndex.ts'

interface LaneTrack {
  laneId: string
  edgeId: string
  laneIndex: number
  internal: boolean
  widthMeters: number
  sourcePoints: Array<[number, number]>
  coordinates: Array<[number, number]>
  cumulativeMeters: number[]
  lengthMeters: number
}

interface CompiledCanonicalSegment {
  id: string
  evidence: CanonicalRouteEvidence
  tracks: LaneTrack[]
  offsets: number[]
  startStation: number
  endStation: number
  laneChange?: { left: LaneTrack; right: LaneTrack; leftStation: number; rightStation: number }
}

export type CanonicalRouteEvidence =
  | 'same_lane'
  | 'lane_change'
  | 'unique_connection'
  | 'authoritative_endpoint'
  | 'unresolved'

export type CanonicalVehicleInterpolationSource =
  | 'lane_frenet'
  | 'lane_change_corridor'
  | 'sumo_connection'
  | 'authoritative_endpoint'
  | 'unresolved'

export interface CanonicalVehicleInterpolation {
  longitude: number | null
  latitude: number | null
  sourceX: number | null
  sourceY: number | null
  laneId: string
  laneStation: number | null
  headingRadians: number | null
  segmentId: string
  routeEvidence: CanonicalRouteEvidence
  source: CanonicalVehicleInterpolationSource
  resolved: boolean
}

export interface VehicleMotionLayerAudit {
  networkSourceSha256: string
  laneCount: number
  connectionCount: number
  compiledSegmentCount: number
  compiledSegmentCacheHitCount: number
  unresolvedSegmentCount: number
  authoritativeEndpointCount: number
  sourceLaneMismatchCount: number
  maximumSourceLaneErrorMeters: number
  coordinateConversionMismatchCount: number
  maximumCoordinateConversionErrorMeters: number
}

let laneTracks = new Map<string, LaneTrack>()
let connections: VehicleMotionConnectionIndexEntry[] = []
let connectionsByLanePair = new Map<string, VehicleMotionConnectionIndexEntry[]>()
let networkSourceSha256 = ''
const compiledSegments = new Map<string, CompiledCanonicalSegment | null>()
const compiledSegmentsById = new Map<string, CompiledCanonicalSegment>()
let compiledSegmentCount = 0
let compiledSegmentCacheHitCount = 0
let unresolvedSegmentCount = 0
let authoritativeEndpointCount = 0
let sourceLaneMismatchCount = 0
let maximumSourceLaneErrorMeters = 0
let coordinateConversionMismatchCount = 0
let maximumCoordinateConversionErrorMeters = 0
const auditedEndpoints = new Set<string>()

function lanePairKey(leftLaneId: string, rightLaneId: string): string {
  return `${leftLaneId}\u0000${rightLaneId}`
}

function indexConnectionsByLanePair(
  entries: readonly VehicleMotionConnectionIndexEntry[],
): Map<string, VehicleMotionConnectionIndexEntry[]> {
  const indexed = new Map<string, VehicleMotionConnectionIndexEntry[]>()
  for (const connection of entries) {
    const path = [connection.fromLaneId, ...connection.viaLaneIds, connection.toLaneId]
    for (let leftIndex = 0; leftIndex < path.length - 1; leftIndex += 1) {
      for (let rightIndex = leftIndex + 1; rightIndex < path.length; rightIndex += 1) {
        const key = lanePairKey(path[leftIndex], path[rightIndex])
        const candidates = indexed.get(key)
        if (candidates) candidates.push(connection)
        else indexed.set(key, [connection])
      }
    }
  }
  return indexed
}

function distanceMeters(left: readonly number[], right: readonly number[]): number {
  return Math.hypot(right[0] - left[0], right[1] - left[1])
}

function geographicDistanceMeters(left: readonly number[], right: readonly number[]): number {
  const latitude = (left[1] + right[1]) * Math.PI / 360
  return Math.hypot(
    (right[0] - left[0]) * 111_320 * Math.cos(latitude),
    (right[1] - left[1]) * 110_574,
  )
}

function buildTrack(entry: VehicleMotionLaneIndexEntry): LaneTrack {
  const cumulativeMeters = [0]
  for (let index = 1; index < entry.sourcePoints.length; index += 1) {
    cumulativeMeters.push(
      cumulativeMeters[index - 1]
      + distanceMeters(entry.sourcePoints[index - 1], entry.sourcePoints[index]),
    )
  }
  return {
    laneId: entry.laneId,
    edgeId: entry.edgeId,
    laneIndex: entry.laneIndex,
    internal: entry.internal,
    widthMeters: entry.widthMeters,
    sourcePoints: entry.sourcePoints.map((point) => [point[0], point[1]]),
    coordinates: entry.coordinates.map((point) => [point[0], point[1]]),
    cumulativeMeters,
    lengthMeters: cumulativeMeters.at(-1) ?? entry.lengthMeters,
  }
}

export function registerCanonicalVehicleMotionIndex(index: VehicleMotionIndexGeneration): void {
  laneTracks = new Map(index.lanes.map((lane) => [lane.laneId, buildTrack(lane)]))
  connections = index.connections.map((connection) => ({
    ...connection,
    viaLaneIds: [...connection.viaLaneIds],
  }))
  connectionsByLanePair = indexConnectionsByLanePair(connections)
  networkSourceSha256 = index.networkSource.sha256
  resetCanonicalVehicleMotionDiagnostics()
}

// Kept for lightweight tests and event-only startup. The full SUMO motion
// index replaces these approximate tracks as soon as it has loaded.
export function registerCanonicalVehicleLaneGeometry(
  entries: readonly EventLanePositionIndexEntry[],
): void {
  if (networkSourceSha256) return
  laneTracks = new Map(entries
    .filter((entry) => entry.kind === 'driving' && entry.coordinates.length >= 2)
    .map((entry) => {
      const origin = entry.coordinates[0]
      const latitude = origin[1] * Math.PI / 180
      const sourcePoints = entry.coordinates.map((point) => [
        (point[0] - origin[0]) * 111_320 * Math.cos(latitude),
        (point[1] - origin[1]) * 110_574,
      ] as [number, number])
      return [entry.laneId, buildTrack({
        laneId: entry.laneId,
        edgeId: entry.edgeId,
        laneIndex: 0,
        intersectionId: entry.intersectionId,
        internal: entry.laneId.startsWith(':'),
        widthMeters: 3.5,
        lengthMeters: 0,
        sourcePoints,
        coordinates: entry.coordinates,
      })] as const
    }))
  connections = []
  connectionsByLanePair = new Map()
  compiledSegments.clear()
}

function projectStation(track: LaneTrack, point: readonly [number, number]): { station: number; error: number } | null {
  let bestDistance = Number.POSITIVE_INFINITY
  let bestStation = 0
  for (let index = 1; index < track.sourcePoints.length; index += 1) {
    const start = track.sourcePoints[index - 1]
    const end = track.sourcePoints[index]
    const dx = end[0] - start[0]
    const dy = end[1] - start[1]
    const lengthSquared = dx * dx + dy * dy
    const amount = lengthSquared > 1e-9
      ? Math.max(0, Math.min(1, ((point[0] - start[0]) * dx + (point[1] - start[1]) * dy) / lengthSquared))
      : 0
    const px = start[0] + dx * amount
    const py = start[1] + dy * amount
    const candidate = Math.hypot(point[0] - px, point[1] - py)
    if (candidate >= bestDistance) continue
    bestDistance = candidate
    bestStation = track.cumulativeMeters[index - 1] + Math.sqrt(lengthSquared) * amount
  }
  if (!Number.isFinite(bestDistance)) return null
  return { station: bestStation, error: bestDistance }
}

function vehicleStation(track: LaneTrack, vehicle: TrafficVehicleView): number | null {
  const lanePosition = Number(vehicle.lane_position)
  const reliableLanePosition = Number.isFinite(lanePosition)
    && lanePosition >= -0.5
    && lanePosition <= track.lengthMeters + 2
  let sourcePoint: [number, number] = [vehicle.x, vehicle.y]
  if (
    (!Number.isFinite(sourcePoint[0]) || !Number.isFinite(sourcePoint[1]))
    && vehicle.longitude != null
    && vehicle.latitude != null
  ) {
    const nearest = track.coordinates
      .map((point, index) => ({
        index,
        distance: geographicDistanceMeters(point, [vehicle.longitude!, vehicle.latitude!]),
      }))
      .sort((left, right) => left.distance - right.distance)[0]
    if (!nearest || nearest.distance > track.widthMeters / 2 + 10) return null
    sourcePoint = track.sourcePoints[nearest.index]
  }
  const projection = projectStation(track, sourcePoint)
  if (!projection) return null
  const endpointKey = `${track.laneId}:${vehicle.x.toFixed(3)}:${vehicle.y.toFixed(3)}`
  if (!auditedEndpoints.has(endpointKey)) {
    auditedEndpoints.add(endpointKey)
    maximumSourceLaneErrorMeters = Math.max(maximumSourceLaneErrorMeters, projection.error)
    if (projection.error > track.widthMeters / 2 + 1.5) sourceLaneMismatchCount += 1
    if (vehicle.longitude != null && vehicle.latitude != null) {
      const sampled = sampleTrack(track, projection.station)
      if (sampled.longitude != null && sampled.latitude != null) {
        const conversionError = geographicDistanceMeters(
          [sampled.longitude, sampled.latitude],
          [vehicle.longitude, vehicle.latitude],
        )
        maximumCoordinateConversionErrorMeters = Math.max(
          maximumCoordinateConversionErrorMeters,
          conversionError,
        )
        if (conversionError > 1) coordinateConversionMismatchCount += 1
      }
    }
  }
  const projectedStation = Math.max(
    0,
    Math.min(track.lengthMeters, projection.station),
  )
  const reportedStation = Math.max(
    0,
    Math.min(track.lengthMeters, lanePosition),
  )

  if (reliableLanePosition && projection.error > track.widthMeters / 2 + 1.5) {
    // Keep the rendered endpoint on its authoritative lane even when the raw
    // x/y sample is temporarily outside the indexed lane corridor.
    return reportedStation
  }

  if (projection.error <= track.widthMeters / 2 + 1.5) {
    if (!reliableLanePosition) return projectedStation

    // lane_position can lag behind the live x/y telemetry. Prefer the live
    // projection when the two longitudinal positions are no longer coherent.
    const stationGapMeters = Math.abs(reportedStation - projectedStation)
    return stationGapMeters <= 2
      ? reportedStation
      : projectedStation
  }

  if (!networkSourceSha256 && vehicle.longitude != null && vehicle.latitude != null) {
    let nearestIndex = 0
    let nearestDistance = Number.POSITIVE_INFINITY
    for (let index = 0; index < track.coordinates.length; index += 1) {
      const candidate = geographicDistanceMeters(
        track.coordinates[index],
        [vehicle.longitude, vehicle.latitude],
      )
      if (candidate < nearestDistance) {
        nearestDistance = candidate
        nearestIndex = index
      }
    }
    if (nearestDistance <= 15) return track.cumulativeMeters[nearestIndex]
  }

  return null
}

function sampleTrack(track: LaneTrack, stationMeters: number): CanonicalVehicleInterpolation {
  const station = Math.max(0, Math.min(track.lengthMeters, stationMeters))
  let upper = track.cumulativeMeters.findIndex((value) => value >= station)
  if (upper <= 0) upper = 1
  const startStation = track.cumulativeMeters[upper - 1]
  const endStation = track.cumulativeMeters[upper]
  const amount = endStation > startStation ? (station - startStation) / (endStation - startStation) : 0
  const sourceStart = track.sourcePoints[upper - 1]
  const sourceEnd = track.sourcePoints[upper]
  const geoStart = track.coordinates[upper - 1]
  const geoEnd = track.coordinates[upper]
  return {
    longitude: geoStart[0] + (geoEnd[0] - geoStart[0]) * amount,
    latitude: geoStart[1] + (geoEnd[1] - geoStart[1]) * amount,
    sourceX: sourceStart[0] + (sourceEnd[0] - sourceStart[0]) * amount,
    sourceY: sourceStart[1] + (sourceEnd[1] - sourceStart[1]) * amount,
    laneId: track.laneId,
    laneStation: station,
    headingRadians: Math.atan2(sourceEnd[1] - sourceStart[1], sourceEnd[0] - sourceStart[0]),
    segmentId: '',
    routeEvidence: 'unresolved',
    source: 'unresolved',
    resolved: true,
  }
}

function segmentCacheKey(left: TrafficVehicleView, right: TrafficVehicleView): string {
  return [
    left.vehicle_id,
    left.lane_id,
    right.lane_id,
    left.x.toFixed(3),
    left.y.toFixed(3),
    right.x.toFixed(3),
    right.y.toFixed(3),
    Number(left.lane_position ?? -1).toFixed(3),
    Number(right.lane_position ?? -1).toFixed(3),
  ].join('|')
}

function connectionCandidates(
  leftLaneId: string,
  rightLaneId: string,
): VehicleMotionConnectionIndexEntry[] {
  const candidates = connectionsByLanePair.get(
    lanePairKey(leftLaneId, rightLaneId),
  ) ?? []

  if (candidates.length <= 1) return candidates

  // 当车辆已经进入 SUMO 内部车道时，优先选择从该内部车道
  // 正式开始的连接，避免同时命中“完整连接”和“内部子连接”。
  const exactFromCandidates = candidates.filter(
    (candidate) => candidate.fromLaneId === leftLaneId,
  )

  return exactFromCandidates.length > 0
    ? exactFromCandidates
    : candidates
}

function compileSegment(left: TrafficVehicleView, right: TrafficVehicleView): CompiledCanonicalSegment | null {
  const key = segmentCacheKey(left, right)
  if (compiledSegments.has(key)) {
    compiledSegmentCacheHitCount += 1
    return compiledSegments.get(key) ?? null
  }
  const leftTrack = laneTracks.get(left.lane_id)
  const rightTrack = laneTracks.get(right.lane_id)
  const reject = () => {
    unresolvedSegmentCount += 1
    compiledSegments.set(key, null)
    return null
  }
  if (!leftTrack || !rightTrack) return reject()
  const leftStation = vehicleStation(leftTrack, left)
  const rightStation = vehicleStation(rightTrack, right)
  if (leftStation == null || rightStation == null) return reject()

  let segment: CompiledCanonicalSegment | null = null
  if (leftTrack.laneId === rightTrack.laneId && rightStation + 0.25 >= leftStation) {
    segment = {
      id: `lane:${leftTrack.laneId}:${leftStation.toFixed(2)}-${rightStation.toFixed(2)}`,
      evidence: 'same_lane',
      tracks: [leftTrack],
      offsets: [0],
      startStation: leftStation,
      endStation: Math.max(leftStation, rightStation),
    }
  } else if (
    !leftTrack.internal
    && !rightTrack.internal
    && leftTrack.edgeId === rightTrack.edgeId
    && Math.abs(leftTrack.laneIndex - rightTrack.laneIndex) === 1
    && rightStation + 1 >= leftStation
  ) {
    segment = {
      id: `change:${leftTrack.laneId}>${rightTrack.laneId}:${leftStation.toFixed(2)}-${rightStation.toFixed(2)}`,
      evidence: 'lane_change',
      tracks: [leftTrack, rightTrack],
      offsets: [0, 0],
      startStation: leftStation,
      endStation: Math.max(leftStation, rightStation),
      laneChange: { left: leftTrack, right: rightTrack, leftStation, rightStation },
    }
  } else {
    const candidates = connectionCandidates(leftTrack.laneId, rightTrack.laneId)
    if (candidates.length !== 1) return reject()
    const connection = candidates[0]
    const pathLaneIds = [connection.fromLaneId, ...connection.viaLaneIds, connection.toLaneId]
    const tracks = pathLaneIds.map((laneId) => laneTracks.get(laneId)).filter((track): track is LaneTrack => Boolean(track))
    if (tracks.length !== pathLaneIds.length) return reject()
    const offsets = tracks.map((_, index) => (
      index === 0 ? 0 : tracks.slice(0, index).reduce((sum, track) => sum + track.lengthMeters, 0)
    ))
    const leftIndex = pathLaneIds.indexOf(leftTrack.laneId)
    const rightIndex = pathLaneIds.lastIndexOf(rightTrack.laneId)
    const startStation = offsets[leftIndex] + leftStation
    const endStation = offsets[rightIndex] + rightStation
    if (endStation + 0.25 < startStation) return reject()
    segment = {
      id: `connection:${connection.connectionId}:${startStation.toFixed(2)}-${endStation.toFixed(2)}`,
      evidence: 'unique_connection',
      tracks,
      offsets,
      startStation,
      endStation: Math.max(startStation, endStation),
    }
  }
  compiledSegmentCount += 1
  compiledSegments.set(key, segment)
  compiledSegmentsById.set(segment.id, segment)
  if (compiledSegments.size > 20_000) compiledSegments.delete(compiledSegments.keys().next().value as string)
  return segment
}

function sampleSegment(segment: CompiledCanonicalSegment, ratio: number): CanonicalVehicleInterpolation {
  const amount = Math.max(0, Math.min(1, ratio))
  if (segment.laneChange) {
    const smooth = amount * amount * (3 - 2 * amount)
    const leftStation = segment.laneChange.leftStation
      + (segment.laneChange.rightStation - segment.laneChange.leftStation) * amount
    const rightStation = segment.laneChange.leftStation
      + (segment.laneChange.rightStation - segment.laneChange.leftStation) * amount
    const leftPoint = sampleTrack(segment.laneChange.left, leftStation)
    const rightPoint = sampleTrack(segment.laneChange.right, rightStation)
    const longitude = Number(leftPoint.longitude) + (Number(rightPoint.longitude) - Number(leftPoint.longitude)) * smooth
    const latitude = Number(leftPoint.latitude) + (Number(rightPoint.latitude) - Number(leftPoint.latitude)) * smooth
    const sourceX = Number(leftPoint.sourceX) + (Number(rightPoint.sourceX) - Number(leftPoint.sourceX)) * smooth
    const sourceY = Number(leftPoint.sourceY) + (Number(rightPoint.sourceY) - Number(leftPoint.sourceY)) * smooth
    const epsilon = 0.002
    const beforeAmount = Math.max(0, amount - epsilon)
    const afterAmount = Math.min(1, amount + epsilon)
    const beforeSmooth = beforeAmount * beforeAmount * (3 - 2 * beforeAmount)
    const afterSmooth = afterAmount * afterAmount * (3 - 2 * afterAmount)
    const beforeLeft = sampleTrack(segment.laneChange.left, segment.laneChange.leftStation
      + (segment.laneChange.rightStation - segment.laneChange.leftStation) * beforeAmount)
    const beforeRight = sampleTrack(segment.laneChange.right, segment.laneChange.leftStation
      + (segment.laneChange.rightStation - segment.laneChange.leftStation) * beforeAmount)
    const afterLeft = sampleTrack(segment.laneChange.left, segment.laneChange.leftStation
      + (segment.laneChange.rightStation - segment.laneChange.leftStation) * afterAmount)
    const afterRight = sampleTrack(segment.laneChange.right, segment.laneChange.leftStation
      + (segment.laneChange.rightStation - segment.laneChange.leftStation) * afterAmount)
    const bx = Number(beforeLeft.sourceX) + (Number(beforeRight.sourceX) - Number(beforeLeft.sourceX)) * beforeSmooth
    const by = Number(beforeLeft.sourceY) + (Number(beforeRight.sourceY) - Number(beforeLeft.sourceY)) * beforeSmooth
    const ax = Number(afterLeft.sourceX) + (Number(afterRight.sourceX) - Number(afterLeft.sourceX)) * afterSmooth
    const ay = Number(afterLeft.sourceY) + (Number(afterRight.sourceY) - Number(afterLeft.sourceY)) * afterSmooth
    return {
      longitude,
      latitude,
      sourceX,
      sourceY,
      laneId: amount < 0.5 ? segment.laneChange.left.laneId : segment.laneChange.right.laneId,
      laneStation: leftStation,
      headingRadians: Math.atan2(ay - by, ax - bx),
      segmentId: segment.id,
      routeEvidence: segment.evidence,
      source: 'lane_change_corridor',
      resolved: true,
    }
  }
  const station = segment.startStation + (segment.endStation - segment.startStation) * amount
  let trackIndex = segment.tracks.length - 1
  for (let index = 0; index < segment.tracks.length; index += 1) {
    if (station <= segment.offsets[index] + segment.tracks[index].lengthMeters + 1e-6) {
      trackIndex = index
      break
    }
  }
  const sampled = sampleTrack(segment.tracks[trackIndex], station - segment.offsets[trackIndex])
  return {
    ...sampled,
    segmentId: segment.id,
    routeEvidence: segment.evidence,
    source: segment.evidence === 'same_lane' ? 'lane_frenet' : 'sumo_connection',
    resolved: true,
  }
}

function authoritativeEndpoint(vehicle: TrafficVehicleView): CanonicalVehicleInterpolation {
  authoritativeEndpointCount += 1
  const track = laneTracks.get(vehicle.lane_id)
  const station = track ? vehicleStation(track, vehicle) : null
  if (track && station != null) {
    const sampled = sampleTrack(track, station)
    return {
      ...sampled,
      sourceX: vehicle.x,
      sourceY: vehicle.y,
      segmentId: `endpoint:${vehicle.vehicle_id}:${vehicle.lane_id}:${station.toFixed(2)}`,
      routeEvidence: 'authoritative_endpoint',
      source: 'authoritative_endpoint',
      resolved: true,
    }
  }
  return {
    longitude: vehicle.longitude,
    latitude: vehicle.latitude,
    sourceX: vehicle.x,
    sourceY: vehicle.y,
    laneId: vehicle.lane_id,
    laneStation: Number.isFinite(Number(vehicle.lane_position)) ? Number(vehicle.lane_position) : null,
    headingRadians: null,
    segmentId: `endpoint:${vehicle.vehicle_id}:${vehicle.lane_id}`,
    routeEvidence: 'authoritative_endpoint',
    source: 'authoritative_endpoint',
    resolved: vehicle.longitude != null && vehicle.latitude != null,
  }
}

export function interpolateCanonicalVehiclePosition(
  left: TrafficVehicleView,
  right: TrafficVehicleView,
  ratio: number,
): CanonicalVehicleInterpolation {
  const amount = Math.max(0, Math.min(1, ratio))
  if (amount <= 1e-9) return authoritativeEndpoint(left)
  if (amount >= 1 - 1e-9) return authoritativeEndpoint(right)
  const segment = compileSegment(left, right)
  if (segment) return sampleSegment(segment, amount)
  return {
    longitude: null,
    latitude: null,
    sourceX: null,
    sourceY: null,
    laneId: amount < 0.5 ? left.lane_id : right.lane_id,
    laneStation: null,
    headingRadians: null,
    segmentId: `unresolved:${left.vehicle_id}:${left.lane_id}>${right.lane_id}`,
    routeEvidence: 'unresolved',
    source: 'unresolved',
    resolved: false,
  }
}

export function canonicalVehicleLaneGeometryCount(): number {
  return laneTracks.size
}

export function sampleCanonicalVehicleSegment(
  segmentId: string | undefined,
  ratio: number,
): CanonicalVehicleInterpolation | null {
  if (!segmentId) return null
  const segment = compiledSegmentsById.get(segmentId)
  return segment ? sampleSegment(segment, ratio) : null
}

export function canonicalVehicleMotionAudit(): VehicleMotionLayerAudit {
  return {
    networkSourceSha256,
    laneCount: laneTracks.size,
    connectionCount: connections.length,
    compiledSegmentCount,
    compiledSegmentCacheHitCount,
    unresolvedSegmentCount,
    authoritativeEndpointCount,
    sourceLaneMismatchCount,
    maximumSourceLaneErrorMeters,
    coordinateConversionMismatchCount,
    maximumCoordinateConversionErrorMeters,
  }
}

export function resetCanonicalVehicleMotionDiagnostics(): void {
  compiledSegments.clear()
  compiledSegmentsById.clear()
  compiledSegmentCount = 0
  compiledSegmentCacheHitCount = 0
  unresolvedSegmentCount = 0
  authoritativeEndpointCount = 0
  sourceLaneMismatchCount = 0
  maximumSourceLaneErrorMeters = 0
  coordinateConversionMismatchCount = 0
  maximumCoordinateConversionErrorMeters = 0
  auditedEndpoints.clear()
}

export function canonicalGeographicSegmentLength(
  left: CanonicalVehicleInterpolation,
  right: CanonicalVehicleInterpolation,
): number {
  if (left.longitude == null || left.latitude == null || right.longitude == null || right.latitude == null) return 0
  return geographicDistanceMeters(
    [left.longitude, left.latitude],
    [right.longitude, right.latitude],
  )
}
