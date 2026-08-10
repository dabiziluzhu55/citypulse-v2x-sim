import type { RealisticIntersectionManifest, RealisticLane } from './intersectionManifest.ts'
import type { SimulationLaneRuntime } from '../../types/simulation.ts'
import type { RoadCoordinateProjector } from '../roadGeometry.ts'
import { shortestAngleDelta } from '../vehicleOrientation.ts'
import {
  buildCollisionFreeIntersectionApproaches,
  buildIntersectionApproachGeometry,
} from './intersectionApproachGeometry.ts'
import {
  projectBd09ToWebMercator,
  unprojectWebMercatorToBd09,
} from '../sceneCoordinates.ts'
import {
  nearestPolylineProgress,
  samplePolyline,
  tangentAtProgress,
  visualLanePoints,
} from './intersectionRoadGeometry.ts'

export type LaneHeadingResolver = (laneId: string, lanePosition?: number) => number | null
export interface ResolvedLanePose {
  longitude: number
  latitude: number
  heading: number
  modelCenterResolved: boolean
  trackKey: string
  trackProgress: number
  modelCenterDistanceMeters: number
  naturalFrontDistanceMeters: number
  stopFrontLimitDistanceMeters?: number
  stopClamped: boolean
}
export interface LanePoseRuntimeOptions {
  speedMetersPerSecond?: number
  expectedHeading?: number | null
  laneRuntime?: SimulationLaneRuntime | null
  previousTrackProgress?: number
  allowStopClamp?: boolean
  minimumModelCenterDistanceMeters?: number
  maximumModelCenterDistanceMeters?: number
}
export type LanePoseResolver = (
  laneId: string,
  coordinate: readonly [number, number],
  modelCenterOffsetMeters?: number,
  previousLaneId?: string,
  previousTrackKey?: string,
  options?: LanePoseRuntimeOptions,
) => ResolvedLanePose | null

export const VISUAL_STOP_BOUNDARY_CLEARANCE_METERS = 0.25
export const VISUAL_STOP_LINE_HALF_WIDTH_METERS = 0.21

export function visualStopFrontLimitDistance(
  stopBoundaryDistance: number,
  horizontalScale: number,
): number {
  return stopBoundaryDistance - (
    VISUAL_STOP_LINE_HALF_WIDTH_METERS
    + VISUAL_STOP_BOUNDARY_CLEARANCE_METERS
  ) * horizontalScale
}

interface LaneTrack {
  key: string
  laneId: string
  sourcePoints: [number, number][]
  renderPoints: [number, number][]
  beforeRenderPoints?: [number, number][]
  routeKey?: string
  routeLaneIds: string[]
  stopBoundaryDistance?: number
}

function polylineLength(points: [number, number][]): number {
  return points.slice(1).reduce((total, point, index) => (
    total + Math.hypot(point[0] - points[index][0], point[1] - points[index][1])
  ), 0)
}

export function mapSourceProgressToRenderDistance(
  progress: number,
  sourceLength: number,
  renderLength: number,
): number {
  const x = Math.max(0, Math.min(1, progress))
  if (sourceLength <= 1e-6 || renderLength <= 1e-6) return x * renderLength
  const endpointSlope = sourceLength / renderLength
  const x2 = x * x
  const x3 = x2 * x
  const normalizedDistance = (
    (-2 * x3 + 3 * x2)
    + (2 * x3 - 3 * x2 + x) * endpointSlope
  )
  return Math.max(0, Math.min(renderLength, normalizedDistance * renderLength))
}

function joinPolylines(...polylines: Array<[number, number][]>): [number, number][] {
  const joined: [number, number][] = []
  for (const points of polylines) {
    for (const point of points) {
      const previous = joined.at(-1)
      if (previous && Math.hypot(point[0] - previous[0], point[1] - previous[1]) <= 0.001) continue
      joined.push(point)
    }
  }
  return joined
}

function laneHeadingAtPosition(lane: RealisticLane, lanePosition = 0): number | null {
  const points = visualLanePoints(lane)
  if (points.length < 2) return null
  const scale = lane.widthMeters && lane.width > 0 ? lane.width / lane.widthMeters : 1
  let remaining = Math.max(0, lanePosition * scale)
  for (let index = 1; index < points.length; index += 1) {
    const previous = points[index - 1]
    const current = points[index]
    const dx = current[0] - previous[0]
    const dy = current[1] - previous[1]
    const length = Math.hypot(dx, dy)
    if (length <= 1e-6) continue
    if (remaining <= length || index === points.length - 1) return Math.atan2(dy, dx)
    remaining -= length
  }
  return null
}

export function createIntersectionLanePoseResolver(
  manifest: Pick<RealisticIntersectionManifest, 'edges' | 'connections' | 'origin' | 'horizontalScale'>,
  projector: RoadCoordinateProjector,
): LanePoseResolver {
  const horizontalScale = manifest.horizontalScale ?? 1
  const stopBoundaries = new Map<string, number>()
  for (const { geometry } of buildCollisionFreeIntersectionApproaches(
    manifest.edges,
    horizontalScale,
  )) {
    for (const sample of geometry.laneSamples) {
      const points = visualLanePoints(sample.lane)
      const nearest = nearestPolylineProgress(sample.point, points)
      if (!nearest) continue
      stopBoundaries.set(sample.lane.id, nearest.progress * polylineLength(points))
    }
  }
  for (const edge of manifest.edges.filter((candidate) => candidate.incoming)) {
    const fallback = buildIntersectionApproachGeometry(
      edge,
      horizontalScale,
      manifest.edges,
    )
    if (!fallback) continue
    for (const sample of fallback.laneSamples) {
      if (stopBoundaries.has(sample.lane.id)) continue
      const points = visualLanePoints(sample.lane)
      const nearest = nearestPolylineProgress(sample.point, points)
      if (!nearest) continue
      stopBoundaries.set(sample.lane.id, nearest.progress * polylineLength(points))
    }
  }
  const tracks = new Map<string, LaneTrack[]>()
  const tracksByKey = new Map<string, LaneTrack>()
  const addTrack = (track: LaneTrack) => {
    const candidates = tracks.get(track.laneId) ?? []
    candidates.push(track)
    tracks.set(track.laneId, candidates)
    tracksByKey.set(track.key, track)
  }
  for (const edge of manifest.edges) {
    for (const lane of edge.lanes) {
      if (!lane.renderPoints?.length) continue
      addTrack({
        key: `lane:${lane.id}`,
        laneId: lane.id,
        sourcePoints: lane.points,
        renderPoints: lane.renderPoints,
        routeLaneIds: [lane.id],
        stopBoundaryDistance: edge.incoming ? stopBoundaries.get(lane.id) : undefined,
      })
    }
  }
  for (const connection of manifest.connections) {
    const fromEdge = manifest.edges.find((edge) => edge.id === connection.fromEdge)
    const fromLane = fromEdge?.lanes.find((lane) => lane.index === connection.fromLane)
    const toEdge = manifest.edges.find((edge) => edge.id === connection.toEdge)
    const toLane = toEdge?.lanes.find((lane) => lane.index === connection.toLane)
    const segments = connection.viaSegments?.length
      ? connection.viaSegments
      : connection.viaLaneId && connection.viaPoints?.length && connection.renderPoints?.length
        ? [{
            laneId: connection.viaLaneId,
            points: connection.viaPoints,
            renderPoints: connection.renderPoints,
          }]
        : []
    if (!fromLane?.renderPoints?.length || !toLane?.renderPoints?.length || segments.length === 0) continue
    const routeKey = `${connection.tlsId}:${connection.linkIndex}`
    const routeLaneIds = [fromLane.id, ...segments.map((segment) => segment.laneId), toLane.id]
    let beforeRenderPoints = fromLane.renderPoints
    for (const segment of segments) {
      addTrack({
        key: `${routeKey}:${segment.laneId}`,
        laneId: segment.laneId,
        sourcePoints: segment.points,
        renderPoints: segment.renderPoints,
        beforeRenderPoints,
        routeKey,
        routeLaneIds,
      })
      beforeRenderPoints = joinPolylines(beforeRenderPoints, segment.renderPoints)
    }
    addTrack({
      key: `${routeKey}:${toLane.id}`,
      laneId: toLane.id,
      sourcePoints: toLane.points,
      renderPoints: toLane.renderPoints,
      beforeRenderPoints,
      routeKey,
      routeLaneIds,
    })
  }
  const projectedOrigin = projector([
    manifest.origin.longitude,
    manifest.origin.latitude,
    0,
  ])
  const originPlane = projectBd09ToWebMercator([projectedOrigin[0], projectedOrigin[1]])
  const maximumSnapDistance = 12 * (manifest.horizontalScale ?? 1)
  return (
    laneId,
    coordinate,
    modelCenterOffsetMeters = 0,
    previousLaneId,
    previousTrackKey,
    options,
  ) => {
    const candidates = tracks.get(laneId)
    if (!candidates?.length) return null
    const projected = projectBd09ToWebMercator(coordinate)
    const local: [number, number] = [
      projected[0] - originPlane[0],
      projected[1] - originPlane[1],
    ]
    const previousTrack = previousTrackKey ? tracksByKey.get(previousTrackKey) : undefined
    const routeCandidates = previousTrack?.routeKey
      ? candidates.filter((candidate) => candidate.routeKey === previousTrack.routeKey)
      : previousLaneId
        ? candidates.filter((candidate) => candidate.routeLaneIds.includes(previousLaneId))
        : []
    if (
      previousTrack?.routeKey
      && previousLaneId
      && previousLaneId !== laneId
      && routeCandidates.length === 0
    ) return null
    const resolvedCandidates = (routeCandidates.length ? routeCandidates : candidates)
      .map((track) => {
        const nearest = nearestPolylineProgress(local, track.sourcePoints)
        const heading = nearest ? tangentAtProgress(track.sourcePoints, nearest.progress) : null
        const headingPenalty = heading != null && options?.expectedHeading != null
          ? Math.abs(shortestAngleDelta(heading, options.expectedHeading)) * 2.5 * horizontalScale
          : 0
        const progressPenalty = nearest
          && previousTrackKey === track.key
          && options?.previousTrackProgress != null
          && nearest.progress + 0.04 < options.previousTrackProgress
          ? (options.previousTrackProgress - nearest.progress) * 20 * horizontalScale
          : 0
        return {
          track,
          nearest,
          score: (nearest?.distance ?? Number.POSITIVE_INFINITY) + headingPenalty + progressPenalty,
        }
      })
      .filter((candidate) => candidate.nearest !== null)
      .sort((left, right) => left.score - right.score)
    const resolved = resolvedCandidates[0]
    if (!resolved || resolved.nearest!.distance > maximumSnapDistance) return null
    const { track } = resolved
    const nearest = resolved.nearest!
    const sourceLength = polylineLength(track.sourcePoints)
    const renderLength = polylineLength(track.renderPoints)
    const backtrackSceneUnits = Math.max(0, modelCenterOffsetMeters) * horizontalScale
    const mappedFrontDistance = mapSourceProgressToRenderDistance(
      nearest.progress,
      sourceLength,
      renderLength,
    )
    let targetDistance = mappedFrontDistance - backtrackSceneUnits
    let stopClamped = false
    const stopFrontLimitDistance = track.stopBoundaryDistance == null
      ? undefined
      : visualStopFrontLimitDistance(track.stopBoundaryDistance, horizontalScale)
    if (
      track.stopBoundaryDistance != null
      && options?.allowStopClamp !== false
      && (options?.speedMetersPerSecond ?? Number.POSITIVE_INFINITY) <= 0.35
      && options?.laneRuntime?.role !== 'outgoing'
      && options?.laneRuntime?.role !== 'internal'
    ) {
      const runtime = options?.laneRuntime
      const signalState = runtime?.signal_state?.toLowerCase() ?? ''
      const explicitStop = runtime?.lane_has_green === false
        || (runtime?.lane_has_green !== true && /^[ry]+$/.test(signalState))
      const lacksSignalDetail = !runtime
        || (runtime.lane_has_green == null && !signalState)
      const maximumFrontDistance = stopFrontLimitDistance!
      const overrunMeters = (mappedFrontDistance - maximumFrontDistance) / horizontalScale
      const conservativeFallback = lacksSignalDetail
        && (options?.speedMetersPerSecond ?? Number.POSITIVE_INFINITY) <= 0.05
        && overrunMeters > 0
        && overrunMeters <= 1.5
      if (mappedFrontDistance > maximumFrontDistance && (explicitStop || conservativeFallback)) {
        targetDistance = maximumFrontDistance - backtrackSceneUnits
        stopClamped = true
      }
    }
    if (
      previousTrackKey === track.key
      && Number.isFinite(options?.minimumModelCenterDistanceMeters)
    ) {
      targetDistance = Math.max(
        targetDistance,
        Number(options?.minimumModelCenterDistanceMeters) * horizontalScale,
      )
    }
    if (Number.isFinite(options?.maximumModelCenterDistanceMeters)) {
      targetDistance = Math.min(
        targetDistance,
        Number(options?.maximumModelCenterDistanceMeters) * horizontalScale,
      )
    }
    let activePoints = track.renderPoints
    let renderProgress = renderLength > 1e-6 ? Math.max(0, targetDistance / renderLength) : nearest.progress
    if (targetDistance < 0 && track.beforeRenderPoints?.length) {
      const beforeLength = polylineLength(track.beforeRenderPoints)
      activePoints = track.beforeRenderPoints
      renderProgress = beforeLength > 1e-6
        ? Math.max(0, 1 + targetDistance / beforeLength)
        : 1
    }
    const renderPoint = samplePolyline(activePoints, renderProgress)
    const heading = tangentAtProgress(activePoints, renderProgress)
    if (heading == null) return null
    const [longitude, latitude] = unprojectWebMercatorToBd09([
      originPlane[0] + renderPoint[0],
      originPlane[1] + renderPoint[1],
    ])
    return {
      longitude,
      latitude,
      heading,
      modelCenterResolved: modelCenterOffsetMeters > 0,
      trackKey: track.key,
      trackProgress: nearest.progress,
      modelCenterDistanceMeters: targetDistance / horizontalScale,
      naturalFrontDistanceMeters: mappedFrontDistance / horizontalScale,
      stopFrontLimitDistanceMeters: stopFrontLimitDistance == null
        ? undefined
        : stopFrontLimitDistance / horizontalScale,
      stopClamped,
    }
  }
}

export function createIntersectionLaneHeadingResolver(
  manifest: Pick<RealisticIntersectionManifest, 'edges'>,
): LaneHeadingResolver {
  const lanes = new Map(
    manifest.edges.flatMap((edge) => edge.lanes.map((lane) => [lane.id, lane] as const)),
  )
  return (laneId, lanePosition) => {
    const lane = lanes.get(laneId)
    return lane ? laneHeadingAtPosition(lane, lanePosition) : null
  }
}
